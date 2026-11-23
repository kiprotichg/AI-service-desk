"""ERP lookups the assistant may call while answering (Gemini function calling).

Every lookup is a fixed, parameterised SELECT on the configured company (ERP_COMPANY),
returns a small number of rows, and runs through erp.query (read-only, rolled back).
On purpose there are no lookups for employees, payroll, resources (national ID and
social security numbers) or contact phone numbers.

Who may use them: users whose email domain is in ERP_ALLOWED_EMAIL_DOMAINS.
"""
import datetime as dt
import decimal
import logging
import re

from flask import current_app

from . import erp

log = logging.getLogger(__name__)

NAV_EMPTY_DATE = dt.datetime(1753, 1, 1)

# NAV option fields are stored as numbers.
CUSTOMER_BLOCKED = {0: "not blocked", 1: "blocked for shipping", 2: "blocked for invoicing",
                    3: "blocked for everything"}
ITEM_STATUS = {0: "none", 1: "own service item", 2: "installed", 3: "temporarily installed",
               4: "defective"}
CONTRACT_TYPE = {0: "quote", 1: "contract", 2: "template"}
CONTRACT_STATUS = {0: "not signed", 1: "signed", 2: "cancelled"}

# NAV stores date formulas (e.g. "1Y") with each unit as a control character.
_DF_UNITS = {"\x01": ("C", "current"), "\x02": ("D", "day"), "\x03": ("WD", "weekday"),
             "\x04": ("W", "week"), "\x05": ("M", "month"), "\x06": ("Q", "quarter"),
             "\x07": ("Y", "year")}
_DF_PART = re.compile(r"([+-]?)(\d*)([\x01-\x07])")


def nav_date_formula(value):
    """NAV date formula -> readable text: "1\x07" -> "1Y (1 year)", "" -> None.
    Anything that isn't a date formula is returned unchanged."""
    if not value:
        return None
    code, words = "", []
    for sign, digits, unit in _DF_PART.findall(value):
        short, name = _DF_UNITS[unit]
        code += f"{sign}{digits}{short}"
        if short == "C":
            words.append("current period")
        else:
            n = int(digits or 1)
            words.append(f"{'-' if sign == '-' else ''}{n} {name}{'' if n == 1 else 's'}")
    return f"{code} ({', '.join(words)})" if code else value


# ------------------------------------------------------------- who may use them
def allowed_domains(app=None):
    raw = (app or current_app).config.get("ERP_ALLOWED_EMAIL_DOMAINS", "")
    return {d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()}


def allowed_emails(app=None):
    raw = (app or current_app).config.get("ERP_ALLOWED_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if "@" in e}


def user_may_use(user, app=None):
    """ERP lookups need ERP settings, a signed-in user with a confirmed email, and that
    email's domain in ERP_ALLOWED_EMAIL_DOMAINS or the address in ERP_ALLOWED_EMAILS."""
    if user is None or not erp.erp_enabled(app):
        return False
    cfg = (app or current_app).config
    if cfg.get("REQUIRE_EMAIL_VERIFICATION", True) and not getattr(user, "email_verified", False):
        return False   # the domain only counts once they've proved they own the address
    if not (app or current_app).config.get("ERP_COMPANY"):
        return False
    email = (user.email or "").strip().lower()
    return email.rsplit("@", 1)[-1] in allowed_domains(app) or email in allowed_emails(app)


# ------------------------------------------------------------------ helpers
def _like(text):
    """A LIKE pattern for text anywhere in a value, with wildcards in the input escaped."""
    text = (text or "").strip()[:60].upper()   # compared with UPPER(column): case doesn't matter
    return "%" + text.replace("[", "[[]").replace("%", "[%]").replace("_", "[_]") + "%"


def _clean(value):
    if isinstance(value, dt.datetime):
        return None if value <= NAV_EMPTY_DATE else value.date().isoformat()
    if isinstance(value, dt.date):   # e.g. CAST(GETDATE() AS date)
        return None if value <= NAV_EMPTY_DATE.date() else value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, str):
        return value.strip()
    return value


def _rows(rows):
    return [{k: _clean(v) for k, v in r.items()} for r in rows]


def _t(table):
    return erp.company_table(table)


# ------------------------------------------------------------------ lookups
def find_customers(query):
    """Customers whose number or name contains the text."""
    rows = erp.query(
        f"SELECT TOP 10 c.[No_] AS number, c.[Name] AS name, c.[City] AS city, "
        f"c.[Salesperson Code] AS salesperson, c.[Payment Terms Code] AS payment_terms, "
        f"c.[Blocked] AS blocked, c.[Responsibility Center] AS responsibility_center, "
        f"(SELECT COUNT(*) FROM {_t('Service Item')} i WHERE i.[Customer No_] = c.[No_]) AS service_items "
        f"FROM {_t('Customer')} c "
        f"WHERE c.[No_] = :exact OR UPPER(c.[Name]) LIKE :like OR UPPER(c.[Search Name]) LIKE :like "
        f"ORDER BY CASE WHEN c.[No_] = :exact THEN 0 ELSE 1 END, c.[Name]",
        {"exact": (query or "").strip().upper(), "like": _like(query)})
    out = _rows(rows)
    for r in out:
        r["blocked"] = CUSTOMER_BLOCKED.get(r["blocked"], r["blocked"])
    return {"customers": out, "note": "Showing at most 10 matches." if len(out) == 10 else ""}


def customer_services(customer_no, group=""):
    """A customer's service items (guard posts, alarms, cash in transit...) and totals by group."""
    params = {"cust": (customer_no or "").strip().upper(), "group": (group or "").strip().upper()}
    group_filter = "AND (:group = '' OR i.[Service Item Group Code] = :group)"
    items = erp.query(
        f"SELECT TOP 40 i.[No_] AS number, i.[Description] AS description, "
        f"i.[Service Item Group Code] AS group_code, g.[Description] AS group_name, i.[Status] AS status, "
        f"i.[Response Time (Hours)] AS response_time_hours, i.[Last Service Date] AS last_service_date, "
        f"i.[Installation Date] AS installation_date, i.[Warranty Ending Date (Labor)] AS warranty_ends "
        f"FROM {_t('Service Item')} i LEFT JOIN {_t('Service Item Group')} g ON g.[Code] = i.[Service Item Group Code] "
        f"WHERE i.[Customer No_] = :cust {group_filter} ORDER BY i.[No_]", params)
    totals = erp.query(
        f"SELECT i.[Service Item Group Code] AS group_code, MAX(g.[Description]) AS group_name, COUNT(*) AS items "
        f"FROM {_t('Service Item')} i LEFT JOIN {_t('Service Item Group')} g ON g.[Code] = i.[Service Item Group Code] "
        f"WHERE i.[Customer No_] = :cust {group_filter} GROUP BY i.[Service Item Group Code] ORDER BY items DESC",
        params)
    out = _rows(items)
    for r in out:
        r["status"] = ITEM_STATUS.get(r["status"], r["status"])
    total = sum(t["items"] for t in totals)
    return {"customer_no": params["cust"], "total_items": total, "by_group": _rows(totals),
            "items": out, "note": f"Listing the first {len(out)} of {total}." if total > len(out) else ""}


def service_item(number_or_text):
    """One service item by number (with recent changes), or items whose description matches."""
    key = (number_or_text or "").strip()
    select = (f"SELECT TOP 10 i.[No_] AS number, i.[Description] AS description, i.[Customer No_] AS customer_no, "
              f"c.[Name] AS customer_name, i.[Service Item Group Code] AS group_code, g.[Description] AS group_name, "
              f"i.[Status] AS status, i.[Response Time (Hours)] AS response_time_hours, "
              f"i.[Installation Date] AS installation_date, i.[Last Service Date] AS last_service_date, "
              f"i.[Warranty Starting Date (Labor)] AS warranty_starts, i.[Warranty Ending Date (Labor)] AS warranty_ends, "
              f"i.[Default Contract Value] AS default_contract_value "
              f"FROM {_t('Service Item')} i LEFT JOIN {_t('Customer')} c ON c.[No_] = i.[Customer No_] "
              f"LEFT JOIN {_t('Service Item Group')} g ON g.[Code] = i.[Service Item Group Code] ")
    items = erp.query(select + "WHERE i.[No_] = :exact", {"exact": key.upper()})
    if not items:
        items = erp.query(select + "WHERE UPPER(i.[Description]) LIKE :like OR UPPER(i.[Search Description]) LIKE :like "
                          "ORDER BY i.[No_]", {"like": _like(key)})
    out = _rows(items)
    for r in out:
        r["status"] = ITEM_STATUS.get(r["status"], r["status"])
    result = {"items": out}
    if len(out) == 1:
        result["recent_changes"] = _rows(erp.query(
            f"SELECT TOP 10 [Change Date] AS date, [Event No_] AS event_no, [After] AS new_value "
            f"FROM {_t('Service Item Log')} WHERE [Service Item No_] = :no "
            f"ORDER BY [Change Date] DESC, [Entry No_] DESC", {"no": out[0]["number"]}))
    return result


def service_summary(group=""):
    """How many service items there are per group (or in one group), and per status."""
    params = {"group": (group or "").strip().upper(), "like": _like(group)}
    where = ("WHERE (:group = '' OR i.[Service Item Group Code] = :group OR UPPER(g.[Description]) LIKE :like)")
    by_group = erp.query(
        f"SELECT TOP 25 i.[Service Item Group Code] AS group_code, MAX(g.[Description]) AS group_name, "
        f"COUNT(*) AS items, COUNT(DISTINCT i.[Customer No_]) AS customers "
        f"FROM {_t('Service Item')} i LEFT JOIN {_t('Service Item Group')} g ON g.[Code] = i.[Service Item Group Code] "
        f"{where} GROUP BY i.[Service Item Group Code] ORDER BY items DESC", params)
    by_status = erp.query(
        f"SELECT i.[Status] AS status, COUNT(*) AS items FROM {_t('Service Item')} i "
        f"LEFT JOIN {_t('Service Item Group')} g ON g.[Code] = i.[Service Item Group Code] {where} "
        f"GROUP BY i.[Status]", params)
    groups = _rows(by_group)
    for r in groups:
        r["group_code"] = r["group_code"] or "(no group)"
    return {"by_group": groups,
            "by_status": [{"status": ITEM_STATUS.get(r["status"], r["status"]), "items": r["items"]}
                          for r in by_status]}


def find_contracts(query):
    """Service contracts by contract number or customer number."""
    key = (query or "").strip().upper()
    rows = erp.query(
        f"SELECT TOP 15 h.[Contract No_] AS contract_no, h.[Contract Type] AS type, h.[Status] AS status, "
        f"h.[Description] AS description, h.[Customer No_] AS customer_no, c.[Name] AS customer_name, "
        f"h.[Starting Date] AS starting_date, h.[Expiration Date] AS expiration_date, "
        f"h.[Next Invoice Date] AS next_invoice_date, h.[Next Invoice Period Start] AS next_period_start, "
        f"h.[Next Invoice Period End] AS next_period_end, h.[Annual Amount] AS annual_amount, "
        f"h.[Price Update Period] AS price_update_period "
        f"FROM {_t('Service Contract Header')} h LEFT JOIN {_t('Customer')} c ON c.[No_] = h.[Customer No_] "
        f"WHERE h.[Contract No_] = :key OR h.[Customer No_] = :key OR UPPER(h.[Description]) LIKE :like "
        f"ORDER BY h.[Contract No_]", {"key": key, "like": _like(query)})
    out = _rows(rows)
    for r in out:
        r["type"] = CONTRACT_TYPE.get(r["type"], r["type"])
        r["status"] = CONTRACT_STATUS.get(r["status"], r["status"])
        r["price_update_period"] = nav_date_formula(r.get("price_update_period"))
    return {"contracts": out}


# A contract counts as active when it's a signed contract (not a quote), it has
# started, and it hasn't expired (NAV's empty date, 1753-01-01, means no expiry).
_TODAY = "CAST(GETDATE() AS date)"
_ACTIVE = (f"h.[Contract Type] = 1 AND h.[Status] = 1 AND h.[Starting Date] <= {_TODAY} "
           f"AND (h.[Expiration Date] < '1753-01-02' OR h.[Expiration Date] >= {_TODAY})")
_CONTRACT_LISTS = {
    "active": _ACTIVE,
    "signed": "h.[Status] = 1",
    "not signed": "h.[Status] = 0",
    "cancelled": "h.[Status] = 2",
    "expiring": f"{_ACTIVE} AND h.[Expiration Date] >= '1753-01-02' "
                f"AND h.[Expiration Date] < DATEADD(day, 60, {_TODAY})",
    "invoice due": f"{_ACTIVE} AND h.[Next Invoice Date] >= {_TODAY} "
                   f"AND h.[Next Invoice Date] < DATEADD(day, 30, {_TODAY})",
    "invoice overdue": f"{_ACTIVE} AND h.[Next Invoice Date] >= '1753-01-02' "
                       f"AND h.[Next Invoice Date] < {_TODAY}",
    "all": "1 = 1",
}


def contract_summary(show="active"):
    """Contract totals (active, signed, not signed, cancelled, expiring, invoices due)
    and a list of the contracts in one of those groups."""
    show = (show or "active").strip().lower()
    if show not in _CONTRACT_LISTS:
        show = "active"
    flag = lambda cond: f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END)"
    totals = erp.query(
        f"SELECT {_TODAY} AS as_of, COUNT(*) AS all_records, "
        f"{flag('h.[Contract Type] = 1')} AS contracts, {flag('h.[Contract Type] = 0')} AS quotes, "
        f"{flag(_ACTIVE)} AS active, {flag('h.[Status] = 1')} AS signed, "
        f"{flag('h.[Status] = 0')} AS not_signed, {flag('h.[Status] = 2')} AS cancelled, "
        f"{flag(_CONTRACT_LISTS['expiring'])} AS expiring_within_60_days, "
        f"{flag(_CONTRACT_LISTS['invoice due'])} AS invoices_due_within_30_days, "
        f"{flag(_CONTRACT_LISTS['invoice overdue'])} AS invoices_overdue, "
        f"SUM(CASE WHEN {_ACTIVE} THEN h.[Annual Amount] ELSE 0 END) AS active_annual_amount, "
        f"COUNT(DISTINCT CASE WHEN {_ACTIVE} THEN NULLIF(h.[Customer No_], '') END) AS active_customers "
        f"FROM {_t('Service Contract Header')} h")
    listed = erp.query(
        f"SELECT TOP 30 h.[Contract No_] AS contract_no, h.[Description] AS description, "
        f"h.[Status] AS status, h.[Customer No_] AS customer_no, c.[Name] AS customer_name, "
        f"h.[Starting Date] AS starting_date, h.[Expiration Date] AS expiration_date, "
        f"h.[Next Invoice Date] AS next_invoice_date, h.[Annual Amount] AS annual_amount "
        f"FROM {_t('Service Contract Header')} h LEFT JOIN {_t('Customer')} c ON c.[No_] = h.[Customer No_] "
        f"WHERE {_CONTRACT_LISTS[show]} ORDER BY h.[Contract No_]")
    out = _rows(listed)
    for r in out:
        r["status"] = CONTRACT_STATUS.get(r["status"], r["status"])
    summary = _rows(totals)[0] if totals else {}
    shown_total = {"active": "active", "signed": "signed", "not signed": "not_signed",
                   "cancelled": "cancelled", "expiring": "expiring_within_60_days",
                   "invoice due": "invoices_due_within_30_days",
                   "invoice overdue": "invoices_overdue", "all": "all_records"}[show]
    count = summary.get(shown_total) or 0
    return {"summary": summary,
            "definition_of_active": "signed contract that has started and has not expired",
            "list": show, "contracts": out,
            "note": f"Listing the first {len(out)} of {count}." if count > len(out) else ""}


def blocked_customers():
    """Customers that are blocked, and how many."""
    counted = erp.query(f"SELECT COUNT(*) AS n FROM {_t('Customer')} WHERE [Blocked] <> 0")
    count = counted[0]["n"] if counted else 0
    rows = erp.query(f"SELECT TOP 25 [No_] AS number, [Name] AS name, [Blocked] AS blocked "
                     f"FROM {_t('Customer')} WHERE [Blocked] <> 0 ORDER BY [Name]")
    out = _rows(rows)
    for r in out:
        r["blocked"] = CUSTOMER_BLOCKED.get(r["blocked"], r["blocked"])
    return {"blocked_count": count, "customers": out}


def _int(value, default, low, high):
    """A whole number from the model's argument, kept within [low, high]."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def _flag(cond):
    return f"SUM(CASE WHEN {cond} THEN 1 ELSE 0 END)"


def rank_customers(by="services", group="", limit="10"):
    """Top customers by number of service items (optionally in one group), by number of
    active contracts, or by yearly value of active contracts."""
    by = (by or "services").strip().lower().replace(" ", "_")
    n = _int(limit, 10, 1, 25)
    if by in ("contract_value", "value", "annual_amount", "yearly_value"):
        by, order = "contract_value", "active_annual_amount DESC"
    elif by in ("contracts", "active_contracts"):
        by, order = "contracts", "active_contracts DESC, active_annual_amount DESC"
    else:
        by, order = "services", ""
    if by == "services":
        rows = erp.query(
            f"SELECT TOP {n} i.[Customer No_] AS number, MAX(c.[Name]) AS name, COUNT(*) AS service_items, "
            f"COUNT(DISTINCT NULLIF(i.[Service Item Group Code], '')) AS service_groups "
            f"FROM {_t('Service Item')} i LEFT JOIN {_t('Customer')} c ON c.[No_] = i.[Customer No_] "
            f"LEFT JOIN {_t('Service Item Group')} g ON g.[Code] = i.[Service Item Group Code] "
            f"WHERE i.[Customer No_] <> '' AND (:grp = '' OR i.[Service Item Group Code] = :grp "
            f"OR UPPER(g.[Description]) LIKE :glike) "
            f"GROUP BY i.[Customer No_] ORDER BY service_items DESC",
            {"grp": (group or "").strip().upper(), "glike": _like(group)})
    else:
        rows = erp.query(
            f"SELECT TOP {n} h.[Customer No_] AS number, MAX(c.[Name]) AS name, COUNT(*) AS active_contracts, "
            f"SUM(h.[Annual Amount]) AS active_annual_amount "
            f"FROM {_t('Service Contract Header')} h LEFT JOIN {_t('Customer')} c ON c.[No_] = h.[Customer No_] "
            f"WHERE {_ACTIVE} AND h.[Customer No_] <> '' GROUP BY h.[Customer No_] ORDER BY {order}")
    return {"ranked_by": by, "group": group or None, "customers": _rows(rows)}


# Ways to group customers: (column, optional (table, label column) giving a readable name)
_BREAKDOWNS = {
    "city": ("c.[City]", None),
    "salesperson": ("c.[Salesperson Code]", ("Salesperson_Purchaser", "[Name]")),
    "payment_terms": ("c.[Payment Terms Code]", ("Payment Terms", "[Description]")),
    "responsibility_center": ("c.[Responsibility Center]", ("Responsibility Center", "[Name]")),
    "blocked": ("c.[Blocked]", None),
}
_BREAKDOWN_WORDS = {"sales_person": "salesperson", "salesman": "salesperson", "payment": "payment_terms",
                    "terms": "payment_terms", "branch": "responsibility_center",
                    "center": "responsibility_center", "centre": "responsibility_center",
                    "town": "city", "location": "city", "status": "blocked"}


def customer_breakdown(by="city", limit="15"):
    """How customers (and their service items) are spread by city, salesperson, payment
    terms, responsibility center or blocked status."""
    key = (by or "city").strip().lower().replace(" ", "_")
    key = _BREAKDOWN_WORDS.get(key, key)
    if key not in _BREAKDOWNS:
        key = "city"
    col, lookup = _BREAKDOWNS[key]
    n = _int(limit, 15, 1, 40)
    join, label = "", "NULL"
    if lookup:
        join = f"LEFT JOIN {_t(lookup[0])} x ON x.[Code] = {col} "
        label = f"MAX(x.{lookup[1]})"
    services = f"(SELECT [Customer No_] AS cust, COUNT(*) AS n FROM {_t('Service Item')} GROUP BY [Customer No_]) s"
    rows = erp.query(
        f"SELECT TOP {n} {col} AS value, {label} AS label, COUNT(*) AS customers, "
        f"SUM(COALESCE(s.n, 0)) AS service_items FROM {_t('Customer')} c "
        f"LEFT JOIN {services} ON s.cust = c.[No_] {join}"
        f"GROUP BY {col} ORDER BY customers DESC")
    distinct = erp.query(f"SELECT COUNT(DISTINCT {col}) AS n FROM {_t('Customer')} c")
    out = _rows(rows)
    for r in out:
        if key == "blocked":
            r["value"] = CUSTOMER_BLOCKED.get(r["value"], r["value"])
        elif r["value"] in ("", None):
            r["value"] = "(not set)"
    groups = distinct[0]["n"] if distinct else len(out)
    return {"by": key, "groups": groups, "breakdown": out,
            "note": f"Showing the {len(out)} largest of {groups}." if groups > len(out) else ""}


def services_at_location(place, group=""):
    """Service items whose description mentions a place or site (e.g. 'Kisumu', 'ATM',
    'Westlands'), with totals per service group and per customer."""
    place = (place or "").strip()
    if len(place) < 2:
        return {"error": "Give a place or site name of at least 2 letters."}
    params = {"like": _like(place), "grp": (group or "").strip().upper(), "glike": _like(group)}
    base = (f"FROM {_t('Service Item')} i "
            f"LEFT JOIN {_t('Service Item Group')} g ON g.[Code] = i.[Service Item Group Code] "
            f"LEFT JOIN {_t('Customer')} c ON c.[No_] = i.[Customer No_] "
            f"WHERE (UPPER(i.[Description]) LIKE :like OR UPPER(i.[Description 2]) LIKE :like "
            f"OR UPPER(i.[Search Description]) LIKE :like) "
            f"AND (:grp = '' OR i.[Service Item Group Code] = :grp OR UPPER(g.[Description]) LIKE :glike) ")
    totals = erp.query(f"SELECT COUNT(*) AS service_items, COUNT(DISTINCT i.[Customer No_]) AS customers {base}",
                       params)
    by_group = erp.query(
        f"SELECT TOP 15 i.[Service Item Group Code] AS group_code, MAX(g.[Description]) AS group_name, "
        f"COUNT(*) AS items {base} GROUP BY i.[Service Item Group Code] ORDER BY items DESC", params)
    by_customer = erp.query(
        f"SELECT TOP 10 i.[Customer No_] AS customer_no, MAX(c.[Name]) AS customer_name, COUNT(*) AS items "
        f"{base} GROUP BY i.[Customer No_] ORDER BY items DESC", params)
    examples = erp.query(
        f"SELECT TOP 10 i.[No_] AS number, i.[Description] AS description, i.[Customer No_] AS customer_no "
        f"{base} ORDER BY i.[No_]", params)
    summary = _rows(totals)[0] if totals else {"service_items": 0, "customers": 0}
    groups = _rows(by_group)
    for r in groups:
        r["group_code"] = r["group_code"] or "(no group)"
    return {"place": place, "group": group or None, **summary, "by_group": groups,
            "by_customer": _rows(by_customer), "examples": _rows(examples)}


def recent_service_changes(days="7", customer_no=""):
    """What changed on service items in the last N days (from the change log), optionally
    for one customer: totals, changes per day and the most-changed items."""
    d = _int(days, 7, 1, 90)
    params = {"neg": -d, "cust": (customer_no or "").strip().upper()}
    base = (f"FROM {_t('Service Item Log')} l JOIN {_t('Service Item')} i ON i.[No_] = l.[Service Item No_] "
            f"WHERE l.[Change Date] >= DATEADD(day, :neg, CAST(GETDATE() AS date)) "
            f"AND (:cust = '' OR i.[Customer No_] = :cust) ")
    totals = erp.query(f"SELECT COUNT(*) AS changes, COUNT(DISTINCT l.[Service Item No_]) AS items_changed {base}",
                       params)
    per_day = erp.query(
        f"SELECT TOP 14 CAST(l.[Change Date] AS date) AS day, COUNT(*) AS changes {base} "
        f"GROUP BY CAST(l.[Change Date] AS date) ORDER BY day DESC", params)
    top_items = erp.query(
        f"SELECT TOP 10 l.[Service Item No_] AS number, MAX(i.[Description]) AS description, "
        f"MAX(i.[Customer No_]) AS customer_no, COUNT(*) AS changes, MAX(l.[Change Date]) AS last_change "
        f"{base} GROUP BY l.[Service Item No_] ORDER BY changes DESC", params)
    summary = _rows(totals)[0] if totals else {"changes": 0, "items_changed": 0}
    return {"days": d, "customer_no": params["cust"] or None, **summary,
            "per_day": _rows(per_day), "most_changed_items": _rows(top_items)}


def customers_without_active_contract(min_services="1", limit="20"):
    """Customers that have service items but no active contract (a follow-up list for
    sales), with how many service items each has."""
    m = _int(min_services, 1, 1, 1000)
    n = _int(limit, 20, 1, 40)
    no_contract = (f"NOT EXISTS (SELECT 1 FROM {_t('Service Contract Header')} h "
                   f"WHERE h.[Customer No_] = i.[Customer No_] AND {_ACTIVE})")
    grouped = (f"FROM {_t('Service Item')} i LEFT JOIN {_t('Customer')} c ON c.[No_] = i.[Customer No_] "
               f"WHERE i.[Customer No_] <> '' AND {no_contract} "
               f"GROUP BY i.[Customer No_] HAVING COUNT(*) >= :m")
    rows = erp.query(f"SELECT TOP {n} i.[Customer No_] AS number, MAX(c.[Name]) AS name, "
                     f"COUNT(*) AS service_items {grouped} ORDER BY service_items DESC", {"m": m})
    total = erp.query(f"SELECT COUNT(*) AS n FROM (SELECT i.[Customer No_] AS cust {grouped}) x", {"m": m})
    count = total[0]["n"] if total else len(rows)
    return {"min_services": m, "customers_without_active_contract": count, "customers": _rows(rows),
            "note": f"Listing the top {len(rows)} of {count} by service items." if count > len(rows) else ""}


def contract_trend(months="12"):
    """Contracts started per month over the last N months, with how many are signed and
    their yearly value."""
    m = _int(months, 12, 1, 36)
    rows = erp.query(
        f"SELECT YEAR(h.[Starting Date]) AS y, MONTH(h.[Starting Date]) AS mo, COUNT(*) AS contracts_started, "
        f"SUM(CASE WHEN h.[Status] = 1 THEN 1 ELSE 0 END) AS signed, SUM(h.[Annual Amount]) AS annual_amount "
        f"FROM {_t('Service Contract Header')} h "
        f"WHERE h.[Starting Date] >= DATEADD(month, :neg, CAST(GETDATE() AS date)) "
        f"GROUP BY YEAR(h.[Starting Date]), MONTH(h.[Starting Date]) ORDER BY y, mo", {"neg": -m})
    out = [{"month": f"{r['y']:04d}-{r['mo']:02d}",
            **{k: _clean(v) for k, v in r.items() if k not in ("y", "mo")}} for r in rows]
    return {"months": m, "per_month": out}


def data_quality():
    """Gaps in the ERP data that affect answers: service items without a group, customer
    or serial number; customers without city or salesperson; unsigned or incomplete contracts."""
    empty = "''"
    no_date = "'1753-01-02'"
    items = erp.query(
        f"SELECT COUNT(*) AS total, "
        f"{_flag('[Service Item Group Code] = ' + empty)} AS without_group, "
        f"{_flag('[Customer No_] = ' + empty)} AS without_customer, "
        f"{_flag('[Serial No_] = ' + empty)} AS without_serial_number, "
        f"{_flag('[Last Service Date] < ' + no_date)} AS never_serviced "
        f"FROM {_t('Service Item')}")
    customers = erp.query(
        f"SELECT COUNT(*) AS total, {_flag('[City] = ' + empty)} AS without_city, "
        f"{_flag('[Salesperson Code] = ' + empty)} AS without_salesperson, "
        f"{_flag('[Blocked] <> 0')} AS blocked FROM {_t('Customer')}")
    contracts = erp.query(
        f"SELECT COUNT(*) AS total, {_flag('h.[Status] = 0')} AS not_signed, "
        f"{_flag('h.[Customer No_] = ' + empty)} AS without_customer, "
        f"{_flag('h.[Description] = ' + empty)} AS without_description, "
        f"{_flag(_ACTIVE + ' AND h.[Next Invoice Date] < ' + no_date)} AS active_without_next_invoice_date "
        f"FROM {_t('Service Contract Header')} h")
    first = lambda rows: _rows(rows)[0] if rows else {}
    return {"service_items": first(items), "customers": first(customers), "contracts": first(contracts)}


# --------------------------------------------------- what the model is told
_STR = {"type": "string"}
LOOKUPS = {
    "find_customers": (find_customers, "Find customers by customer number or (part of) their name. "
                       "Returns number, name, city, salesperson, payment terms, blocked status and "
                       "how many service items they have.",
                       {"query": dict(_STR, description="Customer number or part of the name")}, ["query"]),
    "customer_services": (customer_services, "List a customer's service items (guard posts, alarm "
                          "services, cash in transit and other services) with totals per service group. "
                          "Needs the customer number (use find_customers first if you only have a name).",
                          {"customer_no": dict(_STR, description="Customer number, e.g. C00123"),
                           "group": dict(_STR, description="Optional service item group code, e.g. GDS or ALM")},
                          ["customer_no"]),
    "service_item": (service_item, "Look up one service item by its number (with its recent change "
                     "history), or find service items whose description contains the text "
                     "(e.g. 'Kisumu ATM').",
                     {"number_or_text": dict(_STR, description="Service item number or description text")},
                     ["number_or_text"]),
    "service_summary": (service_summary, "Count service items per service group (and per status), "
                        "optionally for one group by code or name (e.g. 'alarm', 'CIT').",
                        {"group": dict(_STR, description="Optional group code or name; empty for all")}, []),
    "find_contracts": (find_contracts, "Find service contracts by contract number, customer number or "
                       "description: type, status, start/expiry dates, next invoice date and period.",
                       {"query": dict(_STR, description="Contract number, customer number or description text")},
                       ["query"]),
    "contract_summary": (contract_summary, "Count service contracts: how many are active (signed, "
                         "started, not expired), signed, not signed, cancelled, expiring within 60 days, "
                         "have an invoice due within the next 30 days, or have an overdue next "
                         "invoice date (in the past), plus the yearly value of active "
                         "contracts. Also lists the contracts in one of those groups. Use it for any "
                         "question about how many contracts there are or which are active.",
                         {"show": dict(_STR, description="Which contracts to list: active (default), "
                                       "signed, not signed, cancelled, expiring, invoice due, "
                                       "invoice overdue, or all")},
                         []),
    "blocked_customers": (blocked_customers, "How many customers are blocked, and which ones.", {}, []),
    "rank_customers": (rank_customers, "Top customers ranked by number of service items (optionally "
                       "within one service group, e.g. alarms), by number of active contracts, or by "
                       "yearly value of active contracts.",
                       {"by": dict(_STR, description="services (default), contracts, or contract_value"),
                        "group": dict(_STR, description="Optional service group code or name, for by=services"),
                        "limit": dict(_STR, description="How many customers, 1-25 (default 10)")}, []),
    "customer_breakdown": (customer_breakdown, "How customers and their service items are spread by city, "
                           "salesperson, payment terms, responsibility center (branch) or blocked status.",
                           {"by": dict(_STR, description="city (default), salesperson, payment_terms, "
                                       "responsibility_center, or blocked"),
                            "limit": dict(_STR, description="How many groups to list, 1-40 (default 15)")}, []),
    "services_at_location": (services_at_location, "Service items whose description mentions a place, "
                             "site or keyword (e.g. Kisumu, Mombasa, ATM, a branch name), with totals per "
                             "service group and per customer. Use for 'how many guards in X' questions.",
                             {"place": dict(_STR, description="Place, site or keyword to look for"),
                              "group": dict(_STR, description="Optional service group code or name")},
                             ["place"]),
    "recent_service_changes": (recent_service_changes, "What changed on service items in the last N days "
                               "(from the change log), optionally for one customer: totals, changes per "
                               "day and the most-changed items.",
                               {"days": dict(_STR, description="Number of days back, 1-90 (default 7)"),
                                "customer_no": dict(_STR, description="Optional customer number")}, []),
    "customers_without_active_contract": (customers_without_active_contract, "Customers that have service "
                                          "items but no active contract, biggest first (a follow-up list).",
                                          {"min_services": dict(_STR, description="Only customers with at "
                                                                "least this many service items (default 1)"),
                                           "limit": dict(_STR, description="How many to list, 1-40")}, []),
    "contract_trend": (contract_trend, "Contracts started per month over the last N months, with how many "
                       "are signed and their yearly value (for growth or trend questions).",
                       {"months": dict(_STR, description="Number of months back, 1-36 (default 12)")}, []),
    "data_quality": (data_quality, "Gaps in the ERP data: service items without a group, customer or "
                     "serial number, or never serviced; customers without city or salesperson; contracts "
                     "not signed or missing a customer, description or next invoice date.", {}, []),
}


# Example questions and the lookups that answer them. They're shown to the model so
# it picks the right lookups (and combines them for harder questions), and the tests
# check every lookup named here exists. Numbers like C00123 are placeholders.
EXAMPLE_QUESTIONS = [
    # single facts
    ("Who is customer C00123?", ["find_customers"]),
    ("Find customers with BANK in their name", ["find_customers"]),
    ("What services does C00123 have?", ["customer_services"]),
    ("Show service item SIT0001 and what changed on it", ["service_item"]),
    ("When is contract SC00123 next invoiced?", ["find_contracts"]),
    # counts and totals
    ("How many alarm services do we have?", ["service_summary"]),
    ("How many active contracts do we have, and what are they worth a year?", ["contract_summary"]),
    ("Which contract invoices are overdue?", ["contract_summary"]),
    ("How many customers are blocked, and who are they?", ["blocked_customers"]),
    # rankings and comparisons
    ("Who are our top 5 customers by number of guard posts?", ["rank_customers"]),
    ("Which customers bring in the most contract value?", ["rank_customers"]),
    ("Which customer has the most alarm services?", ["rank_customers"]),
    ("Compare cash-in-transit and alarm services: how many of each, and for how many customers?",
     ["service_summary"]),
    # breakdowns
    ("Which towns do most of our customers come from?", ["customer_breakdown"]),
    ("How many customers does each salesperson handle?", ["customer_breakdown"]),
    ("Which branch (responsibility center) has the most customers?", ["customer_breakdown"]),
    # places and sites
    ("How many guards do we have in Kisumu?", ["services_at_location"]),
    ("Which customers have ATM guards in Eldoret?", ["services_at_location"]),
    # change history and trends
    ("What changed on service items this week?", ["recent_service_changes"]),
    ("Has anything changed on C00123's services in the last 30 days?", ["recent_service_changes"]),
    ("How many contracts did we start each month this year?", ["contract_trend"]),
    # follow-ups and data gaps
    ("Which customers have services but no active contract?", ["customers_without_active_contract"]),
    ("How complete is our ERP data? What's missing?", ["data_quality"]),
    # combined (several lookups)
    ("Do our top 3 customers by services all have active contracts?",
     ["rank_customers", "find_contracts"]),
    ("Customer C00123: what services do they have, and are they covered by a signed contract?",
     ["customer_services", "find_contracts"]),
    ("Of the customers in Mombasa, which have alarm services?", ["services_at_location"]),
]


def gemini_tool():
    from google.genai import types
    return types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name=name, description=desc,
            parameters_json_schema={"type": "object", "properties": props, "required": required})
        for name, (_fn, desc, props, required) in LOOKUPS.items()])


def run_lookup(name, args):
    """Run one lookup the model asked for; errors come back as a message, never raise."""
    entry = LOOKUPS.get(name)
    if entry is None:
        return {"error": f"Unknown lookup {name}"}
    fn, _desc, props, _required = entry
    kwargs = {k: str(v) for k, v in (args or {}).items() if k in props}
    log.info("ERP lookup %s(%s)", name, kwargs)
    try:
        return fn(**kwargs)
    except TypeError as exc:
        return {"error": f"Bad arguments for {name}: {exc}"}
    except erp.ERPUnreachable as exc:   # quick, expected failure: no traceback needed
        log.error("ERP lookup %s failed: %s", name, exc)
        return {"error": "The ERP server isn't answering right now (network or firewall). "
                         "Ask the user to try again in a minute."}
    except Exception as exc:   # network, timeout, permissions: tell the model, don't crash
        log.exception("ERP lookup %s failed", name)
        return {"error": f"The ERP database couldn't be reached right now ({type(exc).__name__})."}


def instructions(app=None):
    company = (app or current_app).config.get("ERP_COMPANY", "")
    examples = "\n".join(f"- {q} -> {' + '.join(names)}" for q, names in EXAMPLE_QUESTIONS)
    return f"""

Live ERP data: you can look up the company's Dynamics NAV data (company {company}) with the
provided functions: customers, their service items (guard posts, alarm services, cash in
transit and other services), single service items and their change history, totals per
service group, service contracts (single ones, and totals such as how many are active),
blocked customers, customer rankings and breakdowns (by city, salesperson, branch), service
items at a place or site, recent changes, contract trends, customers without an active
contract, and gaps in the data. Use a lookup whenever the user asks about a specific
customer, service, contract, a count, a ranking or a comparison; never guess or invent ERP
data. For harder questions, call several lookups (in the same turn when they don't depend
on each other) and combine the results; do the arithmetic (totals, differences,
percentages) yourself from the numbers returned. If a question is ambiguous, answer the
most likely reading and say which one you used.

Say briefly that the figures come from the ERP. If a lookup finds nothing or fails, say so
plainly. If a list was cut short (a "note" says so), say that it's a partial list. There is
no invoice, payment or balance data, and you can't see employees, payroll or personal ID
numbers; say so if asked. Customer numbers look like the "number" values the lookups return.

Examples of questions and the lookups that answer them:
{examples}"""

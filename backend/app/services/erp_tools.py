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
    return {"contracts": out}


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
    "blocked_customers": (blocked_customers, "How many customers are blocked, and which ones.", {}, []),
}


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
    return f"""

Live ERP data: you can look up the company's Dynamics NAV data (company {company}) with the
provided functions: customers, their service items (guard posts, alarm services, cash in
transit and other services), single service items and their change history, totals per
service group, service contracts and blocked customers. Use a lookup whenever the user asks
about a specific customer, service, contract or a count; never guess or invent ERP data.
Say briefly that the figures come from the ERP. If a lookup finds nothing or fails, say so
plainly. There is no invoice, payment or balance data, and you can't see employees, payroll
or personal ID numbers; say so if asked. Customer numbers look like the "number" values the
lookups return."""

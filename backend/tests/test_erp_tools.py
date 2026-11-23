"""ERP lookups in chat: who may use them, what they return, and the model's lookup loop."""
import datetime as dt
import decimal
import json
from types import SimpleNamespace

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.services import ai_service, erp, erp_tools

ERP = dict(ERP_DB_HOST="h", ERP_DB_PORT="1433", ERP_DB_NAME="IDEALDYNAMICS_TEST", ERP_DB_USER="u",
           ERP_DB_PASSWORD="p", ERP_COMPANY="IDEALDYNAMICS_PROP",
           ERP_ALLOWED_EMAIL_DOMAINS="IdealDynamics.co.ke, @partner.example")


@pytest.fixture
def app(tmp_path):
    app = create_app(TestConfig)
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    yield app
    with app.app_context():
        db.drop_all()


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture
def fake_query(app, monkeypatch):
    """erp.query stand-in: records SQL and params, returns rows queued per call."""
    app.config.update(ERP)
    calls, queue = [], []

    def query(sql, params=None, limit=200):
        erp.check_read_only(sql)          # the real safety check still applies
        calls.append((sql, params or {}))
        return queue.pop(0) if queue else []
    monkeypatch.setattr(erp, "query", query)
    return SimpleNamespace(calls=calls, queue=queue)


# -------------------------------------------------------------------- access
@pytest.mark.parametrize("email,ok", [
    ("jane@idealdynamics.co.ke", True), ("JANE@IDEALDYNAMICS.CO.KE", True),
    ("bob@partner.example", True), ("eve@gmail.com", False),
    ("eve@notidealdynamics.co.ke", False), ("eve@idealdynamics.co.ke.evil.com", False),
])
def test_only_allowed_email_domains(app, email, ok):
    app.config.update(ERP)
    with app.app_context():
        assert erp_tools.user_may_use(SimpleNamespace(email=email)) is ok


def test_nobody_without_settings(app):
    with app.app_context():
        assert not erp_tools.user_may_use(SimpleNamespace(email="jane@idealdynamics.co.ke"))
        app.config.update(ERP, ERP_ALLOWED_EMAIL_DOMAINS="")
        assert not erp_tools.user_may_use(SimpleNamespace(email="jane@idealdynamics.co.ke"))
        assert not erp_tools.user_may_use(None)


# ------------------------------------------------------------------- lookups
def test_find_customers_labels_and_escapes(app, fake_query):
    fake_query.queue.append([{"number": "C00309", "name": "Kenya Bank ", "city": "Kisumu",
                              "salesperson": "JO", "payment_terms": "30D", "blocked": 3,
                              "responsibility_center": "", "service_items": 265}])
    with app.app_context():
        r = erp_tools.find_customers("50%_bank")
    sql, params = fake_query.calls[0]
    assert "[IDEALDYNAMICS_PROP$Customer]" in sql and "TOP 10" in sql
    assert params["like"] == "%50[%][_]BANK%"          # wildcards in the input can't widen the search
    assert r["customers"][0]["blocked"] == "blocked for everything"
    assert r["customers"][0]["name"] == "Kenya Bank"


def test_customer_services_dates_and_totals(app, fake_query):
    fake_query.queue += [
        [{"number": "SI01", "description": "Night Guard-Kisumu ATM", "group_code": "GDS",
          "group_name": "Regular Guards", "status": 2, "response_time_hours": decimal.Decimal("4"),
          "last_service_date": dt.datetime(1753, 1, 1), "installation_date": dt.datetime(2026, 3, 1),
          "warranty_ends": dt.datetime(1753, 1, 1)}],
        [{"group_code": "GDS", "group_name": "Regular Guards", "items": 264},
         {"group_code": "ALM", "group_name": "Alarms Services", "items": 1}],
    ]
    with app.app_context():
        r = erp_tools.customer_services("c00309")
    assert fake_query.calls[0][1]["cust"] == "C00309"
    item = r["items"][0]
    assert item["status"] == "installed" and item["response_time_hours"] == 4.0
    assert item["last_service_date"] is None and item["installation_date"] == "2026-03-01"
    assert r["total_items"] == 265 and "first 1 of 265" in r["note"]
    json.dumps(r)                                        # must be sendable to the model


def test_service_item_with_history(app, fake_query):
    fake_query.queue += [[{"number": "SI01", "description": "Alarm", "status": 2}],
                         [{"date": dt.datetime(2026, 11, 3), "event_no": 12, "new_value": "Installed"}]]
    with app.app_context():
        r = erp_tools.service_item("si01")
    assert r["recent_changes"][0]["date"] == "2026-11-03"
    assert "[IDEALDYNAMICS_PROP$Service Item Log]" in fake_query.calls[1][0]


def test_lookups_never_touch_hr_or_payroll_tables(app, fake_query):
    sql_by_lookup = {}
    with app.app_context():
        for name, (fn, _d, props, _r) in erp_tools.LOOKUPS.items():
            before = len(fake_query.calls)
            fn(**{k: "x" for k in props})
            sql_by_lookup[name] = " ".join(sql for sql, _ in fake_query.calls[before:])
    all_sql = " ".join(sql_by_lookup.values())
    for forbidden in ("$Employee", "$Payroll", "$Resource]", "Social Security", "National ID"):
        assert forbidden not in all_sql
    # The contract's own contact phone/email (asked for by the user) only in contract_details.
    for name, sql in sql_by_lookup.items():
        if name != "contract_details":
            assert "Phone" not in sql and "E-Mail" not in sql, name


def test_run_lookup_reports_problems(app, fake_query, monkeypatch):
    with app.app_context():
        assert "Unknown lookup" in erp_tools.run_lookup("drop_tables", {})["error"]
        monkeypatch.setitem(erp_tools.LOOKUPS, "find_customers",
                            (lambda query: 1 / 0,) + erp_tools.LOOKUPS["find_customers"][1:])
        assert "couldn't be reached" in erp_tools.run_lookup("find_customers", {"query": "a"})["error"]


# ------------------------------------------------------- the model's lookups
class LookupGemini:
    """Round 1: asks for find_customers. Round 2: writes the answer."""
    def __init__(self):
        self.calls = []
        self.models = SimpleNamespace(generate_content_stream=self._stream)

    def _stream(self, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1:
            call = SimpleNamespace(name="find_customers", args={"query": "Kenya Bank"}, id="c1")
            part = SimpleNamespace(function_call=call, text=None, thought=False)
            return iter([SimpleNamespace(text=None, candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])])
        text = SimpleNamespace(function_call=None, text="Kenya Bank (C00309) has 265 services, per the ERP.\n"
                               '<meta>{"intent": "service_request"}</meta>', thought=False)
        return iter([SimpleNamespace(text=text.text, candidates=[SimpleNamespace(content=SimpleNamespace(parts=[text]))])])


def _setup(app, monkeypatch, client, email):
    app.config.update(ERP, AI_ENABLED=True, AI_PROVIDER="gemini", GEMINI_MODEL="m",
                      GEMINI_FALLBACK_MODEL="", GEMINI_THINKING="")
    fake = LookupGemini()
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: fake)
    looked_up = []
    monkeypatch.setattr(erp_tools, "run_lookup",
                        lambda name, args: looked_up.append((name, args)) or {"customers": [{"number": "C00309"}]})
    token = client.post("/api/users/register", json={"name": "Jane", "email": email,
                                                     "password": "Secret123"}).get_json()["token"]
    conv = client.post("/api/conversations", json={"mode": "service"},
                       headers={"X-User-Token": token}).get_json()
    return fake, looked_up, conv


def test_model_looks_up_erp_data_then_answers(app, client, monkeypatch):
    fake, looked_up, conv = _setup(app, monkeypatch, client, "jane@idealdynamics.co.ke")
    res = client.post(f"/api/conversations/{conv['id']}/messages?stream=1",
                      headers={"X-Conversation-Token": conv["token"]},
                      json={"content": "How many services does Kenya Bank have?", "mode": "service"})
    events = [json.loads(line) for line in res.get_data(as_text=True).splitlines()]
    assert looked_up == [("find_customers", {"query": "Kenya Bank"})]
    assert len(fake.calls) == 2
    second = fake.calls[1]["contents"]
    assert second[-1].parts[0].function_response.name == "find_customers"
    assert second[-1].parts[0].function_response.id == "c1"
    assert "Live ERP data" in fake.calls[0]["config"].system_instruction
    done = events[-1]
    assert done["reply"]["content"].startswith("Kenya Bank (C00309)")
    assert "IDEALDYNAMICS_PROP" in done["reply"]["erp_note"]


def test_other_domains_get_no_lookups(app, client, monkeypatch):
    fake, looked_up, conv = _setup(app, monkeypatch, client, "eve@gmail.com")
    client.post(f"/api/conversations/{conv['id']}/messages?stream=1",
                headers={"X-Conversation-Token": conv["token"]},
                json={"content": "How many services does Kenya Bank have?", "mode": "service"}
                ).get_data()   # a streamed answer only runs when it's read
    cfg = fake.calls[0]["config"]
    assert not cfg.tools and "Live ERP data" not in cfg.system_instruction
    assert looked_up == []          # even though the (fake) model asked for one


def test_general_mode_gets_no_lookups(app, client, monkeypatch):
    fake, _, conv = _setup(app, monkeypatch, client, "jane@idealdynamics.co.ke")
    conv = client.post("/api/conversations", json={"mode": "general"}).get_json()
    client.post(f"/api/conversations/{conv['id']}/messages?stream=1",
                headers={"X-Conversation-Token": conv["token"]},
                json={"content": "Why is the sky blue?", "mode": "general"}).get_data()
    assert not fake.calls[0]["config"].tools


@pytest.mark.parametrize("email,verified,ok", [
    ("Boss@Gmail.com", True, True),          # approved address (any case)
    ("boss@gmail.com", False, False),        # ...but only once confirmed
    ("other@gmail.com", True, False),        # same domain, not approved
    ("jane@idealdynamics.co.ke", True, True),  # company domain still works
])
def test_individually_approved_addresses(app, email, verified, ok):
    app.config.update(ERP, ERP_ALLOWED_EMAILS=" boss@gmail.com , x@y.com", REQUIRE_EMAIL_VERIFICATION=True)
    with app.app_context():
        assert erp_tools.user_may_use(SimpleNamespace(email=email, email_verified=verified)) is ok


def test_failed_lookup_gets_no_live_data_note(app, client, monkeypatch):
    fake, looked_up, conv = _setup(app, monkeypatch, client, "jane@idealdynamics.co.ke")
    monkeypatch.setattr(erp_tools, "run_lookup",
                        lambda name, args: {"error": "The ERP database couldn't be reached right now (OperationalError)."})
    res = client.post(f"/api/conversations/{conv['id']}/messages?stream=1",
                      headers={"X-Conversation-Token": conv["token"]},
                      json={"content": "How many services does Kenya Bank have?", "mode": "service"})
    done = [json.loads(line) for line in res.get_data(as_text=True).splitlines()][-1]
    assert len(fake.calls) == 2                 # the model still got the error and answered
    assert done["reply"]["erp_note"] == ""      # ...but without claiming live ERP data


class GreedyGemini(LookupGemini):
    """Keeps asking for lookups, even on the last round, until told to answer now."""
    def _stream(self, **kw):
        self.calls.append(kw)
        last = kw["contents"][-1].parts[0]
        if getattr(last, "text", None) == ai_service.ANSWER_NOW:
            text = SimpleNamespace(function_call=None, text="Both have contracts, per the ERP.", thought=False)
            return iter([SimpleNamespace(text=text.text, candidates=[SimpleNamespace(content=SimpleNamespace(parts=[text]))])])
        call = SimpleNamespace(name="find_contracts", args={"query": f"q{len(self.calls)}"}, id=f"c{len(self.calls)}")
        part = SimpleNamespace(function_call=call, text=None, thought=False)
        return iter([SimpleNamespace(text=None, candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])])


def test_model_that_never_stops_looking_up_is_asked_to_answer(app, client, monkeypatch):
    _, looked_up, conv = _setup(app, monkeypatch, client, "jane@idealdynamics.co.ke")
    fake = GreedyGemini()
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: fake)
    res = client.post(f"/api/conversations/{conv['id']}/messages?stream=1",
                      headers={"X-Conversation-Token": conv["token"]},
                      json={"content": "Do our top customers have contracts?", "mode": "service"})
    done = [json.loads(line) for line in res.get_data(as_text=True).splitlines()][-1]
    assert len(looked_up) == ai_service.MAX_LOOKUP_ROUNDS     # no lookups beyond the limit
    assert len(fake.calls) == ai_service.MAX_LOOKUP_ROUNDS + 2  # last round, then "answer now"
    assert done["reply"]["content"].startswith("Both have contracts")


def test_busy_model_gives_a_clear_message_not_a_canned_article(app, client, monkeypatch):
    fake, looked_up, conv = _setup(app, monkeypatch, client, "jane@idealdynamics.co.ke")

    def busy(**kw):
        raise RuntimeError("503 UNAVAILABLE: high demand")

    fake.models.generate_content_stream = busy
    res = client.post(f"/api/conversations/{conv['id']}/messages?stream=1",
                      headers={"X-Conversation-Token": conv["token"]},
                      json={"content": "Show details for contract SC90146", "mode": "service"})
    done = [json.loads(line) for line in res.get_data(as_text=True).splitlines()][-1]
    assert done["reply"]["content"] == ai_service.AI_BUSY_FOR_ERP and looked_up == []


@pytest.mark.parametrize("stored,shown", [
    ("1\x07", "1Y (1 year)"), ("2\x07", "2Y (2 years)"), ("3\x05", "3M (3 months)"),
    ("-1\x04", "-1W (-1 week)"), ("1\x01", "1C (current period)"), ("", None), ("odd", "odd"),
])
def test_nav_date_formulas_are_readable(stored, shown):
    assert erp_tools.nav_date_formula(stored) == shown


def test_contract_price_update_period_is_decoded(app, fake_query):
    fake_query.queue.append([{"contract_no": "SC00146", "type": 1, "status": 1,
                              "price_update_period": "1\x07"}])
    with app.app_context():
        c = erp_tools.find_contracts("SC00146")["contracts"][0]
    assert c["price_update_period"] == "1Y (1 year)" and c["status"] == "signed"


def test_contract_summary_counts_and_lists_active(app, fake_query):
    fake_query.queue += [
        [{"as_of": dt.date(2030, 1, 15), "all_records": 120, "contracts": 118, "quotes": 2,
          "active": 12, "signed": 13, "not_signed": 105, "cancelled": 2,
          "expiring_within_60_days": 1, "invoices_due_within_30_days": 3,
          "active_annual_amount": decimal.Decimal("1000.50"), "active_customers": 9}],
        [{"contract_no": "SC90001", "status": 1, "description": "GUARDING SERVICES",
          "expiration_date": dt.datetime(1753, 1, 1)}],
    ]
    with app.app_context():
        r = erp_tools.contract_summary()
    totals_sql, list_sql = fake_query.calls[0][0], fake_query.calls[1][0]
    # "Active" = signed contract, started, not expired.
    assert "h.[Status] = 1" in list_sql and "[Starting Date] <=" in list_sql and "[Expiration Date]" in list_sql
    assert "[IDEALDYNAMICS_PROP$Service Contract Header]" in totals_sql
    assert r["summary"]["active"] == 12 and r["summary"]["active_annual_amount"] == 1000.5
    assert r["contracts"][0]["status"] == "signed" and r["contracts"][0]["expiration_date"] is None
    json.dumps(r)


def test_contract_summary_other_lists_and_bad_input(app, fake_query):
    with app.app_context():
        assert erp_tools.contract_summary("not signed")["list"] == "not signed"
        assert "h.[Status] = 0" in fake_query.calls[-1][0]
        assert erp_tools.contract_summary("drop table")["list"] == "active"   # unknown -> active


def test_contract_summary_is_offered_to_the_model():
    names = [d.name for d in erp_tools.gemini_tool().function_declarations]
    assert "contract_summary" in names


def test_due_and_overdue_invoices_are_separate(app, fake_query):
    with app.app_context():
        erp_tools.contract_summary("invoice due")
        due_sql = fake_query.calls[-1][0]
        erp_tools.contract_summary("invoice overdue")
        overdue_sql = fake_query.calls[-1][0]
    assert "[Next Invoice Date] >= CAST(GETDATE() AS date)" in due_sql       # not in the past
    assert "[Next Invoice Date] < CAST(GETDATE() AS date)" in overdue_sql    # only the past
    assert "invoices_overdue" in fake_query.calls[0][0]


# ----------------------------------------------------- broader question types
def test_every_example_question_names_real_lookups():
    named = {n for _, names in erp_tools.EXAMPLE_QUESTIONS for n in names}
    assert named <= set(erp_tools.LOOKUPS)
    assert len(erp_tools.EXAMPLE_QUESTIONS) >= 20
    assert any(len(names) > 1 for _, names in erp_tools.EXAMPLE_QUESTIONS)   # combined questions too


def test_examples_and_guidance_reach_the_model(app):
    app.config.update(ERP)
    with app.app_context():
        text = erp_tools.instructions()
    assert "How many guards do we have in Kisumu? -> services_at_location" in text
    assert "rank_customers + find_contracts" in text and "several lookups" in text


def test_every_lookup_is_declared_to_the_model():
    declared = {d.name for d in erp_tools.gemini_tool().function_declarations}
    assert declared == set(erp_tools.LOOKUPS) and len(declared) == 15


def test_rank_customers_by_services_and_value(app, fake_query):
    fake_query.queue += [[{"number": "C90001", "name": "Test Ltd", "service_items": 7, "service_groups": 2}],
                         [{"number": "C90002", "name": "Other Ltd", "active_contracts": 2,
                           "active_annual_amount": decimal.Decimal("500.25")}]]
    with app.app_context():
        a = erp_tools.rank_customers(group="alarm", limit="3")
        b = erp_tools.rank_customers(by="contract value", limit="999")
    assert "TOP 3" in fake_query.calls[0][0] and fake_query.calls[0][1]["glike"] == "%ALARM%"
    assert "TOP 25" in fake_query.calls[1][0] and "h.[Status] = 1" in fake_query.calls[1][0]   # capped, active only
    assert a["customers"][0]["service_items"] == 7 and b["ranked_by"] == "contract_value"
    assert b["customers"][0]["active_annual_amount"] == 500.25


@pytest.mark.parametrize("asked,used", [("city", "city"), ("branch", "responsibility_center"),
                                        ("salesman", "salesperson"), ("nonsense", "city")])
def test_customer_breakdown_understands_synonyms(app, fake_query, asked, used):
    with app.app_context():
        r = erp_tools.customer_breakdown(by=asked)
    assert r["by"] == used


def test_customer_breakdown_labels(app, fake_query):
    fake_query.queue += [[{"value": "", "label": None, "customers": 5, "service_items": 9},
                          {"value": 3, "label": None, "customers": 1, "service_items": 0}],
                         [{"n": 40}]]
    with app.app_context():
        r = erp_tools.customer_breakdown(by="blocked", limit="2")
    assert r["breakdown"][1]["value"] == "blocked for everything"
    assert r["groups"] == 40 and "2 largest of 40" in r["note"]


def test_services_at_location(app, fake_query):
    fake_query.queue += [[{"service_items": 4, "customers": 2}],
                         [{"group_code": "", "group_name": None, "items": 4}], [], []]
    with app.app_context():
        r = erp_tools.services_at_location("kisumu")
        short = erp_tools.services_at_location("k")
    assert fake_query.calls[0][1]["like"] == "%KISUMU%"
    assert r["service_items"] == 4 and r["by_group"][0]["group_code"] == "(no group)"
    assert "error" in short


def test_recent_changes_window_is_limited(app, fake_query):
    with app.app_context():
        erp_tools.recent_service_changes(days="5000", customer_no="c90001")
    sql, params = fake_query.calls[0]
    assert params["neg"] == -90 and params["cust"] == "C90001" and "$Service Item Log]" in sql


def test_customers_without_active_contract(app, fake_query):
    fake_query.queue += [[{"number": "C90003", "name": "Gap Ltd", "service_items": 12}], [{"n": 30}]]
    with app.app_context():
        r = erp_tools.customers_without_active_contract(min_services="5", limit="1")
    assert "NOT EXISTS" in fake_query.calls[0][0] and fake_query.calls[0][1]["m"] == 5
    assert r["customers_without_active_contract"] == 30 and "top 1 of 30" in r["note"]


def test_contract_trend_months(app, fake_query):
    fake_query.queue.append([{"y": 2030, "mo": 3, "contracts_started": 4, "signed": 1,
                              "annual_amount": decimal.Decimal("10.5")}])
    with app.app_context():
        r = erp_tools.contract_trend(months="6")
    assert fake_query.calls[0][1]["neg"] == -6
    assert r["per_month"] == [{"month": "2030-03", "contracts_started": 4, "signed": 1, "annual_amount": 10.5}]


def test_data_quality_sections(app, fake_query):
    fake_query.queue += [[{"total": 10, "without_group": 3}], [{"total": 5, "without_city": 1}],
                         [{"total": 2, "not_signed": 1}]]
    with app.app_context():
        r = erp_tools.data_quality()
    assert r["service_items"]["without_group"] == 3 and r["contracts"]["not_signed"] == 1


def test_contract_extra_fields(app, fake_query):
    fake_query.queue.append([{"contract_no": "SC90001", "type": 1, "status": 1, "service_period": "2\x07",
                              "invoice_period": 2, "payment_terms": "30 DAYS", "salesperson_code": "S1",
                              "salesperson_name": "Test Person"}])
    with app.app_context():
        c = erp_tools.find_contracts("SC90001")["contracts"][0]
    sql = fake_query.calls[0][0]
    assert "$Salesperson_Purchaser]" in sql and "$Responsibility Center]" in sql
    assert c["service_period"] == "2Y (2 years)" and c["invoice_period"] == "quarterly"


def test_contract_details_header_and_lines(app, fake_query):
    fake_query.queue += [
        [{"contract_no": "SC90001", "type": 1, "status": 1, "change_status": 1, "invoice_period": 0,
          "prepaid": 1, "invoice_after_service": 0, "combine_invoices": 0, "contract_lines_on_invoice": 1,
          "automatic_credit_memos": 0, "service_period": "1\x07", "contract_period": "",
          "price_update_period": "1\x07", "phone": "", "email": None, "customer_name": "Test Ltd"}],
        [{"line_no": 10000, "service_item_no": "SI1", "line_amount": decimal.Decimal("100.50"),
          "service_period": "1\x05", "credited": 0, "serial_no": ""},
         {"line_no": 20000, "service_item_no": "SI2", "line_amount": decimal.Decimal("50"), "credited": 1}],
    ]
    with app.app_context():
        r = erp_tools.contract_details("sc90001")
    head_sql, line_sql = fake_query.calls[0][0], fake_query.calls[1][0]
    assert fake_query.calls[0][1]["key"] == "SC90001" and "[Phone No_]" in head_sql
    assert "$Service Contract Line]" in line_sql
    c = r["contract"]
    assert c["change_status"] == "locked" and c["prepaid"] is True and c["invoice_period"] == "monthly"
    assert c["service_period"] == "1Y (1 year)" and "phone" not in c and "email" not in c   # empties dropped
    assert r["line_count"] == 2 and r["lines_total_amount"] == 150.5
    assert r["lines"][0]["service_period"] == "1M (1 month)" and "serial_no" not in r["lines"][0]
    json.dumps(r)


def test_contract_details_not_found(app, fake_query):
    with app.app_context():
        assert erp_tools.contract_details("NOPE")["found"] is False

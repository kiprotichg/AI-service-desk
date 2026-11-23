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
    with app.app_context():
        for name, (fn, _d, props, _r) in erp_tools.LOOKUPS.items():
            fn(**{k: "x" for k in props})
    all_sql = " ".join(sql for sql, _ in fake_query.calls)
    for forbidden in ("$Employee", "$Payroll", "$Resource]", "Social Security", "National ID", "Phone"):
        assert forbidden not in all_sql


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

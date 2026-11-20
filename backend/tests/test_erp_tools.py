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

ERP = dict(ERP_DB_HOST="h", ERP_DB_PORT="2026", ERP_DB_NAME="IDEALDYNAMICS_TEST", ERP_DB_USER="u",
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


# --------------------------------------------- one AI call for obvious numbers
@pytest.mark.parametrize("text,expected", [
    ("Who is customer C00309?", [("find_customers", {"query": "C00309"})]),
    ("What services does c00309 have?", [("find_customers", {"query": "C00309"}),
                                         ("customer_services", {"customer_no": "C00309"})]),
    ("Show service item sit1175", [("service_item", {"number_or_text": "SIT1175"})]),
    ("When is SC01008 invoiced?", [("find_contracts", {"query": "SC01008"})]),
    ("How do I log a service request?", []),
    ("Order C0030912 and XC00309", []),      # not a whole customer number
])
def test_planned_lookups(text, expected):
    assert erp_tools.planned_lookups(text) == expected


class OneCallGemini:
    """Answers straight away (no lookup requests)."""
    def __init__(self):
        self.calls = []
        self.models = SimpleNamespace(generate_content_stream=self._stream)

    def _stream(self, **kw):
        self.calls.append(kw)
        part = SimpleNamespace(function_call=None, thought=False,
                               text='C00309 has 265 services.\n<meta>{"intent": "service_request"}</meta>')
        return iter([SimpleNamespace(text=part.text, candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])])


def test_numbers_in_the_question_are_looked_up_first(app, client, monkeypatch):
    _, looked_up, conv = _setup(app, monkeypatch, client, "jane@idealdynamics.co.ke")
    fake = OneCallGemini()
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: fake)
    res = client.post(f"/api/conversations/{conv['id']}/messages?stream=1",
                      headers={"X-Conversation-Token": conv["token"]},
                      json={"content": "What services does C00309 have?", "mode": "service"})
    done = [json.loads(line) for line in res.get_data(as_text=True).splitlines()][-1]
    assert sorted(name for name, _ in looked_up) == ["customer_services", "find_customers"]
    assert len(fake.calls) == 1                              # one AI call, not two
    sent = str(fake.calls[0]["contents"][-1])
    assert "<erp_data>" in sent and "C00309" in sent
    assert "IDEALDYNAMICS_PROP" in done["reply"]["erp_note"]


def test_no_prefetch_for_users_without_erp_access(app, client, monkeypatch):
    _, looked_up, conv = _setup(app, monkeypatch, client, "eve@gmail.com")
    fake = OneCallGemini()
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: fake)
    client.post(f"/api/conversations/{conv['id']}/messages?stream=1",
                headers={"X-Conversation-Token": conv["token"]},
                json={"content": "What services does C00309 have?", "mode": "service"}).get_data()
    assert looked_up == [] and "<erp_data>" not in str(fake.calls[0]["contents"][-1])

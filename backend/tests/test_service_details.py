"""Equipment serial number + customer site on Service tickets."""
import json
from types import SimpleNamespace

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.seed import seed_agent
from app.services import ai_service
from app.services.ai_service import extract_service_details


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        seed_agent("agent", "s3cret-pass")
    yield app
    with app.app_context():
        db.drop_all()


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture
def agent(client):
    login = client.post("/api/auth/login", json={"username": "agent", "password": "s3cret-pass"})
    return {"Authorization": f"Bearer {login.get_json()['token']}"}


def _conv(client, mode="service"):
    conv = client.post("/api/conversations", json={"mode": mode}).get_json()
    return conv, {"X-Conversation-Token": conv["token"]}


def _say(client, conv, h, text, mode="service"):
    return client.post(f"/api/conversations/{conv['id']}/messages",
                       json={"content": text, "mode": mode}, headers=h).get_json()


class FakeGemini:
    """Returns the queued payloads in order, one per call."""
    def __init__(self, *payloads):
        self.calls, self.payloads = [], list(payloads)
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, **kw):
        self.calls.append(kw)
        base = {"reply": "ok", "intent": "service_request", "sentiment": "neutral",
                "escalate": False, "ticket_subject": "", "serial_number": "",
                "customer_site": ""}
        return SimpleNamespace(text=json.dumps({**base, **self.payloads.pop(0)}))


def _live(app, monkeypatch, *payloads):
    app.config.update(AI_ENABLED=True, AI_PROVIDER="gemini")
    fake = FakeGemini(*payloads)
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: fake)
    return fake


# --------------------------------------------------------------- extraction
@pytest.mark.parametrize("text,expected", [
    ("Pump serial no: AB-1234 failed, site: Nairobi West branch", ("AB-1234", "Nairobi West branch")),
    ("S/N 99812 is leaking", ("99812", "")),
    ("Serial number is XJ9-220. Customer site is Acme Ltd, Mombasa.", ("XJ9-220", "Acme Ltd, Mombasa")),
    ("serial number please", ("", "")),        # no digit: not a serial
    ("book a site visit tomorrow", ("", "")),  # "site visit" isn't a site name
])
def test_extract_service_details(text, expected):
    assert extract_service_details(text) == expected


def test_schema_and_prompt_ask_for_the_details():
    assert {"serial_number", "customer_site"} <= set(ai_service.REPLY_SCHEMA["required"])
    p = " ".join(ai_service.SERVICE_PROMPT.split())
    assert "equipment serial number and the customer site" in p
    assert "Never guess or make one up" in p


# ---------------------------------------------------------------- live AI
def test_ticket_gets_serial_and_site_from_the_model(app, client, agent, monkeypatch):
    _live(app, monkeypatch, {"escalate": True, "ticket_subject": "Compressor down",
                             "serial_number": "CMP-7781", "customer_site": "Kisumu depot"})
    conv, h = _conv(client)
    body = _say(client, conv, h, "Our compressor CMP-7781 at the Kisumu depot is down")
    assert body["ticket"]["serial_number"] == "CMP-7781"
    assert body["ticket"]["customer_site"] == "Kisumu depot"
    t = client.get("/api/tickets", headers=agent).get_json()[0]
    assert (t["serial_number"], t["customer_site"]) == ("CMP-7781", "Kisumu depot")


def test_later_message_fills_in_the_missing_detail(app, client, monkeypatch):
    _live(app, monkeypatch,
          {"escalate": True, "ticket_subject": "Pump fault", "serial_number": "P-100"},
          {"customer_site": "Eldoret plant"})  # the follow-up answer, no new escalation
    conv, h = _conv(client)
    first = _say(client, conv, h, "Pump P-100 has a fault")
    assert first["ticket"]["customer_site"] == ""
    second = _say(client, conv, h, "It's at our Eldoret plant")
    assert second["ticket"]["id"] == first["ticket"]["id"]
    assert (second["ticket"]["serial_number"], second["ticket"]["customer_site"]) == \
        ("P-100", "Eldoret plant")


def test_sales_and_general_never_carry_service_details(app, client, monkeypatch):
    _live(app, monkeypatch,
          {"escalate": True, "intent": "invoicing", "ticket_subject": "Invoice issue",
           "serial_number": "X-1", "customer_site": "HQ"})
    conv, h = _conv(client, "sales")
    body = _say(client, conv, h, "Invoice won't post", mode="sales")
    assert body["ticket"]["module"] == "sales"
    assert (body["ticket"]["serial_number"], body["ticket"]["customer_site"]) == ("", "")


# ---------------------------------------------------------------- offline
def test_offline_ticket_asks_for_missing_details(client):
    conv, h = _conv(client)
    body = _say(client, conv, h, "I need a person, the generator won't start")
    assert body["ticket"] is not None
    reply = body["reply"]["content"]
    assert "equipment serial number" in reply and "customer site" in reply


def test_offline_reply_fills_details_from_explicit_forms(client):
    conv, h = _conv(client)
    _say(client, conv, h, "I need a person, the generator won't start")
    body = _say(client, conv, h, "Serial no: GEN-5521, site: Nakuru branch")
    assert (body["ticket"]["serial_number"], body["ticket"]["customer_site"]) == \
        ("GEN-5521", "Nakuru branch")
    assert "please reply with" not in body["reply"]["content"]  # nothing left to ask


# ------------------------------------------------------------ agent editing
def test_agent_can_edit_details(client, agent):
    conv, h = _conv(client)
    t = _say(client, conv, h, "I need a person please")["ticket"]
    res = client.patch(f"/api/tickets/{t['id']}", headers=agent,
                       json={"serial_number": "  SN-42 ", "customer_site": "Thika"})
    assert (res.get_json()["serial_number"], res.get_json()["customer_site"]) == ("SN-42", "Thika")
    cleared = client.patch(f"/api/tickets/{t['id']}", headers=agent, json={"customer_site": ""})
    assert cleared.get_json()["customer_site"] == ""


@pytest.mark.parametrize("body", [{"serial_number": 123}, {"customer_site": "x" * 201}])
def test_invalid_detail_edits_are_rejected(client, agent, body):
    conv, h = _conv(client)
    t = _say(client, conv, h, "I need a person please")["ticket"]
    assert client.patch(f"/api/tickets/{t['id']}", headers=agent, json=body).status_code == 400


def test_details_need_an_agent(client):
    conv, h = _conv(client)
    t = _say(client, conv, h, "I need a person please")["ticket"]
    assert client.patch(f"/api/tickets/{t['id']}", json={"serial_number": "x1"}).status_code == 401

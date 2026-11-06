"""Service vs General answer modes."""
import json
from types import SimpleNamespace

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.seed import seed_agent
from app.services import ai_service


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


def _conv(client, **body):
    conv = client.post("/api/conversations", json=body).get_json()
    return conv, {"X-Conversation-Token": conv["token"]}


def _say(client, conv, h, text, **extra):
    return client.post(f"/api/conversations/{conv['id']}/messages",
                       json={"content": text, **extra}, headers=h)


class FakeGemini:
    def __init__(self, payload):
        self.calls = []
        self.payload = payload
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(text=json.dumps(self.payload))


def _live(app, monkeypatch, payload):
    app.config.update(AI_ENABLED=True, AI_PROVIDER="gemini")
    fake = FakeGemini(payload)
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: fake)
    return fake


ESCALATING = {"reply": "ok", "intent": "complaint", "sentiment": "negative",
              "escalate": True, "ticket_subject": "Broken"}


def test_new_chats_default_to_service(client):
    conv, _ = _conv(client)
    assert conv["mode"] == "service"


def test_chat_can_start_in_general_mode(client):
    conv, _ = _conv(client, mode="general")
    assert conv["mode"] == "general"


def test_invalid_mode_is_rejected(client):
    assert client.post("/api/conversations", json={"mode": "pirate"}).status_code == 400
    conv, h = _conv(client)
    assert _say(client, conv, h, "hi", mode="pirate").status_code == 400


def test_service_mode_uses_service_prompt_and_knowledge_base(app, client, monkeypatch):
    fake = _live(app, monkeypatch, {**ESCALATING, "sentiment": "neutral", "escalate": False})
    conv, h = _conv(client)
    _say(client, conv, h, "How do I assign a service request to a technician?", mode="service")
    call = fake.calls[0]
    assert call["config"].system_instruction == ai_service.SERVICE_PROMPT
    assert "technician's schedule" in call["contents"][-1].parts[-1].text  # article included


def test_general_mode_uses_general_prompt_without_knowledge_base(app, client, monkeypatch):
    fake = _live(app, monkeypatch, {**ESCALATING, "escalate": False})
    conv, h = _conv(client)
    _say(client, conv, h, "How do I assign a service request to a technician?", mode="general")
    call = fake.calls[0]
    assert call["config"].system_instruction == ai_service.GENERAL_PROMPT
    text = call["contents"][-1].parts[-1].text
    assert "<knowledge_base>" not in text and "<user_message>" in text


def test_general_mode_never_opens_tickets(app, client, monkeypatch):
    _live(app, monkeypatch, ESCALATING)  # the model even asks to escalate
    conv, h = _conv(client)
    body = _say(client, conv, h, "this is broken!", mode="general").get_json()
    assert body["ticket"] is None and body["conversation"]["status"] == "open"


def test_service_mode_still_opens_tickets(app, client, monkeypatch):
    _live(app, monkeypatch, ESCALATING)
    conv, h = _conv(client)
    body = _say(client, conv, h, "my order arrived broken", mode="service").get_json()
    assert body["ticket"]["subject"] == "Broken"


def test_mode_can_switch_mid_chat_and_is_remembered(client):
    conv, h = _conv(client)
    assert _say(client, conv, h, "hi", mode="general").get_json()["conversation"]["mode"] == "general"
    # No mode sent: keeps the last one used.
    assert _say(client, conv, h, "hi again").get_json()["conversation"]["mode"] == "general"
    _say(client, conv, h, "assign a technician", mode="service")
    assert client.get(f"/api/conversations/{conv['id']}", headers=h).get_json()["mode"] == "service"


def test_offline_general_mode_points_to_service(client):
    conv, h = _conv(client)
    body = _say(client, conv, h, "I want a human, my order is late", mode="general").get_json()
    assert "Service mode" in body["reply"]["content"]
    assert body["ticket"] is None


def test_analytics_counts_modes(client):
    login = client.post("/api/auth/login", json={"username": "agent", "password": "s3cret-pass"})
    agent = {"Authorization": f"Bearer {login.get_json()['token']}"}
    _conv(client, mode="general")
    _conv(client)
    assert client.get("/api/analytics", headers=agent).get_json()["modes"] == {
        "general": 1, "service": 1}

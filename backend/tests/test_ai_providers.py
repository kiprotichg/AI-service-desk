"""Live-provider code paths, with each SDK client replaced by a fake."""
import json
from types import SimpleNamespace

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.services import ai_service

GOOD = {"reply": "Open a new service request for the customer.", "intent": "service_request",
        "sentiment": "neutral", "escalate": False, "ticket_subject": ""}


@pytest.fixture
def app():
    app = create_app(TestConfig)
    yield app
    with app.app_context():
        db.drop_all()


def _use(app, provider):
    app.config.update(AI_ENABLED=True, AI_PROVIDER=provider)


HISTORY = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello!"}]


# --------------------------------------------------------------------- fakes
class FakeAnthropic:
    def __init__(self, response=None, error=None):
        self.calls = []
        self._response, self._error = response, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


def claude_response(payload, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason,
                           content=[SimpleNamespace(type="text", text=json.dumps(payload))])


class FakeGemini:
    def __init__(self, text=None, error=None):
        self.calls = []
        self._text, self._error = text, error
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return SimpleNamespace(text=self._text)


# -------------------------------------------------------------------- Claude
def test_claude_request_and_reply(app, monkeypatch):
    _use(app, "claude")
    fake = FakeAnthropic(claude_response(GOOD))
    monkeypatch.setattr(ai_service, "_get_anthropic_client", lambda: fake)
    with app.app_context():
        r = ai_service.generate_reply(HISTORY, "How do I log a service request?")
    assert (r.source, r.intent, r.reply) == ("claude", "service_request", GOOD["reply"])
    call = fake.calls[0]
    assert call["model"] == app.config["CLAUDE_MODEL"]
    assert call["output_config"]["format"]["schema"] == ai_service.REPLY_SCHEMA
    assert call["messages"][:2] == HISTORY
    assert "<user_message>" in call["messages"][-1]["content"]


def test_claude_refusal_escalates(app, monkeypatch):
    _use(app, "claude")
    fake = FakeAnthropic(SimpleNamespace(stop_reason="refusal", content=[]))
    monkeypatch.setattr(ai_service, "_get_anthropic_client", lambda: fake)
    with app.app_context():
        r = ai_service.generate_reply([], "something")
    assert r.escalate and r.source == "claude"


# -------------------------------------------------------------------- Gemini
def test_gemini_request_and_reply(app, monkeypatch):
    _use(app, "gemini")
    fake = FakeGemini(json.dumps(GOOD))
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: fake)
    with app.app_context():
        r = ai_service.generate_reply(HISTORY, "How do I log a service request?")
    assert (r.source, r.intent, r.reply) == ("gemini", "service_request", GOOD["reply"])
    call = fake.calls[0]
    assert call["model"] == app.config["GEMINI_MODEL"]
    # History is converted to Gemini's format; "assistant" becomes "model".
    assert [c.role for c in call["contents"]] == ["user", "model", "user"]
    assert call["contents"][1].parts[0].text == "Hello!"
    cfg = call["config"]
    assert cfg.system_instruction == ai_service.SERVICE_PROMPT
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema == ai_service.REPLY_SCHEMA


def test_gemini_blocked_response_escalates(app, monkeypatch):
    _use(app, "gemini")
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: FakeGemini(text=None))
    with app.app_context():
        r = ai_service.generate_reply([], "something")
    assert r.escalate and r.source == "gemini"


def test_gemini_through_the_chat_endpoint(app, monkeypatch):
    _use(app, "gemini")
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: FakeGemini(json.dumps(GOOD)))
    with app.test_client() as c:
        assert c.get("/api/health").get_json()["ai_mode"] == "gemini"
        conv = c.post("/api/conversations", json={}).get_json()
        body = c.post(f"/api/conversations/{conv['id']}/messages", json={"content": "shipping?"},
                      headers={"X-Conversation-Token": conv["token"]}).get_json()
    assert body["source"] == "gemini"
    assert body["reply"]["content"] == GOOD["reply"]


# ------------------------------------------------------------ shared handling
@pytest.mark.parametrize("provider,getter,fake", [
    ("claude", "_get_anthropic_client", lambda: FakeAnthropic(error=RuntimeError("down"))),
    ("gemini", "_get_gemini_client", lambda: FakeGemini(error=RuntimeError("down"))),
    ("gemini", "_get_gemini_client", lambda: FakeGemini(text="not json")),
    ("gemini", "_get_gemini_client", lambda: FakeGemini(text=json.dumps({**GOOD, "reply": ""}))),
])
def test_failures_fall_back_to_offline(app, monkeypatch, provider, getter, fake):
    _use(app, provider)
    client = fake()
    monkeypatch.setattr(ai_service, getter, lambda: client)
    with app.app_context():
        r = ai_service.generate_reply([], "How do I log a new service request?")
    assert r.source == "offline"
    assert "new service request" in r.reply


def test_off_schema_values_are_coerced(app, monkeypatch):
    _use(app, "gemini")
    odd = {**GOOD, "intent": "astrology", "sentiment": "ecstatic"}
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: FakeGemini(json.dumps(odd)))
    with app.app_context():
        r = ai_service.generate_reply([], "hi")
    assert (r.intent, r.sentiment) == ("general", "neutral")


def test_unknown_provider_is_rejected():
    class Bad(TestConfig):
        AI_PROVIDER = "openai"
    with pytest.raises(ValueError, match="AI_PROVIDER"):
        create_app(Bad)


def test_gemini_client_retries_temporary_errors(monkeypatch):
    """Gemini often returns 503 'high demand' briefly; the client must retry it."""
    from google import genai
    captured = {}

    class FakeClient:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(genai, "Client", FakeClient)
    monkeypatch.setattr(ai_service, "_gemini_client", None)
    ai_service._get_gemini_client()
    retry = captured["http_options"].retry_options
    assert retry.attempts >= 3
    assert {429, 503} <= set(retry.http_status_codes)
    monkeypatch.setattr(ai_service, "_gemini_client", None)  # don't leak the fake

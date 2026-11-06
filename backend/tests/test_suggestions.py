"""Follow-up suggestions offered after every reply."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect, text

from app import _add_missing_columns, create_app
from app.config import TestConfig
from app.extensions import db
from app.services import ai_service


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


def _gemini(app, monkeypatch, suggestions):
    app.config.update(AI_ENABLED=True, AI_PROVIDER="gemini")
    calls = []

    def gen(**kw):
        calls.append(kw)
        return SimpleNamespace(text=json.dumps({
            "reply": "Here you go.", "intent": "question", "sentiment": "neutral",
            "escalate": False, "ticket_subject": "", "serial_number": "",
            "customer_site": "", "suggestions": suggestions}))
    monkeypatch.setattr(ai_service, "_get_gemini_client",
                        lambda: SimpleNamespace(models=SimpleNamespace(generate_content=gen)))
    return calls


def _send(client, content, mode):
    conv = client.post("/api/conversations", json={"mode": mode}).get_json()
    h = {"X-Conversation-Token": conv["token"]}
    res = client.post(f"/api/conversations/{conv['id']}/messages",
                      json={"content": content, "mode": mode}, headers=h).get_json()
    return conv, h, res


def test_schema_and_every_prompt_ask_for_suggestions():
    assert "suggestions" in ai_service.REPLY_SCHEMA["required"]
    for mode, prompt in ai_service.PROMPTS.items():
        assert '"suggestions"' in prompt, mode


def test_model_suggestions_are_returned_and_saved(app, client, monkeypatch):
    calls = _gemini(app, monkeypatch, ["What is a black hole?", "How hot is the sun?",
                                       "Why do stars twinkle?"])
    conv, h, res = _send(client, "Why is the sky blue?", "general")
    assert res["reply"]["suggestions"] == ["What is a black hole?", "How hot is the sun?",
                                           "Why do stars twinkle?"]
    assert calls[0]["config"].response_json_schema["properties"]["suggestions"]
    # Saved with the reply, so they're still there when the chat is reopened.
    msgs = client.get(f"/api/conversations/{conv['id']}", headers=h).get_json()["messages"]
    assert msgs[1]["suggestions"] == res["reply"]["suggestions"]
    assert msgs[0]["suggestions"] == []  # user messages never carry any


def test_suggestions_are_cleaned_up(app, client, monkeypatch):
    _gemini(app, monkeypatch, ["1. Why is the sky blue?",            # repeats the question
                               "- What about sunsets?", "what about SUNSETS",  # duplicate
                               "", 42, "x" * 300, "Is space black?", "One too many?"])
    _, _, res = _send(client, "Why is the sky blue?", "general")
    got = res["reply"]["suggestions"]
    # List markers removed, the repeat, duplicate, blank and too-short ones dropped,
    # long ones cut to 100 characters, and no more than 3.
    assert got == ["What about sunsets?", "x" * 100, "Is space black?"]


def test_offline_service_reply_suggests_help_articles(client):
    _, _, res = _send(client, "How do I record spare parts used on a job?", "service")
    s = res["reply"]["suggestions"]
    assert len(s) == 3 and "How do I record spare parts used on a job?" not in s
    # Each offline suggestion is itself answered by a help article.
    for q in s:
        _, _, follow = _send(client, q, "service")
        assert "couldn't find" not in follow["reply"]["content"], q


def test_offline_sales_reply_suggests_sales_topics(client):
    _, _, res = _send(client, "How do I create a sales quotation?", "sales")
    assert "How do I convert a quotation into a sales order?" in res["reply"]["suggestions"]


def test_model_without_suggestions_falls_back_to_topic_ones(app, client, monkeypatch):
    _gemini(app, monkeypatch, [])
    _, _, res = _send(client, "How do I log a service request?", "service")
    assert len(res["reply"]["suggestions"]) == 3


def test_general_mode_offline_offers_none(client):
    _, _, res = _send(client, "Why is the sky blue?", "general")
    assert res["reply"]["suggestions"] == []


def test_regenerate_gives_fresh_suggestions(app, client, monkeypatch):
    _gemini(app, monkeypatch, ["First idea?"])
    conv, h, res = _send(client, "Tell me a fact", "general")
    _gemini(app, monkeypatch, ["Second idea?"])
    again = client.post(f"/api/conversations/{conv['id']}/messages/{res['reply']['id']}"
                        "/regenerate", json={"mode": "general"}, headers=h).get_json()
    assert again["reply"]["suggestions"] == ["Second idea?"]


def test_old_database_gets_the_new_column(app):
    with app.app_context():
        with db.engine.begin() as conn:
            conn.execute(text('ALTER TABLE "message" DROP COLUMN "suggestions_json"'))
        _add_missing_columns()
        cols = {c["name"] for c in inspect(db.engine).get_columns("message")}
    assert "suggestions_json" in cols

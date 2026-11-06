"""Sales module mode: its own knowledge base, prompt, tickets and offline answers."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import FAQ
from app.seed import seed_agent
from app.services import ai_service, knowledge_base


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


def _conv(client, **body):
    conv = client.post("/api/conversations", json=body).get_json()
    return conv, {"X-Conversation-Token": conv["token"]}


def _say(client, conv, h, text, mode="sales"):
    return client.post(f"/api/conversations/{conv['id']}/messages",
                       json={"content": text, "mode": mode}, headers=h).get_json()


class FakeGemini:
    def __init__(self, payload):
        self.calls, self.payload = [], payload
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(text=json.dumps(self.payload))


def _live(app, monkeypatch, **payload):
    app.config.update(AI_ENABLED=True, AI_PROVIDER="gemini")
    fake = FakeGemini({"reply": "ok", "intent": "quotation", "sentiment": "neutral",
                       "escalate": False, "ticket_subject": "", **payload})
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: fake)
    return fake


# ------------------------------------------------------------ knowledge base
def test_sales_articles_are_seeded_separately(client):
    sales = client.get("/api/faqs?module=sales").get_json()
    service = client.get("/api/faqs?module=service").get_json()
    assert len(sales) >= 6 and all(f["module"] == "sales" for f in sales)
    assert len(service) >= 5 and all(f["module"] == "service" for f in service)
    assert client.get("/api/faqs?module=hr").status_code == 400


def test_search_stays_inside_its_module(app):
    with app.app_context():
        sales = knowledge_base.search("how do I create a quotation", module="sales")
        service = knowledge_base.search("how do I create a quotation", module="service")
    assert sales and "quotation" in sales[0].question.lower()
    assert all(f.module != "sales" for f in service)


def test_one_shared_question_word_is_not_a_match(app):
    # "why" alone used to match "Why can't I save an order because ... credit limit?"
    with app.app_context():
        assert knowledge_base.search("Why is the sky blue?", module="sales") == []


def test_agent_can_add_a_sales_article(client, agent):
    res = client.post("/api/faqs", headers=agent, json={
        "question": "How do I set a sales target?", "answer": "Ask your sales manager.",
        "keywords": "target,goal", "module": "sales"})
    assert res.status_code == 201 and res.get_json()["module"] == "sales"
    bad = client.post("/api/faqs", headers=agent,
                      json={"question": "q", "answer": "a", "module": "general"})
    assert bad.status_code == 400


def test_old_database_gets_sales_articles_and_keeps_its_own(tmp_path):
    """A database from before modules existed: old articles count as Service."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE faq (id INTEGER PRIMARY KEY, question VARCHAR(300) NOT NULL,"
                " answer TEXT NOT NULL, keywords VARCHAR(300))")
    con.execute("INSERT INTO faq (question, answer, keywords) VALUES ('Old Q', 'Old A', 'old')")
    con.commit()
    con.close()

    class OldDB(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{path}"
    app = create_app(OldDB)
    with app.app_context():
        assert FAQ.query.filter_by(module="sales").count() >= 6
        service = knowledge_base.articles_for("service").all()
        # The old article is still the only Service one: no duplicate defaults added.
        assert [f.question for f in service] == ["Old Q"]


# -------------------------------------------------------------------- live AI
def test_sales_mode_uses_sales_prompt_and_sales_articles(app, client, monkeypatch):
    fake = _live(app, monkeypatch)
    conv, h = _conv(client, mode="sales")
    _say(client, conv, h, "How do I convert a quotation into a sales order?")
    call = fake.calls[0]
    assert call["config"].system_instruction == ai_service.SALES_PROMPT
    text = call["contents"][-1].parts[-1].text
    assert "convert it to a sales" in text or "convert or copy it to a sales order" in text
    assert "technician" not in text.lower()  # no Service-module articles
    assert "<user_message>" in text


def test_sales_prompt_names_the_product_and_forbids_inventing_menus():
    assert "Ideal Dynamics" in ai_service.SALES_PROMPT
    assert "Never invent menu names" in ai_service.SALES_PROMPT


def test_sales_ticket_is_tagged_sales(app, client, agent, monkeypatch):
    _live(app, monkeypatch, escalate=True, ticket_subject="Invoice won't post",
          intent="invoicing")
    conv, h = _conv(client, mode="sales")
    body = _say(client, conv, h, "My invoice won't post, error 500")
    assert body["ticket"]["module"] == "sales"
    assert body["user_message"]["intent"] == "invoicing"
    tickets = client.get("/api/tickets", headers=agent).get_json()
    assert tickets[0]["module"] == "sales"


def test_sales_intents_are_accepted(app, client, monkeypatch):
    for intent in ai_service.SALES_INTENTS:
        _live(app, monkeypatch, intent=intent)
        conv, h = _conv(client, mode="sales")
        assert _say(client, conv, h, "hi")["user_message"]["intent"] == intent


# -------------------------------------------------------------------- offline
def test_offline_sales_answers_from_sales_articles(client):
    conv, h = _conv(client, mode="sales")
    body = _say(client, conv, h, "How do I issue a credit note?")
    assert "credit note" in body["reply"]["content"].lower()
    assert body["user_message"]["intent"] == "invoicing"
    assert body["ticket"] is None


def test_offline_sales_off_topic_points_to_other_modes(client):
    conv, h = _conv(client, mode="sales")
    body = _say(client, conv, h, "Why is the sky blue?")
    assert "Sales mode" in body["reply"]["content"]
    assert body["user_message"]["intent"] == "question" and body["ticket"] is None


def test_offline_sales_human_request_opens_sales_ticket(client):
    conv, h = _conv(client, mode="sales")
    body = _say(client, conv, h, "I need a person to help with a customer price list")
    assert body["ticket"]["module"] == "sales"


def test_service_off_topic_mentions_sales_mode(client):
    conv, h = _conv(client)
    body = _say(client, conv, h, "Why is the sky blue?", mode="service")
    assert "Sales mode" in body["reply"]["content"]

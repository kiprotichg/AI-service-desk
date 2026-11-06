"""Service mode = the ERP Service module assistant."""
import sqlite3

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.seed import LEGACY_STORE_ARTICLES
from app.services import ai_service, knowledge_base


@pytest.fixture
def app():
    app = create_app(TestConfig)
    yield app
    with app.app_context():
        db.drop_all()


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


def _say(client, text):
    conv = client.post("/api/conversations", json={"mode": "service"}).get_json()
    return client.post(f"/api/conversations/{conv['id']}/messages",
                       json={"content": text, "mode": "service"},
                       headers={"X-Conversation-Token": conv["token"]}).get_json()


def test_service_prompt_is_about_the_erp_service_module():
    p = " ".join(ai_service.SERVICE_PROMPT.split())  # ignore line breaks
    assert "Service module assistant for Ideal Dynamics" in p
    assert "Never invent menu names" in p
    assert "online store" not in p


def test_service_articles_are_erp_service_topics(client):
    questions = [f["question"] for f in client.get("/api/faqs?module=service").get_json()]
    assert "How do I log a new service request?" in questions
    assert not set(questions) & LEGACY_STORE_ARTICLES


@pytest.mark.parametrize("text,intent", [
    ("How do I log a new service request?", "service_request"),
    ("Assign a technician to this job", "scheduling"),
    ("Is this equipment under warranty?", "warranty"),
    ("When does the SLA on this contract expire?", "contract"),
    ("Set up preventive maintenance every 3 months", "maintenance"),
    ("Record spare parts used", "spare_parts"),
    ("Show me the SLA compliance report", "service_report"),
    ("Export the technician workload report", "service_report"),
    ("Close the job card for this visit", "scheduling"),  # "visit" wins over "job card"
    ("Close the job card", "job_card"),
])
def test_offline_service_intents(client, text, intent):
    assert _say(client, text)["user_message"]["intent"] == intent


def test_offline_service_answers_from_service_articles(client):
    body = _say(client, "How do I record spare parts used on a job?")
    assert "job card" in body["reply"]["content"]
    assert body["ticket"] is None


def test_offline_service_off_topic_points_to_other_modes(client):
    body = _say(client, "Write me a poem about the sea")
    assert "Service mode" in body["reply"]["content"]
    assert "Sales mode" in body["reply"]["content"] and body["ticket"] is None


def test_upgrade_replaces_old_store_articles_but_keeps_custom_ones(tmp_path):
    """An existing database with the old store defaults gets the ERP Service articles."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE faq (id INTEGER PRIMARY KEY, question VARCHAR(300) NOT NULL,"
                " answer TEXT NOT NULL, keywords VARCHAR(300))")
    for q in LEGACY_STORE_ARTICLES:
        con.execute("INSERT INTO faq (question, answer, keywords) VALUES (?, 'old', 'old')", (q,))
    con.commit()
    con.close()

    class OldDB(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{path}"
    app = create_app(OldDB)
    with app.app_context():
        questions = {f.question for f in knowledge_base.articles_for("service").all()}
    assert not questions & LEGACY_STORE_ARTICLES
    assert "How do I log a new service request?" in questions


def test_custom_service_articles_are_never_removed(tmp_path):
    path = tmp_path / "custom.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE faq (id INTEGER PRIMARY KEY, question VARCHAR(300) NOT NULL,"
                " answer TEXT NOT NULL, keywords VARCHAR(300))")
    con.execute("INSERT INTO faq (question, answer, keywords) VALUES "
                "('How do I raise a callout?', 'Our own answer', 'callout')")
    con.commit()
    con.close()

    class CustomDB(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{path}"
    app = create_app(CustomDB)
    with app.app_context():
        questions = [f.question for f in knowledge_base.articles_for("service").all()]
    assert questions == ["How do I raise a callout?"]

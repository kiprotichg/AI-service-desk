import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.seed import seed_agent


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
def agent_headers(client):
    token = client.post("/api/auth/login",
                        json={"username": "agent", "password": "s3cret-pass"}).get_json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _new_conv(client, name="Ada"):
    """Start a chat; returns (conversation, customer headers)."""
    conv = client.post("/api/conversations", json={"customer_name": name}).get_json()
    return conv, {"X-Conversation-Token": conv["token"]}


def _say(client, conv, headers, text):
    return client.post(f"/api/conversations/{conv['id']}/messages",
                       json={"content": text}, headers=headers)


# ------------------------------------------------------------------- chat
def test_health_reports_offline_mode(client):
    assert client.get("/api/health").get_json() == {"status": "ok", "ai_mode": "offline"}


def test_faqs_are_seeded_and_public(client):
    assert len(client.get("/api/faqs").get_json()) >= 5


def test_faq_answer_without_escalation(client):
    conv, h = _new_conv(client)
    res = _say(client, conv, h, "How do I log a new service request?")
    body = res.get_json()
    assert res.status_code == 201
    assert "new service request" in body["reply"]["content"]
    assert body["user_message"]["intent"] == "service_request"
    assert body["ticket"] is None
    assert body["conversation"]["status"] == "open"


def test_request_for_human_creates_one_ticket(client, agent_headers):
    conv, h = _new_conv(client)
    first = _say(client, conv, h, "I want to talk to a human").get_json()
    second = _say(client, conv, h, "agent please").get_json()
    assert first["ticket"] is not None
    assert second["ticket"]["id"] == first["ticket"]["id"]  # no duplicate tickets
    assert len(client.get("/api/tickets", headers=agent_headers).get_json()) == 1


def test_angry_message_is_high_priority(client):
    conv, h = _new_conv(client)
    body = _say(client, conv, h, "The technician never showed up, this is unacceptable").get_json()
    assert body["user_message"]["sentiment"] == "negative"
    assert body["ticket"]["priority"] == "high"
    assert "sorry" in body["reply"]["content"].lower()  # not a canned FAQ answer


def test_offline_general_question_opens_no_ticket(client):
    conv, h = _new_conv(client)
    body = _say(client, conv, h, "Why is the sky blue?").get_json()
    assert body["user_message"]["intent"] == "question"
    assert body["ticket"] is None
    assert body["conversation"]["status"] == "open"
    assert "General mode" in body["reply"]["content"]  # points to the other mode


@pytest.mark.parametrize("text", ["I feel happy today", "What is the border of France?"])
def test_word_fragments_are_not_service_topics(client, text):
    # Whole words only: "happy" isn't "app", "border" isn't "order".
    conv, h = _new_conv(client)
    assert _say(client, conv, h, text).get_json()["user_message"]["intent"] == "question"


def test_message_validation(client):
    conv, h = _new_conv(client)
    assert _say(client, conv, h, "   ").status_code == 400
    assert _say(client, conv, h, "x" * 5000).status_code == 400


def test_closed_conversation_rejects_messages(client):
    conv, h = _new_conv(client)
    client.post(f"/api/conversations/{conv['id']}/close", headers=h)
    assert _say(client, conv, h, "hi").status_code == 409


def test_update_ticket(client, agent_headers):
    conv, h = _new_conv(client)
    t = _say(client, conv, h, "speak to a manager").get_json()["ticket"]
    res = client.patch(f"/api/tickets/{t['id']}", json={"status": "resolved"},
                       headers=agent_headers)
    assert res.get_json()["status"] == "resolved"
    assert client.patch(f"/api/tickets/{t['id']}", json={"status": "bogus"},
                        headers=agent_headers).status_code == 400
    resolved = client.get("/api/tickets?status=resolved", headers=agent_headers).get_json()
    assert resolved[0]["id"] == t["id"]


def test_analytics(client, agent_headers):
    conv, h = _new_conv(client)
    _say(client, conv, h, "How do I set up preventive maintenance visits?")
    data = client.get("/api/analytics", headers=agent_headers).get_json()
    assert data["conversations"] == 1
    assert data["messages"] == 2
    assert data["intents"] == {"maintenance": 1}


# ------------------------------------------------------------------- auth
def test_login(client):
    ok = client.post("/api/auth/login", json={"username": "agent", "password": "s3cret-pass"})
    assert ok.status_code == 200 and ok.get_json()["agent"]["username"] == "agent"
    bad_pw = client.post("/api/auth/login", json={"username": "agent", "password": "nope"})
    no_user = client.post("/api/auth/login", json={"username": "ghost", "password": "nope"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.get_json() == no_user.get_json()  # don't reveal which usernames exist


def test_me(client, agent_headers):
    assert client.get("/api/auth/me", headers=agent_headers).get_json()["username"] == "agent"
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("method,path", [
    ("get", "/api/tickets"),
    ("patch", "/api/tickets/1"),
    ("get", "/api/analytics"),
    ("get", "/api/conversations"),
    ("post", "/api/faqs"),
    ("delete", "/api/faqs/1"),
])
def test_agent_endpoints_need_login(client, method, path):
    assert getattr(client, method)(path, json={}).status_code == 401
    bogus = {"Authorization": "Bearer not-a-real-token"}
    assert getattr(client, method)(path, json={}, headers=bogus).status_code == 401


def test_customer_token_only_opens_its_own_chat(client):
    conv_a, h_a = _new_conv(client, "A")
    conv_b, _ = _new_conv(client, "B")
    assert client.get(f"/api/conversations/{conv_a['id']}", headers=h_a).status_code == 200
    # A's token can't read or post to B's chat, and no token gets nothing.
    assert client.get(f"/api/conversations/{conv_b['id']}", headers=h_a).status_code == 401
    assert _say(client, conv_b, h_a, "hi").status_code == 401
    assert _say(client, conv_a, {}, "hi").status_code == 401
    assert client.post(f"/api/conversations/{conv_b['id']}/close", headers=h_a).status_code == 401


def test_tokens_are_not_interchangeable(client, agent_headers):
    conv, h = _new_conv(client)
    # A customer token used as a bearer token must not grant agent access...
    as_agent = {"Authorization": f"Bearer {h['X-Conversation-Token']}"}
    assert client.get("/api/tickets", headers=as_agent).status_code == 401
    # ...and an agent can read a chat but can't post as the customer.
    assert client.get(f"/api/conversations/{conv['id']}", headers=agent_headers).status_code == 200
    assert _say(client, conv, agent_headers, "hi").status_code == 401


def test_deactivated_agent_is_locked_out(app, client, agent_headers):
    from app.models import Agent
    with app.app_context():
        Agent.query.filter_by(username="agent").first().is_active = False
        db.session.commit()
    assert client.get("/api/tickets", headers=agent_headers).status_code == 401


def test_create_agent_cli(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["create-agent", "bob"], input="pw12345\npw12345\n")
    assert "saved" in result.output
    with app.test_client() as c:
        res = c.post("/api/auth/login", json={"username": "bob", "password": "pw12345"})
        assert res.status_code == 200

"""User accounts: register, sign in, password reset by emailed code, owned chats."""
import re
from datetime import timedelta

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import PasswordReset, utcnow
from app.routes import users as users_routes

PW = "Secret123"


class AccountsConfig(TestConfig):
    REQUIRE_USER_LOGIN = True


@pytest.fixture
def app():
    app = create_app(AccountsConfig)
    yield app
    with app.app_context():
        db.drop_all()


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture
def outbox(monkeypatch):
    """Capture reset emails instead of sending them."""
    sent = []
    monkeypatch.setattr(users_routes, "send_email",
                        lambda to, subject, body: sent.append((to, subject, body)) or True)
    return sent


def register(client, email="jane@example.com", password=PW, name="Jane Doe"):
    return client.post("/api/users/register",
                       json={"name": name, "email": email, "password": password})


def auth(token):
    return {"X-User-Token": token}


def code_from(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][2]).group(1)


# ------------------------------------------------------------------ register
def test_register_signs_you_in(client):
    res = register(client, email="  Jane@Example.COM ")
    body = res.get_json()
    assert res.status_code == 201
    assert body["user"] == {"id": body["user"]["id"], "name": "Jane Doe",
                            "email": "jane@example.com"}       # email stored lower-case
    me = client.get("/api/users/me", headers=auth(body["token"]))
    assert me.get_json()["email"] == "jane@example.com"


@pytest.mark.parametrize("payload,error", [
    ({"name": "", "email": "a@b.co", "password": PW}, "name"),
    ({"name": "A", "email": "not-an-email", "password": PW}, "valid email"),
    ({"name": "A", "email": "a@b.co", "password": "short1"}, "8 characters"),
    ({"name": "A", "email": "a@b.co", "password": "onlyletters"}, "letter and one number"),
    ({"name": "A", "email": "a@b.co", "password": "12345678"}, "letter and one number"),
])
def test_register_validation(client, payload, error):
    res = client.post("/api/users/register", json=payload)
    assert res.status_code == 400 and error in res.get_json()["error"]


def test_duplicate_email_is_rejected(client):
    register(client)
    res = register(client, email="JANE@example.com")
    assert res.status_code == 409 and "already exists" in res.get_json()["error"]


# ------------------------------------------------------------------- sign in
def test_login(client):
    register(client)
    ok = client.post("/api/users/login", json={"email": "JANE@example.com", "password": PW})
    assert ok.status_code == 200 and ok.get_json()["user"]["name"] == "Jane Doe"
    wrong = client.post("/api/users/login", json={"email": "jane@example.com", "password": "nope"})
    nobody = client.post("/api/users/login", json={"email": "ghost@example.com", "password": PW})
    assert wrong.status_code == nobody.status_code == 401
    assert wrong.get_json() == nobody.get_json()  # don't reveal which emails exist


def test_bad_or_missing_token(client):
    assert client.get("/api/users/me").status_code == 401
    assert client.get("/api/users/me", headers=auth("garbage")).status_code == 401


def test_agent_and_chat_tokens_are_not_user_tokens(client):
    conv_token = client.post("/api/conversations", json={},
                             headers=auth(register(client).get_json()["token"])).get_json()["token"]
    assert client.get("/api/users/me", headers=auth(conv_token)).status_code == 401


# --------------------------------------------------------------- reset flow
def test_forgot_password_emails_a_code_and_reset_works(client, outbox):
    old_token = register(client).get_json()["token"]
    res = client.post("/api/users/forgot-password", json={"email": "Jane@example.com"})
    assert res.status_code == 200 and "If an account exists" in res.get_json()["message"]
    assert outbox[-1][0] == "jane@example.com" and "reset code" in outbox[-1][1].lower()

    new = client.post("/api/users/reset-password", json={
        "email": "jane@example.com", "code": code_from(outbox), "password": "NewSecret9"})
    assert new.status_code == 200 and new.get_json()["token"]
    # The new password works, the old one doesn't, and old sessions are signed out.
    assert client.post("/api/users/login", json={"email": "jane@example.com",
                                                 "password": "NewSecret9"}).status_code == 200
    assert client.post("/api/users/login", json={"email": "jane@example.com",
                                                 "password": PW}).status_code == 401
    assert client.get("/api/users/me", headers=auth(old_token)).status_code == 401


def test_forgot_password_for_unknown_email_looks_the_same(client, outbox):
    register(client)
    known = client.post("/api/users/forgot-password", json={"email": "jane@example.com"})
    unknown = client.post("/api/users/forgot-password", json={"email": "ghost@example.com"})
    assert known.get_json() == unknown.get_json() and unknown.status_code == 200
    assert len(outbox) == 1  # only the real account got an email


def test_code_can_be_used_once(client, outbox):
    register(client)
    client.post("/api/users/forgot-password", json={"email": "jane@example.com"})
    code = code_from(outbox)
    body = {"email": "jane@example.com", "code": code, "password": "NewSecret9"}
    assert client.post("/api/users/reset-password", json=body).status_code == 200
    assert client.post("/api/users/reset-password", json=body).status_code == 400


def test_wrong_codes_lock_the_code_after_five_tries(client, outbox):
    register(client)
    client.post("/api/users/forgot-password", json={"email": "jane@example.com"})
    real = code_from(outbox)
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        r = client.post("/api/users/reset-password",
                        json={"email": "jane@example.com", "code": wrong, "password": "NewSecret9"})
        assert r.status_code == 400
    late = client.post("/api/users/reset-password",
                       json={"email": "jane@example.com", "code": real, "password": "NewSecret9"})
    assert late.status_code == 400 and "Too many" in late.get_json()["error"]


def test_weak_new_password_doesnt_use_up_a_try(client, outbox, app):
    register(client)
    client.post("/api/users/forgot-password", json={"email": "jane@example.com"})
    r = client.post("/api/users/reset-password",
                    json={"email": "jane@example.com", "code": code_from(outbox), "password": "short"})
    assert r.status_code == 400 and "8 characters" in r.get_json()["error"]
    with app.app_context():
        assert PasswordReset.query.one().attempts == 0


def test_expired_code_is_rejected(client, outbox, app):
    register(client)
    client.post("/api/users/forgot-password", json={"email": "jane@example.com"})
    with app.app_context():
        r = PasswordReset.query.one()
        r.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    res = client.post("/api/users/reset-password", json={
        "email": "jane@example.com", "code": code_from(outbox), "password": "NewSecret9"})
    assert res.status_code == 400 and "expired" in res.get_json()["error"]


def test_codes_are_not_resent_within_a_minute_and_old_codes_die(client, outbox, app):
    register(client)
    client.post("/api/users/forgot-password", json={"email": "jane@example.com"})
    client.post("/api/users/forgot-password", json={"email": "jane@example.com"})
    assert len(outbox) == 1                       # second request too soon: no new email
    first = code_from(outbox)
    with app.app_context():                       # pretend a minute has passed
        r = PasswordReset.query.one()
        r.created_at = utcnow() - timedelta(minutes=2)
        db.session.commit()
    client.post("/api/users/forgot-password", json={"email": "jane@example.com"})
    assert len(outbox) == 2
    old = client.post("/api/users/reset-password",
                      json={"email": "jane@example.com", "code": first, "password": "NewSecret9"})
    assert old.status_code == 400 or code_from(outbox) == first   # older code no longer works


def test_reset_codes_are_not_stored_in_plain_text(client, outbox, app):
    register(client)
    client.post("/api/users/forgot-password", json={"email": "jane@example.com"})
    with app.app_context():
        assert code_from(outbox) not in PasswordReset.query.one().code_hash


def test_dev_mode_logs_the_email_instead_of_sending(client, app, caplog):
    register(client)
    with caplog.at_level("WARNING"):
        client.post("/api/users/forgot-password", json={"email": "jane@example.com"})
    assert "NOT sent" in caplog.text and re.search(r"code is: \d{6}", caplog.text)


# --------------------------------------------------------------- owned chats
def test_chats_need_an_account_and_belong_to_it(client):
    assert client.post("/api/conversations", json={}).status_code == 401
    jane = register(client).get_json()["token"]
    bob = register(client, email="bob@example.com", name="Bob").get_json()["token"]
    conv = client.post("/api/conversations", json={"mode": "general"},
                       headers=auth(jane)).get_json()
    assert conv["customer_name"] == "Jane Doe"
    mine = client.get("/api/users/me/conversations", headers=auth(jane)).get_json()
    assert [c["id"] for c in mine] == [conv["id"]] and mine[0]["token"]
    assert client.get("/api/users/me/conversations", headers=auth(bob)).get_json() == []
    # The listed token opens the chat (works on any device after signing in).
    h = {"X-Conversation-Token": mine[0]["token"]}
    assert client.get(f"/api/conversations/{conv['id']}", headers=h).status_code == 200


def test_claiming_chats_started_before_accounts(app, client):
    app.config["REQUIRE_USER_LOGIN"] = False
    anon = client.post("/api/conversations", json={}).get_json()      # made without an account
    app.config["REQUIRE_USER_LOGIN"] = True
    jane = register(client).get_json()["token"]
    res = client.post("/api/users/me/claim-chats", json={"tokens": [anon["token"], "junk"]},
                      headers=auth(jane))
    assert res.get_json() == {"claimed": 1}
    assert [c["id"] for c in client.get("/api/users/me/conversations",
                                        headers=auth(jane)).get_json()] == [anon["id"]]
    # Someone else can't take it over afterwards.
    bob = register(client, email="bob@example.com", name="Bob").get_json()["token"]
    again = client.post("/api/users/me/claim-chats", json={"tokens": [anon["token"]]},
                        headers=auth(bob))
    assert again.get_json() == {"claimed": 0}


def test_config_reports_email_and_login(client):
    body = client.get("/api/config").get_json()
    assert body["email_enabled"] is False and body["require_login"] is True

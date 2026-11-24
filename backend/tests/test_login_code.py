"""Every sign-in needs the 6-digit code emailed to the account, after the password."""
import re

import pytest
from sqlalchemy import text

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import User


class CodeConfig(TestConfig):
    REQUIRE_USER_LOGIN = True
    REQUIRE_EMAIL_VERIFICATION = True
    REQUIRE_LOGIN_CODE = True


EMAIL, PASSWORD = "jane@example.com", "Secret123"


@pytest.fixture
def app():
    app = create_app(CodeConfig)
    with app.app_context():
        u = User(name="Jane", email=EMAIL, email_verified=True)
        u.set_password(PASSWORD)
        db.session.add(u)
        db.session.commit()
    yield app
    with app.app_context():
        db.drop_all()


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr("app.routes.users.send_email",
                        lambda to, subject, body: sent.append({"to": to, "subject": subject, "body": body}))
    return sent


def _code(mail):
    return re.search(r"\b(\d{6})\b", mail["body"]).group(1)


def _login(client, password=PASSWORD):
    return client.post("/api/users/login", json={"email": EMAIL, "password": password})


def _enter(client, code, email=EMAIL):
    return client.post("/api/users/login-code", json={"email": email, "code": code})


def test_right_password_sends_a_code_but_no_token(client, outbox):
    res = _login(client)
    body = res.get_json()
    assert res.status_code == 202 and body["code_required"] and "token" not in body
    assert outbox[0]["to"] == EMAIL and "sign-in code" in outbox[0]["subject"].lower()


def test_the_code_signs_you_in(client, outbox):
    _login(client)
    res = _enter(client, _code(outbox[0]))
    body = res.get_json()
    assert res.status_code == 200 and body["token"]
    assert client.get("/api/users/me", headers={"X-User-Token": body["token"]}).status_code == 200


def test_wrong_password_sends_nothing(client, outbox):
    res = _login(client, password="Wrong1234")
    assert res.status_code == 401 and "code_required" not in res.get_json()
    assert outbox == []


def test_code_works_once(client, outbox):
    _login(client)
    code = _code(outbox[0])
    assert _enter(client, code).status_code == 200
    assert _enter(client, code).status_code == 400


def test_five_wrong_tries_then_blocked(client, outbox):
    _login(client)
    real = _code(outbox[0])
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        assert _enter(client, wrong).status_code == 400
    res = _enter(client, real)
    assert res.status_code == 400 and "Too many" in res.get_json()["error"]


def test_expired_code_fails(app, client, outbox):
    _login(client)
    with app.app_context():
        db.session.execute(text("UPDATE password_reset SET expires_at = '2000-01-01'"))
        db.session.commit()
    assert _enter(client, _code(outbox[0])).status_code == 400


def test_signing_in_again_within_a_minute_keeps_the_same_code(client, outbox):
    _login(client)
    res = _login(client)          # e.g. "Resend code" pressed straight away
    assert res.status_code == 202 and len(outbox) == 1
    assert _enter(client, _code(outbox[0])).status_code == 200


def test_new_code_replaces_the_old_one(app, client, outbox):
    _login(client)
    with app.app_context():      # pretend the first code was sent long ago
        db.session.execute(text("UPDATE password_reset SET created_at = '2000-01-01'"))
        db.session.commit()
    _login(client)
    old, new = _code(outbox[0]), _code(outbox[1])
    if old != new:
        assert _enter(client, old).status_code == 400
    assert _enter(client, new).status_code == 200


def test_other_codes_cant_be_used_to_sign_in(client, outbox):
    client.post("/api/users/forgot-password", json={"email": EMAIL})
    reset_code = _code(outbox[0])
    assert _enter(client, reset_code).status_code == 400   # a reset code isn't a sign-in code


def test_no_code_without_the_password(client, outbox):
    """Nothing but /login (with the password) issues a sign-in code."""
    assert _enter(client, "123456").status_code == 400
    assert client.post("/api/users/resend-verification", json={"email": EMAIL}).status_code == 200
    assert outbox == []   # the account is already confirmed: no code of any kind


def test_unconfirmed_accounts_confirm_first(app, client, outbox):
    with app.app_context():
        db.session.get(User, 1).email_verified = False
        db.session.commit()
    res = _login(client)
    assert res.status_code == 403 and res.get_json()["verify_required"]
    assert "confirm" in outbox[0]["subject"].lower()


def test_can_be_switched_off(app, client, outbox):
    app.config["REQUIRE_LOGIN_CODE"] = False
    res = _login(client)
    assert res.status_code == 200 and res.get_json()["token"] and outbox == []

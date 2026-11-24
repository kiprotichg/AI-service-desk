"""Email confirmation: new accounts prove they own their email before signing in."""
import re

import pytest
from sqlalchemy import inspect, text

from app import _add_missing_columns, create_app
from app.config import TestConfig
from app.extensions import db
from app.models import User
from app.services import mailer


class VerifyConfig(TestConfig):
    REQUIRE_USER_LOGIN = True
    REQUIRE_EMAIL_VERIFICATION = True


@pytest.fixture
def app():
    app = create_app(VerifyConfig)
    yield app
    with app.app_context():
        db.drop_all()


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


@pytest.fixture
def outbox(monkeypatch):
    """Capture emails instead of sending them."""
    sent = []
    monkeypatch.setattr("app.routes.users.send_email",
                        lambda to, subject, body: sent.append({"to": to, "subject": subject, "body": body}))
    return sent


def _code(mail):
    return re.search(r"\b(\d{6})\b", mail["body"]).group(1)


def _register(client, email="jane@idealdynamics.co.ke"):
    return client.post("/api/users/register", json={"name": "Jane", "email": email,
                                                    "password": "Secret123"})


def test_register_sends_a_code_instead_of_signing_in(client, outbox):
    res = _register(client)
    body = res.get_json()
    assert res.status_code == 201 and body["verify_required"] and "token" not in body
    assert outbox[0]["to"] == "jane@idealdynamics.co.ke"
    assert "confirm" in outbox[0]["subject"].lower()


def test_the_code_confirms_and_signs_in(client, outbox):
    _register(client)
    res = client.post("/api/users/verify-email",
                      json={"email": "JANE@idealdynamics.co.ke", "code": _code(outbox[0])})
    body = res.get_json()
    assert res.status_code == 200 and body["token"] and body["user"]["email_verified"] is True
    me = client.get("/api/users/me", headers={"X-User-Token": body["token"]})
    assert me.status_code == 200
    # From now on the password works as normal.
    login = client.post("/api/users/login", json={"email": "jane@idealdynamics.co.ke",
                                                  "password": "Secret123"})
    assert login.status_code == 200 and login.get_json()["token"]


def test_wrong_code_and_too_many_tries(client, outbox):
    _register(client)
    real = _code(outbox[0])
    wrong = "000000" if real != "000000" else "111111"
    for _ in range(5):
        res = client.post("/api/users/verify-email", json={"email": "jane@idealdynamics.co.ke", "code": wrong})
        assert res.status_code == 400 and "wrong" in res.get_json()["error"]
    # The sixth try is refused even with the right code.
    res = client.post("/api/users/verify-email", json={"email": "jane@idealdynamics.co.ke", "code": real})
    assert res.status_code == 400 and "Too many" in res.get_json()["error"]


def test_unconfirmed_account_cant_sign_in_and_gets_a_new_code(client, outbox, app):
    _register(client)
    with app.app_context():   # pretend the first code was sent long ago
        db.session.execute(text("UPDATE password_reset SET created_at = '2000-01-01'"))
        db.session.commit()
    res = client.post("/api/users/login", json={"email": "jane@idealdynamics.co.ke", "password": "Secret123"})
    assert res.status_code == 403 and res.get_json()["verify_required"]
    assert "token" not in res.get_json() and len(outbox) == 2
    # The old code no longer works; the new one does.
    old, new = _code(outbox[0]), _code(outbox[1])
    if old != new:
        assert client.post("/api/users/verify-email", json={"email": "jane@idealdynamics.co.ke",
                                                             "code": old}).status_code == 400
    assert client.post("/api/users/verify-email", json={"email": "jane@idealdynamics.co.ke",
                                                         "code": new}).status_code == 200


def test_wrong_password_reveals_nothing(client, outbox):
    _register(client)
    res = client.post("/api/users/login", json={"email": "jane@idealdynamics.co.ke", "password": "Nope12345"})
    assert res.status_code == 401 and "verify_required" not in res.get_json()
    assert len(outbox) == 1   # no new code for a wrong password


def test_resend_is_rate_limited_and_generic(client, outbox):
    _register(client)
    r1 = client.post("/api/users/resend-verification", json={"email": "jane@idealdynamics.co.ke"})
    r2 = client.post("/api/users/resend-verification", json={"email": "nobody@idealdynamics.co.ke"})
    assert r1.get_json() == r2.get_json()        # same answer for unknown emails
    assert len(outbox) == 1                       # within a minute: no second email


def test_old_sessions_of_unconfirmed_accounts_end(app, client, outbox):
    """Accounts from before verification have NULL: their saved sign-in stops working."""
    with app.app_context():
        u = User(name="Old", email="old@idealdynamics.co.ke", email_verified=None)
        u.set_password("Secret123")
        db.session.add(u)
        db.session.commit()
        from app.auth import issue_user_token
        token = issue_user_token(u)
    assert client.get("/api/users/me", headers={"X-User-Token": token}).status_code == 401
    res = client.post("/api/users/login", json={"email": "old@idealdynamics.co.ke", "password": "Secret123"})
    assert res.status_code == 403 and outbox[-1]["to"] == "old@idealdynamics.co.ke"


def test_password_reset_also_confirms_the_email(client, outbox):
    _register(client)
    client.post("/api/users/forgot-password", json={"email": "jane@idealdynamics.co.ke"})
    reset_mail = outbox[-1]
    assert "reset" in reset_mail["subject"].lower()
    res = client.post("/api/users/reset-password", json={"email": "jane@idealdynamics.co.ke",
                                                         "code": _code(reset_mail), "password": "NewPass456"})
    assert res.status_code == 200 and res.get_json()["user"]["email_verified"] is True


def test_reset_and_confirmation_codes_are_separate(client, outbox):
    _register(client)
    verify_code = _code(outbox[0])
    res = client.post("/api/users/reset-password", json={"email": "jane@idealdynamics.co.ke",
                                                         "code": verify_code, "password": "NewPass456"})
    assert res.status_code == 400               # a confirmation code can't reset the password


def test_already_confirmed(client, outbox):
    _register(client)
    client.post("/api/users/verify-email", json={"email": "jane@idealdynamics.co.ke", "code": _code(outbox[0])})
    res = client.post("/api/users/verify-email", json={"email": "jane@idealdynamics.co.ke", "code": "123456"})
    assert res.status_code == 409


def test_erp_needs_a_confirmed_company_email(app):
    from types import SimpleNamespace
    from app.services import erp_tools
    app.config.update(ERP_DB_HOST="h", ERP_DB_NAME="d", ERP_DB_USER="u", ERP_DB_PASSWORD="p",
                      ERP_COMPANY="IDEALDYNAMICS_PROP", ERP_ALLOWED_EMAIL_DOMAINS="idealdynamics.co.ke")
    with app.app_context():
        assert not erp_tools.user_may_use(SimpleNamespace(email="jane@idealdynamics.co.ke", email_verified=False))
        assert not erp_tools.user_may_use(SimpleNamespace(email="jane@idealdynamics.co.ke", email_verified=None))
        assert erp_tools.user_may_use(SimpleNamespace(email="jane@idealdynamics.co.ke", email_verified=True))


def test_existing_database_gets_the_new_columns(app):
    with app.app_context():
        with db.engine.begin() as conn:
            conn.execute(text('ALTER TABLE "user" DROP COLUMN "email_verified"'))
            conn.execute(text('ALTER TABLE "password_reset" DROP COLUMN "purpose"'))
        _add_missing_columns()
        insp = inspect(db.engine)
        assert "email_verified" in {c["name"] for c in insp.get_columns("user")}
        assert "purpose" in {c["name"] for c in insp.get_columns("password_reset")}


def test_email_is_logged_when_smtp_is_off(app, monkeypatch, caplog):
    """No SMTP set up: the code still reaches the backend log so testing works."""
    with app.test_client() as c:
        c.post("/api/users/register", json={"name": "Jane", "email": "jane@idealdynamics.co.ke",
                                            "password": "Secret123"})
    assert "NOT sent" in caplog.text and "confirm this email" in caplog.text


# ------------------------------------------------------------------- admins
def _account(app, email, verified):
    with app.app_context():
        u = User(name="A", email=email, email_verified=verified)
        u.set_password("Secret123")
        db.session.add(u)
        db.session.commit()
        return u.id


@pytest.mark.parametrize("email,verified,admin", [
    ("boss@example.com", True, True),          # listed and confirmed
    ("BOSS@Example.com", True, True),          # any case
    ("boss@example.com", False, False),        # listed but not confirmed yet
    ("staff@example.com", True, False),        # confirmed but not listed
])
def test_only_listed_confirmed_accounts_are_admins(app, email, verified, admin):
    app.config["ADMIN_EMAILS"] = " boss@example.com , other@example.com"
    uid = _account(app, email.lower(), verified)
    with app.app_context():
        u = db.session.get(User, uid)
        assert u.is_admin is admin and u.to_dict()["is_admin"] is admin


def test_me_reports_admin_flag(app, client, outbox):
    app.config["ADMIN_EMAILS"] = "jane@idealdynamics.co.ke"
    _register(client)
    signed_in = client.post("/api/users/verify-email", json={"email": "jane@idealdynamics.co.ke",
                                                             "code": _code(outbox[0])}).get_json()
    assert signed_in["user"]["is_admin"] is True
    app.config["ADMIN_EMAILS"] = ""
    me = client.get("/api/users/me", headers={"X-User-Token": signed_in["token"]}).get_json()
    assert me["is_admin"] is False


def test_agent_data_still_needs_the_agent_login(client):
    assert client.get("/api/tickets").status_code == 401
    assert client.get("/api/analytics").status_code == 401


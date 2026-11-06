"""Email sending, with smtplib replaced by a fake server."""
import smtplib

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.services import mailer


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None, **kw):
        self.host, self.port, self.sent, self.logged_in = host, port, [], None
        self.fail_login = False
        FakeSMTP.instances.append(self)

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        if FakeSMTP.reject:
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
        self.logged_in = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def app(monkeypatch):
    class Mail(TestConfig):
        SMTP_HOST = "smtp.gmail.com"
        SMTP_PORT = 587
        SMTP_USERNAME = "helpdesk@example.com"
        SMTP_PASSWORD = "abcd efgh ijkl mnop"     # Google shows App Passwords with spaces
        SMTP_FROM = "AI in Ideal <helpdesk@example.com>"
        SMTP_USE_SSL = False
    FakeSMTP.instances, FakeSMTP.reject = [], False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    app = create_app(Mail)
    yield app
    with app.app_context():
        db.drop_all()


def test_send_now_uses_the_settings(app):
    with app.app_context():
        mailer.send_now("jane@example.com", "Hi", "Body text")
    server = FakeSMTP.instances[-1]
    assert (server.host, server.port, server.tls) == ("smtp.gmail.com", 587, True)
    assert server.logged_in == ("helpdesk@example.com", "abcdefghijklmnop")  # spaces removed
    msg = server.sent[0]
    assert (msg["To"], msg["From"], msg["Subject"]) == \
        ("jane@example.com", "AI in Ideal <helpdesk@example.com>", "Hi")


def test_wrong_password_gets_a_plain_explanation(app):
    FakeSMTP.reject = True
    with app.app_context(), pytest.raises(mailer.SMTPSetupError, match="App Password"):
        mailer.send_now("jane@example.com", "Hi", "Body")


def test_send_now_without_smtp_host_explains():
    app = create_app(TestConfig)
    with app.app_context(), pytest.raises(mailer.SMTPSetupError, match="SMTP_HOST is empty"):
        mailer.send_now("jane@example.com", "Hi", "Body")
    with app.app_context():
        db.drop_all()


def test_reset_code_is_emailed_for_real_when_smtp_is_set(app, monkeypatch):
    # Run the background delivery inline so the test can see it.
    monkeypatch.setattr(mailer.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    with app.test_client() as c:
        c.post("/api/users/register", json={"name": "Jane", "email": "jane@example.com",
                                            "password": "Secret123"})
        c.post("/api/users/forgot-password", json={"email": "jane@example.com"})
    msg = FakeSMTP.instances[-1].sent[0]
    assert msg["To"] == "jane@example.com" and "reset code" in msg["Subject"].lower()
    assert "Your password reset code is:" in msg.get_content()


def test_every_registered_email_gets_its_own_code(app, monkeypatch):
    """Each account's code goes to the address that account registered with, any provider."""
    monkeypatch.setattr(mailer.threading, "Thread",
                        lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    people = {"Amina": "amina@gmail.com", "Brian": "Brian.Otieno@Yahoo.com",
              "Chen": "chen@outlook.com", "Dee": "dee@idealdynamics.co.ke"}
    with app.test_client() as c:
        for name, email in people.items():
            c.post("/api/users/register", json={"name": name, "email": email,
                                                "password": "Secret123"})
        for name, email in people.items():
            # Typed differently from how they registered: still matches the account.
            c.post("/api/users/forgot-password", json={"email": f"  {email.upper()} "})
        # Someone who never registered gets nothing.
        c.post("/api/users/forgot-password", json={"email": "stranger@gmail.com"})

        sent = [m for s in FakeSMTP.instances for m in s.sent]
        assert [m["To"] for m in sent] == [e.lower() for e in people.values()]
        codes = {}
        for (name, _), msg in zip(people.items(), sent):
            body = msg.get_content()
            assert f"Hello {name}," in body
            codes[msg["To"]] = body.split("reset code is: ")[1][:6]
        assert len(set(codes.values())) == len(codes)  # each person has their own code

        # Each code resets only its own account.
        amina, brian = codes["amina@gmail.com"], codes["brian.otieno@yahoo.com"]
        wrong = c.post("/api/users/reset-password", json={
            "email": "amina@gmail.com", "code": brian, "password": "NewPass456"})
        assert wrong.status_code == 400
        ok = c.post("/api/users/reset-password", json={
            "email": "amina@gmail.com", "code": amina, "password": "NewPass456"})
        assert ok.status_code == 200 and ok.get_json()["user"]["email"] == "amina@gmail.com"


def test_cli_test_email_command(app):
    result = app.test_cli_runner().invoke(args=["send-test-email", "jane@example.com"])
    assert "Sent." in result.output and FakeSMTP.instances[-1].sent
    FakeSMTP.reject = True
    bad = app.test_cli_runner().invoke(args=["send-test-email", "jane@example.com"])
    assert bad.exit_code != 0 and "App Password" in bad.output

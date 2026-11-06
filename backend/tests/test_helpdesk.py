"""WhatsApp help desk contact exposed to the page."""
import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.routes.analytics import whatsapp_number


def _client(**overrides):
    class Cfg(TestConfig):
        pass
    for k, v in overrides.items():
        setattr(Cfg, k, v)
    app = create_app(Cfg)
    return app, app.test_client()


@pytest.mark.parametrize("raw,expected", [
    ("+254 712 345 678", "254712345678"),
    ("254-712-345-678", "254712345678"),
    ("00254712345678", "254712345678"),
    ("(+1) 415 555 0100", "14155550100"),
    ("0712345678", None),        # local number: no country code
    ("12345", None),             # too short
    ("1" * 16, None),            # too long
    ("", None),
    ("call me", None),
])
def test_whatsapp_number(raw, expected):
    assert whatsapp_number(raw) == expected


def test_config_without_a_number():
    app, c = _client()
    body = c.get("/api/config").get_json()
    assert body["helpdesk"] == {"name": "ICT Help Desk", "whatsapp": None, "hours": ""}
    with app.app_context():
        db.drop_all()


def test_config_with_number_name_and_hours():
    app, c = _client(HELPDESK_WHATSAPP_NUMBER="+254 712 345 678",
                     HELPDESK_NAME="Ideal ICT Desk", HELPDESK_HOURS="Mon-Fri, 8am-5pm")
    body = c.get("/api/config").get_json()["helpdesk"]
    assert body == {"name": "Ideal ICT Desk", "whatsapp": "254712345678",
                    "hours": "Mon-Fri, 8am-5pm"}
    with app.app_context():
        db.drop_all()


def test_invalid_number_is_hidden_and_logged(caplog):
    app, c = _client(HELPDESK_WHATSAPP_NUMBER="0712345678")
    with caplog.at_level("WARNING"):
        assert c.get("/api/config").get_json()["helpdesk"]["whatsapp"] is None
    assert "country code" in caplog.text
    with app.app_context():
        db.drop_all()


def test_config_is_public():
    app, c = _client(HELPDESK_WHATSAPP_NUMBER="+254712345678")
    assert c.get("/api/config").status_code == 200   # no sign-in needed
    with app.app_context():
        db.drop_all()

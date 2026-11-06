"""Regenerate the latest reply, and edit one of your messages (ChatGPT-style)."""
import io
import json
import os
from types import SimpleNamespace

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.services import ai_service

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


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


class FakeGemini:
    """Replies "answer 1", "answer 2", ... so each new reply is recognisable."""
    def __init__(self):
        self.calls = []
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(text=json.dumps({
            "reply": f"answer {len(self.calls)}", "intent": "question", "sentiment": "neutral",
            "escalate": False, "ticket_subject": "", "serial_number": "", "customer_site": ""}))


@pytest.fixture
def fake(app, monkeypatch):
    app.config.update(AI_ENABLED=True, AI_PROVIDER="gemini")
    f = FakeGemini()
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: f)
    return f


def _chat(client, *texts, mode="general"):
    conv = client.post("/api/conversations", json={"mode": mode}).get_json()
    h = {"X-Conversation-Token": conv["token"]}
    for t in texts:
        client.post(f"/api/conversations/{conv['id']}/messages",
                    json={"content": t, "mode": mode}, headers=h)
    msgs = client.get(f"/api/conversations/{conv['id']}", headers=h).get_json()["messages"]
    return conv, h, msgs


def _contents(client, conv, h):
    msgs = client.get(f"/api/conversations/{conv['id']}", headers=h).get_json()["messages"]
    return [(m["role"], m["content"]) for m in msgs]


# --------------------------------------------------------------- regenerate
def test_regenerate_replaces_the_latest_reply(client, fake):
    conv, h, msgs = _chat(client, "first", "second")
    last = msgs[-1]
    res = client.post(f"/api/conversations/{conv['id']}/messages/{last['id']}/regenerate",
                      json={}, headers=h)
    body = res.get_json()
    assert res.status_code == 201
    assert body["replaced_id"] == last["id"] and body["reply"]["id"] > last["id"]  # no id reuse
    assert _contents(client, conv, h) == [
        ("user", "first"), ("assistant", "answer 1"),
        ("user", "second"), ("assistant", "answer 3")]
    # The AI saw the same question again, with the earlier turns as history.
    regen_call = fake.calls[-1]["contents"]
    assert [c.role for c in regen_call] == ["user", "model", "user"]
    assert "second" in regen_call[-1].parts[-1].text


def test_only_the_latest_reply_can_be_regenerated(client, fake):
    conv, h, msgs = _chat(client, "first", "second")
    older_reply, user_msg = msgs[1], msgs[2]
    url = f"/api/conversations/{conv['id']}/messages"
    assert client.post(f"{url}/{older_reply['id']}/regenerate", headers=h).status_code == 409
    assert client.post(f"{url}/{user_msg['id']}/regenerate", headers=h).status_code == 409
    assert client.post(f"{url}/99999/regenerate", headers=h).status_code == 404


def test_regenerate_can_switch_mode(client, fake):
    conv, h, msgs = _chat(client, "How do I log a service request?", mode="general")
    res = client.post(f"/api/conversations/{conv['id']}/messages/{msgs[-1]['id']}/regenerate",
                      json={"mode": "service"}, headers=h).get_json()
    assert res["conversation"]["mode"] == "service"
    assert fake.calls[-1]["config"].system_instruction == ai_service.SERVICE_PROMPT


# -------------------------------------------------------------------- edit
def test_edit_replaces_everything_after_the_message(client, fake):
    conv, h, msgs = _chat(client, "first", "second", "third")
    second = msgs[2]
    res = client.patch(f"/api/conversations/{conv['id']}/messages/{second['id']}",
                       json={"content": "second, edited"}, headers=h)
    body = res.get_json()
    assert res.status_code == 200
    assert body["user_message"]["id"] == second["id"]
    assert body["user_message"]["content"] == "second, edited"
    assert set(body["removed_ids"]) == {m["id"] for m in msgs[3:]}
    assert _contents(client, conv, h) == [
        ("user", "first"), ("assistant", "answer 1"),
        ("user", "second, edited"), ("assistant", "answer 4")]


def test_editing_the_first_message_renames_the_chat(client, fake):
    conv, h, msgs = _chat(client, "old question")
    body = client.patch(f"/api/conversations/{conv['id']}/messages/{msgs[0]['id']}",
                        json={"content": "new question"}, headers=h).get_json()
    assert body["conversation"]["title"] == "new question"


def test_edit_validation(client, fake):
    conv, h, msgs = _chat(client, "hello")
    url = f"/api/conversations/{conv['id']}/messages"
    assert client.patch(f"{url}/{msgs[0]['id']}", json={"content": "  "}, headers=h).status_code == 400
    assert client.patch(f"{url}/{msgs[1]['id']}", json={"content": "x"}, headers=h).status_code == 400
    assert client.patch(f"{url}/{msgs[0]['id']}", json={"content": "x" * 5000},
                        headers=h).status_code == 400
    assert client.patch(f"{url}/99999", json={"content": "x"}, headers=h).status_code == 404


def test_edit_keeps_its_own_files_and_deletes_later_ones(app, client, fake):
    conv = client.post("/api/conversations", json={"mode": "general"}).get_json()
    h = {"X-Conversation-Token": conv["token"]}
    url = f"/api/conversations/{conv['id']}/messages"
    first = client.post(url, data={"content": "what is this?", "mode": "general",
                                   "files": [(io.BytesIO(PNG), "a.png")]},
                        headers=h, content_type="multipart/form-data").get_json()["user_message"]
    client.post(url, data={"content": "and this?", "mode": "general",
                           "files": [(io.BytesIO(PNG), "b.png")]},
                headers=h, content_type="multipart/form-data")
    folder = app.config["UPLOAD_FOLDER"]
    assert len(os.listdir(folder)) == 2

    body = client.patch(f"{url}/{first['id']}", json={"content": "describe it"},
                        headers=h).get_json()
    assert [a["filename"] for a in body["user_message"]["attachments"]] == ["a.png"]
    assert len(os.listdir(folder)) == 1                       # b.png's file is gone
    sent = fake.calls[-1]["contents"][-1].parts
    assert sent[0].inline_data.data == PNG                    # a.png sent to the AI again
    # A file-only message may be edited to have no text.
    assert client.patch(f"{url}/{first['id']}", json={"content": ""}, headers=h).status_code == 200


def test_edit_can_open_a_ticket(client, monkeypatch, app):
    conv, h, msgs = _chat(client, "hello", mode="service")   # offline: no ticket
    assert client.get(f"/api/conversations/{conv['id']}", headers=h).get_json()["status"] == "open"
    body = client.patch(f"/api/conversations/{conv['id']}/messages/{msgs[0]['id']}",
                        json={"content": "I need a person please"}, headers=h).get_json()
    assert body["ticket"] is not None and body["conversation"]["status"] == "escalated"


# ------------------------------------------------------------------ access
def test_other_chats_cannot_edit_or_regenerate(client, fake):
    conv, _, msgs = _chat(client, "mine")
    _, h_other, _ = _chat(client, "theirs")
    url = f"/api/conversations/{conv['id']}/messages"
    assert client.patch(f"{url}/{msgs[0]['id']}", json={"content": "x"},
                        headers=h_other).status_code == 401
    assert client.post(f"{url}/{msgs[1]['id']}/regenerate", headers=h_other).status_code == 401
    assert client.patch(f"{url}/{msgs[0]['id']}", json={"content": "x"}).status_code == 401


def test_closed_chat_cannot_be_edited(client, fake):
    conv, h, msgs = _chat(client, "hello")
    client.post(f"/api/conversations/{conv['id']}/close", headers=h)
    url = f"/api/conversations/{conv['id']}/messages"
    assert client.patch(f"{url}/{msgs[0]['id']}", json={"content": "x"}, headers=h).status_code == 409
    assert client.post(f"{url}/{msgs[1]['id']}/regenerate", headers=h).status_code == 409


def test_attachments_have_a_unique_cache_key(app, client, fake):
    """Ids can repeat in databases made before AUTOINCREMENT; the key never does."""
    conv = client.post("/api/conversations", json={"mode": "general"}).get_json()
    h = {"X-Conversation-Token": conv["token"]}
    url = f"/api/conversations/{conv['id']}/messages"
    keys = set()
    for name in ("a.png", "b.png"):
        att = client.post(url, data={"content": "x", "mode": "general",
                                     "files": [(io.BytesIO(PNG), name)]},
                          headers=h, content_type="multipart/form-data"
                          ).get_json()["user_message"]["attachments"][0]
        assert len(att["key"]) == 16
        keys.add(att["key"])
    assert len(keys) == 2

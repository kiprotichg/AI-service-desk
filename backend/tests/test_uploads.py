"""File/photo uploads, chat titles, deleting chats, and the DB upgrade."""
import io
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.seed import seed_agent
from app.services import ai_service

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64       # enough for type sniffing
PDF = b"%PDF-1.4\n" + b"0" * 64
GOOD = {"reply": "That's a cat.", "intent": "question", "sentiment": "neutral",
        "escalate": False, "ticket_subject": ""}


@pytest.fixture
def app(tmp_path):
    app = create_app(TestConfig)
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    with app.app_context():
        seed_agent("agent", "s3cret-pass")
    yield app
    with app.app_context():
        db.drop_all()


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


def _conv(client):
    conv = client.post("/api/conversations", json={}).get_json()
    return conv, {"X-Conversation-Token": conv["token"]}


def _upload(client, conv, h, files, content=""):
    data = {"content": content, "files": [(io.BytesIO(b), name) for name, b in files]}
    return client.post(f"/api/conversations/{conv['id']}/messages", data=data, headers=h,
                       content_type="multipart/form-data")


def _stored_files(app):
    folder = app.config["UPLOAD_FOLDER"]
    return os.listdir(folder) if os.path.isdir(folder) else []


# ------------------------------------------------------------------ uploads
def test_photo_upload_is_saved_and_downloadable(app, client):
    conv, h = _conv(client)
    res = _upload(client, conv, h, [("cat.png", PNG)], "what is this?")
    assert res.status_code == 201
    att = res.get_json()["user_message"]["attachments"][0]
    assert (att["filename"], att["mime_type"], att["kind"]) == ("cat.png", "image/png", "image")
    assert len(_stored_files(app)) == 1

    dl = client.get(f"/api/attachments/{att['id']}", headers=h)
    assert dl.status_code == 200 and dl.data == PNG
    assert dl.headers["X-Content-Type-Options"] == "nosniff"


def test_file_only_message_is_allowed(client):
    conv, h = _conv(client)
    body = _upload(client, conv, h, [("notes.txt", b"hello")]).get_json()
    assert body["user_message"]["content"] == ""
    assert body["conversation"]["title"] == "notes.txt"  # title falls back to the file name


@pytest.mark.parametrize("name,data,error", [
    ("virus.exe", b"MZ\x90\x00", "can be uploaded"),
    ("fake.png", b"just text pretending", "can be uploaded"),     # name lies about type
    ("big.png", PNG + b"\x00" * (5 * 1024 * 1024), "larger than"),
    ("big.txt", b"a" * (201 * 1024), "larger than"),
    ("latin.txt", "caf\xe9".encode("latin-1"), "UTF-8"),
    ("empty.txt", b"", "empty"),
])
def test_bad_uploads_are_rejected(app, client, name, data, error):
    conv, h = _conv(client)
    res = _upload(client, conv, h, [(name, data)])
    assert res.status_code == 400 and error in res.get_json()["error"]
    assert _stored_files(app) == []  # nothing written when validation fails


def test_too_many_files(client):
    conv, h = _conv(client)
    res = _upload(client, conv, h, [(f"{i}.txt", b"x") for i in range(4)])
    assert res.status_code == 400 and "up to 3" in res.get_json()["error"]


def test_request_too_large_returns_json(client):
    conv, h = _conv(client)
    res = _upload(client, conv, h, [("huge.pdf", PDF + b"0" * (17 * 1024 * 1024))])
    assert res.status_code == 413 and "too large" in res.get_json()["error"]


def test_empty_message_without_files_is_rejected(client):
    conv, h = _conv(client)
    res = client.post(f"/api/conversations/{conv['id']}/messages", json={"content": " "},
                      headers=h)
    assert res.status_code == 400


def test_attachments_are_private_to_their_chat(client):
    conv_a, h_a = _conv(client)
    _, h_b = _conv(client)
    att = _upload(client, conv_a, h_a, [("a.png", PNG)]).get_json()["user_message"]["attachments"][0]
    assert client.get(f"/api/attachments/{att['id']}", headers=h_b).status_code == 401
    assert client.get(f"/api/attachments/{att['id']}").status_code == 401
    login = client.post("/api/auth/login", json={"username": "agent", "password": "s3cret-pass"})
    agent = {"Authorization": f"Bearer {login.get_json()['token']}"}
    assert client.get(f"/api/attachments/{att['id']}", headers=agent).status_code == 200


def test_offline_mode_explains_it_cannot_read_files(client):
    conv, h = _conv(client)
    body = _upload(client, conv, h, [("cat.png", PNG)], "what is this?").get_json()
    assert "photos and files" in body["reply"]["content"]
    assert body["ticket"] is None


# ---------------------------------------------------- files reach the AI
class FakeGemini:
    def __init__(self):
        self.calls = []
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(text=json.dumps(GOOD))


def test_gemini_receives_photo_and_text_file(app, client, monkeypatch):
    app.config.update(AI_ENABLED=True, AI_PROVIDER="gemini")
    fake = FakeGemini()
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: fake)
    conv, h = _conv(client)
    _upload(client, conv, h, [("cat.png", PNG), ("notes.txt", b"buy milk")], "what's this?")
    parts = fake.calls[0]["contents"][-1].parts
    assert parts[0].inline_data.mime_type == "image/png" and parts[0].inline_data.data == PNG
    assert "buy milk" in parts[1].text and 'name="notes.txt"' in parts[1].text
    assert "what's this?" in parts[2].text

    # A follow-up still sends the earlier photo, so "what colour was it?" works.
    client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "colour?"},
                headers=h)
    earlier = fake.calls[1]["contents"][0].parts
    assert earlier[0].inline_data.data == PNG


def test_claude_receives_image_and_pdf_blocks(app, client, monkeypatch):
    app.config.update(AI_ENABLED=True, AI_PROVIDER="claude")
    calls = []

    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps(GOOD))])
    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(ai_service, "_get_anthropic_client", lambda: fake)
    conv, h = _conv(client)
    _upload(client, conv, h, [("cat.png", PNG), ("doc.pdf", PDF)], "summarise")
    blocks = calls[0]["messages"][-1]["content"]
    assert [b["type"] for b in blocks] == ["image", "document", "text"]
    assert blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[1]["source"]["media_type"] == "application/pdf"


def test_old_attachments_beyond_budget_become_a_note(app, client, monkeypatch):
    from app.services import attachments
    monkeypatch.setattr(attachments, "AI_ATTACHMENT_BUDGET", len(PNG))  # room for one file
    app.config.update(AI_ENABLED=True, AI_PROVIDER="gemini")
    fake = FakeGemini()
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: fake)
    conv, h = _conv(client)
    _upload(client, conv, h, [("old.png", PNG)], "first")
    _upload(client, conv, h, [("new.png", PNG)], "second")
    contents = fake.calls[1]["contents"]
    assert "old.png was attached earlier" in contents[0].parts[0].text   # dropped, noted
    assert contents[-1].parts[0].inline_data.data == PNG                 # newest kept


# --------------------------------------------------- titles + deleting chats
def test_title_comes_from_first_message(client):
    conv, h = _conv(client)
    assert conv["title"] == "New chat"
    url = f"/api/conversations/{conv['id']}/messages"
    client.post(url, json={"content": "How do I assign a technician?"}, headers=h)
    client.post(url, json={"content": "thanks"}, headers=h)
    got = client.get(f"/api/conversations/{conv['id']}", headers=h).get_json()
    assert got["title"] == "How do I assign a technician?"


def test_delete_chat_removes_messages_and_files_but_keeps_ticket(app, client):
    conv, h = _conv(client)
    _upload(client, conv, h, [("cat.png", PNG)], "I want a human")
    assert len(_stored_files(app)) == 1

    assert client.delete(f"/api/conversations/{conv['id']}", headers=h).status_code == 204
    assert _stored_files(app) == []
    assert client.get(f"/api/conversations/{conv['id']}", headers=h).status_code == 404

    login = client.post("/api/auth/login", json={"username": "agent", "password": "s3cret-pass"})
    agent = {"Authorization": f"Bearer {login.get_json()['token']}"}
    tickets = client.get("/api/tickets", headers=agent).get_json()
    assert len(tickets) == 1 and tickets[0]["conversation_id"] is None


def test_cannot_delete_someone_elses_chat(client):
    conv_a, _ = _conv(client)
    _, h_b = _conv(client)
    assert client.delete(f"/api/conversations/{conv_a['id']}", headers=h_b).status_code == 401
    assert client.delete(f"/api/conversations/{conv_a['id']}").status_code == 401


# ------------------------------------------------------------- DB upgrade
def test_old_database_gets_new_columns(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE conversation (id INTEGER PRIMARY KEY, customer_name VARCHAR(120),"
                " status VARCHAR(20), created_at DATETIME)")
    con.execute("INSERT INTO conversation (customer_name, status) VALUES ('old', 'open')")
    con.commit()
    con.close()

    class OldDB(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{path}"
    create_app(OldDB)

    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(conversation)")}
    assert {"title", "updated_at"} <= cols

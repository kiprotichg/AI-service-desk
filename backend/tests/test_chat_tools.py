"""Chat title menu (pin, rename, projects, share), temporary chats and web search."""
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.models import Conversation, utcnow
from app.services import ai_service


@pytest.fixture
def app(tmp_path):
    app = create_app(TestConfig)
    app.config["UPLOAD_FOLDER"] = str(tmp_path / "uploads")
    yield app
    with app.app_context():
        db.drop_all()


@pytest.fixture(autouse=True)
def search_allowed(monkeypatch):
    monkeypatch.setattr(ai_service, "_search_off_until", 0.0)   # no leftover pause between tests


@pytest.fixture
def client(app):
    with app.test_client() as c:
        yield c


def _user(client, email="jane@example.com"):
    res = client.post("/api/users/register", json={"name": "Jane", "email": email,
                                                   "password": "Secret123"}).get_json()
    return {"X-User-Token": res["token"]}


def _chat(client, user_headers=None, text="How do I log a service request?", **extra):
    conv = client.post("/api/conversations", json={"mode": "service", **extra},
                       headers=user_headers or {}).get_json()
    h = {"X-Conversation-Token": conv["token"]}
    if text:
        client.post(f"/api/conversations/{conv['id']}/messages",
                    json={"content": text, "mode": "service"}, headers=h)
    return conv, h


# ------------------------------------------------------------ rename and pin
def test_rename_and_pin(client):
    conv, h = _chat(client)
    res = client.patch(f"/api/conversations/{conv['id']}",
                       json={"title": "  Service   requests  ", "pinned": True}, headers=h)
    body = res.get_json()
    assert res.status_code == 200 and body["title"] == "Service requests" and body["pinned"]
    assert client.patch(f"/api/conversations/{conv['id']}", json={"title": "  "},
                        headers=h).status_code == 400
    unpinned = client.patch(f"/api/conversations/{conv['id']}", json={"pinned": False},
                            headers=h).get_json()
    assert unpinned["pinned"] is False


def test_changing_a_chat_needs_its_token(client):
    conv, _ = _chat(client)
    assert client.patch(f"/api/conversations/{conv['id']}", json={"pinned": True}).status_code == 401
    assert client.post(f"/api/conversations/{conv['id']}/share").status_code == 401


# ------------------------------------------------------------------ projects
def test_projects_crud_and_moving_chats(client):
    u = _user(client)
    p = client.post("/api/projects", json={"name": " Kisumu branch "}, headers=u).get_json()
    assert p["name"] == "Kisumu branch"
    assert client.post("/api/projects", json={"name": "kisumu BRANCH"}, headers=u).status_code == 409
    assert client.post("/api/projects", json={"name": ""}, headers=u).status_code == 400

    conv, h = _chat(client, u)
    moved = client.patch(f"/api/conversations/{conv['id']}", json={"project_id": p["id"]},
                         headers=h).get_json()
    assert moved["project_id"] == p["id"]
    listed = client.get("/api/users/me/conversations", headers=u).get_json()
    assert listed[0]["project_id"] == p["id"]

    renamed = client.patch(f"/api/projects/{p['id']}", json={"name": "Nairobi"}, headers=u)
    assert renamed.get_json()["name"] == "Nairobi"
    assert [x["name"] for x in client.get("/api/projects", headers=u).get_json()] == ["Nairobi"]

    # Deleting a project keeps its chats; they go back to Recent.
    assert client.delete(f"/api/projects/{p['id']}", headers=u).status_code == 204
    again = client.get(f"/api/conversations/{conv['id']}", headers=h).get_json()
    assert again["project_id"] is None


def test_projects_are_private(client):
    jane, bob = _user(client), _user(client, "bob@example.com")
    p = client.post("/api/projects", json={"name": "Jane's"}, headers=jane).get_json()
    assert client.get("/api/projects", headers=bob).get_json() == []
    assert client.patch(f"/api/projects/{p['id']}", json={"name": "x"}, headers=bob).status_code == 404
    assert client.delete(f"/api/projects/{p['id']}", headers=bob).status_code == 404
    # Bob can't put his chat into Jane's project either.
    conv, h = _chat(client, bob)
    assert client.patch(f"/api/conversations/{conv['id']}", json={"project_id": p["id"]},
                        headers=h).status_code == 404
    assert client.patch(f"/api/conversations/{conv['id']}", json={"project_id": True},
                        headers=h).status_code == 404
    assert client.get("/api/projects").status_code == 401


# --------------------------------------------------------------------- share
def test_share_link_shows_a_snapshot(client):
    conv, h = _chat(client)
    assert client.get(f"/api/conversations/{conv['id']}", headers=h).get_json()["shared"] is False
    shared = client.post(f"/api/conversations/{conv['id']}/share", headers=h).get_json()
    sid = shared["share_id"]
    assert shared["messages"] == 2 and len(sid) >= 20

    public = client.get(f"/api/conversations/shared/{sid}").get_json()   # no token needed
    assert [m["role"] for m in public["messages"]] == ["user", "assistant"]
    assert "token" not in json.dumps(public)

    # Later messages aren't shown until the link is updated.
    client.post(f"/api/conversations/{conv['id']}/messages",
                json={"content": "And warranties?", "mode": "service"}, headers=h)
    assert len(client.get(f"/api/conversations/shared/{sid}").get_json()["messages"]) == 2
    again = client.post(f"/api/conversations/{conv['id']}/share", headers=h).get_json()
    assert again["share_id"] == sid and again["messages"] == 4

    assert client.delete(f"/api/conversations/{conv['id']}/share", headers=h).status_code == 204
    assert client.get(f"/api/conversations/shared/{sid}").status_code == 404


def test_empty_chat_cant_be_shared(client):
    conv, h = _chat(client, text=None)
    assert client.post(f"/api/conversations/{conv['id']}/share", headers=h).status_code == 400


# ----------------------------------------------------------------- temporary
def test_temporary_chats_stay_out_of_recent(client):
    u = _user(client)
    normal, _ = _chat(client, u)
    temp, th = _chat(client, u, temporary=True)
    assert temp["temporary"] is True
    ids = [c["id"] for c in client.get("/api/users/me/conversations", headers=u).get_json()]
    assert ids == [normal["id"]]
    for change in ({"pinned": True}, {"project_id": 1}):
        assert client.patch(f"/api/conversations/{temp['id']}", json=change,
                            headers=th).status_code == 400
    assert client.post(f"/api/conversations/{temp['id']}/share", headers=th).status_code == 400


def test_old_temporary_chats_are_cleaned_up(app, client):
    u = _user(client)
    temp, _ = _chat(client, u, temporary=True)
    fresh, _ = _chat(client, u, temporary=True)
    with app.app_context():
        db.session.get(Conversation, temp["id"]).updated_at = utcnow() - timedelta(days=2)
        db.session.commit()
    client.get("/api/users/me/conversations", headers=u)
    with app.app_context():
        assert db.session.get(Conversation, temp["id"]) is None
        assert db.session.get(Conversation, fresh["id"]) is not None


# ---------------------------------------------------------------- web search
class FakeGemini:
    def __init__(self, fail_with_tools=False):
        self.calls, self.fail_with_tools = [], fail_with_tools
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, **kw):
        self.calls.append(kw)
        if kw["config"].tools and self.fail_with_tools:
            raise RuntimeError("tools not supported with JSON output")
        chunks = [SimpleNamespace(web=SimpleNamespace(title="kenyanews.example", uri="https://kenyanews.example/a")),
                  SimpleNamespace(web=SimpleNamespace(title="dup", uri="https://kenyanews.example/a")),
                  SimpleNamespace(web=None)]
        return SimpleNamespace(
            text=json.dumps({"reply": "Today it's sunny in Nairobi.", "intent": "question",
                             "sentiment": "neutral", "escalate": False, "ticket_subject": "",
                             "serial_number": "", "customer_site": "", "suggestions": []}),
            candidates=[SimpleNamespace(grounding_metadata=SimpleNamespace(grounding_chunks=chunks))])


def _gemini(app, monkeypatch, **kw):
    app.config.update(AI_ENABLED=True, AI_PROVIDER="gemini")
    fake = FakeGemini(**kw)
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: fake)
    return fake


def _ask(client, web_search):
    conv = client.post("/api/conversations", json={"mode": "general"}).get_json()
    h = {"X-Conversation-Token": conv["token"]}
    res = client.post(f"/api/conversations/{conv['id']}/messages", headers=h,
                      json={"content": "Weather in Nairobi today?", "mode": "general",
                            "web_search": web_search}).get_json()
    return conv, h, res


def test_web_search_uses_google_search_and_saves_sources(app, client, monkeypatch):
    fake = _gemini(app, monkeypatch)
    conv, h, res = _ask(client, True)
    cfg = fake.calls[0]["config"]
    assert cfg.tools and cfg.tools[0].google_search is not None
    assert "Web search is on" in str(fake.calls[0]["contents"][-1])
    assert res["reply"]["sources"] == [{"title": "kenyanews.example",
                                        "url": "https://kenyanews.example/a"}]
    assert res["reply"]["notice"] == ""
    msgs = client.get(f"/api/conversations/{conv['id']}", headers=h).get_json()["messages"]
    assert msgs[1]["sources"] == res["reply"]["sources"]


def test_without_web_search_no_tools_are_sent(app, client, monkeypatch):
    fake = _gemini(app, monkeypatch)
    _, _, res = _ask(client, False)
    assert not fake.calls[0]["config"].tools and res["reply"]["sources"] == []


def test_web_search_failure_retries_without_search(app, client, monkeypatch):
    fake = _gemini(app, monkeypatch, fail_with_tools=True)
    _, _, res = _ask(client, True)
    assert len(fake.calls) == 2 and not fake.calls[1]["config"].tools
    assert res["source"] == "gemini" and res["reply"]["content"].startswith("Today")
    assert "Web search wasn't available" in res["reply"]["notice"]   # the user is told


def test_quota_error_pauses_web_search(app, client, monkeypatch):
    fake = _gemini(app, monkeypatch)
    real = fake._gen

    def quota_then_ok(**kw):
        if kw["config"].tools:
            fake.calls.append(kw)
            raise RuntimeError("429 RESOURCE_EXHAUSTED. You exceeded your current quota")
        return real(**kw)
    fake.models.generate_content = quota_then_ok
    _ask(client, True)
    assert len(fake.calls) == 2                      # tried search, then without
    _, _, res = _ask(client, True)
    assert len(fake.calls) == 3                      # second message skipped search at once
    assert not fake.calls[-1]["config"].tools and res["reply"]["notice"]


def test_claude_web_search_reads_the_text_after_the_search(app, monkeypatch):
    app.config.update(AI_ENABLED=True, AI_PROVIDER="claude")
    answer = json.dumps({"reply": "It's 24°C.", "intent": "question", "sentiment": "neutral",
                         "escalate": False, "ticket_subject": "", "serial_number": "",
                         "customer_site": "", "suggestions": ["Tomorrow?"]})
    calls = []

    def create(**kw):
        calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", content=[
            SimpleNamespace(type="text", text="Let me look that up."),
            SimpleNamespace(type="server_tool_use"),
            SimpleNamespace(type="web_search_tool_result", content=[
                SimpleNamespace(url="https://weather.example/nbo", title="Nairobi weather")]),
            SimpleNamespace(type="text", text=answer[:20]),
            SimpleNamespace(type="text", text=answer[20:]),
        ])
    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(ai_service, "_get_anthropic_client", lambda: fake)
    with app.app_context():
        reply = ai_service.generate_reply([], "Weather?", mode="general", web_search=True)
    assert calls[0]["tools"][0]["type"].startswith("web_search")
    assert reply.reply == "It's 24°C." and reply.sources[0]["url"] == "https://weather.example/nbo"

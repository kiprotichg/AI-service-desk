"""Streamed answers (?stream=1), the <meta> format, fallback model and thinking setting."""
import json
from types import SimpleNamespace

import pytest

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.services import ai_service

META = ('<meta>{"intent": "job_card", "sentiment": "negative", "escalate": true, '
        '"ticket_subject": "Job card won\'t save", "serial_number": "SN-4471", '
        '"customer_site": "Mombasa branch", "suggestions": ["How do I reopen a job card?", '
        '"Can I attach photos?", "Who approves job cards?"]}</meta>')


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


@pytest.fixture(autouse=True)
def search_allowed(monkeypatch):
    monkeypatch.setattr(ai_service, "_search_off_until", 0.0)


class FakeGemini:
    """generate_content_stream yields the given pieces; models in `busy` raise 503."""
    def __init__(self, pieces, busy=(), fail_after=None):
        self.pieces, self.busy, self.fail_after, self.calls = pieces, set(busy), fail_after, []
        self.models = SimpleNamespace(generate_content_stream=self._stream)

    def _stream(self, **kw):
        self.calls.append(kw)
        if kw["model"] in self.busy:
            raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")

        def gen():
            for i, p in enumerate(self.pieces):
                if self.fail_after is not None and i == self.fail_after:
                    raise RuntimeError("connection reset")
                yield SimpleNamespace(text=p, candidates=[])
        return gen()


def _use(app, monkeypatch, fake):
    app.config.update(AI_ENABLED=True, AI_PROVIDER="gemini", GEMINI_MODEL="main-model",
                      GEMINI_FALLBACK_MODEL="backup-model", GEMINI_THINKING="minimal")
    monkeypatch.setattr(ai_service, "_get_gemini_client", lambda: fake)


def _stream(client, text, mode="service", **extra):
    conv = client.post("/api/conversations", json={"mode": mode}).get_json()
    h = {"X-Conversation-Token": conv["token"]}
    res = client.post(f"/api/conversations/{conv['id']}/messages?stream=1", headers=h,
                      json={"content": text, "mode": mode, **extra})
    assert res.mimetype == "application/x-ndjson"
    events = [json.loads(line) for line in res.get_data(as_text=True).splitlines() if line]
    return conv, h, events


def test_answer_streams_in_pieces_and_is_saved(app, client, monkeypatch):
    fake = FakeGemini(["Sorry about that. ", "I've passed it ", "to the team.\n<me", "ta>", META[6:]])
    _use(app, monkeypatch, fake)
    conv, h, events = _stream(client, "The job card won't save, I need a person")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "done" and kinds.count("delta") >= 2
    shown = "".join(e["text"] for e in events if e["type"] == "delta")
    assert "<me" not in shown and "meta" not in shown           # the tag never reaches the screen
    assert shown.strip() == "Sorry about that. I've passed it to the team."

    done = events[-1]
    assert done["reply"]["content"] == shown.strip()
    assert done["reply"]["suggestions"][0] == "How do I reopen a job card?"
    assert done["ticket"]["serial_number"] == "SN-4471"
    assert done["ticket"]["customer_site"] == "Mombasa branch"
    assert done["ticket"]["priority"] == "high"
    # Saved: reopening the chat shows the question and the answer.
    msgs = client.get(f"/api/conversations/{conv['id']}", headers=h).get_json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["content"] == shown.strip()
    # Plain text + <meta> (no JSON mode), with minimal thinking.
    cfg = fake.calls[0]["config"]
    assert cfg.response_mime_type is None and cfg.thinking_config.thinking_level.lower() == "minimal"
    assert "<meta>" in cfg.system_instruction


def test_missing_meta_falls_back_to_keywords(app, client, monkeypatch):
    _use(app, monkeypatch, FakeGemini(["Open Service, then New service request."]))
    _, _, events = _stream(client, "How do I log a service request?")
    done = events[-1]
    assert done["reply"]["content"] == "Open Service, then New service request."
    assert done["user_message"]["intent"] == "service_request"   # topic is stored on the question
    assert len(done["reply"]["suggestions"]) == 3 and done["ticket"] is None


def test_busy_model_switches_to_the_fallback(app, client, monkeypatch):
    fake = FakeGemini(["Here's how.", '<meta>{"intent": "question"}</meta>'], busy={"main-model"})
    _use(app, monkeypatch, fake)
    _, _, events = _stream(client, "Why is the sky blue?", mode="general")
    assert [c["model"] for c in fake.calls] == ["main-model", "backup-model"]
    assert events[-1]["reply"]["content"] == "Here's how." and events[-1]["source"] == "gemini"


def test_failure_mid_answer_resets_and_answers_offline(app, client, monkeypatch):
    _use(app, monkeypatch, FakeGemini(["Half an ans", "wer"], fail_after=1))
    _, _, events = _stream(client, "How do I record spare parts used on a job?")
    kinds = [e["type"] for e in events]
    assert "reset" in kinds and kinds[-1] == "done"
    after = "".join(e["text"] for e in events[kinds.index("reset"):] if e["type"] == "delta")
    assert events[-1]["source"] == "offline" and events[-1]["reply"]["content"] == after


def test_offline_mode_streams_one_piece(client):
    _, _, events = _stream(client, "How do I record spare parts used on a job?")
    assert [e["type"] for e in events] == ["start", "delta", "done"]
    assert events[1]["text"] == events[2]["reply"]["content"]


def test_web_search_in_stream_and_quota_fallback(app, client, monkeypatch):
    fake = FakeGemini(["Rates today.", '<meta>{"intent": "question"}</meta>'])
    real = fake._stream

    def quota_on_search(**kw):
        if kw["config"].tools:
            fake.calls.append(kw)
            raise RuntimeError("429 RESOURCE_EXHAUSTED quota")
        return real(**kw)
    fake.models.generate_content_stream = quota_on_search
    _use(app, monkeypatch, fake)
    _, _, events = _stream(client, "Shilling rate today?", mode="general", web_search=True)
    tried = [(c["model"], bool(c["config"].tools)) for c in fake.calls]
    assert tried == [("main-model", True), ("main-model", False)]   # no wasted fallback call
    assert "Web search wasn't available" in events[-1]["reply"]["notice"]


def test_stream_error_is_reported(app, client, monkeypatch):
    _use(app, monkeypatch, FakeGemini(["ok"]))
    monkeypatch.setattr("app.routes.chat._store_answer",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("db down")))
    _, _, events = _stream(client, "hello")
    assert events[-1]["type"] == "error"


def test_meta_splitter_holds_back_a_partial_tag():
    s = ai_service._MetaSplitter()
    assert s.feed("Answer <") == "Answer "
    assert s.feed("b>bold</b>") == "<b>bold</b>"      # "<" turned out not to be the tag
    assert s.feed(" end<met") == " end"
    assert s.feed('a>{"intent": "general"}</meta>') == ""
    assert s.close() == "" and s.meta() == {"intent": "general"}
    assert s.visible == "Answer <b>bold</b> end"

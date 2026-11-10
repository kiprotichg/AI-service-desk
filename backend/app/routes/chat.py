import json
import logging
import secrets
from datetime import timedelta

from flask import Blueprint, current_app, jsonify, request, stream_with_context

from ..auth import (agent_required, current_agent, current_user, has_conversation_access,
                    issue_conversation_token, unauthorized)
from ..extensions import db
from ..models import Conversation, Message, Project, Ticket, utcnow
from ..services import ai_service, attachments

bp = Blueprint("chat", __name__, url_prefix="/api/conversations")


def _error(msg, code=400):
    return jsonify({"error": msg}), code


@bp.post("")
def create_conversation():
    """Start a chat for the signed-in user. The returned token is the key to it."""
    user = current_user()
    if user is None and current_app.config["REQUIRE_USER_LOGIN"]:
        return unauthorized("Please sign in")
    data = request.get_json(silent=True) or {}
    name = user.name if user else (data.get("customer_name") or "Guest").strip()[:120] or "Guest"
    mode = data.get("mode") or "service"
    if mode not in ai_service.MODES:
        return _error(f"mode must be one of {list(ai_service.MODES)}")
    temporary = data.get("temporary") is True
    if user:
        delete_stale_temporary_chats(user.id)
    conv = Conversation(customer_name=name, mode=mode, user_id=user.id if user else None,
                        temporary=temporary)
    db.session.add(conv)
    db.session.commit()
    body = conv.to_dict(with_messages=True)
    body["token"] = issue_conversation_token(conv)
    return jsonify(body), 201


@bp.get("")
@agent_required
def list_conversations():
    convs = Conversation.query.order_by(Conversation.id.desc()).limit(100).all()
    return jsonify([c.to_dict() for c in convs])


@bp.get("/<int:conv_id>")
def get_conversation(conv_id):
    if not (has_conversation_access(conv_id) or current_agent()):
        return unauthorized()
    conv = db.get_or_404(Conversation, conv_id)
    return jsonify(conv.to_dict(with_messages=True))


TEMPORARY_MAX_AGE = timedelta(days=1)
_NOT_FOR_TEMPORARY = "Temporary chats aren't saved, so they can't be pinned, shared or added to a project."


def delete_stale_temporary_chats(user_id):
    """Temporary chats left behind (tab closed) are removed after a day."""
    cutoff = utcnow() - TEMPORARY_MAX_AGE
    for conv in Conversation.query.filter(Conversation.user_id == user_id,
                                          Conversation.temporary.is_(True),
                                          Conversation.updated_at < cutoff).all():
        _delete_chat(conv)


def _flag(value):
    """true/false from JSON, or "true"/"1"/"on" from a form field."""
    return value is True or str(value).lower() in ("1", "true", "yes", "on")


def _mode_from(fields, conv):
    """The answer mode for this request (the switch at the top), or an error message."""
    mode = fields.get("mode") or conv.mode or "service"
    return (mode, None) if mode in ai_service.MODES else (
        None, f"mode must be one of {list(ai_service.MODES)}")


def _open_chat(conv_id):
    """Only the chat's own user may post, regenerate or edit. Returns (conv, error)."""
    if not has_conversation_access(conv_id):
        return None, unauthorized()
    conv = db.get_or_404(Conversation, conv_id)
    if conv.status == "closed":
        return None, _error("Conversation is closed", 409)
    return conv, None


log = logging.getLogger(__name__)


def _ai_input(user_msg, prior):
    """(history, files of this message) to send to the AI, within the size budget."""
    budget = attachments.AI_ATTACHMENT_BUDGET
    current_parts, budget = attachments.load_parts(user_msg.attachments, budget)
    history = attachments.build_history(
        prior[-current_app.config["MAX_HISTORY_MESSAGES"]:], budget)
    return history, current_parts


def _answer(conv, user_msg, prior, mode, web_search=False):
    """Ask the AI to reply to user_msg (given the earlier messages) and store the reply.

    Shared by sending, regenerating and editing. Opens or updates the chat's ticket.
    Returns (ai_reply, assistant_message, ticket_or_None).
    """
    history, current_parts = _ai_input(user_msg, prior)
    ai = ai_service.generate_reply(history, user_msg.content, current_parts, mode=mode,
                                   web_search=web_search)
    bot_msg, ticket = _store_answer(conv, user_msg, ai, mode)
    return ai, bot_msg, ticket


def _store_answer(conv, user_msg, ai, mode):
    """Save the assistant's reply; open or update the chat's ticket. Returns (message, ticket)."""
    user_msg.intent, user_msg.sentiment = ai.intent, ai.sentiment
    bot_msg = Message(conversation=conv, role="assistant", content=ai.reply,
                      suggestions=ai.suggestions, sources=ai.sources)
    db.session.add(bot_msg)
    conv.mode = mode
    conv.updated_at = utcnow()

    # The chat's open ticket, if any; a new one is opened when the reply escalates.
    ticket = Ticket.query.filter(Ticket.conversation_id == conv.id,
                                 Ticket.status != "resolved").first()
    if ai.escalate and ticket is None:
        ticket = Ticket(
            conversation_id=conv.id,
            subject=(ai.ticket_subject or conv.title or "Support request")[:200],
            summary=user_msg.content or f"(sent {len(user_msg.attachments)} file(s))",
            module=mode,  # general never escalates, so this is service or sales
            priority="high" if ai.sentiment == "negative" else "medium",
        )
        db.session.add(ticket)
    if ai.escalate:
        conv.status = "escalated"
    if ticket is not None and mode == "service" and (ticket.module or "service") == "service":
        _apply_service_details(ticket, ai, bot_msg)
    return bot_msg, ticket


def _ndjson(obj):
    return json.dumps(obj) + "\n"


def _stream_answer(conv, user_msg, prior, mode, web_search):
    """Send the answer as it's written: one JSON object per line.

    {"type": "start", "user_message"}, then {"type": "delta", "text"} pieces
    ({"type": "reset"} means drop what was shown so far), then {"type": "done", ...}
    with the same body as the normal response, or {"type": "error", "error"}.
    """
    history, parts = _ai_input(user_msg, prior)
    question = user_msg.to_dict()
    conv_id, msg_id = conv.id, user_msg.id

    def events():
        yield _ndjson({"type": "start", "user_message": question})
        try:
            ai = None
            for kind, value in ai_service.stream_reply(history, question["content"], parts,
                                                       mode=mode, web_search=web_search):
                if kind == "delta":
                    yield _ndjson({"type": "delta", "text": value})
                elif kind == "reset":
                    yield _ndjson({"type": "reset"})
                else:
                    ai = value
            chat = db.session.get(Conversation, conv_id)
            asked = db.session.get(Message, msg_id)
            bot_msg, ticket = _store_answer(chat, asked, ai, mode)
            db.session.commit()
            yield _ndjson({"type": "done", **_reply_body(chat, ai, bot_msg, ticket,
                                                         user_message=asked.to_dict())})
        except Exception:
            log.exception("streaming answer failed")
            db.session.rollback()
            yield _ndjson({"type": "error",
                           "error": "Something went wrong while answering. Please try again."})

    return current_app.response_class(
        stream_with_context(events()), mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _reply_body(conv, ai, bot_msg, ticket, **extra):
    return {"reply": {**bot_msg.to_dict(), "notice": ai.notice}, "source": ai.source,
            "conversation": conv.to_dict(),
            "ticket": ticket.to_dict() if ticket else None, **extra}


@bp.post("/<int:conv_id>/messages")
def send_message(conv_id):
    conv, err = _open_chat(conv_id)
    if err:
        return err

    # JSON for text-only messages; multipart/form-data when files are attached.
    if request.mimetype == "multipart/form-data":
        fields = request.form
        uploads = request.files.getlist("files")
    else:
        fields = request.get_json(silent=True) or {}
        uploads = []
    content = (fields.get("content") or "").strip()
    mode, mode_error = _mode_from(fields, conv)
    if mode_error:
        return _error(mode_error)
    if len(content) > current_app.config["MAX_MESSAGE_LENGTH"]:
        return _error("Message is too long")
    try:
        new_files = attachments.save_uploads(uploads)
    except attachments.UploadError as exc:
        return _error(str(exc))
    if not content and not new_files:
        return _error("Type a message or attach a file")

    prior = list(conv.messages)
    user_msg = Message(conversation=conv, role="user", content=content, attachments=new_files)
    db.session.add(user_msg)
    if not conv.title:
        conv.title = (content or new_files[0].filename)[:60]
    if request.args.get("stream") == "1":
        db.session.commit()   # the question is kept even if answering fails
        return _stream_answer(conv, user_msg, prior, mode, _flag(fields.get("web_search")))
    ai, bot_msg, ticket = _answer(conv, user_msg, prior, mode, _flag(fields.get("web_search")))
    db.session.commit()
    return jsonify(_reply_body(conv, ai, bot_msg, ticket,
                               user_message=user_msg.to_dict())), 201


@bp.post("/<int:conv_id>/messages/<int:message_id>/regenerate")
def regenerate_reply(conv_id, message_id):
    """Replace the latest assistant reply with a fresh answer to the same question."""
    conv, err = _open_chat(conv_id)
    if err:
        return err
    msgs = list(conv.messages)
    if not any(m.id == message_id for m in msgs):
        return _error("Message not found", 404)
    if msgs[-1].id != message_id or msgs[-1].role != "assistant":
        return _error("Only the latest reply can be regenerated", 409)
    if len(msgs) < 2 or msgs[-2].role != "user":
        return _error("There's no question to answer again", 409)
    data = request.get_json(silent=True) or {}
    mode, mode_error = _mode_from(data, conv)
    if mode_error:
        return _error(mode_error)

    old_reply, user_msg = msgs[-1], msgs[-2]
    conv.messages.remove(old_reply)  # delete-orphan: the old reply row is deleted
    ai, bot_msg, ticket = _answer(conv, user_msg, msgs[:-2], mode,
                                  _flag(data.get("web_search")))
    db.session.commit()
    return jsonify(_reply_body(conv, ai, bot_msg, ticket, replaced_id=message_id,
                               user_message=user_msg.to_dict())), 201


@bp.patch("/<int:conv_id>/messages/<int:message_id>")
def edit_message(conv_id, message_id):
    """Change one of your messages and get a new answer.

    Like ChatGPT: everything after the edited message is removed (and those messages'
    uploaded files deleted), then the assistant answers the edited message. Files
    attached to the edited message itself are kept and sent again.
    """
    conv, err = _open_chat(conv_id)
    if err:
        return err
    msgs = list(conv.messages)
    target = next((m for m in msgs if m.id == message_id), None)
    if target is None:
        return _error("Message not found", 404)
    if target.role != "user":
        return _error("Only your own messages can be edited")
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    mode, mode_error = _mode_from(data, conv)
    if mode_error:
        return _error(mode_error)
    if len(content) > current_app.config["MAX_MESSAGE_LENGTH"]:
        return _error("Message is too long")
    if not content and not target.attachments:
        return _error("Type a message")

    later = [m for m in msgs if m.id > target.id]
    removed_files = [a for m in later for a in m.attachments]
    for m in later:
        conv.messages.remove(m)
    target.content = content
    prior = [m for m in msgs if m.id < target.id]
    if not any(m.role == "user" for m in prior):  # the first question names the chat
        conv.title = (content or target.attachments[0].filename)[:60]
    ai, bot_msg, ticket = _answer(conv, target, prior, mode, _flag(data.get("web_search")))
    db.session.commit()
    attachments.delete_files(removed_files)  # only after the rows are gone
    return jsonify(_reply_body(conv, ai, bot_msg, ticket, user_message=target.to_dict(),
                               removed_ids=[m.id for m in later])), 200


def _apply_service_details(ticket, ai, bot_msg):
    """Copy the serial number / customer site from the chat onto the Service ticket.

    A later message can fill in (or correct) a detail, e.g. after the assistant asked
    for it. The live model asks for missing details itself; offline replies get the
    question added here so the agent still receives them.
    """
    if ai.serial_number:
        ticket.serial_number = ai.serial_number
    if ai.customer_site:
        ticket.customer_site = ai.customer_site
    missing = [label for label, value in (("the equipment serial number", ticket.serial_number),
                                          ("the customer site", ticket.customer_site))
               if not value]
    if missing and ai.source == "offline":
        bot_msg.content += (f" To help our team, please reply with {' and '.join(missing)}.")


@bp.post("/<int:conv_id>/close")
def close_conversation(conv_id):
    if not (has_conversation_access(conv_id) or current_agent()):
        return unauthorized()
    conv = db.get_or_404(Conversation, conv_id)
    conv.status = "closed"
    db.session.commit()
    return jsonify(conv.to_dict())


@bp.delete("/<int:conv_id>")
def delete_conversation(conv_id):
    """Delete a chat, its messages and uploaded files. Tickets stay for the agents."""
    if not (has_conversation_access(conv_id) or current_agent()):
        return unauthorized()
    _delete_chat(db.get_or_404(Conversation, conv_id))
    return "", 204


def _delete_chat(conv):
    files = [a for m in conv.messages for a in m.attachments]
    Ticket.query.filter_by(conversation_id=conv.id).update({"conversation_id": None})
    db.session.delete(conv)
    db.session.commit()
    attachments.delete_files(files)  # only after the rows are gone


# ------------------------------------------------ rename, pin, project, share
@bp.patch("/<int:conv_id>")
def update_conversation(conv_id):
    """Rename, pin/unpin, or move into a project ({"project_id": null} takes it out)."""
    if not has_conversation_access(conv_id):
        return unauthorized()
    conv = db.get_or_404(Conversation, conv_id)
    data = request.get_json(silent=True) or {}
    if conv.temporary and (data.get("pinned") or data.get("project_id") is not None):
        return _error(_NOT_FOR_TEMPORARY)
    if "title" in data:
        title = " ".join(str(data["title"] or "").split())[:60]
        if not title:
            return _error("Enter a name for the chat")
        conv.title = title
    if "pinned" in data:
        conv.pinned = bool(data["pinned"])
    if "project_id" in data:
        pid = data["project_id"]
        if pid is None:
            conv.project_id = None
        else:
            valid = isinstance(pid, int) and not isinstance(pid, bool)
            project = db.session.get(Project, pid) if valid else None
            if project is None or conv.user_id is None or project.user_id != conv.user_id:
                return _error("Project not found", 404)
            conv.project_id = project.id
    db.session.commit()
    return jsonify(conv.to_dict())


@bp.post("/<int:conv_id>/share")
def share_conversation(conv_id):
    """Create (or update) the chat's public read-only link.

    The link shows the messages as they are now; later messages appear only after
    sharing again ("Update link").
    """
    if not has_conversation_access(conv_id):
        return unauthorized()
    conv = db.get_or_404(Conversation, conv_id)
    if conv.temporary:
        return _error(_NOT_FOR_TEMPORARY)
    if not conv.messages:
        return _error("Send a message before sharing this chat")
    if not conv.share_id:
        conv.share_id = secrets.token_urlsafe(16)
    conv.share_upto = conv.messages[-1].id
    db.session.commit()
    return jsonify({"share_id": conv.share_id, "messages": _shared_count(conv)})


@bp.delete("/<int:conv_id>/share")
def unshare_conversation(conv_id):
    """Stop sharing: the old link stops working."""
    if not has_conversation_access(conv_id):
        return unauthorized()
    conv = db.get_or_404(Conversation, conv_id)
    conv.share_id = conv.share_upto = None
    db.session.commit()
    return "", 204


def _shared_messages(conv):
    return [m for m in conv.messages if conv.share_upto and m.id <= conv.share_upto]


def _shared_count(conv):
    return len(_shared_messages(conv))


@bp.get("/shared/<share_id>")
def shared_conversation(share_id):
    """Anyone with the link can read the shared part of the chat. Files aren't included."""
    conv = Conversation.query.filter_by(share_id=share_id).first() if len(share_id) <= 32 else None
    if conv is None:
        return _error("This shared chat doesn't exist or is no longer shared.", 404)
    return jsonify({
        "title": conv.title or "Shared chat",
        "mode": conv.mode or "service",
        "messages": [{
            "role": m.role, "content": m.content, "created_at": m.created_at.isoformat(),
            "attachments": [{"filename": a.filename, "kind": a.kind}
                            for a in m.attachments],
            "sources": m.sources if m.role == "assistant" else [],
        } for m in _shared_messages(conv)],
    })

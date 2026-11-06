from flask import Blueprint, current_app, jsonify
from sqlalchemy import func

from ..auth import agent_required
from ..extensions import db
from ..models import Conversation, Message, Ticket
from ..services.mailer import email_enabled

bp = Blueprint("analytics", __name__, url_prefix="/api")


def _counts(column, *filters):
    rows = db.session.query(column, func.count()).filter(*filters).group_by(column).all()
    return {key or "unknown": n for key, n in rows}


@bp.get("/analytics")
@agent_required
def analytics():
    total_convs = db.session.query(func.count(Conversation.id)).scalar()
    escalated = db.session.query(func.count(Conversation.id)).filter(
        Conversation.status == "escalated").scalar()
    return jsonify({
        "conversations": total_convs,
        "messages": db.session.query(func.count(Message.id)).scalar(),
        "escalation_rate": round(escalated / total_convs, 3) if total_convs else 0,
        "intents": _counts(Message.intent, Message.role == "user"),
        "sentiment": _counts(Message.sentiment, Message.role == "user"),
        "tickets": _counts(Ticket.status),
        "modes": _counts(func.coalesce(Conversation.mode, "service")),
    })


@bp.get("/health")
def health():
    cfg = current_app.config
    return jsonify({"status": "ok",
                    "ai_mode": cfg["AI_PROVIDER"] if cfg["AI_ENABLED"] else "offline"})


def whatsapp_number(raw):
    """Digits-only international number for wa.me links, or None if it isn't valid.

    "+254 712-345 678" -> "254712345678". International numbers are 8-15 digits
    (E.164); a leading 0 means a local number without the country code, which
    wa.me can't use.
    """
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if raw and raw.strip().startswith("00"):  # 00 254 ... is the same as +254 ...
        digits = digits[2:]
    if not 8 <= len(digits) <= 15 or digits.startswith("0"):
        return None
    return digits


@bp.get("/config")
def public_config():
    """Settings the page needs before anyone signs in (the help desk contact)."""
    cfg = current_app.config
    raw = cfg.get("HELPDESK_WHATSAPP_NUMBER", "")
    number = whatsapp_number(raw)
    if raw and number is None:
        current_app.logger.warning(
            "HELPDESK_WHATSAPP_NUMBER %r isn't a valid international number "
            "(include the country code, e.g. +254 712 345 678)", raw)
    return jsonify({
        "helpdesk": {
            "name": cfg.get("HELPDESK_NAME") or "ICT Help Desk",
            "whatsapp": number,                   # None = not set up yet
            "hours": cfg.get("HELPDESK_HOURS") or "",
        },
        # Lets the Forgot-password page say where the code went in development.
        "email_enabled": email_enabled(),
        "require_login": bool(cfg.get("REQUIRE_USER_LOGIN")),
    })

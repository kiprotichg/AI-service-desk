"""User accounts: register, sign in, forgot / reset password, and the user's chats."""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta, timezone

from flask import Blueprint, current_app, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from ..auth import (conversation_from_token, current_user, issue_conversation_token,
                    issue_user_token, unauthorized, user_required)
from ..extensions import db
from ..models import Conversation, PasswordReset, User, utcnow
from ..services.mailer import send_email

bp = Blueprint("users", __name__, url_prefix="/api/users")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_DUMMY_HASH = generate_password_hash("not-a-real-password")
GENERIC_RESET_REPLY = ("If an account exists for that email, a reset code is on its way. "
                       "It expires in {minutes} minutes.")


def _error(msg, code=400):
    return jsonify({"error": msg}), code


def _clean_email(value):
    return (value or "").strip().lower()


def _email_problem(email):
    if not email:
        return "Enter your email address"
    if len(email) > 254 or not _EMAIL.match(email):
        return "Enter a valid email address"
    return None


def password_problem(password):
    """Why a new password isn't acceptable, or None."""
    if len(password) < 8:
        return "Use at least 8 characters for your password"
    if len(password) > 128:
        return "Use at most 128 characters for your password"
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return "Use at least one letter and one number in your password"
    return None


def _signed_in(user, status=200):
    return jsonify({"token": issue_user_token(user), "user": user.to_dict()}), status


def _as_utc(dt):
    # SQLite gives back naive datetimes; they were stored as UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _code_hash(user_id, code):
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, f"{user_id}:{code}".encode(), hashlib.sha256).hexdigest()


# ------------------------------------------------------------ emailed codes
def _codes(user, purpose):
    q = PasswordReset.query.filter(PasswordReset.user_id == user.id)
    if purpose == "reset":   # rows from before codes had a purpose are reset codes
        return q.filter(db.or_(PasswordReset.purpose == "reset", PasswordReset.purpose.is_(None)))
    return q.filter(PasswordReset.purpose == purpose)


def _issue_code(user, purpose):
    """A fresh 6-digit code (older ones stop working), or None if one was sent moments ago."""
    cfg = current_app.config
    latest = _codes(user, purpose).order_by(PasswordReset.id.desc()).first()
    if latest and (utcnow() - _as_utc(latest.created_at)).total_seconds() < \
            cfg["RESET_CODE_RESEND_SECONDS"]:
        return None   # don't flood their inbox
    _codes(user, purpose).filter(PasswordReset.used.is_(False)).update(
        {"used": True}, synchronize_session=False)
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.session.add(PasswordReset(
        user_id=user.id, purpose=purpose, code_hash=_code_hash(user.id, code),
        expires_at=utcnow() + timedelta(minutes=cfg["RESET_CODE_MINUTES"])))
    db.session.commit()
    return code


def _open_code(user, purpose, code):
    """The user's current code of this kind, if it's usable and `code` looks like one."""
    entry = (_codes(user, purpose).filter(PasswordReset.used.is_(False))
             .order_by(PasswordReset.id.desc()).first()) if user else None
    if entry is None or _as_utc(entry.expires_at) < utcnow() or not re.fullmatch(r"\d{6}", code):
        return None
    return entry


def _wrong_code(entry, user, code):
    """Count a try; an error response if the code is wrong or tried too often, else None."""
    entry.attempts += 1
    if entry.attempts > current_app.config["RESET_CODE_MAX_ATTEMPTS"]:
        entry.used = True
        db.session.commit()
        return _error("Too many wrong tries. Request a new code.")
    if not hmac.compare_digest(entry.code_hash, _code_hash(user.id, code)):
        db.session.commit()  # keep the attempt count
        return _error(WRONG_CODE)
    return None


WRONG_CODE = "That code is wrong or has expired. Request a new one."


# ------------------------------------------------------------ email confirmation
def _verification_on():
    return current_app.config.get("REQUIRE_EMAIL_VERIFICATION", True)


def _send_verification(user):
    code = _issue_code(user, "verify")
    if code:
        minutes = current_app.config["RESET_CODE_MINUTES"]
        send_email(
            user.email, "Confirm your AI in Ideal email",
            f"Hello {user.name},\n\n"
            f"Your code to confirm this email address is: {code}\n\n"
            f"It expires in {minutes} minutes. If you didn't create an AI in Ideal account, "
            f"you can ignore this email.\n\nAI in Ideal")


def _please_verify(user, status, error=None):
    body = {"verify_required": True, "email": user.email,
            "message": f"We sent a 6-digit code to {user.email}. Enter it to confirm your email."}
    if error:
        body["error"] = error
    return jsonify(body), status


@bp.post("/verify-email")
def verify_email():
    """Confirm the email with the emailed code; signs the user in."""
    data = request.get_json(silent=True) or {}
    email = _clean_email(data.get("email"))
    code = re.sub(r"\s", "", str(data.get("code") or ""))
    user = User.query.filter_by(email=email, is_active=True).first() if email else None
    if user is not None and user.email_verified:
        return _error("This email is already confirmed. Please sign in.", 409)
    entry = _open_code(user, "verify", code)
    if entry is None:
        return _error(WRONG_CODE)
    problem = _wrong_code(entry, user, code)
    if problem:
        return problem
    user.email_verified = True
    entry.used = True
    db.session.commit()
    return _signed_in(user)


@bp.post("/resend-verification")
def resend_verification():
    """Send a new confirmation code. Answers the same whether or not the email exists."""
    email = _clean_email((request.get_json(silent=True) or {}).get("email"))
    problem = _email_problem(email)
    if problem:
        return _error(problem)
    user = User.query.filter_by(email=email, is_active=True).first()
    if user is not None and not user.email_verified:
        _send_verification(user)
    return jsonify({"message": "If that account still needs confirming, a new code is on its way."})


# ------------------------------------------------------------- register / sign in
@bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    name = " ".join((data.get("name") or "").split())[:80]
    email = _clean_email(data.get("email"))
    password = data.get("password") or ""
    if not name:
        return _error("Enter your name")
    problem = _email_problem(email) or password_problem(password)
    if problem:
        return _error(problem)
    if User.query.filter_by(email=email).first():
        return _error("An account with this email already exists. Try signing in.", 409)
    user = User(name=name, email=email, email_verified=False)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    if _verification_on():
        _send_verification(user)
        return _please_verify(user, 201)   # signed in once the code is entered
    return _signed_in(user, 201)


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = _clean_email(data.get("email"))
    password = data.get("password") or ""
    user = User.query.filter_by(email=email).first() if email else None
    # Same message and similar timing whether or not the email is registered.
    if user is None:
        check_password_hash(_DUMMY_HASH, password)
        return unauthorized("Incorrect email or password")
    if not user.is_active or not user.check_password(password):
        return unauthorized("Incorrect email or password")
    if _verification_on() and not user.email_verified:
        _send_verification(user)
        return _please_verify(user, 403, "Please confirm your email first.")
    return _signed_in(user)


@bp.get("/me")
@user_required
def me():
    return jsonify(current_user().to_dict())


# ------------------------------------------------------------ password reset
@bp.post("/forgot-password")
def forgot_password():
    """Email a 6-digit code. Always answers the same, so emails can't be probed."""
    cfg = current_app.config
    email = _clean_email((request.get_json(silent=True) or {}).get("email"))
    problem = _email_problem(email)
    if problem:
        return _error(problem)
    reply = {"message": GENERIC_RESET_REPLY.format(minutes=cfg["RESET_CODE_MINUTES"])}

    user = User.query.filter_by(email=email, is_active=True).first()
    if user is None:
        return jsonify(reply)
    code = _issue_code(user, "reset")
    if code is None:
        return jsonify(reply)  # a code was just sent; don't flood their inbox
    send_email(
        user.email, "Your AI in Ideal password reset code",
        f"Hello {user.name},\n\n"
        f"Your password reset code is: {code}\n\n"
        f"It expires in {cfg['RESET_CODE_MINUTES']} minutes. If you didn't ask to reset "
        f"your password, you can ignore this email; your password won't change.\n\n"
        f"AI in Ideal")
    return jsonify(reply)


@bp.post("/reset-password")
def reset_password():
    data = request.get_json(silent=True) or {}
    email = _clean_email(data.get("email"))
    code = re.sub(r"\s", "", str(data.get("code") or ""))
    password = data.get("password") or ""

    user = User.query.filter_by(email=email, is_active=True).first() if email else None
    reset = _open_code(user, "reset", code)
    if reset is None:
        return _error(WRONG_CODE)
    # Check the new password first, so a too-short password doesn't use up a try.
    problem = password_problem(password)
    if problem:
        return _error(problem)
    wrong = _wrong_code(reset, user, code)
    if wrong:
        return wrong

    user.set_password(password)  # also signs out every old session (token fingerprint)
    user.email_verified = True   # the code reached their inbox, so they own the address
    reset.used = True
    db.session.commit()
    return _signed_in(user)


# ------------------------------------------------------------------- chats
@bp.get("/me/conversations")
@user_required
def my_conversations():
    """The user's chats, newest first, each with a fresh access token."""
    from .chat import delete_stale_temporary_chats
    delete_stale_temporary_chats(current_user().id)
    convs = (Conversation.query.filter_by(user_id=current_user().id)
             .filter(db.or_(Conversation.temporary.is_(None), Conversation.temporary.is_(False)))
             .order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(200).all())
    return jsonify([{**c.to_dict(), "token": issue_conversation_token(c)} for c in convs])


@bp.post("/me/claim-chats")
@user_required
def claim_chats():
    """Move chats this browser started before signing in into the user's account.

    Only chats that belong to nobody yet can be claimed, and only with their token.
    """
    tokens = (request.get_json(silent=True) or {}).get("tokens") or []
    if not isinstance(tokens, list):
        return _error("tokens must be a list")
    ids = {cid for t in tokens[:200] if isinstance(t, str)
           for cid in [conversation_from_token(t)] if cid}
    claimed = 0
    if ids:
        claimed = (Conversation.query.filter(Conversation.id.in_(ids),
                                             Conversation.user_id.is_(None))
                   .update({"user_id": current_user().id}, synchronize_session=False))
        db.session.commit()
    return jsonify({"claimed": claimed})

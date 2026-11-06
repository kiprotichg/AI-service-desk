"""Token auth.

Three kinds of signed tokens (itsdangerous, keyed by SECRET_KEY):
  * user tokens     - a registered user's sign-in, sent as `X-User-Token`
  * agent tokens    - support staff sign-in, sent as `Authorization: Bearer <token>`
  * customer tokens - issued when a chat starts, sent as `X-Conversation-Token`,
                      and only valid for that one conversation.
Different salts keep one kind from being accepted as another.
"""
import hashlib
import hmac
from functools import wraps

from flask import current_app, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .extensions import db
from .models import Agent, User

AGENT_SALT = "agent-auth"
CUSTOMER_SALT = "conversation-access"
USER_SALT = "user-auth"


def _serializer(salt):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt)


def _load(token, salt, max_age):
    if not token:
        return None
    try:
        return _serializer(salt).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def unauthorized(msg="Authentication required"):
    return jsonify({"error": msg}), 401


# ------------------------------------------------------------------ agents
def issue_agent_token(agent):
    return _serializer(AGENT_SALT).dumps({"agent_id": agent.id})


def current_agent():
    """Return the logged-in Agent for this request, or None."""
    if "agent" not in g:
        header = request.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else None
        data = _load(token, AGENT_SALT, current_app.config["AGENT_TOKEN_MAX_AGE"])
        agent = db.session.get(Agent, data["agent_id"]) if data else None
        g.agent = agent if agent and agent.is_active else None
    return g.agent


def agent_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_agent() is None:
            return unauthorized()
        return view(*args, **kwargs)
    return wrapper


# ------------------------------------------------------------------- users
def _password_fingerprint(user):
    """Changes whenever the password changes, so a reset signs out old sessions."""
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, user.password_hash.encode(), hashlib.sha256).hexdigest()[:16]


def issue_user_token(user):
    return _serializer(USER_SALT).dumps({"uid": user.id, "pv": _password_fingerprint(user)})


def current_user():
    """Return the signed-in User for this request, or None."""
    if "user" not in g:
        data = _load(request.headers.get("X-User-Token"), USER_SALT,
                     current_app.config["USER_TOKEN_MAX_AGE"])
        user = db.session.get(User, data.get("uid")) if isinstance(data, dict) else None
        ok = (user is not None and user.is_active
              and hmac.compare_digest(str(data.get("pv", "")), _password_fingerprint(user)))
        g.user = user if ok else None
    return g.user


def user_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            return unauthorized("Please sign in")
        return view(*args, **kwargs)
    return wrapper


def conversation_from_token(token):
    """The conversation id a chat token unlocks, or None."""
    data = _load(token, CUSTOMER_SALT, current_app.config["CONVERSATION_TOKEN_MAX_AGE"])
    return data.get("conv_id") if isinstance(data, dict) else None


# --------------------------------------------------------------- customers
def issue_conversation_token(conversation):
    return _serializer(CUSTOMER_SALT).dumps({"conv_id": conversation.id})


def has_conversation_access(conv_id):
    data = _load(request.headers.get("X-Conversation-Token"), CUSTOMER_SALT,
                 current_app.config["CONVERSATION_TOKEN_MAX_AGE"])
    return bool(data) and data.get("conv_id") == conv_id

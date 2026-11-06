from flask import Blueprint, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from ..auth import agent_required, current_agent, issue_agent_token, unauthorized
from ..models import Agent

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

_DUMMY_HASH = generate_password_hash("not-a-real-password")


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    agent = Agent.query.filter_by(username=username).first() if username else None
    # Same message and similar timing for unknown user vs wrong password,
    # so usernames can't be probed.
    if agent is None:
        check_password_hash(_DUMMY_HASH, password)
        return unauthorized("Invalid username or password")
    if not agent.is_active or not agent.check_password(password):
        return unauthorized("Invalid username or password")
    return jsonify({"token": issue_agent_token(agent), "agent": agent.to_dict()})


@bp.get("/me")
@agent_required
def me():
    return jsonify(current_agent().to_dict())

from flask import Blueprint, jsonify, request

from ..auth import agent_required
from ..extensions import db
from ..models import Ticket

bp = Blueprint("tickets", __name__, url_prefix="/api/tickets")

STATUSES = {"open", "in_progress", "resolved"}
PRIORITIES = {"low", "medium", "high"}


@bp.get("")
@agent_required
def list_tickets():
    query = Ticket.query
    status = request.args.get("status")
    if status:
        if status not in STATUSES:
            return jsonify({"error": f"status must be one of {sorted(STATUSES)}"}), 400
        query = query.filter_by(status=status)
    return jsonify([t.to_dict() for t in query.order_by(Ticket.id.desc()).all()])


@bp.patch("/<int:ticket_id>")
@agent_required
def update_ticket(ticket_id):
    ticket = db.get_or_404(Ticket, ticket_id)
    data = request.get_json(silent=True) or {}
    if "status" in data:
        if data["status"] not in STATUSES:
            return jsonify({"error": f"status must be one of {sorted(STATUSES)}"}), 400
        ticket.status = data["status"]
    if "priority" in data:
        if data["priority"] not in PRIORITIES:
            return jsonify({"error": f"priority must be one of {sorted(PRIORITIES)}"}), 400
        ticket.priority = data["priority"]
    # Agents can fill in or correct the equipment details on Service tickets.
    for field, limit in (("serial_number", 100), ("customer_site", 200)):
        if field in data:
            value = data[field]
            if value is not None and not isinstance(value, str):
                return jsonify({"error": f"{field} must be text"}), 400
            value = (value or "").strip()
            if len(value) > limit:
                return jsonify({"error": f"{field} is longer than {limit} characters"}), 400
            setattr(ticket, field, value or None)
    db.session.commit()
    return jsonify(ticket.to_dict())

from flask import Blueprint, jsonify, request

from ..auth import agent_required
from ..extensions import db
from ..models import FAQ
from ..services.knowledge_base import articles_for

bp = Blueprint("faqs", __name__, url_prefix="/api/faqs")

KB_MODULES = ("service", "sales")  # General mode has no knowledge base


@bp.get("")
def list_faqs():
    """All articles, or one module's with ?module=service|sales."""
    module = request.args.get("module")
    if module and module not in KB_MODULES:
        return jsonify({"error": f"module must be one of {list(KB_MODULES)}"}), 400
    query = articles_for(module) if module else FAQ.query
    return jsonify([f.to_dict() for f in query.order_by(FAQ.id).all()])


@bp.post("")
@agent_required
def create_faq():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    answer = (data.get("answer") or "").strip()
    module = data.get("module") or "service"
    if not question or not answer:
        return jsonify({"error": "question and answer are required"}), 400
    if module not in KB_MODULES:
        return jsonify({"error": f"module must be one of {list(KB_MODULES)}"}), 400
    faq = FAQ(question=question[:300], answer=answer, module=module,
              keywords=(data.get("keywords") or "").strip()[:300])
    db.session.add(faq)
    db.session.commit()
    return jsonify(faq.to_dict()), 201


@bp.delete("/<int:faq_id>")
@agent_required
def delete_faq(faq_id):
    faq = db.get_or_404(FAQ, faq_id)
    db.session.delete(faq)
    db.session.commit()
    return "", 204

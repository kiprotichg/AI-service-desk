"""Projects: named folders that group a user's chats in the sidebar."""
from flask import Blueprint, jsonify, request

from ..auth import current_user, user_required
from ..extensions import db
from ..models import Conversation, Project

bp = Blueprint("projects", __name__, url_prefix="/api/projects")

MAX_PROJECTS = 50


def _error(msg, code=400):
    return jsonify({"error": msg}), code


def _clean_name(value):
    return " ".join(str(value or "").split())[:80]


def _own_project(project_id):
    project = db.session.get(Project, project_id)
    return project if project and project.user_id == current_user().id else None


def _name_taken(name, except_id=None):
    for p in Project.query.filter_by(user_id=current_user().id).all():
        if p.id != except_id and p.name.lower() == name.lower():
            return True
    return False


@bp.get("")
@user_required
def list_projects():
    projects = (Project.query.filter_by(user_id=current_user().id)
                .order_by(db.func.lower(Project.name)).all())
    return jsonify([p.to_dict() for p in projects])


@bp.post("")
@user_required
def create_project():
    name = _clean_name((request.get_json(silent=True) or {}).get("name"))
    if not name:
        return _error("Enter a name for the project")
    if Project.query.filter_by(user_id=current_user().id).count() >= MAX_PROJECTS:
        return _error(f"You can have up to {MAX_PROJECTS} projects")
    if _name_taken(name):
        return _error("You already have a project with that name", 409)
    project = Project(user_id=current_user().id, name=name)
    db.session.add(project)
    db.session.commit()
    return jsonify(project.to_dict()), 201


@bp.patch("/<int:project_id>")
@user_required
def rename_project(project_id):
    project = _own_project(project_id)
    if project is None:
        return _error("Project not found", 404)
    name = _clean_name((request.get_json(silent=True) or {}).get("name"))
    if not name:
        return _error("Enter a name for the project")
    if _name_taken(name, except_id=project.id):
        return _error("You already have a project with that name", 409)
    project.name = name
    db.session.commit()
    return jsonify(project.to_dict())


@bp.delete("/<int:project_id>")
@user_required
def delete_project(project_id):
    """Delete the project only; its chats move back to Recent."""
    project = _own_project(project_id)
    if project is None:
        return _error("Project not found", 404)
    Conversation.query.filter_by(project_id=project.id).update({"project_id": None})
    db.session.delete(project)
    db.session.commit()
    return "", 204

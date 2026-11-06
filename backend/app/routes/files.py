import os

from flask import Blueprint, send_file

from ..auth import current_agent, has_conversation_access, unauthorized
from ..extensions import db
from ..models import Attachment
from ..services import attachments

bp = Blueprint("files", __name__, url_prefix="/api/attachments")


@bp.get("/<int:attachment_id>")
def download(attachment_id):
    """Serve an uploaded file to the chat's customer or to an agent."""
    att = db.get_or_404(Attachment, attachment_id)
    if not (has_conversation_access(att.message.conversation_id) or current_agent()):
        return unauthorized()
    path = attachments.path_of(att)
    if not os.path.exists(path):
        return {"error": "File is no longer available"}, 404
    resp = send_file(path, mimetype=att.mime_type, download_name=att.filename,
                     as_attachment=att.kind != "image", max_age=0)
    # Never let the browser reinterpret an upload as HTML or script.
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp

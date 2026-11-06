import json
from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class Agent(db.Model):
    """A support agent who can log in to manage tickets, FAQs and analytics."""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {"id": self.id, "username": self.username}


class User(db.Model):
    """Anyone can register an account to use the assistant. Separate from Agent:
    agents (support staff) sign in to see every user's tickets."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False)  # stored lower-case
    password_hash = db.Column(db.String(256), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "email": self.email}


class PasswordReset(db.Model):
    """A 6-digit reset code emailed to a user. Only a keyed hash of the code is stored."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    code_hash = db.Column(db.String(64), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    used = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)


class Project(db.Model):
    """A named folder of chats, owned by one account."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "created_at": self.created_at.isoformat()}


class Conversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # The account that owns this chat (NULL for chats made before accounts existed).
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    customer_name = db.Column(db.String(120), default="Guest")
    title = db.Column(db.String(120))  # set from the first message, shown in the sidebar
    mode = db.Column(db.String(20), default="service")  # service | general (last used)
    status = db.Column(db.String(20), default="open")  # open | escalated | closed
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    pinned = db.Column(db.Boolean, default=False)
    project_id = db.Column(db.Integer, db.ForeignKey("project.id"), index=True)
    # Temporary chats aren't listed in Recent and are deleted when the user leaves them.
    temporary = db.Column(db.Boolean, default=False)
    # Public read-only link: /share/<share_id> shows the messages up to share_upto.
    share_id = db.Column(db.String(32), unique=True, index=True)
    share_upto = db.Column(db.Integer)
    messages = db.relationship(
        "Message", backref="conversation", order_by="Message.id", cascade="all, delete-orphan"
    )

    def to_dict(self, with_messages=False):
        data = {
            "id": self.id,
            "customer_name": self.customer_name,
            "title": self.title or "New chat",
            "mode": self.mode or "service",
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": (self.updated_at or self.created_at).isoformat(),
            "pinned": bool(self.pinned),
            "project_id": self.project_id,
            "temporary": bool(self.temporary),
            "shared": bool(self.share_id),
            "share_id": self.share_id,
        }
        if with_messages:
            data["messages"] = [m.to_dict() for m in self.messages]
        return data


class Message(db.Model):
    # Edit and regenerate delete messages; AUTOINCREMENT stops SQLite handing a
    # deleted message's id to a new one (applies to newly created databases).
    __table_args__ = {"sqlite_autoincrement": True}
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversation.id"), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # user | assistant
    content = db.Column(db.Text, nullable=False)
    intent = db.Column(db.String(40))
    sentiment = db.Column(db.String(20))
    # Follow-up questions offered under an assistant reply, stored as a JSON list.
    suggestions_json = db.Column(db.Text)
    # Web pages a web-search answer used, as a JSON list of {"title", "url"}.
    sources_json = db.Column(db.Text)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    attachments = db.relationship(
        "Attachment", backref="message", order_by="Attachment.id", cascade="all, delete-orphan"
    )

    @property
    def suggestions(self):
        try:
            items = json.loads(self.suggestions_json or "[]")
        except ValueError:
            return []
        return [s for s in items if isinstance(s, str)] if isinstance(items, list) else []

    @suggestions.setter
    def suggestions(self, items):
        self.suggestions_json = json.dumps(list(items or [])) if items else None

    @property
    def sources(self):
        try:
            items = json.loads(self.sources_json or "[]")
        except ValueError:
            return []
        return [s for s in items if isinstance(s, dict) and s.get("url")] \
            if isinstance(items, list) else []

    @sources.setter
    def sources(self, items):
        self.sources_json = json.dumps(list(items or [])) if items else None

    def to_dict(self):
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "intent": self.intent,
            "sentiment": self.sentiment,
            "created_at": self.created_at.isoformat(),
            "attachments": [a.to_dict() for a in self.attachments],
            "suggestions": self.suggestions if self.role == "assistant" else [],
            "sources": self.sources if self.role == "assistant" else [],
        }


class Attachment(db.Model):
    """A file or photo the customer uploaded with a message. Bytes live on disk."""
    __table_args__ = {"sqlite_autoincrement": True}
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("message.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)    # original name, for display only
    stored_name = db.Column(db.String(64), nullable=False)  # random name on disk
    mime_type = db.Column(db.String(100), nullable=False)
    size = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    @property
    def kind(self):
        return "image" if self.mime_type.startswith("image/") else "file"

    def to_dict(self):
        return {"id": self.id, "filename": self.filename, "mime_type": self.mime_type,
                "size": self.size, "kind": self.kind,
                # Unique per upload even if an older database reuses ids; the browser
                # keys its image cache on this. Not a secret: downloads need the token.
                "key": self.stored_name[:16]}


class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversation.id"))
    subject = db.Column(db.String(200), nullable=False)
    summary = db.Column(db.Text)
    priority = db.Column(db.String(10), default="medium")  # low | medium | high
    status = db.Column(db.String(20), default="open")  # open | in_progress | resolved
    module = db.Column(db.String(20), default="service")  # service | sales: who handles it
    # Service tickets: which equipment, and where. Filled from the chat, editable by agents.
    serial_number = db.Column(db.String(100))
    customer_site = db.Column(db.String(200))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "subject": self.subject,
            "summary": self.summary,
            "module": self.module or "service",
            "serial_number": self.serial_number or "",
            "customer_site": self.customer_site or "",
            "priority": self.priority,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


class FAQ(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    question = db.Column(db.String(300), nullable=False)
    answer = db.Column(db.Text, nullable=False)
    keywords = db.Column(db.String(300), default="")  # comma separated
    # Which assistant uses this article: "service" or "sales". Rows from before this
    # column existed are NULL and count as "service".
    module = db.Column(db.String(20), default="service")

    def to_dict(self):
        return {"id": self.id, "question": self.question, "answer": self.answer,
                "keywords": self.keywords, "module": self.module or "service"}

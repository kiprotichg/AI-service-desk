import logging
import os
import secrets

import click
from flask import Flask, jsonify
from flask_cors import CORS

from .config import PROVIDER_KEYS, Config
from .extensions import db

log = logging.getLogger(__name__)


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    if app.config["AI_PROVIDER"] not in PROVIDER_KEYS:
        raise ValueError(f"AI_PROVIDER must be one of {sorted(PROVIDER_KEYS)}, "
                         f"got {app.config['AI_PROVIDER']!r}")

    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = secrets.token_hex(32)
        log.warning("SECRET_KEY is not set; using a random key. Logins and chat links "
                    "will stop working when the server restarts.")

    if not app.config.get("UPLOAD_FOLDER"):
        app.config["UPLOAD_FOLDER"] = os.path.join(app.instance_path, "uploads")

    db.init_app(app)
    CORS(app, resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}},
         allow_headers=["Content-Type", "Authorization", "X-Conversation-Token", "X-User-Token"])

    from .routes import analytics, auth, chat, faqs, files, projects, tickets, users
    for module in (auth, users, chat, projects, files, tickets, faqs, analytics):
        app.register_blueprint(module.bp)

    @app.errorhandler(404)
    def not_found(_):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(405)
    def method_not_allowed(_):
        return jsonify({"error": "Method not allowed"}), 405

    @app.errorhandler(413)
    def too_large(_):
        mb = app.config["MAX_CONTENT_LENGTH"] // 1024 // 1024
        return jsonify({"error": f"Upload is too large (max {mb} MB per message)"}), 413

    @app.cli.command("create-agent")
    @click.argument("username")
    @click.password_option()
    def create_agent(username, password):
        """Create a support agent, or reset an existing agent's password."""
        from .models import Agent
        agent = Agent.query.filter_by(username=username).first() or Agent(username=username)
        agent.set_password(password)
        db.session.add(agent)
        db.session.commit()
        click.echo(f"Agent '{username}' saved.")

    @app.cli.command("send-test-email")
    @click.argument("to")
    def send_test_email(to):
        """Check the SMTP settings by sending one email right now."""
        from .services.mailer import SMTPSetupError, send_now
        cfg = app.config
        click.echo(f"Sending through {cfg['SMTP_HOST'] or '(not set)'}:{cfg['SMTP_PORT']} "
                   f"as {cfg['SMTP_USERNAME'] or '(no username)'} ...")
        try:
            send_now(to, "AI in Ideal test email",
                     "This is a test from AI in Ideal. Email is set up correctly: password "
                     "reset codes will arrive like this one.")
        except SMTPSetupError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(f"Sent. Check the inbox (and spam folder) of {to}.")

    with app.app_context():
        db.create_all()
        _add_missing_columns()
        from .seed import seed_agent, seed_faqs
        seed_faqs()
        seed_agent(app.config["AGENT_USERNAME"], app.config["AGENT_PASSWORD"])

    return app


def _add_missing_columns():
    """Add columns introduced after a database was first created.

    db.create_all() creates missing tables but never alters existing ones, so a
    database from an earlier version would lack these. This is a stop-gap; use
    Flask-Migrate (Alembic) once the schema changes more often.
    """
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    for table in db.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing:
                col_type = col.type.compile(dialect=db.engine.dialect)
                with db.engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}'))
                log.warning("Added missing column %s.%s", table.name, col.name)

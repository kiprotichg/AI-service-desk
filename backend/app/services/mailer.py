"""Outgoing email (password reset codes).

With SMTP_HOST set, mail goes out through that server in a background thread, so
the request returns at once and its timing doesn't reveal whether an account
exists. Without SMTP_HOST (development) the message is written to the backend log
instead, so the reset flow can still be tested.
"""
import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage

from flask import current_app

log = logging.getLogger(__name__)


def email_enabled(app=None):
    return bool((app or current_app).config.get("SMTP_HOST"))


def send_email(to, subject, body):
    """Queue an email. Returns True if it was handed to SMTP, False if only logged."""
    cfg = current_app.config
    if not email_enabled():
        log.warning("Email is not configured (SMTP_HOST is empty), so this message was "
                    "NOT sent.\n  To: %s\n  Subject: %s\n%s", to, subject, body)
        return False

    msg, settings = _build(to, subject, body, cfg)
    threading.Thread(target=_deliver, args=(msg, settings), daemon=True).start()
    return True


def send_now(to, subject, body):
    """Send immediately and raise SMTPSetupError with a plain explanation on failure.

    Used by `flask --app run send-test-email` to check the settings.
    """
    if not email_enabled():
        raise SMTPSetupError("SMTP_HOST is empty in backend/.env, so email is switched off.")
    msg, settings = _build(to, subject, body, current_app.config)
    _send(msg, settings)


class SMTPSetupError(Exception):
    """An email problem explained in plain words."""


def _build(to, subject, body, cfg):
    msg = EmailMessage()
    msg["From"] = cfg.get("SMTP_FROM") or cfg.get("SMTP_USERNAME")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    settings = {k: cfg.get(k) for k in
                ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_USE_SSL")}
    # Google shows App Passwords as "abcd efgh ijkl mnop"; the spaces aren't part of it.
    if "gmail" in (settings["SMTP_HOST"] or "").lower():
        settings["SMTP_PASSWORD"] = (settings["SMTP_PASSWORD"] or "").replace(" ", "")
    return msg, settings


def _send(msg, s):
    try:
        context = ssl.create_default_context()
        if s["SMTP_USE_SSL"]:
            server = smtplib.SMTP_SSL(s["SMTP_HOST"], s["SMTP_PORT"], timeout=20, context=context)
        else:
            server = smtplib.SMTP(s["SMTP_HOST"], s["SMTP_PORT"], timeout=20)
            server.starttls(context=context)
        with server:
            if s["SMTP_USERNAME"]:
                server.login(s["SMTP_USERNAME"], s["SMTP_PASSWORD"])
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise SMTPSetupError(
            "The email server rejected SMTP_USERNAME / SMTP_PASSWORD. For Gmail, "
            "SMTP_PASSWORD must be a 16-letter App Password (Google Account > Security > "
            "2-Step Verification > App passwords), not your normal password. "
            f"Server said: {exc.smtp_code} {exc.smtp_error!r}") from exc
    except (OSError, smtplib.SMTPException) as exc:
        raise SMTPSetupError(
            f"Couldn't send through {s['SMTP_HOST']}:{s['SMTP_PORT']} - check SMTP_HOST, "
            f"SMTP_PORT and SMTP_USE_SSL, and your internet connection. ({exc})") from exc


def _deliver(msg, s):
    try:
        _send(msg, s)
        print(f"Sent email {msg['Subject']!r} to {msg['To']}", flush=True)
    except SMTPSetupError as exc:  # background thread: report, never crash the app
        log.error("Couldn't send email %r to %s: %s", msg["Subject"], msg["To"], exc)

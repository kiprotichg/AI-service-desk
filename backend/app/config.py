import os

# Which environment variables hold the key for each AI provider.
PROVIDER_KEYS = {
    "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


class Config:
    # Signs auth tokens. If unset, create_app() generates a random one, so tokens
    # stop working whenever the server restarts - set it in .env.
    SECRET_KEY = os.getenv("SECRET_KEY")
    AGENT_TOKEN_MAX_AGE = 12 * 3600          # agents log in again after 12 hours
    CONVERSATION_TOKEN_MAX_AGE = 30 * 86400  # recent chats stay openable for 30 days
    # Where uploaded files are stored (default: backend/instance/uploads).
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024    # whole request, i.e. up to 3 files of 5 MB
    # Optional: create this agent on startup if it doesn't exist yet.
    AGENT_USERNAME = os.getenv("AGENT_USERNAME")
    AGENT_PASSWORD = os.getenv("AGENT_PASSWORD")
    # Some hosts give postgres:// URLs, which SQLAlchemy rejects; fix the prefix.
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "sqlite:///service.db").replace(
        "postgres://", "postgresql://", 1
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    # "claude" or "gemini". Each SDK reads its own key from the environment.
    AI_PROVIDER = os.getenv("AI_PROVIDER", "claude").strip().lower()
    # AI_MODEL is the older name for CLAUDE_MODEL and still works.
    CLAUDE_MODEL = os.getenv("CLAUDE_MODEL") or os.getenv("AI_MODEL") or "claude-opus-5-5"
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    # Only decides whether we try the live model or go straight to the offline
    # responder: true when the chosen provider's key is set.
    AI_ENABLED = any(os.getenv(k) for k in PROVIDER_KEYS.get(AI_PROVIDER, ()))
    MAX_HISTORY_MESSAGES = 20
    MAX_MESSAGE_LENGTH = 2000
    # WhatsApp help desk button (bottom right). The number is in international
    # format with the country code, e.g. +254 712 345 678; spaces and dashes are fine.
    # Leave it empty to show "not set up yet" instead of a working link.
    HELPDESK_WHATSAPP_NUMBER = os.getenv("HELPDESK_WHATSAPP_NUMBER", "")
    HELPDESK_NAME = os.getenv("HELPDESK_NAME", "ICT Help Desk")
    HELPDESK_HOURS = os.getenv("HELPDESK_HOURS", "")   # optional, e.g. "Mon-Fri, 8am-5pm"

    # ---- user accounts ----
    # Chats need a signed-in user account (register / sign in on the start page).
    REQUIRE_USER_LOGIN = os.getenv("REQUIRE_USER_LOGIN", "true").lower() != "false"
    USER_TOKEN_MAX_AGE = 30 * 86400          # users sign in again after 30 days
    RESET_CODE_MINUTES = 15                  # how long an emailed reset code works
    RESET_CODE_MAX_ATTEMPTS = 5              # wrong guesses before the code is void
    RESET_CODE_RESEND_SECONDS = 60           # minimum gap between two codes

    # ---- outgoing email (password reset codes) ----
    # Leave SMTP_HOST empty in development: the code is then written to the backend
    # log instead of emailed. Gmail: smtp.gmail.com, port 587, and an App Password.
    SMTP_HOST = os.getenv("SMTP_HOST", "")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or 587)
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM = os.getenv("SMTP_FROM", "")          # defaults to SMTP_USERNAME
    SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "").lower() == "true"  # port 465 style


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    AI_PROVIDER = "claude"
    AI_ENABLED = False
    AGENT_USERNAME = None
    AGENT_PASSWORD = None
    HELPDESK_WHATSAPP_NUMBER = ""
    HELPDESK_NAME = "ICT Help Desk"
    HELPDESK_HOURS = ""
    # Most tests exercise chat features without accounts; test_users.py turns it on.
    REQUIRE_USER_LOGIN = False
    SMTP_HOST = ""
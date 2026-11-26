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
    # Hosted Postgres (Render) drops idle connections, and a worker killed mid-request
    # can leave a broken one in the pool ("SSL error: decryption failed or bad record
    # mac"). Test each connection before use and renew it every 5 minutes.
    SQLALCHEMY_ENGINE_OPTIONS = ({"pool_pre_ping": True, "pool_recycle": 300}
                                 if not SQLALCHEMY_DATABASE_URI.startswith("sqlite") else {})
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
    # "claude" or "gemini". Each SDK reads its own key from the environment.
    AI_PROVIDER = os.getenv("AI_PROVIDER", "claude").strip().lower()
    # AI_MODEL is the older name for CLAUDE_MODEL and still works.
    CLAUDE_MODEL = os.getenv("CLAUDE_MODEL") or os.getenv("AI_MODEL") or "claude-opus-5-5"
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    # Tried in turn (comma separated) when GEMINI_MODEL is overloaded ("503 high demand"); empty = none.
    GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL",
                                      "gemini-flash-lite-latest,gemini-3.1-flash-lite,gemini-3.5-flash,"
                                      "gemma-4-31b-it")
    # When every model is busy: wait this long, then try them all once more.
    GEMINI_BUSY_PAUSE_SECONDS = float(os.getenv("GEMINI_BUSY_PAUSE_SECONDS", "3") or 3)
    # If Gemini says "too many requests, retry in N seconds" (free tier: a few requests a
    # minute), wait up to this long for that instead, then try again.
    GEMINI_MAX_RETRY_WAIT_SECONDS = float(os.getenv("GEMINI_MAX_RETRY_WAIT_SECONDS", "20") or 0)
    # How much the model thinks before answering: minimal is fastest. Empty = model default.
    GEMINI_THINKING = os.getenv("GEMINI_THINKING", "minimal")
    # Give up on a Gemini request that hasn't answered in this many seconds (then the
    # fallback model is tried), instead of leaving the user waiting for minutes.
    GEMINI_TIMEOUT_SECONDS = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "25") or 25)

    # Live, read-only access to the Dynamics NAV / Business Central database.
    # Use a login that can only read (db_datareader). Off while any of these is empty.
    ERP_DB_HOST = os.getenv("ERP_DB_HOST", "")
    ERP_DB_PORT = os.getenv("ERP_DB_PORT", "")
    ERP_DB_NAME = os.getenv("ERP_DB_NAME", "")
    ERP_DB_USER = os.getenv("ERP_DB_USER", "")
    ERP_DB_PASSWORD = os.getenv("ERP_DB_PASSWORD", "")
    ERP_COMPANY = os.getenv("ERP_COMPANY", "")          # e.g. IDEALDYNAMICS_PROP
    # More companies users may switch to in the chat (comma separated); ERP_COMPANY is the default.
    ERP_COMPANIES = os.getenv("ERP_COMPANIES", "")
    # New accounts must confirm their email with an emailed code before signing in.
    REQUIRE_EMAIL_VERIFICATION = os.getenv("REQUIRE_EMAIL_VERIFICATION", "true").lower() == "true"
    # Every sign-in needs a 6-digit code emailed to the account, after the password.
    REQUIRE_LOGIN_CODE = os.getenv("REQUIRE_LOGIN_CODE", "true").lower() == "true"
    # Accounts (comma-separated emails) that see the Agent tools (Tickets, Analytics).
    # They still sign in to those with the agent login; everyone else never sees them.
    ADMIN_EMAILS = os.getenv("ADMIN_EMAILS", "")
    # Only users whose email is at one of these domains get ERP lookups in chat
    # (comma separated, e.g. "idealdynamics.co.ke"). Empty = nobody.
    ERP_ALLOWED_EMAIL_DOMAINS = os.getenv("ERP_ALLOWED_EMAIL_DOMAINS", "")
    # Extra individual addresses allowed ERP lookups (e.g. a manager's Gmail), comma separated.
    ERP_ALLOWED_EMAILS = os.getenv("ERP_ALLOWED_EMAILS", "")
    ERP_ODBC_DRIVER = os.getenv("ERP_ODBC_DRIVER", "ODBC Driver 18 for SQL Server")
    # auto: Microsoft's ODBC driver where it's installed, else pymssql (e.g. on Render).
    ERP_DRIVER = os.getenv("ERP_DRIVER", "auto")
    # Most on-premises SQL Servers use a self-signed certificate; set false if yours is trusted.
    ERP_TRUST_CERT = os.getenv("ERP_TRUST_CERT", "true").lower() == "true"
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
    ERP_DB_HOST = ERP_DB_NAME = ERP_DB_USER = ERP_DB_PASSWORD = ERP_COMPANY = ""
    ERP_COMPANIES = ""
    ERP_ALLOWED_EMAIL_DOMAINS = ""
    ERP_ALLOWED_EMAILS = ""
    ADMIN_EMAILS = ""
    # Most tests sign up and use the account straight away; test_verification.py turns it on.
    REQUIRE_EMAIL_VERIFICATION = False
    REQUIRE_LOGIN_CODE = False   # test_login_code.py turns it on
    # Most tests exercise chat features without accounts; test_users.py turns it on.
    REQUIRE_USER_LOGIN = False
    SMTP_HOST = ""
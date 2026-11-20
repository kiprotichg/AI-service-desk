"""Read-only access to the Dynamics NAV / Business Central database.

Settings: ERP_DB_HOST, ERP_DB_PORT, ERP_DB_NAME, ERP_DB_USER, ERP_DB_PASSWORD and
ERP_COMPANY in backend/.env. The login should only be able to read (db_datareader).

Safeguards, so the app can never change ERP data even with a stronger login:
  * only SELECT / WITH statements are run, and only ones written in this codebase
    (the AI never writes SQL; it can only call fixed lookups);
  * every query runs in a transaction that is always rolled back;
  * the connection asks for read-only intent, and queries time out after 15 seconds;
  * reads don't take or wait for locks (READ UNCOMMITTED), so NAV users are never blocked;
  * if the server doesn't answer within REACH_TIMEOUT_SECONDS, lookups fail at once
    (and for the next UNREACHABLE_PAUSE_SECONDS) instead of hanging the answer;
  * at most MAX_ROWS rows come back from any query.

NAV keeps each company's data in its own tables named "<Company>$<Table>", with
. " \\ / ' % [ ] in either name replaced by "_" (for example
"CRONUS International Ltd_$Cust_ Ledger Entry").
"""
import re
import socket
import threading
import time
import urllib.parse
from contextlib import contextmanager

from flask import current_app

MAX_ROWS = 200
QUERY_TIMEOUT_SECONDS = 15
REQUIRED = ("ERP_DB_HOST", "ERP_DB_NAME", "ERP_DB_USER", "ERP_DB_PASSWORD")

REACH_TIMEOUT_SECONDS = 5        # is the SQL Server answering at all?
UNREACHABLE_PAUSE_SECONDS = 30   # after it didn't, fail fast for this long

_engines = {}   # one connection pool per connection string
_engines_lock = threading.Lock()
_unreachable_until = {}   # (host, port) -> time.monotonic() until which we don't try


class ERPNotConfigured(Exception):
    """The ERP_DB_* settings aren't filled in."""


class ERPUnreachable(Exception):
    """The SQL Server didn't answer (network, firewall or server down)."""


class UnsafeQuery(Exception):
    """A statement that isn't a plain read was about to be sent."""


def erp_enabled(app=None):
    cfg = (app or current_app).config
    return all(cfg.get(k) for k in REQUIRED)


def missing_settings(app=None):
    cfg = (app or current_app).config
    return [k for k in REQUIRED + ("ERP_COMPANY",) if not cfg.get(k)]


# --------------------------------------------------------------- connection
def _odbc_value(value):
    """Quote an ODBC connection-string value when it contains special characters."""
    value = str(value)
    if re.search(r"[;{}=\s]", value) or value != value.strip():
        return "{" + value.replace("}", "}}") + "}"
    return value


def connection_string(cfg):
    server = cfg["ERP_DB_HOST"] + (f",{cfg['ERP_DB_PORT']}" if cfg.get("ERP_DB_PORT") else "")
    parts = [
        ("DRIVER", "{" + cfg.get("ERP_ODBC_DRIVER", "ODBC Driver 18 for SQL Server") + "}"),
        ("SERVER", _odbc_value(server)),
        ("DATABASE", _odbc_value(cfg["ERP_DB_NAME"])),
        ("UID", _odbc_value(cfg["ERP_DB_USER"])),
        ("PWD", _odbc_value(cfg["ERP_DB_PASSWORD"])),
        ("Encrypt", "yes"),
        ("TrustServerCertificate", "yes" if cfg.get("ERP_TRUST_CERT", True) else "no"),
        ("ApplicationIntent", "ReadOnly"),
        ("Connection Timeout", "10"),
        ("APP", "AI in Ideal (read-only)"),
    ]
    return ";".join(f"{k}={v}" for k, v in parts)


def choose_driver(cfg):
    """"pyodbc" (Microsoft's ODBC driver, used where it's installed, e.g. Windows) or
    "pymssql" (a self-contained package, used on hosts like Render that can't install
    the ODBC driver). ERP_DRIVER forces one; "auto" (the default) picks."""
    wanted = (cfg.get("ERP_DRIVER") or "auto").lower()
    if wanted in ("pyodbc", "pymssql"):
        return wanted
    try:
        import pyodbc
        if cfg.get("ERP_ODBC_DRIVER", "ODBC Driver 18 for SQL Server") in pyodbc.drivers():
            return "pyodbc"
    except ImportError:   # pyodbc can't load without the system's ODBC library
        pass
    return "pymssql"


def _pymssql_url(cfg):
    import sqlalchemy as sa
    port = int(cfg["ERP_DB_PORT"]) if cfg.get("ERP_DB_PORT") else 1433
    return sa.engine.URL.create("mssql+pymssql", username=cfg["ERP_DB_USER"],
                                password=cfg["ERP_DB_PASSWORD"], host=cfg["ERP_DB_HOST"],
                                port=port, database=cfg["ERP_DB_NAME"])


def _engine():
    if not erp_enabled():
        raise ERPNotConfigured("Fill in ERP_DB_HOST, ERP_DB_NAME, ERP_DB_USER and "
                               "ERP_DB_PASSWORD in backend/.env")
    cfg = current_app.config
    driver = choose_driver(cfg)
    key = (driver, connection_string(cfg))
    engine = _engines.get(key)
    if engine is not None:
        return engine
    with _engines_lock:   # parallel requests must not build two pools
        engine = _engines.get(key)
        if engine is None:
            engine = _engines[key] = _build_engine(cfg, driver, key[1])
    return engine


def _build_engine(cfg, driver, conn_str):
    import sqlalchemy as sa
    common = dict(
        pool_pre_ping=True, pool_size=3, max_overflow=2, pool_recycle=1800,
        # Don't wait for (or hold) row locks: NAV users posting documents must never
        # be slowed by the assistant, and lookups mustn't hang behind their locks.
        # The trade-off: a lookup can see a change that's still being saved.
        isolation_level="READ UNCOMMITTED")
    if driver == "pyodbc":
        engine = sa.create_engine(
            "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(conn_str), **common)

        @sa.event.listens_for(engine, "connect")
        def _set_timeout(dbapi_conn, _record):
            dbapi_conn.timeout = QUERY_TIMEOUT_SECONDS   # pyodbc: per-query timeout
    else:
        engine = sa.create_engine(
            _pymssql_url(cfg), **common,
            connect_args={"login_timeout": 10, "timeout": QUERY_TIMEOUT_SECONDS,
                          "appname": "AI in Ideal (read-only)"})

    host, port = cfg["ERP_DB_HOST"], int(cfg.get("ERP_DB_PORT") or 1433)

    @sa.event.listens_for(engine, "do_connect")
    def _check_reachable(_dialect, _rec, _cargs, _cparams):
        # Only when a new connection is opened (pooled ones are reused as is).
        ensure_reachable(host, port)
    return engine


def ensure_reachable(host, port):
    """Raise ERPUnreachable quickly if the SQL Server doesn't accept a connection.

    Drivers can wait a long time on an unanswered connection (seen on Render:
    "Connection timed out" after ~30 s, killing the request). A plain TCP check
    with a short timeout catches that, and later lookups skip it for a while.
    """
    key = (host, port)
    if time.monotonic() < _unreachable_until.get(key, 0):
        raise ERPUnreachable(f"The ERP server {host}:{port} isn't answering (checked moments ago).")
    try:
        socket.create_connection((host, port), timeout=REACH_TIMEOUT_SECONDS).close()
    except OSError as exc:
        _unreachable_until[key] = time.monotonic() + UNREACHABLE_PAUSE_SECONDS
        raise ERPUnreachable(f"The ERP server {host}:{port} isn't answering ({exc}).") from exc
    _unreachable_until.pop(key, None)


def preload_driver(app):
    """Import the database driver and SQLAlchemy's SQL Server dialect now, in the main
    thread. Importing them for the first time from two threads at once (the startup
    check and a request) left a request waiting on Python's import lock until gunicorn
    killed it."""
    import sqlalchemy.dialects.mssql  # noqa: F401
    if choose_driver(app.config) == "pymssql":
        import pymssql  # noqa: F401
    else:
        import pyodbc  # noqa: F401


_READ_ONLY_START = re.compile(r"^\s*(select|with)\b", re.I)
_WRITE_WORDS = re.compile(r"\b(insert|update|delete|merge|drop|alter|create|truncate|exec|execute|"
                          r"grant|revoke|deny|backup|restore|dbcc|into)\b", re.I)


def check_read_only(sql):
    """Refuse anything but a single plain SELECT (string literals are ignored)."""
    bare = re.sub(r"'(?:[^']|'')*'", "''", sql)          # don't look inside text values
    bare = re.sub(r"\[[^\]]*\]", "[x]", bare)            # nor inside [quoted names]
    if not _READ_ONLY_START.match(bare) or _WRITE_WORDS.search(bare) or ";" in bare.rstrip().rstrip(";"):
        raise UnsafeQuery("Only single SELECT statements may be sent to the ERP database")


@contextmanager
def read_only_connection():
    """A connection whose transaction is always rolled back, whatever happens."""
    conn = _engine().connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        trans.rollback()
        conn.close()


def query(sql, params=None, limit=MAX_ROWS):
    """Run one SELECT and return up to `limit` rows as dicts."""
    import sqlalchemy as sa
    check_read_only(sql)
    with read_only_connection() as conn:
        result = conn.execute(sa.text(sql), params or {})
        return [dict(r) for r in result.mappings().fetchmany(min(limit, MAX_ROWS))]


# ------------------------------------------------------------ NAV naming
_NAV_SPECIAL = re.compile(r"""[."\\/'%\[\]]""")


def sql_name(name):
    """NAV's SQL spelling of a company or table name."""
    return _NAV_SPECIAL.sub("_", name)


def company_table(table, company=None):
    """[Company$Table] for the configured company (or the one given)."""
    company = company or current_app.config.get("ERP_COMPANY")
    if not company:
        raise ERPNotConfigured("Set ERP_COMPANY in backend/.env (for example IDEALDYNAMICS_PROP)")
    return f"[{sql_name(company)}${sql_name(table)}]"


# ------------------------------------------------------------ setup checks
def connection_report():
    """What the setup check command prints: who we are, what we can do, the companies."""
    info = query("SELECT @@VERSION AS version, DB_NAME() AS db, SUSER_NAME() AS login")[0]
    perms = query(
        "SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'INSERT') AS can_insert, "
        "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'UPDATE') AS can_update, "
        "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'DELETE') AS can_delete, "
        "IS_SRVROLEMEMBER('sysadmin') AS is_sysadmin, IS_MEMBER('db_owner') AS is_owner")[0]
    companies = [r["Name"] for r in query("SELECT [Name] FROM [dbo].[Company] ORDER BY [Name]")]
    return info, perms, companies


def company_tables(company=None, with_rows_only=True):
    """Tables of one company with their row counts, from SQL Server's own statistics."""
    company = company or current_app.config.get("ERP_COMPANY")
    prefix = sql_name(company) + "$"
    rows = query(
        "SELECT t.name AS table_name, SUM(p.rows) AS row_count "
        "FROM sys.tables t JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1) "
        "WHERE LEFT(t.name, LEN(:prefix)) = :prefix "
        "GROUP BY t.name ORDER BY t.name",
        {"prefix": prefix}, limit=MAX_ROWS)
    # The listing can be longer than MAX_ROWS; page through it.
    out = rows
    while len(rows) == MAX_ROWS:
        rows = query(
            "SELECT t.name AS table_name, SUM(p.rows) AS row_count "
            "FROM sys.tables t JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1) "
            "WHERE LEFT(t.name, LEN(:prefix)) = :prefix AND t.name > :after "
            "GROUP BY t.name ORDER BY t.name",
            {"prefix": prefix, "after": out[-1]["table_name"]}, limit=MAX_ROWS)
        out += rows
    for r in out:
        r["table"] = r["table_name"][len(prefix):]
    return [r for r in out if r["row_count"]] if with_rows_only else out


def table_columns(table, company=None):
    company = company or current_app.config.get("ERP_COMPANY")
    full = f"{sql_name(company)}${sql_name(table)}"
    return query(
        "SELECT c.name AS column_name, ty.name AS type, c.max_length "
        "FROM sys.columns c JOIN sys.types ty ON ty.user_type_id = c.user_type_id "
        "WHERE c.object_id = OBJECT_ID(:full) ORDER BY c.column_id",
        {"full": "[dbo].[" + full + "]"})

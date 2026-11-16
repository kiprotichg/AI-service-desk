"""Read-only access to the Dynamics NAV / Business Central database.

Settings: ERP_DB_HOST, ERP_DB_PORT, ERP_DB_NAME, ERP_DB_USER, ERP_DB_PASSWORD and
ERP_COMPANY in backend/.env. The login should only be able to read (db_datareader).

Safeguards, so the app can never change ERP data even with a stronger login:
  * only SELECT / WITH statements are run, and only ones written in this codebase
    (the AI never writes SQL; it can only call fixed lookups);
  * every query runs in a transaction that is always rolled back;
  * the connection asks for read-only intent, and queries time out after 15 seconds;
  * reads don't take or wait for locks (READ UNCOMMITTED), so NAV users are never blocked;
  * at most MAX_ROWS rows come back from any query.

NAV keeps each company's data in its own tables named "<Company>$<Table>", with
. " \\ / ' % [ ] in either name replaced by "_" (for example
"CRONUS International Ltd_$Cust_ Ledger Entry").
"""
import re
import urllib.parse
from contextlib import contextmanager

from flask import current_app

MAX_ROWS = 200
QUERY_TIMEOUT_SECONDS = 15
REQUIRED = ("ERP_DB_HOST", "ERP_DB_NAME", "ERP_DB_USER", "ERP_DB_PASSWORD")

_engines = {}   # one connection pool per connection string


class ERPNotConfigured(Exception):
    """The ERP_DB_* settings aren't filled in."""


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


def _engine():
    if not erp_enabled():
        raise ERPNotConfigured("Fill in ERP_DB_HOST, ERP_DB_NAME, ERP_DB_USER and "
                               "ERP_DB_PASSWORD in backend/.env")
    conn_str = connection_string(current_app.config)
    engine = _engines.get(conn_str)
    if engine is None:
        import sqlalchemy as sa
        engine = sa.create_engine(
            "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(conn_str),
            pool_pre_ping=True, pool_size=3, max_overflow=2, pool_recycle=1800,
            # Don't wait for (or hold) row locks: NAV users posting documents must never
            # be slowed by the assistant, and lookups mustn't hang behind their locks.
            # The trade-off: a lookup can see a change that's still being saved.
            isolation_level="READ UNCOMMITTED")

        @sa.event.listens_for(engine, "connect")
        def _set_timeout(dbapi_conn, _record):
            dbapi_conn.timeout = QUERY_TIMEOUT_SECONDS   # pyodbc: per-query timeout

        _engines[conn_str] = engine
    return engine


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

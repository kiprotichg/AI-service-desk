"""Read-only ERP access: settings, connection string, NAV names and the safeguards."""
import pytest
import sqlalchemy as sa

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.services import erp

SETTINGS = dict(ERP_DB_HOST="102.0.0.1", ERP_DB_PORT="1433", ERP_DB_NAME="IDEALDYNAMICS_TEST",
                ERP_DB_USER="ai_reader", ERP_DB_PASSWORD="p;ss{word}", ERP_COMPANY="IDEALDYNAMICS_PROP",
                ERP_ODBC_DRIVER="ODBC Driver 18 for SQL Server", ERP_TRUST_CERT=True)


@pytest.fixture
def app():
    app = create_app(TestConfig)
    yield app
    with app.app_context():
        db.drop_all()


@pytest.fixture
def fake_erp(app, monkeypatch, tmp_path):
    """A small SQLite stand-in for the ERP database."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'erp.db'}")
    with engine.begin() as c:
        c.execute(sa.text('CREATE TABLE "IDEALDYNAMICS_PROP$Customer" ("No_" TEXT, "Name" TEXT)'))
        c.execute(sa.text('''INSERT INTO "IDEALDYNAMICS_PROP$Customer" VALUES ('C001', 'Kisumu Mills'), ('C002', 'Delete Me Ltd')'''))
    app.config.update(SETTINGS)
    monkeypatch.setattr(erp, "_engine", lambda: engine)
    return engine


def test_off_until_all_settings_are_filled(app):
    with app.app_context():
        assert not erp.erp_enabled()
        assert "ERP_DB_PASSWORD" in erp.missing_settings()
        with pytest.raises(erp.ERPNotConfigured):
            erp.query("SELECT 1")
        app.config.update(SETTINGS)
        assert erp.erp_enabled() and erp.missing_settings() == []


def test_connection_string_is_read_only_and_escapes_the_password():
    s = erp.connection_string(SETTINGS)
    assert "SERVER=102.0.0.1,1433" in s and "DATABASE=IDEALDYNAMICS_TEST" in s
    assert "ApplicationIntent=ReadOnly" in s and "Encrypt=yes" in s
    assert "PWD={p;ss{word}}}" in s          # ; and } can't break out of the value


def test_nav_table_names():
    assert erp.sql_name("CRONUS International Ltd.") == "CRONUS International Ltd_"
    assert erp.sql_name("Cust. Ledger Entry") == "Cust_ Ledger Entry"
    with create_app(TestConfig).app_context() as ctx:
        ctx.app.config["ERP_COMPANY"] = "IDEALDYNAMICS_PROP"
        assert erp.company_table("Service Item") == "[IDEALDYNAMICS_PROP$Service Item]"
        assert erp.company_table("Customer", company="GLOBAL AGRO") == "[GLOBAL AGRO$Customer]"


@pytest.mark.parametrize("sql", [
    "DELETE FROM x", "UPDATE x SET a = 1", "INSERT INTO x VALUES (1)", "DROP TABLE x",
    "SELECT * INTO copy FROM x", "SELECT 1; DELETE FROM x", "EXEC sp_who",
    "  with t as (select 1) delete from x", "TRUNCATE TABLE x",
])
def test_only_plain_selects_are_allowed(sql):
    with pytest.raises(erp.UnsafeQuery):
        erp.check_read_only(sql)


@pytest.mark.parametrize("sql", [
    "SELECT [No_], [Name] FROM [IDEALDYNAMICS_PROP$Customer]",
    "SELECT [Last Date Modified] FROM [X$Update Log]",           # words inside [names] are fine
    "SELECT * FROM t WHERE note = 'please delete; then update'",  # and inside text values
    "WITH t AS (SELECT 1 AS a) SELECT a FROM t;",
])
def test_reads_are_allowed(sql):
    erp.check_read_only(sql)


def test_query_reads_rows(app, fake_erp):
    with app.app_context():
        rows = erp.query(f"SELECT No_, Name FROM {erp.company_table('Customer').replace('[', chr(34)).replace(']', chr(34))} "
                         "WHERE No_ = :no", {"no": "C001"})
    assert rows == [{"No_": "C001", "Name": "Kisumu Mills"}]


def test_every_transaction_is_rolled_back(app, fake_erp):
    """Even if a write slipped past the checks, it would never be saved."""
    with app.app_context():
        with erp.read_only_connection() as conn:
            conn.execute(sa.text('DELETE FROM "IDEALDYNAMICS_PROP$Customer"'))
    with fake_erp.connect() as c:
        assert c.execute(sa.text('SELECT COUNT(*) FROM "IDEALDYNAMICS_PROP$Customer"')).scalar() == 2


def test_row_limit(app, fake_erp):
    with app.app_context():
        assert len(erp.query('SELECT * FROM "IDEALDYNAMICS_PROP$Customer"', limit=1)) == 1


def test_check_command_without_settings(app):
    result = app.test_cli_runner().invoke(args=["erp-check"])
    assert result.exit_code != 0 and "ERP_DB_HOST" in result.output


def test_driver_choice(monkeypatch):
    import sys
    import types as t
    assert erp.choose_driver({"ERP_DRIVER": "pymssql"}) == "pymssql"
    assert erp.choose_driver({"ERP_DRIVER": "PYODBC"}) == "pyodbc"
    # auto: the ODBC driver when it's installed...
    monkeypatch.setitem(sys.modules, "pyodbc", t.SimpleNamespace(drivers=lambda: ["ODBC Driver 18 for SQL Server"]))
    assert erp.choose_driver({"ERP_DRIVER": "auto"}) == "pyodbc"
    # ...otherwise pymssql (Linux hosts such as Render have no ODBC driver)
    monkeypatch.setitem(sys.modules, "pyodbc", t.SimpleNamespace(drivers=lambda: []))
    assert erp.choose_driver({}) == "pymssql"
    monkeypatch.setitem(sys.modules, "pyodbc", None)          # import fails
    assert erp.choose_driver({}) == "pymssql"


def test_pymssql_url_keeps_odd_passwords_intact():
    url = erp._pymssql_url(dict(SETTINGS, ERP_DB_PASSWORD="p@ss:w/rd;#1"))
    assert url.drivername == "mssql+pymssql" and url.port == 1433
    assert url.password == "p@ss:w/rd;#1" and url.host == "102.0.0.1"


def test_startup_check_logs_success_and_failure(app, fake_erp, caplog, monkeypatch):
    from app import erp_startup_check
    assert erp_startup_check(app) is True
    assert "ERP check: connected to 102.0.0.1:1433/IDEALDYNAMICS_TEST as ai_reader" in caplog.text

    def refuse(*a, **k):
        raise RuntimeError("Adaptive Server is unavailable or does not exist (102.0.0.1) p;ss{word}")
    monkeypatch.setattr(erp, "query", refuse)
    assert erp_startup_check(app) is False
    assert "ERP check FAILED" in caplog.text and "Adaptive Server is unavailable" in caplog.text
    assert "p;ss{word}" not in caplog.text          # the password is never logged


# ------------------------------------------------- hanging connections (Render)
def _closed_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()          # nothing listens here now: connections are refused at once
    return port


def test_unreachable_server_fails_fast_and_is_remembered(monkeypatch):
    import time
    monkeypatch.setattr(erp, "_unreachable_until", {})
    port = _closed_port()
    with pytest.raises(erp.ERPUnreachable):
        erp.ensure_reachable("127.0.0.1", port)
    calls = []
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: calls.append(a))
    t = time.monotonic()
    with pytest.raises(erp.ERPUnreachable, match="moments ago"):
        erp.ensure_reachable("127.0.0.1", port)    # within the pause: no new attempt
    assert calls == [] and time.monotonic() - t < 0.5


def test_reachable_server_passes(monkeypatch):
    import socket
    monkeypatch.setattr(erp, "_unreachable_until", {("127.0.0.1", 1): 0})
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    try:
        erp.ensure_reachable("127.0.0.1", server.getsockname()[1])
    finally:
        server.close()


def test_new_connections_check_reachability_first(app, monkeypatch):
    """A pool for an unreachable server raises ERPUnreachable instead of hanging."""
    monkeypatch.setattr(erp, "_engines", {})
    monkeypatch.setattr(erp, "_unreachable_until", {})
    app.config.update(SETTINGS, ERP_DB_HOST="127.0.0.1", ERP_DB_PORT=str(_closed_port()),
                      ERP_DRIVER="pymssql")
    with app.app_context():
        with pytest.raises(Exception) as info:
            erp.query("SELECT 1 AS ok")
    assert "ERPUnreachable" in repr(info.value) or isinstance(info.value, erp.ERPUnreachable)


def test_unreachable_lookup_gives_a_clear_message(app, monkeypatch):
    from app.services import erp_tools
    app.config.update(SETTINGS)

    def unreachable(*a, **k):
        raise erp.ERPUnreachable("The ERP server 1.2.3.4:1433 isn't answering")
    monkeypatch.setattr(erp, "query", unreachable)
    with app.app_context():
        out = erp_tools.run_lookup("find_customers", {"query": "bank"})
    assert "isn't answering right now" in out["error"]


def test_gunicorn_settings():
    import runpy
    from pathlib import Path
    conf = runpy.run_path(str(Path(__file__).resolve().parents[1] / "gunicorn.conf.py"))
    assert conf["worker_class"] == "gthread" and conf["threads"] >= 4 and conf["timeout"] >= 120


def test_postgres_connections_are_checked_before_use(monkeypatch):
    import importlib
    from app import config as config_module
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example/app")
    fresh = importlib.reload(config_module)
    try:
        assert fresh.Config.SQLALCHEMY_ENGINE_OPTIONS["pool_pre_ping"] is True
        assert fresh.Config.SQLALCHEMY_DATABASE_URI.startswith("postgresql://")
    finally:
        monkeypatch.delenv("DATABASE_URL")
        importlib.reload(config_module)

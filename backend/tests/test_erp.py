"""Read-only ERP access: settings, connection string, NAV names and the safeguards."""
import pytest
import sqlalchemy as sa

from app import create_app
from app.config import TestConfig
from app.extensions import db
from app.services import erp

SETTINGS = dict(ERP_DB_HOST="102.0.0.1", ERP_DB_PORT="2026", ERP_DB_NAME="IDEALDYNAMICS_TEST",
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
    assert "SERVER=102.0.0.1,2026" in s and "DATABASE=IDEALDYNAMICS_TEST" in s
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
    assert url.drivername == "mssql+pymssql" and url.port == 2026
    assert url.password == "p@ss:w/rd;#1" and url.host == "102.0.0.1"

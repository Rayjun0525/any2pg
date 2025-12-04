import pytest

from src.modules.adapters import db2, hana, mongodb, oracle, snowflake


class DummyConn:
    def __init__(self, rows=None, scalar=None):
        self.rows = rows or []
        self.scalar = scalar
        self.executed = []

    def execute(self, clause, params=None):
        self.executed.append((clause, params or {}))
        if hasattr(clause, "text") and "CURRENT_SCHEMA" in clause.text:
            return ScalarResult(self.scalar)
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class DummyEngine:
    def __init__(self, connections):
        if not isinstance(connections, list):
            connections = [connections]
        self.connections = connections

    def connect(self):
        return self.connections.pop(0)


class FailingInspector:
    def get_table_names(self, *_, **__):
        raise RuntimeError("tables boom")

    def get_columns(self, *_args, **_kwargs):
        return []

    def get_view_names(self, *_, **__):
        raise RuntimeError("views boom")

    def get_view_definition(self, *_args, **_kwargs):
        return None


def test_oracle_uses_schema_when_provided(monkeypatch):
    conn = DummyConn(
        rows=[("HR", "PROC1", "PROCEDURE", 1, "BEGIN"), ("HR", "PROC1", "PROCEDURE", 2, " END;")]
    )
    engine = DummyEngine(conn)
    adapter = oracle.OracleAdapter(engine)

    results = adapter.get_procedures(schema="hr")

    clause, params = conn.executed[0]
    assert "ALL_SOURCE" in clause.text
    assert params == {"owner": "HR"}
    assert results[0]["schema"] == "HR"
    assert results[0]["source"] == "BEGIN END;"


def test_oracle_uses_user_source_when_schema_missing():
    conn = DummyConn(rows=[("APPUSER", "PROC2", "FUNCTION", 1, "RETURN 1;")])
    engine = DummyEngine(conn)
    adapter = oracle.OracleAdapter(engine)

    results = adapter.get_procedures()

    clause, params = conn.executed[0]
    assert "USER_SOURCE" in clause.text
    assert params == {}
    assert results[0]["schema"] == "APPUSER"
    assert results[0]["name"] == "PROC2"


def test_db2_uses_routineschema_and_current_schema_default():
    rows = [("PROC3", "P", "body"), ("FUNC1", "F", "body2")]
    conn = DummyConn(rows=rows)
    engine = DummyEngine(conn)
    adapter = db2.DB2Adapter(engine)

    results = adapter.get_procedures()

    clause, params = conn.executed[0]
    assert "ROUTINESCHEMA = CURRENT SCHEMA" in clause.text
    assert params == {}
    assert {r["type"] for r in results} == {"PROCEDURE", "FUNCTION"}


def test_db2_uses_provided_schema():
    rows = [("PROC4", "P", "body")]
    conn = DummyConn(rows=rows)
    engine = DummyEngine(conn)
    adapter = db2.DB2Adapter(engine)

    adapter.get_procedures(schema="app")

    clause, params = conn.executed[0]
    assert "ROUTINESCHEMA = :schema" in clause.text
    assert params == {"schema": "APP"}


def test_hana_logs_warnings_for_metadata(monkeypatch, caplog):
    inspector = FailingInspector()
    monkeypatch.setattr(hana, "inspect", lambda engine: inspector)
    adapter = hana.HANAAdapter(engine=object())

    with caplog.at_level("WARNING"):
        adapter.get_tables_and_views()

    assert any("Failed to fetch HANA tables" in rec.message for rec in caplog.records)
    assert any("Failed to fetch HANA views" in rec.message for rec in caplog.records)


def test_hana_procedures_use_current_schema(monkeypatch):
    schema_conn = DummyConn(rows=[], scalar="APPSCHEMA")
    routine_conn = DummyConn(rows=[("PROC5", "PROCEDURE", "body")])
    engine = DummyEngine([schema_conn, routine_conn])
    adapter = hana.HANAAdapter(engine)

    results = adapter.get_procedures()

    routine_clause, routine_params = routine_conn.executed[0]
    assert routine_params == {"schema": "APPSCHEMA"}
    assert results[0]["schema"] == "APPSCHEMA"


def test_snowflake_fetches_routines_with_schema_filter():
    conn = DummyConn(rows=[("PROC6", "PROCEDURE", "body")])
    engine = DummyEngine(conn)
    adapter = snowflake.SnowflakeAdapter(engine)

    results = adapter.get_procedures(schema="analytics")

    clause, params = conn.executed[0]
    assert "ROUTINE_SCHEMA = :schema" in clause.text
    assert params == {"schema": "ANALYTICS"}
    assert results[0]["source"] == "body"


def test_mongodb_adapter_sets_engine(monkeypatch):
    created = {}

    class DummyClient:
        def __init__(self, uri):
            created["uri"] = uri

    monkeypatch.setattr(mongodb, "MongoClient", DummyClient)

    adapter = mongodb.MongoDBAdapter("mongodb://example")

    assert adapter.engine is adapter.client
    assert created["uri"] == "mongodb://example"

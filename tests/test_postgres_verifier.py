import psycopg
import pytest

from modules.postgres_verifier import VerifierAgent


class FakeDiag:
    def __init__(self, message_primary=None, context=None):
        self.message_primary = message_primary
        self.context = context


class FakePsycopgError(psycopg.Error):
    def __init__(self, diag=None, text=""):
        super().__init__()
        self._diag = diag
        self._text = text or "db error"

    @property
    def diag(self):
        return self._diag

    def __str__(self):
        return self._text


class FakeCursor:
    def __init__(self, error_after=None):
        self.error_after = error_after or {}
        self.statements_executed = []

    def execute(self, statement):
        idx = len(self.statements_executed) + 1
        self.statements_executed.append(statement)
        if idx in self.error_after:
            raise self.error_after[idx]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self.cursor_obj = cursor
        self.autocommit = None
        self.rollback_called = False

    def cursor(self):
        return self.cursor_obj

    def rollback(self):
        self.rollback_called = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def make_config(**target_overrides):
    target = {"uri": "postgresql://user:pass@localhost/db"}
    target.update(target_overrides)
    return {"database": {"target": target}}


def test_split_statements_returns_normalized_sql():
    verifier = VerifierAgent(make_config())

    statements = verifier._split_statements("select 1; select 2;")

    assert statements == ["SELECT 1", "SELECT 2"]


def test_split_statements_raises_value_error_on_parse_error():
    verifier = VerifierAgent(make_config())

    with pytest.raises(ValueError):
        verifier._split_statements("SELECT * FROM")


def test_verify_sql_executes_all_statements_and_rolls_back(monkeypatch):
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    monkeypatch.setattr(
        psycopg, "connect", lambda dsn, **kwargs: connection
    )
    verifier = VerifierAgent(make_config())

    success, error_msg = verifier.verify_sql("select 1; select 2;")

    assert success is True
    assert error_msg is None
    assert connection.autocommit is False
    assert connection.rollback_called is True
    assert cursor.statements_executed == ["SELECT 1", "SELECT 2"]


def test_verify_sql_reports_db_error_with_context(monkeypatch):
    failing_error = FakePsycopgError(
        diag=FakeDiag(message_primary="bad", context="line 1"),
        text="boom",
    )
    cursor = FakeCursor(error_after={2: failing_error})
    connection = FakeConnection(cursor)
    monkeypatch.setattr(
        psycopg, "connect", lambda dsn, **kwargs: connection
    )
    verifier = VerifierAgent(make_config())

    success, error_msg = verifier.verify_sql("select 1; select 2;")

    assert success is False
    assert "Statement #2 failed: bad" in error_msg
    assert "Context: line 1" in error_msg
    assert "SQL: SELECT 2" in error_msg


def test_verify_sql_falls_back_to_string_message(monkeypatch):
    failing_error = FakePsycopgError(diag=None, text="unstructured failure")
    cursor = FakeCursor(error_after={1: failing_error})
    connection = FakeConnection(cursor)
    monkeypatch.setattr(
        psycopg, "connect", lambda dsn, **kwargs: connection
    )
    verifier = VerifierAgent(make_config())

    success, error_msg = verifier.verify_sql("select 1;")

    assert success is False
    assert "unstructured failure" in error_msg

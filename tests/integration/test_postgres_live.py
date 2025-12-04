import os

import psycopg
import pytest

from modules.postgres_verifier import VerifierAgent

POSTGRES_DSN_ENV = "POSTGRES_TEST_DSN"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def postgres_dsn():
    dsn = os.getenv(POSTGRES_DSN_ENV)
    if not dsn:
        pytest.skip(f"{POSTGRES_DSN_ENV} not set")
    return dsn


@pytest.fixture(scope="session")
def postgres_conn(postgres_dsn):
    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="session")
def sample_table(postgres_conn):
    with postgres_conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.accounts (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL
            )
            """
        )
        cur.execute("TRUNCATE TABLE public.accounts")
    yield "public.accounts"
    with postgres_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS public.accounts")


def test_verifier_executes_live_statements(postgres_dsn, sample_table):
    config = {"database": {"target": {"uri": postgres_dsn, "statement_timeout_ms": 2000}}}
    verifier = VerifierAgent(config)
    sql_script = f"""
    INSERT INTO {sample_table}(name) VALUES ('Alice');
    INSERT INTO {sample_table}(name) VALUES ('Bob');
    """

    ok, message = verifier.verify_sql(sql_script)

    assert ok is True
    assert message is None


def test_verifier_reports_failures_with_context(postgres_dsn, sample_table):
    config = {"database": {"target": {"uri": postgres_dsn, "statement_timeout_ms": 2000}}}
    verifier = VerifierAgent(config)
    sql_script = f"""
    INSERT INTO {sample_table}(name) VALUES ('Charlie');
    INSERT INTO public.missing_table(id) VALUES (1);
    """

    ok, message = verifier.verify_sql(sql_script)

    assert ok is False
    assert "Statement #2 failed" in message
    assert "missing_table" in message
    assert "SQL: INSERT INTO public.missing_table" in message

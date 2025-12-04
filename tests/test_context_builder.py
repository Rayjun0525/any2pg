from modules.context_builder import RAGContextBuilder
from modules.sqlite_store import DBManager


def seed_metadata(db_path):
    db = DBManager(db_path)
    db.init_db()
    with db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO schema_objects(schema_name, obj_name, obj_type, ddl_script, source_code)
            VALUES
                ('PUBLIC', 'FOO', 'TABLE', 'CREATE TABLE foo(id INT);', NULL),
                ('PUBLIC', 'BAZ', 'FUNCTION', NULL, 'CREATE FUNCTION baz(x int) RETURNS int AS $$ BEGIN RETURN x; END; $$'),
                ('PUBLIC', 'IGNORED', 'TABLE', NULL, NULL)
            """
        )
    return db


def test_get_context_orders_and_filters_results(tmp_path):
    db_path = str(tmp_path / "ctx.db")
    db = seed_metadata(db_path)
    builder = RAGContextBuilder(db, source_dialect="postgres")

    context = builder.get_context("SELECT * FROM foo; SELECT baz(1);")

    foo_idx = context.index("PUBLIC.FOO")
    baz_idx = context.index("PUBLIC.BAZ")
    assert foo_idx < baz_idx
    assert "CREATE TABLE foo" in context
    assert "CREATE FUNCTION baz" in context
    assert "IGNORED" not in context


def test_get_context_handles_parse_errors(tmp_path):
    db_path = str(tmp_path / "ctx.db")
    db = seed_metadata(db_path)
    builder = RAGContextBuilder(db, source_dialect="postgres")

    context = builder.get_context("THIS IS NOT VALID SQL :::")

    assert context == ""

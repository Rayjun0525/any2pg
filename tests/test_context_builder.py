from modules.context_builder import RAGContextBuilder
from modules.context_builder import RAGContextBuilder
from modules.sqlite_store import DBManager


PROJECT_NAME = "demo_project"


def seed_metadata(db_path):
    db = DBManager(db_path, project_name=PROJECT_NAME)
    db.init_db()
    with db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO schema_objects(project_name, schema_name, obj_name, obj_type, ddl_script, source_code)
            VALUES
                ('demo_project', 'PUBLIC', 'FOO', 'TABLE', 'CREATE TABLE foo(id INT);', NULL),
                ('demo_project', 'PUBLIC', 'BAZ', 'FUNCTION', NULL, 'CREATE FUNCTION baz(x int) RETURNS int AS $$ BEGIN RETURN x; END; $$'),
                ('demo_project', 'PUBLIC', 'IGNORED', 'TABLE', NULL, NULL)
            """
        )
    return db


def test_get_context_orders_and_filters_results(tmp_path):
    db_path = str(tmp_path / "ctx.db")
    db = seed_metadata(db_path)
    builder = RAGContextBuilder(db, source_dialect="postgres", project_name=PROJECT_NAME)

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
    builder = RAGContextBuilder(db, source_dialect="postgres", project_name=PROJECT_NAME)

    context = builder.get_context("THIS IS NOT VALID SQL :::")

    assert context == ""

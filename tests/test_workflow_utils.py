from agents.workflow import MigrationWorkflow


def test_extract_sql_prefers_code_block():
    response = "Here is the output:\n```sql\nSELECT * FROM foo;\n```\nThanks"

    extracted = MigrationWorkflow._extract_sql(response)

    assert extracted == "SELECT * FROM foo;"


def test_extract_sql_falls_back_to_plain_text():
    response = "SELECT * FROM bar;"

    extracted = MigrationWorkflow._extract_sql(response)

    assert extracted == "SELECT * FROM bar;"


def test_extract_sql_handles_empty_response():
    assert MigrationWorkflow._extract_sql("") == ""

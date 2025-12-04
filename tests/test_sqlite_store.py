import os
import sqlite3

import pytest

from modules.sqlite_store import DBManager


def test_init_db_creates_tables(tmp_path):
    db_path = tmp_path / "store.db"
    db = DBManager(str(db_path))

    db.init_db()

    with db.get_cursor() as cur:
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('schema_objects', 'migration_logs')"
        )
        tables = {row[0] for row in cur.fetchall()}

    assert tables == {"schema_objects", "migration_logs"}


def test_get_cursor_rolls_back_on_error(tmp_path):
    db_path = tmp_path / "store.db"
    db = DBManager(str(db_path))
    db.init_db()

    with pytest.raises(sqlite3.Error):
        with db.get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO migration_logs(file_path, status) VALUES ('/tmp/file.sql', 'PENDING')"
            )
            cur.execute("INSERT INTO missing_table VALUES (2)")

    # Transaction should have been rolled back, so no rows are persisted
    with db.get_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM migration_logs")
        assert cur.fetchone()[0] == 0

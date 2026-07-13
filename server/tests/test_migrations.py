"""Guard the columns the recorder writes against the migration text.

Copied in spirit from sceance: the app and the SQL live in different files and
nothing else checks they agree, so a rename in one is silent until a live call
fails to insert.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from storage import EVENT_COLUMNS, FRAMEWORK, MESSAGE_COLUMNS, METRIC_COLUMNS

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def sql() -> str:
    return "\n".join(p.read_text().lower() for p in sorted(MIGRATIONS.glob("*.sql")))


def test_framework_migration_exists():
    assert (MIGRATIONS / "007_framework.sql").exists()


def test_framework_column_is_declared():
    text = (MIGRATIONS / "007_framework.sql").read_text().lower()
    assert "add column if not exists framework" in text


def test_framework_defaults_to_pipecat_so_existing_rows_backfill_correctly():
    # Every row already in the shared DB was written by sceance. The DEFAULT is
    # what makes this migration safe to run against live data, and what lets
    # sceance keep its INSERT unchanged.
    text = (MIGRATIONS / "007_framework.sql").read_text().lower()
    assert "default 'pipecat'" in text


def test_this_app_tags_its_rows_as_gradbot():
    assert FRAMEWORK == "gradbot"


@pytest.mark.parametrize(
    "table,columns",
    [
        ("messages", MESSAGE_COLUMNS),
        ("events", EVENT_COLUMNS),
        ("metrics", METRIC_COLUMNS),
    ],
)
def test_every_column_the_recorder_writes_exists_in_the_schema(table, columns):
    text = sql()
    for column in columns:
        assert column in text, f"{table}.{column} is written but never declared"

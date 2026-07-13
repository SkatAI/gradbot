"""Guard the schema against the code that writes to it.

The app and the SQL live in different files and nothing else checks they agree,
so a rename in one is silent until a live call fails to insert.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from storage import EVENT_COLUMNS, FRAMEWORK, MESSAGE_COLUMNS, METRIC_COLUMNS

SCHEMA = Path(__file__).resolve().parents[1] / "migrations" / "001_schema.sql"


def sql() -> str:
    return SCHEMA.read_text().lower()


def test_there_is_exactly_one_schema_file():
    # One idempotent file, not a chain of migrations. `make migrate` runs it once
    # against a fresh database and re-running it is a no-op.
    migrations = sorted(SCHEMA.parent.glob("*.sql"))
    assert migrations == [SCHEMA], f"expected only 001_schema.sql, found {migrations}"


def test_schema_is_idempotent():
    # It has to be safe to re-run: the readme tells people to, and `make migrate`
    # gives no way to tell whether it already ran.
    text = sql()
    creates = text.count("create table")
    guarded = text.count("create table if not exists")
    assert creates == guarded, "every CREATE TABLE must be IF NOT EXISTS"
    assert "add column if not exists" in text
    assert "on conflict" in text  # the seeded invite code


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


def test_the_session_row_columns_the_recorder_writes_exist():
    text = sql()
    for column in ("persona_name", "persona_json", "lang", "started_at",
                   "user_id", "environment", "framework"):
        assert column in text, f"sessions.{column} is written but never declared"


def test_this_app_tags_its_rows_as_gradbot():
    assert FRAMEWORK == "gradbot"
    assert "framework" in sql()


def test_accounts_and_the_signup_gate_are_in_the_schema():
    text = sql()
    # profiles + the trigger that fills it: without these, a sign-in succeeds at
    # Supabase and then 403s here with `profile_missing`.
    assert "create table if not exists public.profiles" in text
    assert "on_auth_user_created" in text
    # An unknown email needs a code or it lands on the waitlist.
    assert "invite_codes" in text
    assert "waitlist" in text

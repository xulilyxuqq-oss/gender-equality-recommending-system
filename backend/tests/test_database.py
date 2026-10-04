import sqlite3
from pathlib import Path

import pytest

from backend.app.database import Database, validate_core_schema
from backend.app.migrations import apply_migrations
from backend.tests.db_support import build_test_database


def test_migration_creates_application_schema_idempotently(tmp_path: Path):
    path = build_test_database(tmp_path / "test.sqlite3")
    database = Database(path)
    migrations = Path(__file__).parents[1] / "migrations"

    assert apply_migrations(database, migrations) == (1, 2, 3)
    assert apply_migrations(database, migrations) == ()

    with database.connect() as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        assert "app_accounts" in names
        assert "app_recommendation_items" in names
        assert "app_chat_memory" in names
        assert "app_agent_runs" in names
        assert "app_agent_steps" in names
        assert "admin_accounts" in names
        assert "hyperparameter_experiments" in names
        assert "fairness_policy_snapshots" in names
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(app_agent_steps)")
        }
        assert {"run_id", "step_index", "tool_name", "input_json", "output_json"} <= columns
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_only_one_active_agent_run_is_allowed_per_session(tmp_path: Path):
    path = build_test_database(tmp_path / "active-run.sqlite3")
    database = Database(path)
    migrations = Path(__file__).parents[1] / "migrations"
    apply_migrations(database, migrations)

    with database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO app_accounts(account_id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
            ("acct_agent", "agent-user", "unused", "2026-01-01T00:00:00+00:00"),
        )
        connection.execute("INSERT INTO app_profiles(account_id) VALUES (?)", ("acct_agent",))
        connection.execute(
            """INSERT INTO app_chat_sessions(
                   chat_session_id, account_id, purpose, state, profile_version,
                   created_at, updated_at
               ) VALUES (?, ?, 'RECOMMENDATION', 'COLLECTING_NAME', 0, ?, ?)""",
            ("chat_agent", "acct_agent", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            """INSERT INTO app_chat_messages(
                   message_id, chat_session_id, role, content, created_at
               ) VALUES (?, ?, 'user', ?, ?)""",
            ("msg_1", "chat_agent", "Alice", "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            """INSERT INTO app_agent_runs(
                   run_id, chat_session_id, account_id, trigger_message_id,
                   status, step_count, started_at
               ) VALUES (?, ?, ?, ?, 'RUNNING', 0, ?)""",
            ("run_1", "chat_agent", "acct_agent", "msg_1", "2026-01-01T00:00:00+00:00"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO app_agent_runs(
                       run_id, chat_session_id, account_id, trigger_message_id,
                       status, step_count, started_at
                   ) VALUES (?, ?, ?, ?, 'WAITING_USER', 0, ?)""",
                ("run_2", "chat_agent", "acct_agent", "msg_2", "2026-01-01T00:00:00+00:00"),
            )


def test_validation_rejects_empty_database(tmp_path: Path):
    path = tmp_path / "empty.sqlite3"
    sqlite3.connect(path).close()
    with pytest.raises(RuntimeError, match="缺少推荐数据表"):
        validate_core_schema(Database(path))

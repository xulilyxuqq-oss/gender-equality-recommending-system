from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .database import Database, validate_core_schema, validate_integrity


MIGRATION_NAME = re.compile(r"(?P<version>\d+)_.+\.sql$")


def _statements(script: str) -> list[str]:
    statements: list[str] = []
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statement = pending.strip()
            if statement:
                statements.append(statement)
            pending = ""
    if pending.strip():
        raise RuntimeError("迁移文件包含未完成的 SQL 语句。")
    return statements


def _migration_files(migrations_dir: Path) -> list[tuple[int, Path]]:
    migrations: list[tuple[int, Path]] = []
    for path in migrations_dir.glob("*.sql"):
        match = MIGRATION_NAME.fullmatch(path.name)
        if match:
            migrations.append((int(match.group("version")), path))

    migrations.sort(key=lambda item: item[0])
    versions = [version for version, _ in migrations]
    if len(versions) != len(set(versions)):
        raise RuntimeError("迁移版本重复。")
    return migrations


def apply_migrations(database: Database, migrations_dir: Path) -> tuple[int, ...]:
    validate_core_schema(database)
    validate_integrity(database)
    with database.transaction(immediate=True) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )

    applied: list[int] = []
    for version, migration_path in _migration_files(migrations_dir):
        script = migration_path.read_text(encoding="utf-8")
        with database.transaction(immediate=True) as connection:
            exists = connection.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
            ).fetchone()
            if exists:
                continue

            for statement in _statements(script):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (version, migration_path.name, datetime.now(timezone.utc).isoformat()),
            )
            applied.append(version)

    return tuple(applied)

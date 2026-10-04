from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


CORE_RECOMMENDATION_TABLES = frozenset(
    {
        "users",
        "courses",
        "course_fields",
        "course_prerequisites",
        "course_information",
        "user_completed_courses",
        "interactions",
    }
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if not self.path.is_file():
            raise FileNotFoundError(f"SQLite 数据库不存在：{self.path}")

        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()


def validate_core_schema(database: Database) -> None:
    with database.connect() as connection:
        present = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }

    missing = sorted(CORE_RECOMMENDATION_TABLES - present)
    if missing:
        raise RuntimeError(f"缺少推荐数据表：{', '.join(missing)}")


def validate_integrity(database: Database) -> None:
    with database.connect() as connection:
        results = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    if results != ["ok"]:
        raise RuntimeError("SQLite 数据库完整性检查失败：" + "; ".join(results[:5]))

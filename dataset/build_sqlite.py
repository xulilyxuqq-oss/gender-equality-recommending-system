from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .populate_course_information import populate_course_information
except ImportError:  # 兼容直接运行 python3 dataset/build_sqlite.py
    from populate_course_information import populate_course_information


DATASET_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = DATASET_DIR / "recommender.sqlite3"
BATCH_SIZE = 10_000


def _read_jsonl(path: Path) -> Iterator[tuple[int, Mapping[str, Any]]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path.name} 第 {line_number} 行不是合法 JSON：{exc.msg}"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path.name} 第 {line_number} 行必须是 JSON 对象。")
            yield line_number, value


def _required_text(
    row: Mapping[str, Any], field: str, path: Path, line_number: int
) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{path.name} 第 {line_number} 行的 {field} 必须是非空字符串。"
        )
    return value.strip()


def _optional_text(
    row: Mapping[str, Any], field: str, path: Path, line_number: int
) -> str:
    value = row.get(field, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(
            f"{path.name} 第 {line_number} 行的 {field} 必须是字符串。"
        )
    return value.strip()


def _list_field(
    row: Mapping[str, Any], field: str, path: Path, line_number: int
) -> list[Any]:
    value = row.get(field, [])
    if not isinstance(value, list):
        raise ValueError(
            f"{path.name} 第 {line_number} 行的 {field} 必须是数组。"
        )
    return value


def _flush(
    connection: sqlite3.Connection,
    sql: str,
    rows: list[tuple[Any, ...]],
) -> None:
    if rows:
        connection.executemany(sql, rows)
        rows.clear()


def _import_courses(connection: sqlite3.Connection, path: Path) -> None:
    courses: list[tuple[Any, ...]] = []
    fields: list[tuple[Any, ...]] = []
    prerequisites: list[tuple[Any, ...]] = []

    for line_number, row in _read_jsonl(path):
        course_id = _required_text(row, "course_id", path, line_number)
        course_name = _optional_text(row, "course_name", path, line_number)
        difficulty = _optional_text(row, "difficulty_level", path, line_number)
        rule = _optional_text(row, "advanced_label_rule", path, line_number)
        is_advanced = row.get("is_advanced")
        if not isinstance(is_advanced, bool):
            raise ValueError(
                f"{path.name} 第 {line_number} 行的 is_advanced 必须是布尔值。"
            )
        courses.append(
            (course_id, course_name, difficulty, int(is_advanced), rule)
        )

        for position, field in enumerate(
            _list_field(row, "fields_json", path, line_number)
        ):
            if not isinstance(field, str) or not field.strip():
                raise ValueError(
                    f"{path.name} 第 {line_number} 行包含无效课程领域。"
                )
            fields.append((course_id, field.strip(), position))

        for position, prerequisite in enumerate(
            _list_field(row, "prerequisites", path, line_number)
        ):
            if not isinstance(prerequisite, dict):
                raise ValueError(
                    f"{path.name} 第 {line_number} 行包含无效先修课程。"
                )
            prerequisite_id = _required_text(
                prerequisite,
                "course_id",
                path,
                line_number,
            )
            prerequisite_name = _optional_text(
                prerequisite,
                "course_name",
                path,
                line_number,
            )
            prerequisites.append(
                (course_id, prerequisite_id, prerequisite_name, position)
            )

    connection.executemany(
        "INSERT INTO courses VALUES (?, ?, ?, ?, ?)",
        courses,
    )
    connection.executemany(
        "INSERT INTO course_fields VALUES (?, ?, ?)",
        fields,
    )
    connection.executemany(
        "INSERT INTO course_prerequisites VALUES (?, ?, ?, ?)",
        prerequisites,
    )


def _import_users(connection: sqlite3.Connection, path: Path) -> None:
    users: list[tuple[Any, ...]] = []
    completed: list[tuple[Any, ...]] = []

    for line_number, row in _read_jsonl(path):
        user_id = _required_text(row, "user_id", path, line_number)
        user_name = _optional_text(row, "user_name", path, line_number)
        gender_code = row.get("gender_code")
        if (
            isinstance(gender_code, bool)
            or not isinstance(gender_code, int)
            or gender_code not in (1, 2)
        ):
            raise ValueError(
                f"{path.name} 第 {line_number} 行的 gender_code 必须是 1 或 2。"
            )
        users.append((user_id, user_name, gender_code))

        for position, course_id in enumerate(
            _list_field(row, "completed_course_ids", path, line_number)
        ):
            if not isinstance(course_id, str) or not course_id.strip():
                raise ValueError(
                    f"{path.name} 第 {line_number} 行包含无效已完成课程。"
                )
            completed.append((user_id, course_id.strip(), position))

        if len(users) >= 1_000:
            _flush(connection, "INSERT INTO users VALUES (?, ?, ?)", users)
            _flush(
                connection,
                "INSERT INTO user_completed_courses VALUES (?, ?, ?)",
                completed,
            )

    _flush(connection, "INSERT INTO users VALUES (?, ?, ?)", users)
    _flush(
        connection,
        "INSERT INTO user_completed_courses VALUES (?, ?, ?)",
        completed,
    )


def _import_interactions(connection: sqlite3.Connection, path: Path) -> None:
    interactions: list[tuple[Any, ...]] = []

    for line_number, row in _read_jsonl(path):
        user_id = _required_text(row, "user_id", path, line_number)
        course_id = _required_text(row, "course_id", path, line_number)
        comment = row.get("comment")
        if (
            isinstance(comment, bool)
            or not isinstance(comment, (int, float))
            or not math.isfinite(float(comment))
            or float(comment) < 0
        ):
            raise ValueError(
                f"{path.name} 第 {line_number} 行的 comment 必须是有限非负数。"
            )
        interactions.append((user_id, course_id, float(comment)))
        if len(interactions) >= BATCH_SIZE:
            _flush(
                connection,
                "INSERT INTO interactions VALUES (?, ?, ?)",
                interactions,
            )

    _flush(
        connection,
        "INSERT INTO interactions VALUES (?, ?, ?)",
        interactions,
    )


def _record_metadata(connection: sqlite3.Connection) -> None:
    values = {
        "schema_version": "2",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "users_source": "user_info.jsonl",
        "courses_source": "course_info.jsonl",
        "interactions_source": "user_course_interactions.jsonl",
    }
    for table in (
        "users",
        "courses",
        "course_fields",
        "course_prerequisites",
        "course_information",
        "user_completed_courses",
        "interactions",
    ):
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        values[f"{table}_count"] = str(count)
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        sorted(values.items()),
    )


def build_database(output: Path) -> None:
    output = output.resolve()
    if output.exists():
        raise FileExistsError(
            f"数据库已存在：{output}。请先备份或移走它，再重新构建。"
        )

    temporary = output.with_name(f".{output.name}.tmp")
    temporary.unlink(missing_ok=True)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(temporary)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.executescript((DATASET_DIR / "schema.sql").read_text(encoding="utf-8"))

        connection.execute("BEGIN")
        _import_courses(connection, DATASET_DIR / "course_info.jsonl")
        populate_course_information(connection)
        _import_users(connection, DATASET_DIR / "user_info.jsonl")
        _import_interactions(
            connection,
            DATASET_DIR / "user_course_interactions.jsonl",
        )
        _record_metadata(connection)
        connection.commit()

        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise ValueError(f"数据库存在外键错误：{foreign_key_errors[:5]}")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"数据库完整性检查失败：{integrity}")

        connection.execute("ANALYZE")
        connection.execute("PRAGMA optimize")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.close()
        connection = None
        temporary.replace(output)
    except Exception:
        if connection is not None:
            connection.close()
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="从 dataset JSONL 构建 SQLite 数据库。")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build_database(args.output)
    print(f"SQLite 数据库已创建：{args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

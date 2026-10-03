"""聚合课程领域和先修课，并生成课程介绍与学习资料。"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any


DATASET_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = DATASET_DIR / "recommender.sqlite3"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS course_information (
    course_id TEXT PRIMARY KEY,
    course_category TEXT NOT NULL
        CHECK (json_valid(course_category) AND json_type(course_category) = 'array'),
    detailed_description TEXT NOT NULL,
    is_advanced INTEGER NOT NULL CHECK (is_advanced IN (0, 1)),
    prerequisites TEXT NOT NULL
        CHECK (json_valid(prerequisites) AND json_type(prerequisites) = 'array'),
    course_materials TEXT NOT NULL
        CHECK (json_valid(course_materials) AND json_type(course_materials) = 'array'),
    FOREIGN KEY (course_id) REFERENCES courses(course_id) ON DELETE CASCADE
) WITHOUT ROWID
"""


def _json_text(value: Any) -> str:
    """以紧凑、可读中文的 JSON 保存多值字段。"""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_detailed_description(
    course_name: str,
    categories: Sequence[str],
    is_advanced: bool,
    prerequisites: Sequence[dict[str, str]],
) -> str:
    """基于已有课程元数据生成不虚构教学内容的详细介绍。"""

    category_text = "、".join(categories) if categories else "综合学科"
    level_text = "高阶" if is_advanced else "标准"
    description = (
        f"《{course_name}》是一门归属于{category_text}方向的{level_text}课程。"
        "课程介绍依据现有课程分类、难度标记和先修关系自动整理，"
        "可用于选课检索、学习路径规划与推荐结果说明。"
    )

    if prerequisites:
        prerequisite_names = "、".join(
            item["course_name"] or item["course_id"] for item in prerequisites
        )
        description += f"建议在学习前完成或掌握：{prerequisite_names}。"
    else:
        description += "当前数据未标注先修课程，可直接结合个人基础安排学习。"

    return description


def build_course_materials(
    course_name: str,
    categories: Sequence[str],
    is_advanced: bool,
    prerequisites: Sequence[dict[str, str]],
) -> list[dict[str, str]]:
    """生成结构化学习资料清单，不伪造外部教材或链接。"""

    category_text = "、".join(categories) if categories else "综合学科"
    materials = [
        {
            "type": "学习指南",
            "title": f"《{course_name}》学习指南",
            "description": (
                f"围绕{category_text}方向整理学习目标、知识结构和学习顺序。"
            ),
        },
        {
            "type": "知识清单",
            "title": f"《{course_name}》核心知识清单",
            "description": "用于课前预习、阶段复盘和课程内容检索。",
        },
    ]

    if prerequisites:
        prerequisite_names = "、".join(
            item["course_name"] or item["course_id"] for item in prerequisites
        )
        materials.append(
            {
                "type": "先修复习",
                "title": f"《{course_name}》先修知识复习单",
                "description": f"复习已标注的先修课程：{prerequisite_names}。",
            }
        )

    materials.append(
        {
            "type": "实践练习",
            "title": f"《{course_name}》练习与项目建议",
            "description": (
                "提供综合性练习和项目建议，适合高阶学习者进行能力检验。"
                if is_advanced
                else "提供基础练习和小型实践建议，帮助巩固课程知识。"
            ),
        }
    )
    return materials


def ensure_course_information_table(connection: sqlite3.Connection) -> None:
    """为已有数据库创建课程信息表及查询索引。"""

    connection.execute(CREATE_TABLE_SQL)
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_course_information_advanced
        ON course_information(is_advanced, course_id)
        """
    )


def _source_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def populate_course_information(connection: sqlite3.Connection) -> int:
    """从规范化来源表重建 course_information，并返回课程数量。"""

    required_tables = {"courses", "course_fields", "course_prerequisites"}
    missing_tables = required_tables - _source_tables(connection)
    if missing_tables:
        missing = "、".join(sorted(missing_tables))
        raise ValueError(f"数据库缺少来源表：{missing}")

    categories_by_course: dict[str, list[str]] = defaultdict(list)
    for course_id, field in connection.execute(
        """
        SELECT course_id, field
        FROM course_fields
        ORDER BY course_id, field_position, field
        """
    ):
        categories_by_course[course_id].append(field)

    prerequisites_by_course: dict[str, list[dict[str, str]]] = defaultdict(list)
    for course_id, prerequisite_id, prerequisite_name in connection.execute(
        """
        SELECT course_id, prerequisite_course_id, prerequisite_name
        FROM course_prerequisites
        ORDER BY course_id, prerequisite_position, prerequisite_course_id
        """
    ):
        prerequisites_by_course[course_id].append(
            {
                "course_id": prerequisite_id,
                "course_name": prerequisite_name,
            }
        )

    output_rows: list[tuple[Any, ...]] = []
    for course_id, course_name, is_advanced in connection.execute(
        """
        SELECT course_id, course_name, is_advanced
        FROM courses
        ORDER BY course_id
        """
    ):
        categories = categories_by_course[course_id]
        prerequisites = prerequisites_by_course[course_id]
        output_rows.append(
            (
                course_id,
                _json_text(categories),
                build_detailed_description(
                    course_name,
                    categories,
                    bool(is_advanced),
                    prerequisites,
                ),
                is_advanced,
                _json_text(prerequisites),
                _json_text(
                    build_course_materials(
                        course_name,
                        categories,
                        bool(is_advanced),
                        prerequisites,
                    )
                ),
            )
        )

    connection.execute("DELETE FROM course_information")
    connection.executemany(
        """
        INSERT INTO course_information(
            course_id,
            course_category,
            detailed_description,
            is_advanced,
            prerequisites,
            course_materials
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        output_rows,
    )
    return len(output_rows)


def _update_metadata(connection: sqlite3.Connection, row_count: int) -> None:
    if "metadata" not in _source_tables(connection):
        return
    connection.executemany(
        """
        INSERT INTO metadata(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (
            ("schema_version", "2"),
            ("course_information_count", str(row_count)),
        ),
    )


def update_database(database: Path) -> int:
    """在一个事务中创建并填充 course_information。"""

    if not database.is_file():
        raise FileNotFoundError(f"找不到数据库：{database}")

    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            ensure_course_information_table(connection)
            row_count = populate_course_information(connection)
            _update_metadata(connection, row_count)
            foreign_key_errors = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if foreign_key_errors:
                raise ValueError(f"数据库存在外键错误：{foreign_key_errors[:5]}")
        return row_count
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="聚合课程类别和先修课，并生成课程介绍与学习资料。"
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    row_count = update_database(args.database.resolve())
    print(f"course_information 已更新：{row_count} 门课程")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

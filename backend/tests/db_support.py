from __future__ import annotations

import json
import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_test_database(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            (PROJECT_ROOT / "dataset" / "schema.sql").read_text(encoding="utf-8")
        )

        courses = [
            ("C_BASE", "编程基础", "basic", 0, "prerequisite_count>=2"),
            ("C_DATA", "数据结构", "standard", 0, "prerequisite_count>=2"),
            ("C_ADV", "高级算法", "advanced", 1, "prerequisite_count>=2"),
            ("C_DB", "数据库基础", "basic", 0, "prerequisite_count>=2"),
            ("C_ML", "机器学习", "advanced", 1, "prerequisite_count>=2"),
            ("C_LA", "线性代数", "basic", 0, "prerequisite_count>=2"),
        ]
        fields = [
            ("C_BASE", "计算机科学", 0),
            ("C_DATA", "计算机科学", 0),
            ("C_ADV", "计算机科学", 0),
            ("C_DB", "数据库", 0),
            ("C_ML", "人工智能", 0),
            ("C_LA", "数学", 0),
        ]
        prerequisites = [
            ("C_DATA", "C_BASE", "编程基础", 0),
            ("C_ADV", "C_BASE", "编程基础", 0),
            ("C_ML", "C_BASE", "编程基础", 0),
        ]
        information = [
            (course_id, json.dumps([field], ensure_ascii=False), description, advanced, json.dumps(
                [
                    {"course_id": prerequisite_id, "course_name": prerequisite_name}
                    for candidate_id, prerequisite_id, prerequisite_name, _ in prerequisites
                    if candidate_id == course_id
                ],
                ensure_ascii=False,
            ), json.dumps([], ensure_ascii=False))
            for course_id, description, _, advanced, _ in courses
            for field in [next(value for candidate_id, value, _ in fields if candidate_id == course_id)]
        ]

        connection.executemany("INSERT INTO courses VALUES (?, ?, ?, ?, ?)", courses)
        connection.executemany("INSERT INTO course_fields VALUES (?, ?, ?)", fields)
        connection.executemany(
            "INSERT INTO course_prerequisites VALUES (?, ?, ?, ?)", prerequisites
        )
        connection.executemany(
            "INSERT INTO course_information VALUES (?, ?, ?, ?, ?, ?)", information
        )
        connection.executemany(
            "INSERT INTO users VALUES (?, ?, ?)",
            [("U_APP_TARGET", "目标用户", 1), ("U_APP_NEIGHBOR", "邻居用户", 2)],
        )
        connection.executemany(
            "INSERT INTO user_completed_courses VALUES (?, ?, ?)",
            [
                ("U_APP_TARGET", "C_BASE", 0),
                ("U_APP_NEIGHBOR", "C_BASE", 0),
                ("U_APP_NEIGHBOR", "C_DATA", 1),
            ],
        )
        connection.executemany(
            "INSERT INTO interactions VALUES (?, ?, ?)",
            [
                ("U_APP_TARGET", "C_BASE", 0),
                ("U_APP_NEIGHBOR", "C_BASE", 0),
                ("U_APP_NEIGHBOR", "C_DATA", 4.5),
                ("U_APP_NEIGHBOR", "C_DB", 4.0),
            ],
        )
        connection.commit()
    finally:
        connection.close()
    return path

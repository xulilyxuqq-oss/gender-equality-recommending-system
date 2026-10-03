from __future__ import annotations

import json
import sqlite3
import unittest

from dataset.populate_course_information import (
    ensure_course_information_table,
    populate_course_information,
)


class CourseInformationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(
            """
            CREATE TABLE courses (
                course_id TEXT PRIMARY KEY,
                course_name TEXT NOT NULL,
                difficulty_level TEXT NOT NULL,
                is_advanced INTEGER NOT NULL,
                advanced_label_rule TEXT NOT NULL
            ) WITHOUT ROWID;
            CREATE TABLE course_fields (
                course_id TEXT NOT NULL,
                field TEXT NOT NULL,
                field_position INTEGER NOT NULL,
                PRIMARY KEY (course_id, field)
            ) WITHOUT ROWID;
            CREATE TABLE course_prerequisites (
                course_id TEXT NOT NULL,
                prerequisite_course_id TEXT NOT NULL,
                prerequisite_name TEXT NOT NULL,
                prerequisite_position INTEGER NOT NULL,
                PRIMARY KEY (course_id, prerequisite_course_id)
            ) WITHOUT ROWID;
            """
        )
        ensure_course_information_table(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_merges_categories_and_prerequisites_in_source_order(self) -> None:
        self.connection.execute(
            "INSERT INTO courses VALUES (?, ?, ?, ?, ?)",
            ("c1", "机器学习", "advanced", 1, "test"),
        )
        self.connection.executemany(
            "INSERT INTO course_fields VALUES (?, ?, ?)",
            [("c1", "数学", 1), ("c1", "计算机科学", 0)],
        )
        self.connection.executemany(
            "INSERT INTO course_prerequisites VALUES (?, ?, ?, ?)",
            [
                ("c1", "p2", "概率论", 1),
                ("c1", "p1", "线性代数", 0),
            ],
        )

        self.assertEqual(populate_course_information(self.connection), 1)
        row = self.connection.execute(
            """
            SELECT course_category, detailed_description, is_advanced,
                   prerequisites, course_materials
            FROM course_information
            WHERE course_id = 'c1'
            """
        ).fetchone()

        self.assertEqual(json.loads(row[0]), ["计算机科学", "数学"])
        self.assertIn("高阶课程", row[1])
        self.assertEqual(row[2], 1)
        self.assertEqual(
            [item["course_id"] for item in json.loads(row[3])],
            ["p1", "p2"],
        )
        self.assertEqual(
            [item["type"] for item in json.loads(row[4])],
            ["学习指南", "知识清单", "先修复习", "实践练习"],
        )

    def test_rebuild_is_idempotent_and_supports_empty_lists(self) -> None:
        self.connection.execute(
            "INSERT INTO courses VALUES (?, ?, ?, ?, ?)",
            ("c1", "入门课程", "standard", 0, "test"),
        )
        self.assertEqual(populate_course_information(self.connection), 1)
        first_row = self.connection.execute(
            """
            SELECT course_category, prerequisites, course_materials
            FROM course_information
            WHERE course_id = 'c1'
            """
        ).fetchone()
        self.assertEqual(json.loads(first_row[0]), [])
        self.assertEqual(json.loads(first_row[1]), [])
        self.assertNotIn(
            "先修复习",
            [item["type"] for item in json.loads(first_row[2])],
        )

        self.assertEqual(populate_course_information(self.connection), 1)
        count = self.connection.execute(
            "SELECT COUNT(*) FROM course_information"
        ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()

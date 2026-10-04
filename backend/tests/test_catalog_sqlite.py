from pathlib import Path

import pytest

from backend.app.catalog import CourseCatalog
from backend.app.database import Database
from backend.tests.db_support import build_test_database


@pytest.fixture
def database(tmp_path: Path) -> Database:
    return Database(build_test_database(tmp_path / "catalog.sqlite3"))


def test_catalog_reads_courses_and_prerequisites_from_sqlite(database: Database):
    catalog = CourseCatalog(database)

    course = catalog.get("C_ADV")

    assert course is not None
    assert course["fields"] == ["计算机科学"]
    assert course["prerequisites"] == [
        {"course_id": "C_BASE", "course_name": "编程基础"}
    ]


def test_recommendation_sees_newly_committed_interaction(database: Database):
    catalog = CourseCatalog(database)

    before = catalog.recommendations("U_APP_TARGET", 8)[1]
    with database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO interactions(user_id, course_id, comment) VALUES (?, ?, 0)",
            ("U_APP_NEIGHBOR", "C_ADV"),
        )
    after = catalog.recommendations("U_APP_TARGET", 8)[1]

    assert "C_ADV" not in [item["course"]["course_id"] for item in before]
    assert "C_ADV" in [item["course"]["course_id"] for item in after]

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any

from .database import Database


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[\s\-_/（）()【】\[\]，,。：:；;]+", "", value)


def display_difficulty(value: str) -> str:
    return {"basic": "入门", "standard": "中级", "advanced": "高阶"}.get(value, value)


class CourseCatalog:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.courses: dict[str, dict[str, Any]] = {}
        self.aliases: dict[str, str] = {}
        self._load_courses()

    def _load_courses(self) -> None:
        self.courses.clear()
        self.aliases.clear()
        with self.database.connect() as connection:
            has_admin_tables = connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'admin_course_records'"
            ).fetchone() is not None
            if has_admin_tables:
                rows = connection.execute(
                    """
                    SELECT c.course_id, c.course_name, c.difficulty_level, c.is_advanced,
                           c.advanced_label_rule, ci.course_category, ci.prerequisites
                    FROM courses AS c
                    JOIN course_information AS ci ON ci.course_id = c.course_id
                    LEFT JOIN admin_course_records AS ac USING(course_id)
                    WHERE COALESCE(ac.status, 'ACTIVE') = 'ACTIVE'
                    ORDER BY c.course_id
                    """
                )
            else:
                rows = connection.execute(
                    """
                    SELECT c.course_id, c.course_name, c.difficulty_level, c.is_advanced,
                           c.advanced_label_rule, ci.course_category, ci.prerequisites
                    FROM courses AS c
                    JOIN course_information AS ci ON ci.course_id = c.course_id
                    ORDER BY c.course_id
                    """
                )
            for row in rows:
                course = {
                    "course_id": row["course_id"],
                    "course_name": row["course_name"],
                    "fields": json.loads(row["course_category"]),
                    "difficulty_level": display_difficulty(row["difficulty_level"]),
                    "is_advanced": bool(row["is_advanced"]),
                    "advanced_label_rule": row["advanced_label_rule"],
                    "prerequisites": json.loads(row["prerequisites"]),
                }
                course["normalized_name"] = normalize(course["course_name"])
                self.courses[course["course_id"]] = course
            if has_admin_tables:
                for row in connection.execute(
                    """SELECT ca.normalized_alias, ca.course_id FROM course_aliases AS ca
                       LEFT JOIN admin_course_records AS ac USING(course_id)
                       WHERE ca.status = 'ACTIVE' AND COALESCE(ac.status, 'ACTIVE') = 'ACTIVE'"""
                ):
                    self.aliases[row["normalized_alias"]] = row["course_id"]

    def reload(self) -> None:
        self._load_courses()

    @staticmethod
    def public_course(course: dict[str, Any]) -> dict[str, Any]:
        return {
            "course_id": course["course_id"],
            "course_name": course["course_name"],
            "fields": course["fields"],
            "difficulty_level": course["difficulty_level"],
            "is_advanced": course["is_advanced"],
        }

    def get(self, course_id: str) -> dict[str, Any] | None:
        return self.courses.get(course_id)

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        term = normalize(query)
        values = list(self.courses.values())
        if term:
            values = [
                course
                for course in values
                if term in course["normalized_name"]
                or any(term in normalize(field) for field in course["fields"])
            ]
        values.sort(key=lambda course: (course["course_name"], course["course_id"]))
        return [self.public_course(course) for course in values[:limit]]

    def resolve(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        term = normalize(query)
        if not term:
            return []

        aliases = {
            "python入门": "python程序设计",
            "python基础": "python程序设计",
            "机器学习基础课": "机器学习",
            "数据库入门": "数据库",
        }
        target = aliases.get(term, term)
        managed_alias_course_id = self.aliases.get(term)
        candidates: list[tuple[float, str, dict[str, Any]]] = []

        for course in self.courses.values():
            name = course["normalized_name"]
            reason = "FUZZY_NAME"
            if managed_alias_course_id == course["course_id"]:
                score = 0.99
                reason = "MANAGED_ALIAS"
            elif target == name:
                score = 1.0
                reason = "EXACT_NAME"
            elif target in name or name in target:
                overlap = min(len(target), len(name)) / max(len(target), len(name))
                score = 0.72 + 0.2 * overlap
            else:
                score = SequenceMatcher(None, target, name).ratio()
                field_match = any(target in normalize(field) or normalize(field) in target for field in course["fields"])
                if field_match and score < 0.58:
                    score = 0.46
                    reason = "FIELD_KEYWORD"

            if score >= 0.38:
                candidates.append((score, reason, course))

        candidates.sort(key=lambda item: (-item[0], item[2]["course_name"], item[2]["course_id"]))
        seen: set[str] = set()
        resolved: list[dict[str, Any]] = []
        for score, reason, course in candidates:
            if course["course_id"] in seen:
                continue
            seen.add(course["course_id"])
            resolved.append(
                {
                    **self.public_course(course),
                    "match_score": round(score, 4),
                    "match_reason": reason,
                }
            )
            if len(resolved) == limit:
                break
        return resolved

    def _prerequisites_satisfied(self, course: dict[str, Any], completed: set[str]) -> bool:
        return all(item["course_id"] in completed for item in course["prerequisites"])

    def recommendations(self, user_id: str, top_n: int) -> tuple[str, list[dict[str, Any]]]:
        with self.database.connect() as connection:
            completed = {
                row["course_id"]
                for row in connection.execute(
                    "SELECT course_id FROM user_completed_courses WHERE user_id = ?",
                    (user_id,),
                )
            }
            histories = self._neighbor_histories(connection, completed)

        scores: dict[str, float] = defaultdict(float)
        source = "collaborative" if completed else "popular_fallback"

        if completed:
            similarities: list[tuple[float, str]] = []
            for neighbor_id, history in histories.items():
                union = completed | history
                if not union:
                    continue
                similarity = len(completed & history) / len(union)
                if similarity > 0:
                    similarities.append((similarity, neighbor_id))
            similarities.sort(key=lambda item: (-item[0], item[1]))

            for similarity, neighbor_id in similarities[:80]:
                for course_id in histories[neighbor_id] - completed:
                    scores[course_id] += similarity

        if not scores:
            source = "popular_fallback"
            with self.database.connect() as connection:
                popularity = self._popular_courses(connection)
            for course_id, user_count, average in popularity:
                course = self.courses.get(course_id)
                if not course or course["prerequisites"]:
                    continue
                scores[course_id] = user_count * 100 + average

        ranked: list[tuple[float, dict[str, Any]]] = []
        for course_id, score in scores.items():
            course = self.courses.get(course_id)
            if not course or course_id in completed:
                continue
            if not self._prerequisites_satisfied(course, completed):
                continue
            ranked.append((score, course))

        ranked.sort(key=lambda item: (-item[0], item[1]["course_id"]))
        items: list[dict[str, Any]] = []
        for index, (_, course) in enumerate(ranked[:top_n], start=1):
            if source == "collaborative":
                codes = ["SIMILAR_USERS_COMPLETED", "PREREQUISITES_SATISFIED"]
                reason = "与你学习经历相近的用户学习过这门课，并且你已满足先修要求。"
            else:
                codes = ["POPULAR_WITH_USERS", "NO_PREREQUISITES_REQUIRED"]
                reason = "这门课受到较多学习者关注，并且不要求先修课程。"
            items.append(
                {
                    "rank": index,
                    "course": {
                        **self.public_course(course),
                        "prerequisites_satisfied": True,
                    },
                    "reason_codes": codes,
                    "reason_text": reason,
                }
            )
        return source, items

    @staticmethod
    def _neighbor_histories(
        connection: sqlite3.Connection, completed: set[str]
    ) -> dict[str, set[str]]:
        if not completed:
            return {}

        placeholders = ", ".join("?" for _ in completed)
        neighbor_rows = connection.execute(
            f"""
            SELECT DISTINCT user_id
            FROM interactions
            WHERE course_id IN ({placeholders})
            ORDER BY user_id
            """,
            tuple(completed),
        )
        neighbor_ids = [row["user_id"] for row in neighbor_rows]
        if not neighbor_ids:
            return {}

        neighbor_placeholders = ", ".join("?" for _ in neighbor_ids)
        histories: dict[str, set[str]] = defaultdict(set)
        for row in connection.execute(
            f"""
            SELECT user_id, course_id
            FROM interactions
            WHERE user_id IN ({neighbor_placeholders})
            ORDER BY user_id, course_id
            """,
            tuple(neighbor_ids),
        ):
            histories[row["user_id"]].add(row["course_id"])
        return histories

    @staticmethod
    def _popular_courses(connection: sqlite3.Connection) -> list[tuple[str, int, float]]:
        return [
            (row["course_id"], row["user_count"], row["average_rating"])
            for row in connection.execute(
                """
                SELECT course_id,
                       COUNT(*) AS user_count,
                       COALESCE(AVG(CASE WHEN comment > 0 THEN comment END), 0.0)
                           AS average_rating
                FROM interactions
                GROUP BY course_id
                """
            )
        ]


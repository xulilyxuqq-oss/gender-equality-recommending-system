from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DATASET_DIR = ROOT_DIR / "dataset"


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[\s\-_/（）()【】\[\]，,。：:；;]+", "", value)


def display_difficulty(value: str) -> str:
    return {"basic": "入门", "standard": "中级", "advanced": "高阶"}.get(value, value)


class CourseCatalog:
    def __init__(self) -> None:
        self.courses: dict[str, dict[str, Any]] = {}
        self.user_courses: dict[str, set[str]] = defaultdict(set)
        self.course_users: dict[str, set[str]] = defaultdict(set)
        self.positive_ratings: dict[str, list[float]] = defaultdict(list)
        self._load_courses()
        self._load_interactions()

    def _load_courses(self) -> None:
        path = DATASET_DIR / "course_info.jsonl"
        with path.open("r", encoding="utf-8") as source:
            for line in source:
                raw = json.loads(line)
                course = {
                    "course_id": raw["course_id"],
                    "course_name": raw["course_name"],
                    "fields": raw.get("fields_json", []),
                    "difficulty_level": display_difficulty(raw.get("difficulty_level", "standard")),
                    "is_advanced": bool(raw.get("is_advanced", False)),
                    "advanced_label_rule": raw.get("advanced_label_rule", "prerequisite_count>=2"),
                    "prerequisites": raw.get("prerequisites", []),
                }
                course["normalized_name"] = normalize(course["course_name"])
                self.courses[course["course_id"]] = course

    def _load_interactions(self) -> None:
        path = DATASET_DIR / "user_course_interactions.jsonl"
        with path.open("r", encoding="utf-8") as source:
            for line in source:
                raw = json.loads(line)
                user_id = raw["user_id"]
                course_id = raw["course_id"]
                self.user_courses[user_id].add(course_id)
                self.course_users[course_id].add(user_id)
                comment = raw.get("comment", 0)
                if isinstance(comment, (int, float)) and comment > 0:
                    self.positive_ratings[course_id].append(float(comment))

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
        candidates: list[tuple[float, str, dict[str, Any]]] = []

        for course in self.courses.values():
            name = course["normalized_name"]
            reason = "FUZZY_NAME"
            if target == name:
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

    def recommendations(self, completed_ids: list[str], top_n: int) -> tuple[str, list[dict[str, Any]]]:
        completed = set(completed_ids)
        scores: dict[str, float] = defaultdict(float)
        source = "collaborative" if completed else "popular_fallback"

        if completed:
            neighbor_ids: set[str] = set()
            for course_id in completed:
                neighbor_ids.update(self.course_users.get(course_id, set()))

            similarities: list[tuple[float, str]] = []
            for user_id in neighbor_ids:
                history = self.user_courses[user_id]
                union = completed | history
                if not union:
                    continue
                similarity = len(completed & history) / len(union)
                if similarity > 0:
                    similarities.append((similarity, user_id))
            similarities.sort(key=lambda item: (-item[0], item[1]))

            for similarity, user_id in similarities[:80]:
                for course_id in self.user_courses[user_id] - completed:
                    scores[course_id] += similarity

        if not scores:
            source = "popular_fallback"
            for course_id, users in self.course_users.items():
                course = self.courses.get(course_id)
                if not course or course["prerequisites"]:
                    continue
                ratings = self.positive_ratings.get(course_id, [])
                average = sum(ratings) / len(ratings) if ratings else 0.0
                scores[course_id] = len(users) * 100 + average

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


catalog = CourseCatalog()


"""基线模型使用的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


class DataValidationError(ValueError):
    """输入数据缺失字段、类型错误或引用无效时抛出。"""


@dataclass(frozen=True)
class Course:
    course_id: str
    name: str
    prerequisite_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Neighbor:
    user_id: str
    similarity: float


@dataclass(frozen=True)
class ScoredCourse:
    course_id: str
    predicted_score: float


@dataclass(frozen=True)
class Recommendation:
    user_id: str
    course_id: str
    course_name: str
    predicted_score: float
    rank: int


@dataclass
class InteractionIndex:
    """用户课程评分和课程用户倒排索引。"""

    user_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    course_users: dict[str, set[str]] = field(default_factory=dict)

    def ensure_user(self, user_id: str) -> None:
        self.user_scores.setdefault(user_id, {})

    def add(self, user_id: str, course_id: str, score: float) -> None:
        scores = self.user_scores.setdefault(user_id, {})
        if course_id in scores:
            raise DataValidationError(
                f"发现重复交互：user_id={user_id!r}, course_id={course_id!r}。"
            )
        scores[course_id] = score
        self.course_users.setdefault(course_id, set()).add(user_id)

    def history(self, user_id: str) -> Mapping[str, float]:
        return self.user_scores.get(user_id, {})

    def course_ids(self, user_id: str) -> set[str]:
        return set(self.history(user_id))

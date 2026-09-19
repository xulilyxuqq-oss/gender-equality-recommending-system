"""候选课程生成与先修条件过滤。"""

from __future__ import annotations

from typing import Mapping, Sequence

from .models import Course, InteractionIndex, Neighbor


def candidate_courses(
    index: InteractionIndex,
    courses: Mapping[str, Course],
    target_user_id: str,
    neighbors: Sequence[Neighbor],
    enforce_prerequisites: bool = True,
) -> tuple[str, ...]:
    """返回目标用户未交互且满足先修条件的候选课程 ID。"""

    completed = index.course_ids(target_user_id)
    candidates: set[str] = set()
    for neighbor in neighbors:
        candidates.update(index.history(neighbor.user_id))
    candidates.difference_update(completed)

    eligible: list[str] = []
    for course_id in sorted(candidates):
        course = courses.get(course_id)
        if course is None:
            continue
        if enforce_prerequisites and not course.prerequisite_ids.issubset(completed):
            continue
        eligible.append(course_id)
    return tuple(eligible)

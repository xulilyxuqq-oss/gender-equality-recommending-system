"""候选课程的邻居相似度加权评分。"""

from __future__ import annotations

from typing import Iterable, Sequence

from .models import InteractionIndex, Neighbor, ScoredCourse


def score_candidates(
    index: InteractionIndex,
    neighbors: Sequence[Neighbor],
    course_ids: Iterable[str],
) -> tuple[ScoredCourse, ...]:
    """按 Σ(similarity × score) / Σ(similarity) 计算预测分。"""

    scored: list[ScoredCourse] = []
    for course_id in course_ids:
        numerator = 0.0
        denominator = 0.0
        for neighbor in neighbors:
            score = index.history(neighbor.user_id).get(course_id)
            if score is None:
                continue
            numerator += neighbor.similarity * score
            denominator += neighbor.similarity
        if denominator > 0:
            scored.append(
                ScoredCourse(
                    course_id=course_id,
                    predicted_score=numerator / denominator,
                )
            )
    return tuple(scored)

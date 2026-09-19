"""基于课程集合的 Jaccard 近邻搜索。"""

from __future__ import annotations

import heapq
from collections import Counter
from typing import Iterator

from .models import InteractionIndex, Neighbor


def _positive_neighbors(
    index: InteractionIndex, target_user_id: str
) -> Iterator[Neighbor]:
    target_courses = index.course_ids(target_user_id)
    if not target_courses:
        return

    intersection_sizes: Counter[str] = Counter()
    for course_id in target_courses:
        for other_user_id in index.course_users.get(course_id, set()):
            if other_user_id != target_user_id:
                intersection_sizes[other_user_id] += 1

    for other_user_id, intersection_size in intersection_sizes.items():
        other_course_count = len(index.history(other_user_id))
        union_size = len(target_courses) + other_course_count - intersection_size
        if union_size > 0:
            yield Neighbor(
                user_id=other_user_id,
                similarity=intersection_size / union_size,
            )


def top_k_neighbors(
    index: InteractionIndex, target_user_id: str, k: int
) -> tuple[Neighbor, ...]:
    """返回相似度降序、用户 ID 升序的前 k 个正相似邻居。"""

    if k <= 0:
        raise ValueError("k 必须大于 0。")
    neighbors = heapq.nsmallest(
        k,
        _positive_neighbors(index, target_user_id),
        key=lambda neighbor: (-neighbor.similarity, neighbor.user_id),
    )
    return tuple(neighbors)

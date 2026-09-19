"""Jaccard KNN 基线推荐流程编排。"""

from __future__ import annotations

from typing import Mapping, Sequence

from .candidates import candidate_courses
from .config import BaselineConfig
from .models import Course, InteractionIndex, Recommendation
from .scoring import score_candidates
from .similarity import top_k_neighbors


class BaselineRecommender:
    """组合近邻搜索、候选生成和加权评分的公开模型接口。"""

    def __init__(
        self,
        courses: Mapping[str, Course],
        interactions: InteractionIndex,
        config: BaselineConfig | None = None,
    ) -> None:
        self.courses = courses
        self.interactions = interactions
        self.config = config or BaselineConfig()

    def recommend(self, user_id: str) -> tuple[Recommendation, ...]:
        neighbors = top_k_neighbors(
            self.interactions,
            user_id,
            self.config.k_neighbors,
        )
        candidate_ids = candidate_courses(
            self.interactions,
            self.courses,
            user_id,
            neighbors,
            self.config.enforce_prerequisites,
        )
        scored_courses = score_candidates(
            self.interactions,
            neighbors,
            candidate_ids,
        )
        ranked = sorted(
            scored_courses,
            key=lambda item: (-item.predicted_score, item.course_id),
        )[: self.config.top_n]

        return tuple(
            Recommendation(
                user_id=user_id,
                course_id=item.course_id,
                course_name=self.courses[item.course_id].name,
                predicted_score=item.predicted_score,
                rank=rank,
            )
            for rank, item in enumerate(ranked, start=1)
        )

    def recommend_many(
        self, user_ids: Sequence[str]
    ) -> dict[str, tuple[Recommendation, ...]]:
        return {user_id: self.recommend(user_id) for user_id in user_ids}

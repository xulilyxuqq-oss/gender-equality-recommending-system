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
        completed_courses: Mapping[str, frozenset[str] | None] | None = None,
    ) -> None:
        self.courses = courses
        self.interactions = interactions
        self.config = config or BaselineConfig()
        self.completed_courses = completed_courses or {}

    def rank_candidates(self, user_id: str) -> tuple[Recommendation, ...]:
        """返回供基线和公平重排共同使用的 Top-M 候选。"""

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
            self.completed_courses.get(user_id),
        )
        scored_courses = score_candidates(
            self.interactions,
            neighbors,
            candidate_ids,
        )
        ranked = sorted(
            scored_courses,
            key=lambda item: (-item.predicted_score, item.course_id),
        )[: self.config.candidate_size]

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

    def recommend(self, user_id: str) -> tuple[Recommendation, ...]:
        """从同一份 Top-M 候选中截取最终 Baseline Top-N。"""

        return self.rank_candidates(user_id)[: self.config.top_n]

    def recommend_many(
        self, user_ids: Sequence[str]
    ) -> dict[str, tuple[Recommendation, ...]]:
        return {user_id: self.recommend(user_id) for user_id in user_ids}

    def rank_candidates_many(
        self, user_ids: Sequence[str]
    ) -> dict[str, tuple[Recommendation, ...]]:
        return {user_id: self.rank_candidates(user_id) for user_id in user_ids}

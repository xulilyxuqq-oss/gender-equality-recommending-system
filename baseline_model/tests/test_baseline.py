from __future__ import annotations

import unittest

from recommender.baseline import BaselineRecommender
from recommender.config import BaselineConfig
from recommender.models import Course, InteractionIndex
from recommender.similarity import top_k_neighbors


class BaselineRecommenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.courses = {
            "c1": Course("c1", "基础课"),
            "c2": Course("c2", "进阶基础"),
            "c3": Course("c3", "推荐课程", frozenset({"c2"})),
            "c4": Course("c4", "先修不足", frozenset({"c9"})),
        }
        self.index = InteractionIndex()
        for user_id, course_id, score in [
            ("u1", "c1", 1.0),
            ("u1", "c2", 1.0),
            ("u2", "c1", 1.0),
            ("u2", "c2", 1.0),
            ("u2", "c3", 5.0),
            ("u3", "c1", 1.0),
            ("u3", "c4", 4.0),
        ]:
            self.index.add(user_id, course_id, score)

    def test_jaccard_neighbors_are_ranked(self) -> None:
        neighbors = top_k_neighbors(self.index, "u1", 2)
        self.assertEqual([item.user_id for item in neighbors], ["u2", "u3"])
        self.assertAlmostEqual(neighbors[0].similarity, 2 / 3)
        self.assertAlmostEqual(neighbors[1].similarity, 1 / 3)

    def test_recommendation_filters_prerequisites(self) -> None:
        model = BaselineRecommender(
            self.courses,
            self.index,
            BaselineConfig(k_neighbors=2, top_n=10),
        )
        recommendations = model.recommend("u1")
        self.assertEqual([item.course_id for item in recommendations], ["c3"])
        self.assertEqual(recommendations[0].predicted_score, 5.0)
        self.assertEqual(recommendations[0].rank, 1)

    def test_prerequisite_filter_can_be_disabled(self) -> None:
        model = BaselineRecommender(
            self.courses,
            self.index,
            BaselineConfig(
                k_neighbors=2,
                top_n=10,
                enforce_prerequisites=False,
            ),
        )
        recommendations = model.recommend("u1")
        self.assertEqual(
            [item.course_id for item in recommendations],
            ["c3", "c4"],
        )

    def test_explicit_completed_courses_control_prerequisite_eligibility(self) -> None:
        courses = {
            "c1": Course("c1", "共同课程"),
            "c2": Course("c2", "已完成但不作为偏好交互"),
            "c3": Course("c3", "高阶候选", frozenset({"c2"})),
        }
        index = InteractionIndex()
        index.add("u1", "c1", 1.0)
        index.add("u2", "c1", 1.0)
        index.add("u2", "c2", 1.0)
        index.add("u2", "c3", 5.0)

        without_profile = BaselineRecommender(courses, index).recommend("u1")
        with_profile = BaselineRecommender(
            courses,
            index,
            completed_courses={"u1": frozenset({"c2"})},
        ).recommend("u1")

        self.assertEqual([item.course_id for item in without_profile], ["c2"])
        self.assertEqual([item.course_id for item in with_profile], ["c3"])

    def test_candidate_size_and_final_top_n_are_separate(self) -> None:
        model = BaselineRecommender(
            self.courses,
            self.index,
            BaselineConfig(
                k_neighbors=2,
                candidate_size=2,
                top_n=1,
                enforce_prerequisites=False,
            ),
        )

        candidates = model.rank_candidates("u1")
        recommendations = model.recommend("u1")

        self.assertEqual([item.course_id for item in candidates], ["c3", "c4"])
        self.assertEqual([item.course_id for item in recommendations], ["c3"])

        with self.assertRaisesRegex(ValueError, "candidate_size"):
            BaselineConfig(candidate_size=5, top_n=10)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import run as fair_workflow
from recommender.fair_reranker import (
    _load_candidates,
    main as fair_main,
    rerank_min_cost,
)
from recommender.fair_scoring import regularize_candidates


def _item(user: str, course: str, score: float, rank: int) -> dict[str, object]:
    return {
        "user_id": user,
        "course_id": course,
        "course_name": course,
        "predicted_score": score,
        "rank": rank,
    }


class FairScoringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.candidates = {
            "m1": [
                _item("m1", "am", 1.0, 1),
                _item("m1", "sm", 0.8, 2),
            ],
            "f1": [
                _item("f1", "sf1", 1.0, 1),
                _item("f1", "sf2", 0.8, 2),
                _item("f1", "af", 0.79, 3),
            ],
        }
        self.genders = {"m1": 1, "f1": 2}
        self.advanced = {"am", "af"}

    def test_zero_lambda_preserves_order_and_original_scores(self) -> None:
        result, summary = regularize_candidates(
            self.candidates,
            self.genders,
            self.advanced,
            top_n=2,
            fairness_lambda=0.0,
            target_gap=0.0,
        )

        self.assertEqual(
            [row["course_id"] for row in result["f1"]],
            ["sf1", "sf2", "af"],
        )
        self.assertEqual(
            [row["predicted_score"] for row in result["f1"]],
            [1.0, 0.8, 0.79],
        )
        self.assertEqual(summary["fairness_lambda"], 0.0)
        self.assertEqual(
            self.candidates["f1"][2],
            _item("f1", "af", 0.79, 3),
        )

    def test_only_underexposed_group_advanced_courses_receive_adjustment(
        self,
    ) -> None:
        result, summary = regularize_candidates(
            self.candidates,
            self.genders,
            self.advanced,
            top_n=2,
            fairness_lambda=1.0,
            target_gap=0.0,
        )

        female = {row["course_id"]: row for row in result["f1"]}
        male = {row["course_id"]: row for row in result["m1"]}
        self.assertGreater(female["af"]["fairness_adjustment"], 0.0)
        self.assertEqual(female["sf1"]["fairness_adjustment"], 0.0)
        self.assertEqual(male["am"]["fairness_adjustment"], 0.0)
        self.assertEqual(summary["low_exposure_group"], 2)

    def test_satisfied_target_gap_adds_no_adjustment(self) -> None:
        result, summary = regularize_candidates(
            self.candidates,
            self.genders,
            self.advanced,
            top_n=2,
            fairness_lambda=1.0,
            target_gap=1.0,
        )

        self.assertTrue(
            all(
                row["fairness_adjustment"] == 0.0
                for rows in result.values()
                for row in rows
            )
        )
        self.assertEqual(summary["delta"], 0.0)

    def test_rejects_invalid_lambda(self) -> None:
        for value in (-0.1, float("nan"), float("inf"), True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "fairness_lambda"):
                    regularize_candidates(
                        self.candidates,
                        self.genders,
                        self.advanced,
                        top_n=2,
                        fairness_lambda=value,
                    )

    def test_equal_scores_use_fairness_to_break_tie_without_mutating_input(
        self,
    ) -> None:
        candidates = {
            "m1": [_item("m1", "am", 1.0, 1)],
            "f1": [
                _item("f1", "sf", 1.0, 1),
                _item("f1", "af", 1.0, 2),
            ],
        }
        common = {
            "candidates": candidates,
            "genders": {"m1": 1, "f1": 2},
            "advanced_course_ids": {"am", "af"},
            "top_n": 1,
            "target_gap": 0.0,
        }

        zero, _ = regularize_candidates(**common, fairness_lambda=0.0)
        weighted, _ = regularize_candidates(**common, fairness_lambda=1.0)

        self.assertEqual(
            [row["course_id"] for row in zero["f1"]],
            ["sf", "af"],
        )
        self.assertEqual(
            [row["course_id"] for row in weighted["f1"]],
            ["af", "sf"],
        )
        self.assertNotIn("ranking_score", candidates["f1"][1])

    def test_reranker_orders_by_ranking_score_but_costs_original_score(
        self,
    ) -> None:
        candidates = {
            "m1": [
                {**_item("m1", "am", 1.0, 1), "ranking_score": 1.0},
                {**_item("m1", "sm", 0.9, 2), "ranking_score": 0.9},
            ],
            "f1": [
                {**_item("f1", "sf1", 0.9, 1), "ranking_score": 0.8},
                {**_item("f1", "sf2", 0.8, 2), "ranking_score": 0.7},
                {**_item("f1", "af", 0.7, 3), "ranking_score": 0.95},
            ],
        }

        result, summary = rerank_min_cost(
            candidates,
            genders={"m1": 1, "f1": 2},
            advanced_course_ids={"am", "af"},
            top_n=2,
            target_gap=0.0,
        )

        self.assertEqual(result["f1"][0]["course_id"], "af")
        self.assertAlmostEqual(summary["total_swap_cost"], 0.1)

    def test_candidate_loader_accepts_regularized_ranking_order(self) -> None:
        rows = [
            {
                **_item("f1", "af", 0.7, 1),
                "ranking_score": 0.95,
            },
            {
                **_item("f1", "sf", 0.9, 2),
                "ranking_score": 0.8,
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "regularized.jsonl"
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            loaded = _load_candidates(path)

        self.assertEqual(
            [row["course_id"] for row in loaded["f1"]],
            ["af", "sf"],
        )

    def test_fair_cli_writes_regularized_candidates_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates_path = root / "candidates.jsonl"
            users_path = root / "users.jsonl"
            courses_path = root / "courses.jsonl"
            regularized_path = root / "regularized.jsonl"
            output_path = root / "fair.jsonl"
            summary_path = root / "summary.json"
            candidate_rows = [
                row
                for user_rows in self.candidates.values()
                for row in user_rows
            ]
            candidates_path.write_text(
                "".join(json.dumps(row) + "\n" for row in candidate_rows),
                encoding="utf-8",
            )
            users_path.write_text(
                '{"user_id":"m1","gender_code":1}\n'
                '{"user_id":"f1","gender_code":2}\n',
                encoding="utf-8",
            )
            courses_path.write_text(
                '{"course_id":"am","is_advanced":true}\n'
                '{"course_id":"sm","is_advanced":false}\n'
                '{"course_id":"sf1","is_advanced":false}\n'
                '{"course_id":"sf2","is_advanced":false}\n'
                '{"course_id":"af","is_advanced":true}\n',
                encoding="utf-8",
            )

            exit_code = fair_main(
                [
                    "--candidates",
                    str(candidates_path),
                    "--users",
                    str(users_path),
                    "--courses",
                    str(courses_path),
                    "--top-n",
                    "2",
                    "--target-gap",
                    "0",
                    "--fairness-lambda",
                    "1",
                    "--regularized-output",
                    str(regularized_path),
                    "--output",
                    str(output_path),
                    "--summary",
                    str(summary_path),
                ]
            )

            regularized_rows = [
                json.loads(line)
                for line in regularized_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            output_rows = [
                json.loads(line)
                for line in output_path.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertTrue(
            all("ranking_score" in row for row in regularized_rows)
        )
        final_advanced = next(
            row
            for row in output_rows
            if row["user_id"] == "f1" and row["course_id"] == "af"
        )
        self.assertEqual(final_advanced["original_rank"], 3)
        self.assertEqual(summary["regularization"]["fairness_lambda"], 1.0)
        self.assertEqual(summary["swaps_executed"], 0)

    def test_workflow_forwards_lambda_and_regularized_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            with (
                patch.object(
                    fair_workflow,
                    "baseline_main",
                    return_value=0,
                ),
                patch.object(
                    fair_workflow,
                    "fair_main",
                    return_value=0,
                ) as fair_main,
            ):
                exit_code = fair_workflow.main(
                    [
                        "--user-id",
                        "m1",
                        "--user-id",
                        "f1",
                        "--fairness-lambda",
                        "0.4",
                        "--output-dir",
                        str(output_dir),
                    ]
                )

        self.assertEqual(exit_code, 0)
        fair_args = fair_main.call_args.args[0]
        self.assertEqual(
            fair_args[fair_args.index("--fairness-lambda") + 1],
            "0.4",
        )
        self.assertEqual(
            Path(fair_args[fair_args.index("--regularized-output") + 1]),
            output_dir / "regularized_candidates.jsonl",
        )


if __name__ == "__main__":
    unittest.main()

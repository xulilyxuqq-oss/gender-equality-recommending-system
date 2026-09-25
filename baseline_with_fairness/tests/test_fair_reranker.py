from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from recommender.fair_reranker import (
    _load_candidates,
    _load_course_flags,
    main,
    rerank_min_cost,
)


def _item(user: str, course: str, score: float, rank: int) -> dict[str, object]:
    return {
        "user_id": user,
        "course_id": course,
        "course_name": course,
        "predicted_score": score,
        "rank": rank,
    }


class FairRerankerTest(unittest.TestCase):
    def test_rejects_non_finite_or_out_of_range_fairness_parameters(self) -> None:
        candidates = {
            "m1": [_item("m1", "advanced_m", 1.0, 1)],
            "f1": [_item("f1", "standard_f", 1.0, 1)],
        }
        common = {
            "candidates": candidates,
            "genders": {"m1": 1, "f1": 2},
            "advanced_course_ids": {"advanced_m"},
            "top_n": 1,
        }

        with self.assertRaisesRegex(ValueError, "target_gap"):
            rerank_min_cost(**common, target_gap=float("nan"))
        with self.assertRaisesRegex(ValueError, "target_gap"):
            rerank_min_cost(**common, target_gap=1.1)
        with self.assertRaisesRegex(ValueError, "max_total_cost"):
            rerank_min_cost(**common, max_total_cost=float("nan"))

    def test_initial_gap_within_target_needs_no_swap(self) -> None:
        candidates = {
            "m1": [_item("m1", "advanced_m", 1.0, 1)],
            "f1": [_item("f1", "standard_f", 1.0, 1)],
        }

        _, summary = rerank_min_cost(
            candidates,
            genders={"m1": 1, "f1": 2},
            advanced_course_ids={"advanced_m"},
            top_n=1,
            target_gap=1.0,
        )

        self.assertEqual(summary["swaps_executed"], 0)
        self.assertEqual(summary["stop_reason"], "target_gap_reached")

    def test_rejects_candidate_user_outside_comparison_genders(self) -> None:
        candidates = {
            "m1": [_item("m1", "advanced_m", 1.0, 1)],
            "f1": [_item("f1", "standard_f", 1.0, 1)],
            "x1": [_item("x1", "standard_x", 1.0, 1)],
        }

        with self.assertRaisesRegex(ValueError, "性别编码"):
            rerank_min_cost(
                candidates,
                genders={"m1": 1, "f1": 2, "x1": 0},
                advanced_course_ids={"advanced_m"},
                top_n=1,
            )

    def test_input_loaders_reject_ambiguous_types_and_rank_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            courses_path = root / "courses.jsonl"
            candidates_path = root / "candidates.jsonl"
            courses_path.write_text(
                '{"course_id":"c1","is_advanced":"false"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "布尔值"):
                _load_course_flags(courses_path)

            candidates_path.write_text(
                '{"user_id":"u1","course_id":"c1","course_name":"一","predicted_score":NaN,"rank":1}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "有限数值"):
                _load_candidates(candidates_path)

            candidates_path.write_text(
                '{"user_id":"u1","course_id":"c1","course_name":"一","predicted_score":0.7,"rank":1.5}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "正整数"):
                _load_candidates(candidates_path)

            candidates_path.write_text(
                '{"user_id":"u1","course_id":"c1","course_name":"一","predicted_score":0.7,"rank":1}\n'
                '{"user_id":"u1","course_id":"c2","course_name":"二","predicted_score":0.8,"rank":2}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "预测分降序"):
                _load_candidates(candidates_path)

    def test_one_minimum_cost_swap_closes_exposure_gap(self) -> None:
        candidates = {
            "m1": [_item("m1", "advanced_m", 1.0, 1), _item("m1", "standard_m", 0.9, 2)],
            "f1": [
                _item("f1", "standard_f1", 1.0, 1),
                _item("f1", "standard_f2", 0.8, 2),
                _item("f1", "advanced_f", 0.79, 3),
            ],
        }

        result, summary = rerank_min_cost(
            candidates,
            genders={"m1": 1, "f1": 2},
            advanced_course_ids={"advanced_m", "advanced_f"},
            top_n=2,
            target_gap=0.0,
        )

        self.assertEqual([row["course_id"] for row in result["m1"]], ["advanced_m", "standard_m"])
        self.assertEqual([row["course_id"] for row in result["f1"]], ["standard_f1", "advanced_f"])
        self.assertEqual(result["f1"][1]["original_rank"], 3)
        self.assertTrue(result["f1"][1]["reranked"])
        self.assertAlmostEqual(result["f1"][1]["swap_cost"], 0.01)
        self.assertEqual(summary["swaps_executed"], 1)
        self.assertEqual(summary["after_exposure"], {"1": 0.5, "2": 0.5})

    def test_global_sort_uses_lowest_cost_swap_first(self) -> None:
        candidates = {
            "m1": [_item("m1", "am1", 1.0, 1), _item("m1", "sm1", 0.8, 2)],
            "m2": [_item("m2", "am2", 1.0, 1), _item("m2", "sm2", 0.8, 2)],
            "f1": [
                _item("f1", "sf11", 1.0, 1),
                _item("f1", "sf12", 0.8, 2),
                _item("f1", "af1", 0.70, 3),
            ],
            "f2": [
                _item("f2", "sf21", 1.0, 1),
                _item("f2", "sf22", 0.8, 2),
                _item("f2", "af2", 0.79, 3),
            ],
        }

        result, summary = rerank_min_cost(
            candidates,
            genders={"m1": 1, "m2": 1, "f1": 2, "f2": 2},
            advanced_course_ids={"am1", "am2", "af1", "af2"},
            top_n=2,
            target_gap=0.25,
        )

        self.assertEqual([row["course_id"] for row in result["f1"]], ["sf11", "sf12"])
        self.assertEqual([row["course_id"] for row in result["f2"]], ["sf21", "af2"])
        self.assertEqual(summary["swap_records"][0]["user_id"], "f2")
        self.assertAlmostEqual(summary["total_swap_cost"], 0.01)

    def test_cli_reads_baseline_jsonl_and_writes_fair_top_n(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates_path = root / "candidates.jsonl"
            users_path = root / "users.jsonl"
            courses_path = root / "courses.jsonl"
            output_path = root / "fair.jsonl"
            summary_path = root / "summary.json"
            candidate_rows = [
                _item("m1", "advanced_m", 1.0, 1),
                _item("m1", "standard_m", 0.9, 2),
                _item("f1", "standard_f1", 1.0, 1),
                _item("f1", "standard_f2", 0.8, 2),
                _item("f1", "advanced_f", 0.79, 3),
            ]
            with candidates_path.open("w", encoding="utf-8") as handle:
                for row in candidate_rows:
                    handle.write(json.dumps(row) + "\n")
            users_path.write_text(
                '{"user_id":"m1","gender_code":1}\n'
                '{"user_id":"f1","gender_code":2}\n',
                encoding="utf-8",
            )
            courses_path.write_text(
                '{"course_id":"advanced_m","is_advanced":true}\n'
                '{"course_id":"advanced_f","is_advanced":true}\n'
                '{"course_id":"standard_m","is_advanced":false}\n'
                '{"course_id":"standard_f1","is_advanced":false}\n'
                '{"course_id":"standard_f2","is_advanced":false}\n',
                encoding="utf-8",
            )

            exit_code = main(
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
                    "--output",
                    str(output_path),
                    "--summary",
                    str(summary_path),
                ]
            )

            self.assertEqual(exit_code, 0)
            output_rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(output_rows), 4)
            self.assertIn("advanced_f", {row["course_id"] for row in output_rows if row["user_id"] == "f1"})
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["swaps_executed"], 1)
            self.assertEqual(summary["stop_reason"], "target_gap_reached")


if __name__ == "__main__":
    unittest.main()

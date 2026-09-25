from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from recommender.cli import main


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class BaselineCliTest(unittest.TestCase):
    def test_writes_candidate_pool_and_final_top_n_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            users = root / "users.jsonl"
            courses = root / "courses.jsonl"
            interactions = root / "interactions.jsonl"
            baseline_output = root / "baseline.jsonl"
            candidates_output = root / "candidates.jsonl"
            _write_jsonl(users, [{"user_id": user_id} for user_id in ("u1", "u2", "u3")])
            _write_jsonl(
                courses,
                [
                    {"course_id": "c1", "course_name": "共同一"},
                    {"course_id": "c2", "course_name": "共同二"},
                    {"course_id": "c3", "course_name": "候选三"},
                    {"course_id": "c4", "course_name": "候选四"},
                ],
            )
            _write_jsonl(
                interactions,
                [
                    {"user_id": "u1", "course_id": "c1", "comment": 0},
                    {"user_id": "u1", "course_id": "c2", "comment": 0},
                    {"user_id": "u2", "course_id": "c1", "comment": 0},
                    {"user_id": "u2", "course_id": "c2", "comment": 0},
                    {"user_id": "u2", "course_id": "c3", "comment": 5},
                    {"user_id": "u3", "course_id": "c1", "comment": 0},
                    {"user_id": "u3", "course_id": "c4", "comment": 4},
                ],
            )

            exit_code = main(
                [
                    "--user-id",
                    "u1",
                    "--users",
                    str(users),
                    "--courses",
                    str(courses),
                    "--interactions",
                    str(interactions),
                    "--k",
                    "2",
                    "--candidate-size",
                    "2",
                    "--top-n",
                    "1",
                    "--ignore-prerequisites",
                    "--output",
                    str(baseline_output),
                    "--candidates-output",
                    str(candidates_output),
                ]
            )

            self.assertEqual(exit_code, 0)
            baseline_rows = [json.loads(line) for line in baseline_output.read_text(encoding="utf-8").splitlines()]
            candidate_rows = [json.loads(line) for line in candidates_output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["course_id"] for row in baseline_rows], ["c3"])
            self.assertEqual([row["course_id"] for row in candidate_rows], ["c3", "c4"])


if __name__ == "__main__":
    unittest.main()

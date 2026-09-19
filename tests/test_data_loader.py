from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from recommender.data_loader import load_baseline_data


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


class DataLoaderTest(unittest.TestCase):
    def test_loads_only_target_and_potential_neighbors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            users_path = root / "users.jsonl"
            courses_path = root / "courses.jsonl"
            interactions_path = root / "interactions.jsonl"
            _write_jsonl(
                users_path,
                [
                    {"user_id": "u1"},
                    {"user_id": "u2"},
                    {"user_id": "unrelated"},
                ],
            )
            _write_jsonl(
                courses_path,
                [
                    {"course_id": "c1", "course_name": "课程一"},
                    {
                        "course_id": "c2",
                        "course_name": "课程二",
                        "prerequisites": [{"course_id": "c1"}],
                    },
                    {"course_id": "c3", "course_name": "课程三"},
                ],
            )
            _write_jsonl(
                interactions_path,
                [
                    {"user_id": "u1", "course_id": "c1", "comment": 0},
                    {"user_id": "u2", "course_id": "c1", "comment": 0},
                    {"user_id": "u2", "course_id": "c2", "comment": 5},
                    {
                        "user_id": "unrelated",
                        "course_id": "c3",
                        "comment": 4,
                    },
                ],
            )

            data = load_baseline_data(
                users_path,
                courses_path,
                interactions_path,
                ["u1"],
                implicit_score=1.5,
            )

            self.assertEqual(set(data.interactions.user_scores), {"u1", "u2"})
            self.assertEqual(data.interactions.history("u1")["c1"], 1.5)
            self.assertEqual(data.interactions.history("u2")["c2"], 5.0)
            self.assertEqual(data.courses["c2"].prerequisite_ids, frozenset({"c1"}))


if __name__ == "__main__":
    unittest.main()

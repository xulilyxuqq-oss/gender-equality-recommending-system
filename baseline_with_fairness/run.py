"""一条命令依次运行基线候选生成和公平重排。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from recommender.cli import main as baseline_main
from recommender.fair_reranker import main as fair_main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行基线模型并执行公平重排。")
    parser.add_argument("--user-id", action="append", required=True)
    parser.add_argument("--users", type=Path, default=Path("dataset/bias_demo/user_info.jsonl"))
    parser.add_argument("--courses", type=Path, default=Path("dataset/bias_demo/course_info.jsonl"))
    parser.add_argument("--interactions", type=Path, default=Path("dataset/bias_demo/user_course_interactions.jsonl"))
    parser.add_argument("--k", type=int, default=20)
    parser.add_argument("--candidate-size", type=int, default=50)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--implicit-score", type=float, default=1.0)
    parser.add_argument("--ignore-prerequisites", action="store_true")
    parser.add_argument("--target-gap", type=float, default=0.05)
    parser.add_argument("--max-total-cost", type=float)
    parser.add_argument("--output-dir", type=Path, default=Path("output/fair_workflow"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidates = args.output_dir / "baseline_candidates.jsonl"
    baseline = args.output_dir / "baseline_recommendations.jsonl"
    fair = args.output_dir / "fair_recommendations.jsonl"
    summary = args.output_dir / "fair_rerank_summary.json"
    baseline_args = [
        "--users", str(args.users),
        "--courses", str(args.courses),
        "--interactions", str(args.interactions),
        "--k", str(args.k),
        "--candidate-size", str(args.candidate_size),
        "--top-n", str(args.top_n),
        "--implicit-score", str(args.implicit_score),
        "--output", str(baseline),
        "--candidates-output", str(candidates),
    ]
    for user_id in args.user_id:
        baseline_args.extend(["--user-id", user_id])
    if args.ignore_prerequisites:
        baseline_args.append("--ignore-prerequisites")
    baseline_exit = baseline_main(baseline_args)
    if baseline_exit != 0:
        return baseline_exit
    fair_args = [
        "--candidates", str(candidates),
        "--users", str(args.users),
        "--courses", str(args.courses),
        "--top-n", str(args.top_n),
        "--target-gap", str(args.target_gap),
        "--output", str(fair),
        "--summary", str(summary),
    ]
    if args.max_total_cost is not None:
        fair_args.extend(["--max-total-cost", str(args.max_total_cost)])
    return fair_main(fair_args)


if __name__ == "__main__":
    raise SystemExit(main())

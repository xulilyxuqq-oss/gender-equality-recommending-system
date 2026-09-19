"""基线推荐模型命令行入口。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .baseline import BaselineRecommender
from .config import BaselineConfig
from .data_loader import load_baseline_data
from .models import DataValidationError
from .output import write_recommendations


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 Jaccard KNN 课程推荐基线模型。")
    parser.add_argument(
        "--user-id",
        action="append",
        required=True,
        help="目标用户 ID；可重复传入。",
    )
    parser.add_argument("--users", type=Path, default=Path("dataset/user_info.jsonl"))
    parser.add_argument("--courses", type=Path, default=Path("dataset/course_info.jsonl"))
    parser.add_argument(
        "--interactions",
        type=Path,
        default=Path("dataset/user_course_interactions.jsonl"),
    )
    parser.add_argument("--k", type=int, default=20, help="近邻数量。")
    parser.add_argument("--n", type=int, default=10, help="每位用户的推荐数量。")
    parser.add_argument(
        "--implicit-score",
        type=float,
        default=1.0,
        help="comment 为 0 时使用的隐式反馈分数。",
    )
    parser.add_argument(
        "--ignore-prerequisites",
        action="store_true",
        help="不执行先修课程过滤。",
    )
    parser.add_argument("--output", type=Path, help="输出 JSONL 路径；默认打印到终端。")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = BaselineConfig(
            k_neighbors=args.k,
            top_n=args.n,
            implicit_score=args.implicit_score,
            enforce_prerequisites=not args.ignore_prerequisites,
        )
        data = load_baseline_data(
            users_path=args.users,
            courses_path=args.courses,
            interactions_path=args.interactions,
            target_user_ids=args.user_id,
            implicit_score=config.implicit_score,
        )
        model = BaselineRecommender(data.courses, data.interactions, config)
        results = model.recommend_many(data.target_user_ids)
        write_recommendations(results, args.output)
    except (DataValidationError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0

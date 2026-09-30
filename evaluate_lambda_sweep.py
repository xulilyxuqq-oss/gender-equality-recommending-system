"""扫描公平正则权重并评估推荐结果。"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from baseline_with_fairness.recommender.baseline import BaselineRecommender
from baseline_with_fairness.recommender.config import BaselineConfig
from baseline_with_fairness.recommender.data_loader import load_course_catalog
from baseline_with_fairness.recommender.fair_reranker import rerank_min_cost
from baseline_with_fairness.recommender.fair_scoring import regularize_candidates
from baseline_with_fairness.recommender.models import InteractionIndex


FIELDNAMES = (
    "lambda",
    "stage",
    "top_k",
    "users_total",
    "users_hit",
    "recall_at_k",
    "ndcg_at_k",
    "male_users_hit",
    "male_recall_at_k",
    "male_ndcg_at_k",
    "female_users_hit",
    "female_recall_at_k",
    "female_ndcg_at_k",
    "exposure_gap_at_k",
    "total_swap_cost",
)


def select_users_and_holdouts(
    user_rows: Sequence[Mapping[str, Any]],
    interaction_rows: Sequence[Mapping[str, Any]],
    per_group: int,
    seed: int,
) -> tuple[tuple[str, ...], dict[str, int], dict[str, str]]:
    """按性别均衡抽样，并为每位用户留出一门测试课程。"""

    if isinstance(per_group, bool) or not isinstance(per_group, int) or per_group <= 0:
        raise ValueError("per_group 必须是正整数。")

    histories: dict[str, set[str]] = defaultdict(set)
    for row in interaction_rows:
        histories[str(row["user_id"])].add(str(row["course_id"]))

    genders: dict[str, int] = {}
    eligible = {1: [], 2: []}
    for row in user_rows:
        user_id = str(row["user_id"])
        gender = row["gender_code"]
        if gender in (1, 2) and len(histories[user_id]) >= 2:
            genders[user_id] = int(gender)
            eligible[int(gender)].append(user_id)

    rng = random.Random(seed)
    selected_by_group: dict[int, list[str]] = {}
    for group in (1, 2):
        population = sorted(eligible[group])
        if len(population) < per_group:
            raise ValueError(f"性别组 {group} 的合格用户不足 {per_group} 人。")
        selected_by_group[group] = rng.sample(population, per_group)

    selected = tuple(selected_by_group[1] + selected_by_group[2])
    selected_genders = {user_id: genders[user_id] for user_id in selected}
    holdouts = {
        user_id: rng.choice(sorted(histories[user_id]))
        for user_id in selected
    }
    return selected, selected_genders, holdouts

#函数内部的定义域写的是topk，topk=topn
def evaluate_rankings(
    rankings: Mapping[str, Sequence[Mapping[str, Any]]],
    holdouts: Mapping[str, str],
    genders: Mapping[str, int],
    advanced_course_ids: set[str],
    top_k: int,
) -> dict[str, float | int]:
    """计算 leave-one-out每个用户对应的一门课可能不止一个 Recall、NDCG 和群体曝光差。"""
    # 处理一下异常情况
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("所有的top_k必须是一个正整数。")

    # 初始化一些变量
    # 用户序列
    users = tuple(holdouts)
    # 命中用户的数量
    hits = {1:0, 2:0}
    # ndcg 指数， 这里使用浮点数
    ndcg = {1:0.0, 2:0.0}
    # top_n的位置
    position = {1:0, 2:0}
    # 高阶课程的数量
    advanced = {1: 0, 2:0}
    # 累计一下两个分组对应的用户数量，后续作为坟墓
    counts = {1:0 , 2:0}

    # 逐个的处理每个测试用户，每个用户实际上只保留了一个holdout课程
    for user_id in users:
        # 获取用户的性别
        group = genders.get(user_id)
        # 处理一下异常，防止用户的性别不在1，2
        if group not in (1,2):
            raise ValueError(f"用户{user_id}缺少有效性别编码。")#内联在字符串当中
        # 记录一下数量
        counts[group] += 1
        # 截取前top_n项的列表
        rows = list(rankings.get(user_id,()))[:top_k]
        # 提取一下课程id
        course_ids = [str(row["course_id"])for row in rows]
        # 统计一下用户实际获得的推荐数量
        position[group] += len(rows)
        # 计算一下高阶课程的数量
        advanced[group] += sum(
            course_ids in advanced_course_ids for course_id in course_ids
        )
        #检查一下用户的holdout是否在top_k推荐序列当中
        if holdouts[user_id] in course_ids:
            # 加1是为了将零下标开始的下标转换为1开始的下标
            rank = course_ids.index(holdouts[user_id]) + 1
            # 命中的情况下需要记录
            hits[group] += 1
            # 此时需要计算一下ndcg指标，调用数学库
            ndcg[group] += 1.0 / math.log2(rank + 1)


            # 首先我们需要计算一下分母，确保分母不会除以0
            if counts[1] == 0 or counts[2] == 0:
                # 报错，除0异常
                raise ValueError("两个性别组合必须是至少包含一位测试用户")
            if position[1] == 0 or position[2] == 0:
                # 报错，当前的推荐结果无法计算出gap
                raise ValueError("两个性别组合至少需要包含一个推荐。")
            # 获取所有的测试用户的数量，如果某个用户没有推荐的结果，由于他仍然属于测试用户的范畴，所以需要计入Recall和NDCG当中
            total_users = len(users)
            # 计算出两个性别组合的总命中人数
            total_hits = hits[1] + hits[2]
            # 返回本函数需要计算的一些总体指标
            return{
                # 全部的测试用户数量
                "user_total": total_users,
                # 有多少用户的topk推荐当中，有测试课程
                "user_hit": total_hits,
                # 计算所有测试用户的召回率
                "recall at k": total_hits / total_users,
                # 计算整体的ndcg
                "ndcg_at_k": (ndcg[1] + ndcg[2] )/ total_users,
                #性别为1的命中用户数量
                "male_users_hit": hits[1],
                #计算性别为1的用户组的Recall
                "male_recall_at_k": hits[1] / counts[1], 
                #计算性别为1的用户组的ndcg
                "male_ndcg_at_k": ndcg[1] / counts [1],
                #性别为2的命中的用户数量
                "female_users_hit":hits[2],
                #计算性别为2的用户组的Recall
                "female_recall_at_k": hits[2] / counts[2],
                #计算性别为2的用户组的ndcg
                "female_ndcg_at_k": ndcg[2] / counts [2],
                #计算一下曝光差
                "exposure_gap_at_k": abs(
                    advanced[1] / position [1] - advanced[2] / position[2]
                ),
            }

            

    


def build_training_index(
    interaction_rows: Sequence[Mapping[str, Any]],
    holdouts: Mapping[str, str],
    implicit_score: float,
) -> InteractionIndex:
    """构建移除测试真值后的训练交互索引。"""

    index = InteractionIndex()
    for row in interaction_rows:
        user_id = str(row["user_id"])
        course_id = str(row["course_id"])
        if holdouts.get(user_id) == course_id:
            continue
        comment = row.get("comment", 0)
        if isinstance(comment, bool) or not isinstance(comment, (int, float)):
            raise ValueError("comment 必须是数值。")
        score = float(comment) if float(comment) > 0 else implicit_score
        index.add(user_id, course_id, score)

    for user_id in holdouts:
        if not index.history(user_id):
            raise ValueError(f"测试用户 {user_id} 留出后没有训练交互。")
    return index


def _record(
    lambda_value: str | float,
    stage: str,
    metrics: Mapping[str, float | int],
    top_n: int,
    total_swap_cost: float,
) -> dict[str, Any]:
    return {
        "lambda": lambda_value,
        "stage": stage,
        "top_k": top_n,
        **metrics,
        "total_swap_cost": total_swap_cost,
    }

#使用lambda扫描函数，测试基线模型和不同lambda值下的公平性和推荐质量
def run_sweep(
    candidates: Mapping[str, Sequence[Mapping[str, Any]]],
    holdouts: Mapping[str, str],
    genders: Mapping[str, int],
    advanced_course_ids: set[str],
    top_n: int,
    lambda_values: Sequence[float],
    target_gap: float,
    max_total_cost: float | None,
) -> list[dict[str, Any]]:
    """复用同一候选池评估基线及多个 λ 的两个公平阶段。"""
    #评估基线推荐
    baseline = {
        user_id:[dict(row) for row in rows [:top_n]]
        for user_id, rows in candidates.items()

    }
    #记录一下基线模型的评估指标，放到output当中
    output = [
        _record(
        "", 
        "baseline", 
        holdouts,
        genders,
        advanced_course_ids,
        top_n,
    ),
    top_n,
    0.0,
    ]
    #使用for循环逐个扫描lambda，每次循环生成一条对应的记录, lambda = 0 -> 1 进行一个扫描
    for lambda_value in lambda_values:
        regularized = regularize_candidates(
            candidates,
            genders, 
            advanced_course_ids,
            top_n = top_n,
            fairness_lamda = lambda_value,
            target_gap = target_gap
        )
        # 将正则化之后的top—m列表截断成top-n
        regularized_top = {
            user_id: rows[:top_n]
            for user_id, rows in regularized.items()

        }
        #将以上的记录追加到原先的输出列表当中去,第一阶段的软正则化,第二阶段是硬约束换位
        output.append(
            #调用_record()
            _record(
                lambda_value,
                "regularized",
                evaluate_rankings(
                    regularized_top,
                    holdouts,
                    genders,
                    advanced_course_ids,
                    top_n,
                ),
                top_n,
                0.0,
            )
        )
        #进行第二阶段的处理流程
        final, summary = rerank_min_cost(
            regularized,
            genders,
            advanced_course_ids,
            top_n=top_n,
            target_gap = target_gap,
            max_total_cost = max_total_cost,
        )
        #将最终阶段的记录追加到我们的输出列表当中去
        output.append(
            _record(
                lambda_value,
                "final",
                evaluate_rankings(
                    final,
                    holdouts,
                    genders,
                    advanced_course_ids,
                    top_n,
                ),
                top_n,
                float(summary["total_swap_cost"]),
            )
        )
    #返回完整的实验记录列表，包括三个部分，第一部分是基线模型推荐，第二部分是改进模型的第一阶段，第三部分是改进模型的第一第二阶段


    


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path} 第 {line_number} 行必须是 JSON 对象。")
            rows.append(value)
    return rows


def _completed_courses(
    user_rows: Sequence[Mapping[str, Any]],
    selected: Sequence[str],
    holdouts: Mapping[str, str],
) -> dict[str, frozenset[str] | None]:
    selected_set = set(selected)
    completed: dict[str, frozenset[str] | None] = {}
    for row in user_rows:
        user_id = str(row["user_id"])
        if user_id not in selected_set:
            continue
        raw = row.get("completed_course_ids")
        if raw is None:
            completed[user_id] = None
            continue
        if not isinstance(raw, list):
            raise ValueError("completed_course_ids 必须是数组。")
        completed[user_id] = frozenset(
            str(course_id)
            for course_id in raw
            if str(course_id) != holdouts[user_id]
        )
    return completed


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="扫描 λ 并评估推荐质量与公平性。")
    parser.add_argument("--users", type=Path, default=Path("dataset/user_info.jsonl"))
    parser.add_argument("--courses", type=Path, default=Path("dataset/course_info.jsonl"))
    parser.add_argument("--interactions", type=Path, default=Path("dataset/user_course_interactions.jsonl"))
    parser.add_argument("--per-group", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--k-neighbors", type=int, default=20)
    parser.add_argument("--candidate-size", type=int, default=50)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--implicit-score", type=float, default=1.0)
    parser.add_argument("--ignore-prerequisites", action="store_true")
    parser.add_argument("--target-gap", type=float, default=0.05)
    parser.add_argument("--max-total-cost", type=float)
    parser.add_argument(
        "--lambda-values",
        type=float,
        nargs="+",
        default=[step / 10 for step in range(11)],
    )
    parser.add_argument("--output", type=Path, default=Path("output/lambda_sweep.csv"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if not args.lambda_values or any(
            not math.isfinite(value) or value < 0.0
            for value in args.lambda_values
        ):
            raise ValueError("lambda-values 必须是非空的有限非负数序列。")

        user_rows = _read_jsonl(args.users)
        interaction_rows = _read_jsonl(args.interactions)
        selected, genders, holdouts = select_users_and_holdouts(
            user_rows,
            interaction_rows,
            args.per_group,
            args.seed,
        )
        index = build_training_index(
            interaction_rows,
            holdouts,
            args.implicit_score,
        )
        courses = load_course_catalog(args.courses)
        course_rows = _read_jsonl(args.courses)
        advanced: set[str] = set()
        for row in course_rows:
            flag = row.get("is_advanced")
            if not isinstance(flag, bool):
                raise ValueError("is_advanced 必须是布尔值。")
            if flag:
                advanced.add(str(row["course_id"]))

        config = BaselineConfig(
            k_neighbors=args.k_neighbors,
            candidate_size=args.candidate_size,
            top_n=args.top_n,
            implicit_score=args.implicit_score,
            enforce_prerequisites=not args.ignore_prerequisites,
        )
        model = BaselineRecommender(
            courses,
            index,
            config,
            completed_courses=_completed_courses(
                user_rows,
                selected,
                holdouts,
            ),
        )
        candidates = {
            user_id: [
                asdict(recommendation)
                for recommendation in model.rank_candidates(user_id)
            ]
            for user_id in selected
        }
        rows = run_sweep(
            candidates,
            holdouts,
            genders,
            advanced,
            args.top_n,
            args.lambda_values,
            args.target_gap,
            args.max_total_cost,
        )
        write_csv(args.output, rows)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

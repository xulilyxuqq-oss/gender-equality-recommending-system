from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from .fair_scoring import (
    exposure_from_counts,
    group_counts,
    regularize_candidates,
)


def _ranking_score(row: Mapping[str, Any]) -> float:
    """返回公平排序分；旧候选回退到原始预测分。"""

    return float(row.get("ranking_score", row["predicted_score"]))


#最终推荐10个，待选可能是50个，top-n,top-m,gender对应性别，1代表男，2代表女，target_gap是允许的最大群体曝光差，max total cost是可选的累计分数损失上限，d二换出的课程得分 - 换入的课程得分
#输入如上
#处理流程 第一部分是计算每个性别的曝光率，第二部分是換入换出操作过程
#输出 最終的曝光率指标
def rerank_min_cost(candidates: Mapping[str, Sequence[Mapping[str, Any]]], genders: Mapping[str, int], advanced_course_ids: set[str], top_n: int = 10, target_gap: float = 0.0, max_total_cost: float | None = None) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    # 处理异常情况，如果top_n < 0, gap一定是一个大于0的浮点数，男性的曝光率处于0-1，女性的曝光率处于0-1，男性的曝光率 - 女性的曝光率 的绝对值<1, cost一定是一个大于0的浮点数，如果有异常性别值，需要抛出异常
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        raise ValueError("top_n 必须是正整数。")
    if isinstance(target_gap, bool) or not isinstance(target_gap, (int, float)) or not math.isfinite(float(target_gap)) or not 0.0 <= float(target_gap) <= 1.0:
        raise ValueError("target_gap 必须在 0 到 1 之间。")
    if max_total_cost is not None and (isinstance(max_total_cost, bool) or not isinstance(max_total_cost, (int, float)) or not math.isfinite(float(max_total_cost)) or max_total_cost < 0.0):
        raise ValueError("max_total_cost 必须是非负数。")
    #提取一下不合规的性别情况
    invalid_genders_users = sorted(user_id for user_id in candidates if genders.get(user_id) not in (1, 2))
    if invalid_genders_users:
        raise ValueError(f"用户表包含无效的性别编码：{', '.join(invalid_genders_users[:5])}")
    #整理一下top_m 候选课程列表
    ordered = {user_id: sorted((dict(row) for row in rows), key=lambda row: (int(row["rank"]), str(row["course_id"]))) for user_id, rows in candidates.items()}
    #整理一下目前的top_n推荐课程列表
    result = {user_id: [{**row, "original_rank": int(row.get("original_rank", row["rank"])), "reranked": False, "swap_cost": 0.0} for row in rows[:top_n]] for user_id, rows in ordered.items()}
    #统计一下重排之前的推荐位置和高阶数量
    total, advanced_count = group_counts(result, genders, advanced_course_ids)
    #计算重排之前的曝光率
    exposure_before = exposure_from_counts(total, advanced_count)
    #计算重排之前的曝光差
    initial_gap = abs(exposure_before["1"] - exposure_before["2"])
    #处理一下如果初始的曝光差已经小于等于目标曝光差，那么不需要进行重排，直接返回结果
    if initial_gap <= target_gap + 1e-12:
        summary = {
            "underexposure_before": None,
            "before_exposure": exposure_before,
            "after_exposure": exposure_before,
            "initial_gap": initial_gap,
            "swaps_executed": 0,
            "total_swap_cost": 0.0,
            "swap_records": [],
            "stop_reason": "target_gap_reached",
        }
        return result, summary
    #识别高阶课程曝光率较低的群体，low_group是曝光率低的群体，high_group是曝光率高的群体
    low_group = 1 if exposure_before["1"] < exposure_before["2"] else 2
    high_group = 2 if low_group == 1 else 1
    #保存所有用户的一次可行性换位
    proposals: list[dict[str, Any]] = []
    #遍历全部用户的top—m候选课程，找出高阶课程曝光率低的群体的用户，找出他们的top-m候选课程中高阶课程的排名，找出低阶课程的排名，计算换位的代价，并保存换位信息
    for user_id, rows in ordered.items():
        #只需要为低曝光用户群体生成换位即可
        if genders.get(user_id) != low_group:
            continue
        #获取当前用户的原始top-n推荐课程列表
        selected = rows[:top_n]
        #获取当前用户的候选课程列表
        remaining = rows[top_n:]
        #选择分数最高的外部课程和top-n内部的可被换出的非高阶课程
        incoming = next((row for row in remaining if str(row["course_id"]) in advanced_course_ids), None) #待换入课程
        outgoing_pool = [row for row in selected if str(row["course_id"]) not in advanced_course_ids] #待换出课程池
        #没有换入或者换出的时候，此时无法换位
        if incoming is None or not outgoing_pool:
            continue #此时只能跳过本用户，继续处理下一个用户
        #选择分数最低的可被换出的非高阶课程
        outgoing = min(outgoing_pool, key=lambda row: (float(row["predicted_score"]), str(row["course_id"])))
        #计算换位的分数损失
        cost = max(0.0, float(outgoing["predicted_score"]) - float(incoming["predicted_score"]))
        #保存换位的记录
        proposals.append({
            "user_id": user_id,
            "incoming": incoming,
            "outgoing": outgoing,
            "cost": cost
        })
    #对所有的换位提议按照分数损失、用户ID、换入课程ID、换出课程ID进行排序，优先选择分数损失最小的换位
    proposals.sort(key=lambda item: (item["cost"], str(item["user_id"]), str(item["incoming"]["course_id"]), str(item["outgoing"]["course_id"])))
    #保存实际进行的换位记录以及换位造成的总损失
    swap_records: list[dict[str, Any]] = []
    total_cost = 0.0
    #首先记录一下如果未能完成换位的原因
    stop_reason = "没有可行的换位选项" if not proposals else "已经消耗完所有的换位选项"
    #遍历所有的换位提议，尝试执行换位操作，直到达到目标曝光率差距或超过最大累计分数损失
    for proposal in proposals:
        #计算一下当前的群体曝光率
        current_gap = abs(advanced_count[1] / total[1] - advanced_count[2] / total[2])
        #如果当前的曝光率已经小于等于目标差距，那么停止换位操作
        if current_gap <= target_gap + 1e-12:
            stop_reason = "target_gap_reached"
            break
        #模拟执行一次换位之后的低曝光群体曝光率
        next_low_exposure = (advanced_count[low_group] + 1) / total[low_group]
        #模拟执行一次换位之后的高曝光群体曝光率
        next_high_exposure = advanced_count[high_group] / total[high_group]
        #计算模拟换位之后的曝光差是多少
        next_gap = abs(next_low_exposure - next_high_exposure)
        #如果换位之后的曝光差比换位之前还要高，那么实际上换位是无效的，需要跳过本次换位
        if next_gap > current_gap + 1e-12:
            stop_reason = "换位之后会导致曝光差变大不能换位"
            break
        #记录一下当前的换位损失是多少
        cost = float(proposal["cost"])
        #检查一下换位损失预算是否已经超过了最大累计分数损失，如果超过了，那么停止换位操作
        if max_total_cost is not None and total_cost + cost > max_total_cost + 1e-12:
            stop_reason = "已经超过最大累计分数损失"
            break
        #读取需要换位的用户id
        user_id = str(proposal["user_id"])
        #读取需要换出的课程id
        outgoing_id = str(proposal["outgoing"]["course_id"])
        #复制换入课程，备份之后再修改原始候选
        incoming = dict(proposal["incoming"])
        #在topn推荐列表中找到需要换出的课程，并将其替换为换入课程
        updated = [row for row in result[user_id] if str(row["course_id"]) != outgoing_id]
        updated.append({**incoming, "original_rank": int(incoming.get("original_rank", incoming["rank"])), "reranked": True, "swap_cost": cost})
        updated.sort(key=lambda row: (-_ranking_score(row), str(row["course_id"])))
        #为换位之后的列表重新生成排名
        for rank, row in enumerate(updated, start=1):
            row["rank"] = rank
        #保存一下用户的换位结果
        result[user_id] = updated
        #一次换位为低曝光群体增加了一个高阶课程的曝光
        advanced_count[low_group] += 1
        #损失累计
        total_cost += cost
        #记录换位的详细信息
        swap_records.append({
            "user_id": user_id,
            "incoming_course_id": str(incoming["course_id"]),
            "outgoing_course_id": outgoing_id,
            "swap_cost": cost,
        })
    #计算最终的群体曝光率
    exposure_after = exposure_from_counts(total, advanced_count)
    #换位执行完毕之后的最终曝光率差
    if swap_records and abs(exposure_after["1"] - exposure_after["2"]) <= target_gap + 1e-12:
        stop_reason = "target_gap_reached"
    #生成最终的摘要信息
    summary = {
        "underexposure_before": low_group,
        "before_exposure": exposure_before,
        "after_exposure": exposure_after,
        "initial_gap": initial_gap,
        "swaps_executed": len(swap_records),
        "total_swap_cost": total_cost,
        "swap_records": swap_records,
        "stop_reason": stop_reason,
    }
    #信息汇总后
    return result, summary


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


def _load_candidates(path: Path) -> dict[str, list[dict[str, Any]]]:
    required = {"user_id", "course_id", "course_name", "predicted_score", "rank"}
    grouped: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for row in _read_jsonl(path):
        missing = required - set(row)
        if missing:
            raise ValueError(f"候选记录缺少字段：{', '.join(sorted(missing))}")
        user_id = str(row["user_id"])
        course_id = str(row["course_id"])
        pair = (user_id, course_id)
        if pair in seen:
            raise ValueError(f"候选记录重复：{user_id}, {course_id}")
        seen.add(pair)
        row["user_id"] = user_id
        row["course_id"] = course_id
        score_value = row["predicted_score"]
        if isinstance(score_value, bool) or not isinstance(score_value, (int, float)) or not math.isfinite(float(score_value)):
            raise ValueError("predicted_score 必须是有限数值。")
        ranking_value = row.get("ranking_score")
        if ranking_value is not None:
            if isinstance(ranking_value, bool) or not isinstance(ranking_value, (int, float)) or not math.isfinite(float(ranking_value)):
                raise ValueError("ranking_score 必须是有限数值。")
            row["ranking_score"] = float(ranking_value)
        rank_value = row["rank"]
        if isinstance(rank_value, bool) or not isinstance(rank_value, int) or rank_value <= 0:
            raise ValueError("rank 必须是正整数。")
        row["predicted_score"] = float(score_value)
        row["rank"] = rank_value
        grouped.setdefault(user_id, []).append(row)
    for user_id, rows in grouped.items():
        ranked = sorted(rows, key=lambda row: int(row["rank"]))
        ranks = [int(row["rank"]) for row in ranked]
        if ranks != list(range(1, len(ranked) + 1)):
            raise ValueError(f"用户 {user_id} 的 rank 必须从 1 开始连续且唯一。")
        scores = [_ranking_score(row) for row in ranked]
        if any(left < right for left, right in zip(scores, scores[1:])):
            score_name = "排序分" if any("ranking_score" in row for row in ranked) else "预测分"
            raise ValueError(f"用户 {user_id} 的候选必须按{score_name}降序排列。")
    return grouped


def _load_genders(path: Path) -> dict[str, int]:
    genders: dict[str, int] = {}
    for row in _read_jsonl(path):
        if "user_id" not in row or "gender_code" not in row:
            raise ValueError("用户记录必须包含 user_id 和 gender_code。")
        gender_value = row["gender_code"]
        if isinstance(gender_value, bool) or not isinstance(gender_value, int):
            raise ValueError("gender_code 必须是整数。")
        genders[str(row["user_id"])] = gender_value
    return genders


def _load_course_flags(path: Path) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for row in _read_jsonl(path):
        if "course_id" not in row or "is_advanced" not in row:
            raise ValueError("课程记录必须包含 course_id 和 is_advanced。")
        flag_value = row["is_advanced"]
        if not isinstance(flag_value, bool):
            raise ValueError("is_advanced 必须是布尔值。")
        flags[str(row["course_id"])] = flag_value
    return flags


def _write_results(path: Path, results: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for user_id in sorted(results):
            for row in sorted(results[user_id], key=lambda item: int(item["rank"])):
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="执行公平约束下的最小代价推荐重排。")
    parser.add_argument("--candidates", type=Path, required=True, help="基线输出的 Top-M 候选 JSONL。")
    parser.add_argument("--users", type=Path, required=True, help="包含 gender_code 的用户 JSONL。")
    parser.add_argument("--courses", type=Path, required=True, help="包含 is_advanced 的课程 JSONL。")
    parser.add_argument("--top-n", type=int, default=10, help="最终每位用户保留的课程数。")
    parser.add_argument("--target-gap", type=float, default=0.05, help="允许的最大群体曝光差。")
    parser.add_argument("--fairness-lambda", type=float, default=0.0, help="候选排序中的公平正则权重。")
    parser.add_argument("--max-total-cost", type=float, help="可选的累计分数损失上限。")
    parser.add_argument("--output", type=Path, required=True, help="公平 Top-N JSONL 输出路径。")
    parser.add_argument("--regularized-output", type=Path, help="可选的正则化 Top-M 候选 JSONL 输出路径。")
    parser.add_argument("--summary", type=Path, help="可选的重排摘要 JSON 输出路径。")
    args = parser.parse_args(argv)
    try:
        candidates = _load_candidates(args.candidates)
        genders = _load_genders(args.users)
        course_flags = _load_course_flags(args.courses)
        missing_users = sorted(set(candidates) - set(genders))
        if missing_users:
            raise ValueError(f"用户表缺少候选用户：{', '.join(missing_users[:5])}")
        candidate_courses = {str(row["course_id"]) for rows in candidates.values() for row in rows}
        missing_courses = sorted(candidate_courses - set(course_flags))
        if missing_courses:
            raise ValueError(f"课程表缺少候选课程：{', '.join(missing_courses[:5])}")
        advanced = {course_id for course_id, flag in course_flags.items() if flag}
        regularized, regularization_summary = regularize_candidates(
            candidates,
            genders,
            advanced,
            top_n=args.top_n,
            fairness_lambda=args.fairness_lambda,
            target_gap=args.target_gap,
        )
        if args.regularized_output is not None:
            _write_results(args.regularized_output, regularized)
        results, summary = rerank_min_cost(regularized, genders, advanced, args.top_n, args.target_gap, args.max_total_cost)
        summary["regularization"] = regularization_summary
        _write_results(args.output, results)
        summary_text = json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        if args.summary is None:
            print(summary_text, end="")
        else:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            args.summary.write_text(summary_text, encoding="utf-8", newline="\n")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

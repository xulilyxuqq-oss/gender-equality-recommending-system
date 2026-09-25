from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


def _group_counts(lists: Mapping[str, Sequence[Mapping[str, Any]]], genders: Mapping[str, int], advanced: set[str]) -> tuple[dict[int, int], dict[int, int]]:
    total = {1: 0, 2: 0}
    advanced_count = {1: 0, 2: 0}
    for user_id, rows in lists.items():
        gender = genders.get(user_id)
        if gender not in total:
            continue
        total[gender] += len(rows)
        advanced_count[gender] += sum(str(row["course_id"]) in advanced for row in rows)
    if total[1] == 0 or total[2] == 0:
        raise ValueError("两个性别组都必须包含至少一个推荐位置。")
    return total, advanced_count


def _exposure(total: Mapping[int, int], advanced_count: Mapping[int, int]) -> dict[str, float]:
    return {str(group): advanced_count[group] / total[group] for group in (1, 2)}


def rerank_min_cost(candidates: Mapping[str, Sequence[Mapping[str, Any]]], genders: Mapping[str, int], advanced_course_ids: set[str], top_n: int = 10, target_gap: float = 0.0, max_total_cost: float | None = None) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    


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
        scores = [float(row["predicted_score"]) for row in ranked]
        if any(left < right for left, right in zip(scores, scores[1:])):
            raise ValueError(f"用户 {user_id} 的候选必须按预测分降序排列。")
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
    parser.add_argument("--max-total-cost", type=float, help="可选的累计分数损失上限。")
    parser.add_argument("--output", type=Path, required=True, help="公平 Top-N JSONL 输出路径。")
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
        results, summary = rerank_min_cost(candidates, genders, advanced, args.top_n, args.target_gap, args.max_total_cost)
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

"""基于群体曝光差的候选课程公平正则化评分。"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


def group_counts(
    lists: Mapping[str, Sequence[Mapping[str, Any]]],
    genders: Mapping[str, int],
    advanced_course_ids: set[str],
    top_n: int | None = None,
) -> tuple[dict[int, int], dict[int, int]]:
    """统计两个性别组的推荐位置数和高阶课程数。"""

    total = {1: 0, 2: 0}
    advanced_count = {1: 0, 2: 0}
    for user_id, rows in lists.items():
        gender = genders.get(user_id)
        if gender not in total:
            continue
        selected = rows if top_n is None else rows[:top_n]
        total[gender] += len(selected)
        advanced_count[gender] += sum(
            str(row["course_id"]) in advanced_course_ids for row in selected
        )
    if total[1] == 0 or total[2] == 0:
        raise ValueError("两个性别组都必须包含至少一个推荐位置。")
    return total, advanced_count


def group_exposure(
    lists: Mapping[str, Sequence[Mapping[str, Any]]],
    genders: Mapping[str, int],
    advanced_course_ids: set[str],
    top_n: int | None = None,
) -> dict[str, float]:
    """返回两个性别组的高阶课程曝光率。"""

    total, advanced_count = group_counts(
        lists,
        genders,
        advanced_course_ids,
        top_n,
    )
    return exposure_from_counts(total, advanced_count)


def exposure_from_counts(
    total: Mapping[int, int],
    advanced_count: Mapping[int, int],
) -> dict[str, float]:
    """根据已统计的推荐位置数计算两个性别组的曝光率。"""

    return {
        str(group): advanced_count[group] / total[group]
        for group in (1, 2)
    }


def regularize_candidates(
    candidates: Mapping[str, Sequence[Mapping[str, Any]]],
    genders: Mapping[str, int],
    advanced_course_ids: set[str],
    top_n: int = 10,
    fairness_lambda: float = 0.0,
    target_gap: float = 0.0,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """按相关性与群体公平收益重新排列 Top-M 候选。"""

    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        raise ValueError("top_n 必须是正整数。")
    if (
        isinstance(fairness_lambda, bool)
        or not isinstance(fairness_lambda, (int, float))
        or not math.isfinite(float(fairness_lambda))
        or fairness_lambda < 0.0
    ):
        raise ValueError("fairness_lambda 必须是有限的非负数。")
    if (
        isinstance(target_gap, bool)
        or not isinstance(target_gap, (int, float))
        or not math.isfinite(float(target_gap))
        or not 0.0 <= float(target_gap) <= 1.0
    ):
        raise ValueError("target_gap 必须在 0 到 1 之间。")

    invalid_users = sorted(
        user_id
        for user_id in candidates
        if genders.get(user_id) not in (1, 2)
    )
    if invalid_users:
        raise ValueError(
            f"用户表包含无效的性别编码：{', '.join(invalid_users[:5])}"
        )

    ordered = {
        user_id: sorted(
            (dict(row) for row in rows),
            key=lambda row: (int(row["rank"]), str(row["course_id"])),
        )
        for user_id, rows in candidates.items()
    }
    exposure_before = group_exposure(
        ordered,
        genders,
        advanced_course_ids,
        top_n,
    )
    gap_before = abs(exposure_before["1"] - exposure_before["2"])
    delta = max(0.0, gap_before - float(target_gap))
    low_group: int | None = None
    if exposure_before["1"] < exposure_before["2"]:
        low_group = 1
    elif exposure_before["2"] < exposure_before["1"]:
        low_group = 2

    result: dict[str, list[dict[str, Any]]] = {}
    for user_id, rows in ordered.items():
        scores = [float(row["predicted_score"]) for row in rows]
        minimum = min(scores) if scores else 0.0
        maximum = max(scores) if scores else 0.0
        span = maximum - minimum
        adjusted_rows: list[dict[str, Any]] = []
        for row in rows:
            score = float(row["predicted_score"])
            normalized = (score - minimum) / span if span > 0.0 else 0.0
            adjustment = 0.0
            if (
                delta > 0.0
                and genders[user_id] == low_group
                and str(row["course_id"]) in advanced_course_ids
            ):
                adjustment = float(fairness_lambda) * delta
            adjusted_rows.append(
                {
                    **row,
                    "original_rank": int(row["rank"]),
                    "normalized_score": normalized,
                    "fairness_adjustment": adjustment,
                    "ranking_score": normalized + adjustment,
                }
            )
        adjusted_rows.sort(
            key=lambda row: (
                -float(row["ranking_score"]),
                int(row["original_rank"]),
                str(row["course_id"]),
            )
        )
        for rank, row in enumerate(adjusted_rows, start=1):
            row["rank"] = rank
        result[user_id] = adjusted_rows

    exposure_after = group_exposure(
        result,
        genders,
        advanced_course_ids,
        top_n,
    )
    gap_after = abs(exposure_after["1"] - exposure_after["2"])
    changed_users = sum(
        [str(row["course_id"]) for row in ordered[user_id]]
        != [str(row["course_id"]) for row in result[user_id]]
        for user_id in ordered
    )
    summary = {
        "fairness_lambda": float(fairness_lambda),
        "target_gap": float(target_gap),
        "low_exposure_group": low_group,
        "exposure_before": exposure_before,
        "exposure_after": exposure_after,
        "gap_before": gap_before,
        "gap_after": gap_after,
        "delta": delta,
        "changed_users": changed_users,
    }
    return result, summary

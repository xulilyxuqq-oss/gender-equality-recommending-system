"""JSONL 数据校验和目标用户相关交互索引构建。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .models import Course, DataValidationError, InteractionIndex


@dataclass(frozen=True)
class LoadedData:
    courses: Mapping[str, Course]
    interactions: InteractionIndex
    target_user_ids: tuple[str, ...]
    completed_courses: Mapping[str, frozenset[str] | None]


def _iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    if not path.is_file():
        raise DataValidationError(f"找不到数据文件：{path}")

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DataValidationError(
                    f"{path} 第 {line_number} 行不是合法 JSON：{exc.msg}"
                ) from exc
            if not isinstance(value, dict):
                raise DataValidationError(
                    f"{path} 第 {line_number} 行必须是 JSON 对象。"
                )
            yield line_number, value


def _required_id(
    row: Mapping[str, Any], field: str, path: Path, line_number: int
) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(
            f"{path} 第 {line_number} 行的 {field} 必须是非空字符串。"
        )
    return value.strip()


def _interaction_ids(
    row: Mapping[str, Any], path: Path, line_number: int
) -> tuple[str, str]:
    return (
        _required_id(row, "user_id", path, line_number),
        _required_id(row, "course_id", path, line_number),
    )


def _interaction_score(
    row: Mapping[str, Any], implicit_score: float, path: Path, line_number: int
) -> float:
    value = row.get("comment")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DataValidationError(
            f"{path} 第 {line_number} 行的 comment 必须是数值。"
        )
    score = float(value)
    if not math.isfinite(score) or score < 0:
        raise DataValidationError(
            f"{path} 第 {line_number} 行的 comment 必须是有限的非负数。"
        )
    return score if score > 0 else implicit_score


def load_course_catalog(path: Path) -> dict[str, Course]:
    """读取课程元数据并提取先修课程 ID。"""

    courses: dict[str, Course] = {}
    for line_number, row in _iter_jsonl(path):
        course_id = _required_id(row, "course_id", path, line_number)
        if course_id in courses:
            raise DataValidationError(f"课程 ID 重复：{course_id!r}。")

        raw_name = row.get("course_name", "")
        if raw_name is not None and not isinstance(raw_name, str):
            raise DataValidationError(
                f"{path} 第 {line_number} 行的 course_name 必须是字符串。"
            )

        raw_prerequisites = row.get("prerequisites") or []
        if not isinstance(raw_prerequisites, list):
            raise DataValidationError(
                f"{path} 第 {line_number} 行的 prerequisites 必须是数组。"
            )

        prerequisite_ids: set[str] = set()
        for item in raw_prerequisites:
            if isinstance(item, str) and item.strip():
                prerequisite_ids.add(item.strip())
            elif isinstance(item, dict):
                prerequisite_id = item.get("course_id")
                if not isinstance(prerequisite_id, str) or not prerequisite_id.strip():
                    raise DataValidationError(
                        f"{path} 第 {line_number} 行包含无效的先修课程。"
                    )
                prerequisite_ids.add(prerequisite_id.strip())
            else:
                raise DataValidationError(
                    f"{path} 第 {line_number} 行包含无效的先修课程。"
                )

        courses[course_id] = Course(
            course_id=course_id,
            name=(raw_name or "").strip(),
            prerequisite_ids=frozenset(prerequisite_ids),
        )

    if not courses:
        raise DataValidationError(f"课程数据为空：{path}")
    return courses


def validate_target_users(path: Path, target_user_ids: Sequence[str]) -> None:
    """确认目标用户存在，不将数百万用户全部载入内存。"""

    missing = set(target_user_ids)
    for line_number, row in _iter_jsonl(path):
        user_id = _required_id(row, "user_id", path, line_number)
        missing.discard(user_id)
        if not missing:
            return
    if missing:
        joined = ", ".join(sorted(missing))
        raise DataValidationError(f"用户表中不存在目标用户：{joined}")


def load_target_completed_courses(
    path: Path,
    target_user_ids: Sequence[str],
    valid_course_ids: set[str],
) -> dict[str, frozenset[str] | None]:
    """读取目标用户独立的完成课程集合；字段缺失时返回 None 以启用旧逻辑。"""

    missing = set(target_user_ids)
    completed: dict[str, frozenset[str] | None] = {}
    for line_number, row in _iter_jsonl(path):
        user_id = _required_id(row, "user_id", path, line_number)
        if user_id not in missing:
            continue

        if "completed_course_ids" not in row or row["completed_course_ids"] is None:
            completed[user_id] = None
        else:
            raw_ids = row["completed_course_ids"]
            if not isinstance(raw_ids, list):
                raise DataValidationError(
                    f"{path} 第 {line_number} 行的 completed_course_ids 必须是数组。"
                )
            normalized: list[str] = []
            for value in raw_ids:
                if not isinstance(value, str) or not value.strip():
                    raise DataValidationError(
                        f"{path} 第 {line_number} 行包含无效完成课程 ID。"
                    )
                normalized.append(value.strip())
            if len(normalized) != len(set(normalized)):
                raise DataValidationError(
                    f"{path} 第 {line_number} 行的 completed_course_ids 包含重复值。"
                )
            unknown = sorted(set(normalized) - valid_course_ids)
            if unknown:
                raise DataValidationError(
                    f"{path} 第 {line_number} 行引用未知完成课程："
                    + ", ".join(unknown)
                )
            completed[user_id] = frozenset(normalized)

        missing.remove(user_id)
        if not missing:
            break

    if missing:
        joined = ", ".join(sorted(missing))
        raise DataValidationError(f"用户表中不存在目标用户：{joined}")
    return completed


def _target_histories(
    path: Path, target_user_ids: set[str]
) -> dict[str, set[str]]:
    histories = {user_id: set() for user_id in target_user_ids}
    for line_number, row in _iter_jsonl(path):
        user_id, course_id = _interaction_ids(row, path, line_number)
        if user_id in histories:
            histories[user_id].add(course_id)
    return histories


def _candidate_users(path: Path, shared_courses: set[str]) -> set[str]:
    candidate_ids: set[str] = set()
    for line_number, row in _iter_jsonl(path):
        user_id, course_id = _interaction_ids(row, path, line_number)
        if course_id in shared_courses:
            candidate_ids.add(user_id)
    return candidate_ids


def load_target_interactions(
    path: Path,
    target_user_ids: Sequence[str],
    valid_course_ids: set[str],
    implicit_score: float,
) -> InteractionIndex:
    """用三次顺序扫描构建目标用户及其潜在邻居的稀疏索引。"""

    targets = set(target_user_ids)
    histories = _target_histories(path, targets)
    shared_courses = set().union(*histories.values()) if histories else set()

    index = InteractionIndex()
    for target_user_id in target_user_ids:
        index.ensure_user(target_user_id)
    if not shared_courses:
        return index

    relevant_users = _candidate_users(path, shared_courses) | targets
    for line_number, row in _iter_jsonl(path):
        user_id, course_id = _interaction_ids(row, path, line_number)
        if user_id not in relevant_users:
            continue
        if course_id not in valid_course_ids:
            raise DataValidationError(
                f"{path} 第 {line_number} 行引用了未知课程 {course_id!r}。"
            )
        score = _interaction_score(row, implicit_score, path, line_number)
        index.add(user_id, course_id, score)
    return index


def load_baseline_data(
    users_path: Path,
    courses_path: Path,
    interactions_path: Path,
    target_user_ids: Sequence[str],
    implicit_score: float = 1.0,
) -> LoadedData:
    """校验三份输入并返回基线模型需要的数据对象。"""

    targets = tuple(dict.fromkeys(user_id.strip() for user_id in target_user_ids))
    if not targets or any(not user_id for user_id in targets):
        raise DataValidationError("至少需要提供一个非空目标 user_id。")
    if implicit_score <= 0:
        raise DataValidationError("implicit_score 必须大于 0。")

    courses = load_course_catalog(courses_path)
    completed_courses = load_target_completed_courses(
        users_path,
        targets,
        set(courses),
    )
    interactions = load_target_interactions(
        interactions_path,
        targets,
        set(courses),
        implicit_score,
    )
    return LoadedData(
        courses=courses,
        interactions=interactions,
        target_user_ids=targets,
        completed_courses=completed_courses,
    )

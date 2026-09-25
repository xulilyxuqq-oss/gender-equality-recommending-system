"""基线模型配置。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BaselineConfig:
    """控制近邻搜索、推荐数量和隐式反馈的配置。"""

    k_neighbors: int = 20
    candidate_size: int = 50
    top_n: int = 10
    implicit_score: float = 1.0
    enforce_prerequisites: bool = True

    def __post_init__(self) -> None:
        if self.k_neighbors <= 0:
            raise ValueError("k_neighbors 必须大于 0。")
        if self.top_n <= 0:
            raise ValueError("top_n 必须大于 0。")
        if self.candidate_size <= 0:
            raise ValueError("candidate_size 必须大于 0。")
        if self.candidate_size < self.top_n:
            raise ValueError("candidate_size 必须大于或等于 top_n。")
        if self.implicit_score <= 0:
            raise ValueError("implicit_score 必须大于 0。")

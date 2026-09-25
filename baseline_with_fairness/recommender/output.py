"""推荐结果 JSONL 序列化。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from .models import Recommendation


def _write_records(
    results: Mapping[str, Sequence[Recommendation]], handle: TextIO
) -> None:
    for recommendations in results.values():
        for recommendation in recommendations:
            handle.write(
                json.dumps(asdict(recommendation), ensure_ascii=False) + "\n"
            )


def write_recommendations(
    results: Mapping[str, Sequence[Recommendation]], output_path: Path | None
) -> None:
    """写入指定 JSONL 文件；未指定路径时输出到标准输出。"""

    if output_path is None:
        _write_records(results, sys.stdout)
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        _write_records(results, handle)

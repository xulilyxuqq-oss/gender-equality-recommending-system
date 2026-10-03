from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _boolean(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


def _number(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return min(maximum, max(minimum, value))


@dataclass(frozen=True)
class AgentSettings:
    provider: str
    enabled: bool
    api_key: str = field(repr=False)
    base_url: str
    chat_completions_path: str
    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: int
    max_retries: int

    @property
    def configured(self) -> bool:
        return self.enabled and self.provider == "zhipuai" and bool(self.api_key)

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/{self.chat_completions_path.lstrip('/')}"


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    return AgentSettings(
        provider=os.getenv("AGENT_PROVIDER", "zhipuai").strip().casefold(),
        enabled=_boolean("AGENT_LLM_ENABLED"),
        api_key=os.getenv("ZHIPUAI_API_KEY", "").strip(),
        base_url=os.getenv("ZHIPUAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4").strip(),
        chat_completions_path=os.getenv("ZHIPUAI_CHAT_COMPLETIONS_PATH", "/chat/completions").strip(),
        model=os.getenv("ZHIPUAI_MODEL", "glm-5.3-flash").strip(),
        temperature=_number("ZHIPUAI_TEMPERATURE", 0.2, 0.0, 2.0),
        max_tokens=_integer("ZHIPUAI_MAX_TOKENS", 1024, 64, 8192),
        timeout_seconds=_integer("ZHIPUAI_TIMEOUT_SECONDS", 60, 5, 300),
        max_retries=_integer("ZHIPUAI_MAX_RETRIES", 2, 0, 5),
    )

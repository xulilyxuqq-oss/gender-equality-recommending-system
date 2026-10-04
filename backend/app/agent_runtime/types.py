from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


ToolHandler = Callable[[str, str, Mapping[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class AgentContext:
    account_id: str
    session_id: str
    user_message: str
    state: str
    profile: dict[str, Any]
    profile_draft: dict[str, Any]
    pending_resolutions: list[dict[str, Any]]
    memory: dict[str, Any]
    recent_messages: list[dict[str, Any]] = field(default_factory=list)

    def to_prompt_payload(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "session_id": self.session_id,
            "state": self.state,
            "profile": self.profile,
            "profile_draft": self.profile_draft,
            "pending_resolutions": self.pending_resolutions,
            "memory": self.memory,
            "recent_messages": self.recent_messages,
            "user_message": self.user_message,
        }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    read_only: bool = True
    requires_user_confirmation: bool = False
    idempotent: bool = True

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AgentDecision:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            result["data"] = self.data
        else:
            result["error_code"] = self.error_code or "TOOL_FAILED"
            result["error_message"] = self.error_message or "工具执行失败。"
        return result

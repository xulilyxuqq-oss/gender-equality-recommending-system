from __future__ import annotations

from typing import Any

from ..repositories import ApplicationRepository
from .tools import ToolRegistry
from .types import ToolCall


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, repository: ApplicationRepository) -> None:
        self.registry = registry
        self.repository = repository

    def execute(
        self,
        account_id: str,
        session_id: str,
        run_id: str,
        step_index: int,
        call: ToolCall,
    ) -> dict[str, Any]:
        tool = self.registry.get(call.name)
        if self.repository.get_chat_session(account_id, session_id) is None:
            return {
                "ok": False,
                "error_code": "KeyError",
                "error_message": "对话不存在。",
            }
        try:
            result = tool.handler(account_id, session_id, call.arguments)
            if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
                raise ValueError("工具返回格式无效。")
        except Exception as exc:
            result = {
                "ok": False,
                "error_code": type(exc).__name__,
                "error_message": str(exc) or "工具执行失败。",
            }
        self.repository.record_agent_step(
            account_id,
            run_id,
            step_index,
            "TOOL_RESULT",
            call.name,
            call.arguments,
            result,
            "COMPLETED" if result["ok"] else "FAILED",
            idempotency_key=f"{run_id}:{call.call_id}",
        )
        return result

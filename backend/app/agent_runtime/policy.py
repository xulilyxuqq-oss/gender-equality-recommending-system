from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .types import AgentContext, RegisteredTool, ToolCall
from .tools import ToolRegistry


class PolicyViolation(ValueError):
    """A model-proposed action is not legal for the current context."""


@dataclass(frozen=True)
class UserApprovalRequired(Exception):
    call: ToolCall
    reason: str

    def __str__(self) -> str:
        return self.reason


def _string_argument(arguments: dict[str, Any], name: str, minimum: int, maximum: int) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise PolicyViolation(f"{name} 必须是文本。")
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise PolicyViolation(f"{name} 长度不合法。")
    return value


class PolicyGuard:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def validate(self, context: AgentContext, call: ToolCall) -> ToolCall:
        tool = self.registry.get(call.name)
        arguments = dict(call.arguments)
        self._validate_shape(tool, arguments)
        self._validate_state(context, tool, arguments)
        return ToolCall(call.call_id, call.name, arguments)

    def _validate_shape(self, tool: RegisteredTool, arguments: dict[str, Any]) -> None:
        if not isinstance(arguments, dict):
            raise PolicyViolation("工具参数必须是 JSON 对象。")
        allowed = set(tool.spec.parameters.get("properties", {}))
        if set(arguments) - allowed:
            raise PolicyViolation("工具包含未声明的参数。")
        for required in tool.spec.parameters.get("required", []):
            if required not in arguments:
                raise PolicyViolation(f"缺少工具参数：{required}。")
        if tool.spec.name in {"search_courses", "resolve_course", "create_course_resolution"}:
            _string_argument(arguments, "query", 1, 300)
            limit = arguments.get("limit", 5)
            if not isinstance(limit, int) or not 1 <= limit <= 50:
                raise PolicyViolation("课程查询数量不合法。")
        elif tool.spec.name == "get_course":
            _string_argument(arguments, "course_id", 1, 100)
        elif tool.spec.name == "update_draft_name":
            _string_argument(arguments, "name", 1, 80)
        elif tool.spec.name == "update_draft_gender":
            if arguments.get("gender_code") not in (1, 2):
                raise PolicyViolation("性别字段只支持 1 或 2。")
        elif tool.spec.name == "accept_course_candidate":
            _string_argument(arguments, "resolution_id", 1, 100)
            _string_argument(arguments, "course_id", 1, 100)
        elif tool.spec.name == "reject_course_candidate":
            _string_argument(arguments, "resolution_id", 1, 100)
        elif tool.spec.name == "create_recommendation":
            top_n = arguments.get("top_n", 8)
            if not isinstance(top_n, int) or not 1 <= top_n <= 50:
                raise PolicyViolation("推荐数量不合法。")

    def _validate_state(self, context: AgentContext, tool: RegisteredTool, arguments: dict[str, Any]) -> None:
        name = tool.spec.name
        if name == "update_draft_name":
            if context.state != "COLLECTING_NAME":
                raise PolicyViolation("当前不是姓名采集阶段。")
            if arguments["name"] != context.user_message.strip()[:80]:
                raise PolicyViolation("姓名必须来自用户本轮明确输入。")
        elif name == "update_draft_gender":
            if context.state != "COLLECTING_GENDER":
                raise PolicyViolation("当前不是性别采集阶段。")
            selected = context.user_message.strip()
            expected = 1 if selected in {"1", "男", "男性"} else 2 if selected in {"2", "女", "女性"} else None
            if expected != arguments["gender_code"]:
                raise PolicyViolation("用户没有明确选择性别数据组。")
        elif name == "create_course_resolution":
            if context.state not in {"COLLECTING_COURSES", "PROFILE_REVIEW"}:
                raise PolicyViolation("当前阶段不能创建课程候选。")
        elif name in {"accept_course_candidate", "reject_course_candidate"}:
            if context.state != "WAITING_COURSE_CONFIRMATION":
                raise PolicyViolation("当前没有等待确认的课程候选。")
            resolution_id = arguments["resolution_id"]
            resolution = next(
                (item for item in context.pending_resolutions if item["resolution_id"] == resolution_id), None
            )
            if resolution is None:
                raise PolicyViolation("课程候选不存在或已经处理。")
            if name == "accept_course_candidate" and not any(
                candidate["course_id"] == arguments["course_id"] for candidate in resolution["candidates"]
            ):
                raise PolicyViolation("确认的课程不在候选集合中。")
            raise UserApprovalRequired(
                ToolCall("", name, arguments),
                "课程候选需要用户明确确认后才能写入画像草稿。",
            )
        elif name == "request_profile_confirmation":
            if context.state != "PROFILE_REVIEW":
                raise PolicyViolation("画像尚未整理完成。")
        elif name == "create_recommendation":
            if context.profile["status"] != "CONFIRMED":
                raise PolicyViolation("画像尚未确认。")

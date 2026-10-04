from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from ..catalog import CourseCatalog
from ..repositories import ApplicationRepository
from .types import RegisteredTool, ToolHandler, ToolSpec


@dataclass
class ToolRegistry:
    _tools: dict[str, RegisteredTool]

    def __init__(self) -> None:
        self._tools = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._tools[spec.name] = RegisteredTool(spec, handler)

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.spec.schema() for tool in self._tools.values()]


def _query_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 300},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 5},
        },
        "required": ["query"],
        "additionalProperties": False,
    }


def _search_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 300},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
        },
        "required": [],
        "additionalProperties": False,
    }


def _course_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"course_id": {"type": "string", "minLength": 1, "maxLength": 100}},
        "required": ["course_id"],
        "additionalProperties": False,
    }


def build_course_tool_registry(services: Any) -> ToolRegistry:
    catalog: CourseCatalog = services.catalog
    repository: ApplicationRepository = services.repository
    registry = ToolRegistry()

    def search_courses(account_id: str, session_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        del account_id, session_id
        return {"ok": True, "data": {"items": catalog.search(str(arguments.get("query", "")), int(arguments.get("limit", 10)))}}

    def resolve_course(account_id: str, session_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        del account_id, session_id
        query = str(arguments["query"]).strip()
        candidates = catalog.resolve(query, int(arguments.get("limit", 5)))
        return {"ok": True, "data": {"query": query, "candidates": candidates}}

    def get_course(account_id: str, session_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        del account_id, session_id
        course = catalog.get(str(arguments["course_id"]))
        if course is None:
            return {"ok": False, "error_code": "COURSE_NOT_FOUND", "error_message": "课程不存在。"}
        return {"ok": True, "data": {"course": catalog.public_course(course), "prerequisites": course["prerequisites"]}}

    def get_profile_context(account_id: str, session_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        del session_id, arguments
        profile = repository.get_profile(account_id)
        return {"ok": True, "data": {"profile": profile}}

    def get_pending_resolutions(account_id: str, session_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        del arguments
        session = repository.get_chat_session(account_id, session_id)
        if session is None:
            return {"ok": False, "error_code": "CHAT_SESSION_NOT_FOUND", "error_message": "对话不存在。"}
        pending = [item for item in session["course_resolutions"] if item["status"] == "PENDING"]
        return {"ok": True, "data": {"items": pending}}

    registry.register(ToolSpec("search_courses", "搜索课程目录。", _search_parameters()), search_courses)
    registry.register(ToolSpec("resolve_course", "将用户课程描述匹配为候选课程。", _query_parameters()), resolve_course)
    registry.register(ToolSpec("get_course", "读取课程详情和先修课程。", _course_parameters()), get_course)
    registry.register(ToolSpec("get_profile_context", "读取当前用户画像和已确认学习历史。", {"type": "object", "properties": {}, "additionalProperties": False}), get_profile_context)
    registry.register(ToolSpec("get_pending_resolutions", "读取当前会话待确认的课程候选。", {"type": "object", "properties": {}, "additionalProperties": False}), get_pending_resolutions)
    return registry


def register_write_tools(registry: ToolRegistry, services: Any) -> None:
    catalog: CourseCatalog = services.catalog
    repository: ApplicationRepository = services.repository

    def update_draft_name(account_id: str, session_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        session = repository.patch_session_draft(
            account_id, session_id, display_name=str(arguments["name"]), state="COLLECTING_GENDER"
        )
        return {"ok": True, "data": {"profile_draft": session["profile_draft"], "state": session["state"]}}

    def update_draft_gender(account_id: str, session_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        session = repository.patch_session_draft(
            account_id, session_id, gender_code=int(arguments["gender_code"]), state="COLLECTING_COURSES"
        )
        return {"ok": True, "data": {"profile_draft": session["profile_draft"], "state": session["state"]}}

    def create_course_resolution(account_id: str, session_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = str(arguments["query"]).strip()
        resolution = repository.create_resolution(
            account_id,
            session_id,
            query,
            catalog.resolve(query, int(arguments.get("limit", 5))),
            datetime.now(UTC) + timedelta(minutes=30),
        )
        repository.patch_session_draft(
            account_id, session_id, state="WAITING_COURSE_CONFIRMATION"
        )
        return {"ok": True, "data": {"resolution": resolution}}

    def accept_course_candidate(account_id: str, session_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {"ok": True, "data": {"session": repository.decide_resolution(
            account_id, session_id, str(arguments["resolution_id"]), str(arguments["course_id"]), False
        )}}

    def reject_course_candidate(account_id: str, session_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {"ok": True, "data": {"session": repository.decide_resolution(
            account_id, session_id, str(arguments["resolution_id"]), None, True
        )}}

    def request_profile_confirmation(account_id: str, session_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        del arguments
        session = repository.get_chat_session(account_id, session_id)
        if session is None:
            return {"ok": False, "error_code": "CHAT_SESSION_NOT_FOUND", "error_message": "对话不存在。"}
        return {"ok": True, "data": {"state": session["state"], "requires_user_confirmation": True}}

    def create_recommendation(account_id: str, session_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        del session_id
        profile = repository.get_profile(account_id)
        if profile["status"] != "CONFIRMED" or not profile["user_id"]:
            return {"ok": False, "error_code": "PROFILE_NOT_CONFIRMED", "error_message": "请先确认用户画像。"}
        source, items = catalog.recommendations(profile["user_id"], int(arguments.get("top_n", 8)))
        return {"ok": True, "data": {"recommendation": repository.save_recommendation(
            account_id, profile["user_id"], profile["profile_version"], source, items
        )}}

    registry.register(ToolSpec(
        "update_draft_name", "保存用户明确提供的姓名到画像草稿。", {
            "type": "object", "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 80}},
            "required": ["name"], "additionalProperties": False,
        }, read_only=False), update_draft_name)
    registry.register(ToolSpec(
        "update_draft_gender", "保存用户明确选择的性别数据组。", {
            "type": "object", "properties": {"gender_code": {"type": "integer", "enum": [1, 2]}},
            "required": ["gender_code"], "additionalProperties": False,
        }, read_only=False), update_draft_gender)
    registry.register(ToolSpec(
        "create_course_resolution", "将用户课程描述保存为待确认候选。", _query_parameters(), read_only=False),
        create_course_resolution)
    registry.register(ToolSpec(
        "accept_course_candidate", "接受一条待确认的课程候选；需要用户明确确认。", {
            "type": "object", "properties": {
                "resolution_id": {"type": "string", "minLength": 1},
                "course_id": {"type": "string", "minLength": 1},
            }, "required": ["resolution_id", "course_id"], "additionalProperties": False,
        }, read_only=False, requires_user_confirmation=True), accept_course_candidate)
    registry.register(ToolSpec(
        "reject_course_candidate", "拒绝一条待确认的课程候选；需要用户明确拒绝。", {
            "type": "object", "properties": {"resolution_id": {"type": "string", "minLength": 1}},
            "required": ["resolution_id"], "additionalProperties": False,
        }, read_only=False, requires_user_confirmation=True), reject_course_candidate)
    registry.register(ToolSpec(
        "request_profile_confirmation", "提示用户检查并确认画像，不执行最终确认。", {
            "type": "object", "properties": {}, "additionalProperties": False,
        }), request_profile_confirmation)
    registry.register(ToolSpec(
        "create_recommendation", "在画像已确认后生成推荐快照。", {
            "type": "object", "properties": {"top_n": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8}},
            "additionalProperties": False,
        }, read_only=False), create_recommendation)

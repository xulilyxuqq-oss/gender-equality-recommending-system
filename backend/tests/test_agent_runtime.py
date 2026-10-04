from __future__ import annotations

import pytest

from backend.app.agent_runtime.context import AgentContextBuilder
from backend.app.agent_runtime.executor import ToolExecutor
from backend.app.agent_runtime.loop import AgentLoop
from backend.app.agent_runtime.policy import PolicyGuard, PolicyViolation, UserApprovalRequired
from backend.app.agent_runtime.tools import ToolRegistry, build_course_tool_registry, register_write_tools
from backend.app.agent_runtime.types import ToolCall
from backend.app.catalog import CourseCatalog
from backend.app.database import Database
from backend.app.repositories import ApplicationRepository
from backend.app.store import AuthService
from backend.tests.db_support import build_test_database


class Services:
    def __init__(self, database: Database):
        self.database = database
        self.repository = ApplicationRepository(database)
        self.auth = AuthService(self.repository, "agent-runtime-secret")
        self.catalog = CourseCatalog(database)


@pytest.fixture
def services(tmp_path):
    from pathlib import Path

    from backend.app.migrations import apply_migrations

    database = Database(build_test_database(tmp_path / "agent-runtime.sqlite3"))
    apply_migrations(database, Path(__file__).parents[1] / "migrations")
    return Services(database)


def test_context_builder_returns_bounded_durable_context(services):
    account = services.auth.create_account("context-user", "password123")
    session = services.repository.create_chat_session(account["account_id"], "RECOMMENDATION")
    context = AgentContextBuilder(services.repository, recent_message_limit=2).build(
        account["account_id"], session["chat_session_id"], "我叫小林"
    )

    assert context.state == "COLLECTING_NAME"
    assert context.profile_draft["display_name"] is None
    assert context.memory["memory_version"] == 0
    assert context.user_message == "我叫小林"
    assert len(context.recent_messages) <= 2
    assert context.to_prompt_payload()["session_id"] == session["chat_session_id"]


def test_tool_registry_exposes_typed_course_tools(services):
    registry = build_course_tool_registry(services)
    names = {schema["function"]["name"] for schema in registry.schemas()}

    assert {"search_courses", "resolve_course", "get_course", "get_profile_context", "get_pending_resolutions"} <= names
    assert registry.get("resolve_course").spec.parameters["properties"]["query"]["type"] == "string"
    with pytest.raises(KeyError):
        registry.get("unknown_tool")


def test_resolve_course_tool_uses_catalog_without_llm(services):
    account = services.auth.create_account("tool-user", "password123")
    session = services.repository.create_chat_session(account["account_id"], "RECOMMENDATION")
    registry = build_course_tool_registry(services)
    result = registry.get("resolve_course").handler(
        account["account_id"], session["chat_session_id"], {"query": "编程基础", "limit": 3}
    )

    assert result["ok"] is True
    assert result["data"]["candidates"][0]["course_id"] == "C_BASE"


def test_policy_rejects_gender_not_explicitly_present_in_user_message(services):
    account = services.auth.create_account("policy-user", "password123")
    session = services.repository.create_chat_session(account["account_id"], "RECOMMENDATION")
    services.repository.patch_session_draft(
        account["account_id"], session["chat_session_id"], display_name="小林", state="COLLECTING_GENDER"
    )
    context = AgentContextBuilder(services.repository).build(
        account["account_id"], session["chat_session_id"], "我想选一个数据组"
    )
    registry = build_course_tool_registry(services)
    registry.register(
        __import__("backend.app.agent_runtime.types", fromlist=["ToolSpec"]).ToolSpec(
            "update_draft_gender", "更新性别草稿。", {
                "type": "object",
                "properties": {"gender_code": {"type": "integer", "enum": [1, 2]}},
                "required": ["gender_code"],
                "additionalProperties": False,
            },
        ),
        lambda account_id, session_id, arguments: {"ok": True, "data": arguments},
    )

    with pytest.raises(PolicyViolation, match="用户没有明确选择"):
        PolicyGuard(registry).validate(
            context, ToolCall("call-1", "update_draft_gender", {"gender_code": 1})
        )


def test_policy_requires_approval_for_candidate_acceptance(services):
    account = services.auth.create_account("approval-user", "password123")
    session = services.repository.create_chat_session(account["account_id"], "RECOMMENDATION")
    services.repository.patch_session_draft(account["account_id"], session["chat_session_id"], display_name="A", gender_code=1)
    resolution = services.repository.create_resolution(
        account["account_id"], session["chat_session_id"], "编程基础",
        [{"course_id": "C_BASE", "match_score": 1.0, "match_reason": "EXACT_NAME"}],
        __import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    services.repository.patch_session_draft(
        account["account_id"], session["chat_session_id"], state="WAITING_COURSE_CONFIRMATION"
    )
    context = AgentContextBuilder(services.repository).build(
        account["account_id"], session["chat_session_id"], "是第一个"
    )
    registry = build_course_tool_registry(services)
    from backend.app.agent_runtime.tools import register_write_tools

    register_write_tools(registry, services)
    with pytest.raises(UserApprovalRequired):
        PolicyGuard(registry).validate(
            context,
            ToolCall("call-2", "accept_course_candidate", {
                "resolution_id": resolution["resolution_id"], "course_id": "C_BASE"
            }),
        )


def test_executor_records_a_tool_step_and_is_idempotent(services):
    account = services.auth.create_account("executor-user", "password123")
    session = services.repository.create_chat_session(account["account_id"], "RECOMMENDATION")
    message, _ = services.repository.append_message(session["chat_session_id"], "user", "查一下课程", "executor-message")
    run = services.repository.begin_agent_run(account["account_id"], session["chat_session_id"], message["message_id"])
    registry = build_course_tool_registry(services)
    executor = ToolExecutor(registry, services.repository)
    call = ToolCall("call-3", "get_profile_context", {})

    first = executor.execute(account["account_id"], session["chat_session_id"], run["run_id"], 1, call)
    second = executor.execute(account["account_id"], session["chat_session_id"], run["run_id"], 2, call)

    assert first["ok"] is True
    assert second == first
    assert len(services.repository.get_agent_run(account["account_id"], run["run_id"])["steps"]) == 1


def test_executor_does_not_run_a_tool_for_another_account(services):
    owner = services.auth.create_account("tool-owner", "password123")
    stranger = services.auth.create_account("tool-stranger", "password123")
    session = services.repository.create_chat_session(owner["account_id"], "RECOMMENDATION")
    message, _ = services.repository.append_message(session["chat_session_id"], "user", "查课程", "owner-message")
    run = services.repository.begin_agent_run(owner["account_id"], session["chat_session_id"], message["message_id"])
    registry = build_course_tool_registry(services)
    executor = ToolExecutor(registry, services.repository)

    result = executor.execute(
        stranger["account_id"], session["chat_session_id"], run["run_id"], 1,
        ToolCall("foreign-call", "get_profile_context", {}),
    )

    assert result["ok"] is False
    assert result["error_code"] == "KeyError"


def test_agent_loop_executes_tool_then_returns_final_answer(services):
    account = services.auth.create_account("loop-user", "password123")
    session = services.repository.create_chat_session(account["account_id"], "RECOMMENDATION")
    registry = build_course_tool_registry(services)
    register_write_tools(registry, services)

    decisions = iter([
        {"tool_calls": [ToolCall("call-name", "update_draft_name", {"name": "Alice"})]},
        {"text": "好的，Alice，请选择性别数据组。", "tool_calls": []},
    ])

    async def provider(context, tools):
        return next(decisions)

    runtime = AgentLoop(
        repository=services.repository,
        context_builder=AgentContextBuilder(services.repository),
        registry=registry,
        policy=PolicyGuard(registry),
        executor=ToolExecutor(registry, services.repository),
        decision_provider=provider,
    )
    result = __import__("asyncio").run(runtime.run(
        account["account_id"], session["chat_session_id"], "Alice", "loop-message"
    ))

    assert result.status == "COMPLETED"
    assert result.text == "好的，Alice，请选择性别数据组。"
    assert services.repository.get_chat_session(account["account_id"], session["chat_session_id"])["profile_draft"]["display_name"] == "Alice"
    assert services.repository.get_agent_run(account["account_id"], result.run_id)["step_count"] == 1


def test_agent_loop_can_commit_multiple_course_resolutions_from_one_decision(services):
    account = services.auth.create_account("multi-course-loop-user", "password123")
    session = services.repository.create_chat_session(account["account_id"], "RECOMMENDATION")
    services.repository.patch_session_draft(
        account["account_id"], session["chat_session_id"], display_name="Alice", gender_code=1,
        state="COLLECTING_COURSES",
    )
    registry = build_course_tool_registry(services)
    register_write_tools(registry, services)
    decisions = iter([
        {"tool_calls": [
            ToolCall("course-1", "create_course_resolution", {"query": "编程基础"}),
            ToolCall("course-2", "create_course_resolution", {"query": "数据库基础"}),
        ]},
        {"text": "我找到两门候选课程，请逐项确认。", "tool_calls": []},
    ])

    async def provider(context, tools):
        return next(decisions)

    runtime = AgentLoop(
        repository=services.repository,
        context_builder=AgentContextBuilder(services.repository),
        registry=registry,
        policy=PolicyGuard(registry),
        executor=ToolExecutor(registry, services.repository),
        decision_provider=provider,
    )
    result = __import__("asyncio").run(runtime.run(
        account["account_id"], session["chat_session_id"], "编程基础和数据库基础", "multi-course-message"
    ))

    assert result.status == "COMPLETED"
    assert len([event for event, _ in result.events if event == "course.match_required"]) == 2


def test_agent_loop_pauses_before_user_confirmed_course_write(services):
    account = services.auth.create_account("approval-loop-user", "password123")
    session = services.repository.create_chat_session(account["account_id"], "RECOMMENDATION")
    services.repository.patch_session_draft(
        account["account_id"], session["chat_session_id"], display_name="Alice", gender_code=1,
        state="WAITING_COURSE_CONFIRMATION",
    )
    resolution = services.repository.create_resolution(
        account["account_id"], session["chat_session_id"], "编程基础",
        [{"course_id": "C_BASE", "match_score": 1.0, "match_reason": "EXACT_NAME"}],
        __import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    registry = build_course_tool_registry(services)
    register_write_tools(registry, services)

    async def provider(context, tools):
        return {"tool_calls": [ToolCall(
            "call-approval", "accept_course_candidate", {
                "resolution_id": resolution["resolution_id"], "course_id": "C_BASE"
            }
        )]}

    runtime = AgentLoop(
        repository=services.repository,
        context_builder=AgentContextBuilder(services.repository),
        registry=registry,
        policy=PolicyGuard(registry),
        executor=ToolExecutor(registry, services.repository),
        decision_provider=provider,
    )
    result = __import__("asyncio").run(runtime.run(
        account["account_id"], session["chat_session_id"], "是第一个", "approval-message"
    ))

    assert result.status == "WAITING_USER"
    assert services.repository.get_chat_session(account["account_id"], session["chat_session_id"])["profile_draft"]["completed_courses"] == []


def test_agent_loop_persists_bounded_memory_and_context_window(services):
    account = services.auth.create_account("memory-loop-user", "password123")
    session = services.repository.create_chat_session(account["account_id"], "RECOMMENDATION")
    for index in range(20):
        services.repository.append_message(session["chat_session_id"], "user", f"旧消息 {index}")
        services.repository.append_message(session["chat_session_id"], "assistant", f"旧回复 {index}")
    registry = build_course_tool_registry(services)
    observed = []

    async def provider(context, tools):
        observed.append(context)
        return {"text": "我会继续帮你整理。", "tool_calls": []}

    runtime = AgentLoop(
        repository=services.repository,
        context_builder=AgentContextBuilder(services.repository, recent_message_limit=4),
        registry=registry,
        policy=PolicyGuard(registry),
        executor=ToolExecutor(registry, services.repository),
        decision_provider=provider,
    )
    result = __import__("asyncio").run(runtime.run(
        account["account_id"], session["chat_session_id"], "新消息", "memory-message"
    ))

    memory = services.repository.get_chat_memory(session["chat_session_id"])
    assert result.status == "COMPLETED"
    assert observed and len(observed[0].recent_messages) == 4
    assert len(memory["summary_text"]) <= 500
    assert memory["extracted_facts"]["session_state"] == "COLLECTING_NAME"
    assert memory["memory_version"] == 1


def test_agent_loop_uses_rules_fallback_when_provider_is_unavailable(services):
    account = services.auth.create_account("fallback-loop-user", "password123")
    session = services.repository.create_chat_session(account["account_id"], "RECOMMENDATION")
    registry = build_course_tool_registry(services)

    async def unavailable_provider(context, tools):
        return None

    def rules_fallback(account_id, session_id, message):
        updated = services.repository.patch_session_draft(
            account_id, session_id, display_name=message, state="COLLECTING_GENDER"
        )
        services.repository.append_message(session_id, "assistant", "规则式回退回复")
        return {"text": "规则式回退回复", "events": [("profile.updated", {
            "profile_draft": updated["profile_draft"], "state": updated["state"]
        })]}

    runtime = AgentLoop(
        repository=services.repository,
        context_builder=AgentContextBuilder(services.repository),
        registry=registry,
        policy=PolicyGuard(registry),
        executor=ToolExecutor(registry, services.repository),
        decision_provider=unavailable_provider,
        fallback_handler=rules_fallback,
    )
    result = __import__("asyncio").run(runtime.run(
        account["account_id"], session["chat_session_id"], "Alice", "fallback-message"
    ))

    assert result.status == "COMPLETED"
    assert result.text == "规则式回退回复"
    assert services.repository.get_chat_session(account["account_id"], session["chat_session_id"])["state"] == "COLLECTING_GENDER"
    assert services.repository.get_chat_memory(session["chat_session_id"])["extracted_facts"]["session_state"] == "COLLECTING_GENDER"


def test_agent_loop_emits_safe_progress_events_for_tools(services):
    account = services.auth.create_account("trace-loop-user", "password123")
    session = services.repository.create_chat_session(account["account_id"], "RECOMMENDATION")
    registry = build_course_tool_registry(services)
    register_write_tools(registry, services)
    decisions = iter([
        {"tool_calls": [ToolCall("trace-name", "update_draft_name", {"name": "Alice"})]},
        {"text": "Alice，请选择性别数据组。", "tool_calls": []},
    ])
    progress = []

    async def provider(context, tools):
        return next(decisions)

    async def emit(event, data):
        progress.append((event, data))

    runtime = AgentLoop(
        repository=services.repository,
        context_builder=AgentContextBuilder(services.repository),
        registry=registry,
        policy=PolicyGuard(registry),
        executor=ToolExecutor(registry, services.repository),
        decision_provider=provider,
    )
    result = __import__("asyncio").run(runtime.run(
        account["account_id"], session["chat_session_id"], "Alice", "trace-message", emit=emit
    ))

    assert result.status == "COMPLETED"
    assert [name for name, _ in progress] == ["agent.step"] * len(progress)
    assert any(item["phase"] == "tool_call" and item["tool_name"] == "update_draft_name" for _, item in progress)
    assert any(item["phase"] == "tool_result" and item["status"] == "completed" for _, item in progress)
    assert all("prompt" not in item and "arguments" not in item for _, item in progress)

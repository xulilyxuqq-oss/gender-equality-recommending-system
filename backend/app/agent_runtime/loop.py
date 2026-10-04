from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from inspect import isawaitable
from typing import Any, Awaitable, Callable

from ..agent import generate_agent_decision
from ..config import AgentSettings, get_agent_settings
from ..repositories import ApplicationRepository
from .context import AgentContextBuilder, compact_context_memory
from .executor import ToolExecutor
from .policy import PolicyGuard, PolicyViolation, UserApprovalRequired
from .tools import ToolRegistry
from .types import AgentContext, AgentDecision, ToolCall


DecisionProvider = Callable[[AgentContext, list[dict[str, Any]]], Awaitable[Any] | Any]
FallbackHandler = Callable[[str, str, str], Awaitable[dict[str, Any] | None] | dict[str, Any] | None]
ProgressEmitter = Callable[[str, dict[str, Any]], Awaitable[Any] | Any]

TOOL_LABELS = {
    "search_courses": "搜索课程目录",
    "resolve_course": "解析课程描述",
    "get_course": "读取课程详情",
    "get_profile_context": "读取当前画像",
    "get_pending_resolutions": "检查待确认候选",
    "update_draft_name": "更新姓名草稿",
    "update_draft_gender": "更新性别草稿",
    "create_course_resolution": "创建课程候选",
    "accept_course_candidate": "确认课程候选",
    "reject_course_candidate": "排除课程候选",
    "request_profile_confirmation": "检查画像确认条件",
    "create_recommendation": "生成课程推荐",
}


@dataclass(frozen=True)
class AgentRunResult:
    status: str
    run_id: str
    text: str | None = None
    events: list[tuple[str, dict[str, Any]]] | None = None
    duplicate: bool = False


def _coerce_decision(value: Any) -> AgentDecision | None:
    if isinstance(value, AgentDecision):
        return value
    if not isinstance(value, dict):
        return None
    raw_calls = value.get("tool_calls", [])
    calls: list[ToolCall] = []
    for index, raw_call in enumerate(raw_calls):
        if isinstance(raw_call, ToolCall):
            calls.append(raw_call)
        elif isinstance(raw_call, dict):
            calls.append(ToolCall(
                str(raw_call.get("call_id") or raw_call.get("id") or f"tool-{index}"),
                str(raw_call.get("name") or raw_call.get("function", {}).get("name", "")),
                dict(raw_call.get("arguments") or raw_call.get("function", {}).get("arguments") or {}),
            ))
    text = value.get("text")
    if text is not None and not isinstance(text, str):
        return None
    return AgentDecision(text=text, tool_calls=calls)


class AgentLoop:
    def __init__(
        self,
        repository: ApplicationRepository,
        context_builder: AgentContextBuilder,
        registry: ToolRegistry,
        policy: PolicyGuard,
        executor: ToolExecutor,
        decision_provider: DecisionProvider | None = None,
        fallback_handler: FallbackHandler | None = None,
        settings: AgentSettings | None = None,
    ) -> None:
        self.repository = repository
        self.context_builder = context_builder
        self.registry = registry
        self.policy = policy
        self.executor = executor
        self.decision_provider = decision_provider
        self.fallback_handler = fallback_handler
        self.settings = settings or get_agent_settings()

    async def _decide(self, context: AgentContext) -> AgentDecision | None:
        if self.decision_provider is None:
            value = await generate_agent_decision(
                context=context,
                tools=self.registry.schemas(),
                settings=self.settings,
            )
        else:
            value = self.decision_provider(context, self.registry.schemas())
            if isawaitable(value):
                value = await value
        return _coerce_decision(value)

    @staticmethod
    async def _emit_progress(
        emitter: ProgressEmitter | None, run_id: str, step: int, phase: str,
        label: str, status: str, **extra: Any,
    ) -> None:
        if emitter is None:
            return
        payload = {
            "trace_id": f"{run_id}:{step}:{phase}",
            "step": step,
            "phase": phase,
            "label": label,
            "status": status,
            **extra,
        }
        result = emitter("agent.step", payload)
        if isawaitable(result):
            await result

    def _persist_memory(self, account_id: str, session_id: str, context: AgentContext) -> None:
        summary, facts = compact_context_memory(context)
        try:
            self.repository.save_chat_memory(
                account_id, session_id, summary, facts, context.memory["memory_version"]
            )
        except ValueError:
            # A concurrent run won the memory update; canonical session facts remain authoritative.
            return

    async def _fallback(
        self, account_id: str, session_id: str, message: str, run_id: str, context: AgentContext
    ) -> AgentRunResult | None:
        if self.fallback_handler is None:
            return None
        value = self.fallback_handler(account_id, session_id, message)
        if isawaitable(value):
            value = await value
        if not isinstance(value, dict) or not isinstance(value.get("text"), str):
            return None
        fresh_context = self.context_builder.build(account_id, session_id, message)
        self._persist_memory(account_id, session_id, fresh_context)
        self.repository.finish_agent_run(account_id, run_id, "COMPLETED", "LLM_FALLBACK")
        return AgentRunResult("COMPLETED", run_id, value["text"], value.get("events", []))

    @staticmethod
    def _safe_text(text: str | None) -> str:
        if not text:
            return "我暂时无法继续处理这一步，请稍后重试。"
        return " ".join(text.split()).strip()[:160]

    @staticmethod
    def _tool_events(result: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        if not result.get("ok"):
            return []
        data = result.get("data", {})
        events: list[tuple[str, dict[str, Any]]] = []
        if "resolution" in data:
            events.append(("course.match_required", data["resolution"]))
        if "profile_draft" in data:
            events.append(("profile.updated", data))
        return events

    async def run(
        self,
        account_id: str,
        session_id: str,
        message: str,
        client_message_id: str,
        emit: ProgressEmitter | None = None,
    ) -> AgentRunResult:
        # 确保同一个会话只会开启一个agent
        user_message, run, created = self.repository.accept_agent_message(
            account_id, session_id, message, client_message_id
        )
        #判断，如果当前的会话已经存在了，那么说明当前是网络问题或者是意外关闭了浏览器，不需要重新启动新的agent
        if not created:
            return AgentRunResult(
                status="DUOLICATE",
                run_id="",
                events=[("done",{"chat_session_id":session_id, "duplicate": True})]
            )

        # 记录一下当前agent起始时间，需要控制总的运行时间，防止卡死
        started = time.monotonic()
        # 记录一下当前已经调用过的工具的数量，工具调用的总数需要有一个上界
        tool_call_used = 0
        # 暂存一下当前的业务
        events: list[tuple[str, dict[str, Any]]] = []
        # 保存当前调用的工具摘要，下一轮放入大模型的上下文，以供决策
        tool_trace: list[dict[str, Any]] = []
        # 展示当前的agent工作状态，输出当前正在工作状态
        await self._emit_progress(emit, run["run_id"], 0, "thinking", "正在理解你的输入", "running")

        # 一轮用户的输入只能最多执行max_steps次动作循环，“观察上下文，模型决策，执行动作”
        for step_index in range(1, self.settings.max_steps + 1):
            # 检查当前的agent运行时间是否超过了最大运行时间，如果超过了就直接结束
            if time.monotonic() - started > self.settings.turn_timeout_seconds:
                # 超时失败，把当前的失败原因写在数据里
                self.repository.finish_agent_run(account_id, run["run_id"], "FAILED", "AGENT_TIMEOUT")
                # 回复用户
                text = self._safe_text(None)
                # 失败回复也需要写到当前的聊天历史中，并且放入数据库
                self.repository.append_message(session_id, "assistant", text)
                # 利用sse发送结束事件
                return AgentRunResult("FAILED", run["run_id"], text, events)
            # 从数据库里面读取当前的最近会话，画像，待确认的候选以及最近的消息
            context = self.context_builder.build(account_id, session_id, message)
            # 之前执行过的工具那么就把执行的结果附加到上下文中去
            if tool_trace:
                context = replace(context, memory = {**context.memory, "last_tool_results": tool_trace[-8:]})
            # 展示一下当前模型正在决定下一轮的下一步动作
            await self._emit_progress(emit, run["run_id"], step_index, "thinking", "正在决定下一步动作", "running")
            try:
                # 调用模型，等待工具调用
                decision = await asyncio.wait_for(
                    self._decide(context), timeout = self.settings.turn_timeout_seconds 
                )
            except Exception:
                # LLM 超时的话，或者网络错误，返回异常，那么优先尝试规则回退
                fallback = await self._fallback(account_id, session_id, message, run["run_id"], context)
                if fallback is not None:
                    # 回退成功的话就直接结束本轮
                    return fallback
                # 如果当前无法回退的话，那么就记录一下当前的provider失败
                self.repository.finish_agent_run(account_id, run["run_id"], "FAILED", "AGENT_PROVIDER_FAILED")
                # 需要一些解释性说明
                text = self._safe_text(None)
                # 保存到对话历史
                self.repository.append_message(session_id, "assistant", text)
                # 返回失败的结果
                return AgentRunResult("FAILED", run["run_id"], text, events)

            # 如果没有合法的文本返回或者工具调用返回
            if decision is None:
                # 如果决策本身是无效的，那么我们仍然需要回退到规则兜底
                fallback = await self._fallback(account_id, session_id, message, run["run_id"], context)
                if fallback is not None:
                    return fallback
                # 没有回退实现的时候，记录无效决策
                self.registry.finish_agent_run(account_id, run["run_id"], "FAILED", "AGENT_INVALID_DECISION")
                # 需要返回一些兜底的文本
                text = self._safe_text(None)
                # 把兜底的文本写入对话历史
                self.repository.append_message(session_id,"assistant", text)
                # 返回失败的结果
                return AgentRunResult("FAILED", run["run_id"], text, events)
            # 如果模型返回的是最终的文本，说明现在是没有工具调用的，说明本轮已结束
            if decision.text and not decision.tool_calls:
                text = self._safe_text(decision.text)
                # 在回复用户之前还需要保存一下当前的记忆摘要
                self._persist_memory(account_id, session_id, context)
                # 把最终的回复持久化道数据库
                self.repository.append_message(session_id, "assistant", text)
                # 标记一下当前的agent run已经完成
                self.repository.finish_agent_run(account_id, run["run_id"], "COMPLETED")
                # 在前端展示当前的回复已经生成
                await self._emit_progress(emit, run["run_id"], step_index, "final", "已生成回复", "completed")
                # 最终的文本和业务事件需要交给sse
                return AgentRunResult ("COMPLETED", run["run_id"], text, events)


            # 如果既没有最终的文本，也没有工具调用，那么属于空决策
            if not decision.tool_calls:
                # 持久化空决策
                self.repository.finish_agent_run(account_id, run["run_id", "FAILED", "AGENT_EMPTY_DECISION"])
                # 把文本记录一下
                text = self._safe_text(NONE)
                # 把兜底的文本写入消息历史
                self.repository.append_message(session_id, "assistant", text)
                return AgentRunResult("FAILED", run["run_id"],text, events)

            # 一个模型返回的多个工具调用
            for call in decision.tool_calls:
                #检查一下是否超过上界
                tool_calls_used += 1
                # 如果超过上界的话，那么就需要终止
                if tool_calls_used > self.settings.max_tool_calls:
                    self.repository.finish_agent_run(account_id, run["run_id"],"FAILED", "AGNET_TOOL_LIMIT")
                    text = self.safe_text(NONE)
                    self.repository.append_message(session_id, "assistant", text)
                    # 在前端上面展示当前的工具调用已经超过限制
                    await self._emit_progress(emit, run["run_id"], step_index, "error", "工具调用次数已经达到上限","FAILED")
                    # 结束本轮
                    return AgentRunResult("FAILED", run["run_id"],text, events)
                current_context = context
                try:
                    validated = self.policy.validate(current_context, call)
                except UserApprovalRequired as approval:
                    self.repository.record_agent_step(
                        account_id, run["run_id"], tool_calls_used, "TOOL_CALL", call.name,
                        call.arguments, {"ok": False, "error_code":"USER_APPROVAL_REQUIRED"}, "Rejected",
                    )
                    # 提示一下用户，需要在课程确认卡上面进行操作
                    text = "请确认课程候选，随后会继续整理画像。"
                    # 保存一下当前的文本
                    self.repository.append_message(session_id, "assistant", text)
                    # 等待用户操作
                    self.repository.finish_agent_run(account_id, run["run_id"], "WAITING_USER")
                    # 前端显示“等待确认”轨迹
                    await self._emit_progress(emit, run["run_id"], step_index, "waiting_user", "等待你确认课程候选", "waiting", tool_name = call.name)
                    return AgentRunResult("WAITING_USER", run["run_id"], text, events)
                except (PolicyViolation, KeyError) as exc:
                    # 权限不足或者给的参数不对，那么会被policy给拒绝
                    self.repository.record_agent_step(
                        account_id, run["run_id"], tool_calls_used, "TOOL_CALL", call.name,
                        call.arguments, {"ok": False, "error_code": "POLICY_REJECTED", "error_message": str(exc)}, "REJECTED"
                    )
                    # 持久化一下当前的失败原因
                    self.repository.finish_agent_run(account_id,run["run_id"], "FAILED","POLICY_REJECTED")
                    # 失败原因
                    text = self._safe_text(None)
                    # 保存一下失败的回复
                    self.repository.append_message(session_id, "assistant", text)
                    # 通知前端本次工具调用失败
                    await self._emit_progress(emit, run["run_id"], step_index, "error", "工具调用未通过安全校验", "failed", tool_name = call.name)
                    return AgentRunResult("FAILED", run["run_id"], text, events)

                # 开始调用工具
                tool_label = TOOL_LABELS.get(call.name, "执行工具")
                # 工具执行之前, 需要在前端展示 “正在调用”
                await self._emit_progress(
                    emit, run["run_id"], step_index,"tool_call", f"正在{tool_label}", "running",
                    tool_name = call.name
                )
                result = self.executor.execute(
                    account_id, session_id, run["run_id"], tool_calls_used, validated
                )
                # 保存一下当前的工具结果
                tool_trace.append ({"tool": call.name, "argument": call.arguments, "result": result})
                # 把课程的候选， 以及画像加入结果
                events.extend(self._tool_events(result))
                # 工具执行之后，将当前的成功或者失败状态发送给前端
                await self._emit_progress(
                    emit, run["run_id"],
                    f"已完成{tool_label}" if result.get("ok") else f"{tool_label}失败"
                    "completed" if result.get("ok") else "failed", 
                    tool_name = call.name
                )

                # 工具失败的时候不能立即终止，而是让下一轮的模型决定是否换方式进行
                if not result.get("ok"):
                    continue

                # 达到最大的循环步数之后，需要尝试一下规则回退
                fallback_context = self.context_builder(account_id, session_id, message)
                fallback = await self._fallback(account_id, session_id, message, run["run_id"], fallback_context)
                if fallback is not None:
                    return fallback
                # 回退规则失败的话，标记一下当前的错误是最大的循环次数达到了
                self.repository.finish_agent_run(account_id, run["run_id"], "FAILED", "AGENT_STEP_LIMIT")
                # 返回兜底文本
                text = self._safe_text(None)
                self.repository.append_messag(session_id, "assistant", text)
                #返回最终的失败结果
                return AgentRunResult("FAILED", run["run_id"], text, events)
                                                
                

            



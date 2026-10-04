from __future__ import annotations

from typing import Any

from ..repositories import ApplicationRepository
from .types import AgentContext


class AgentContextBuilder:
    def __init__(self, repository: ApplicationRepository, recent_message_limit: int = 12) -> None:
        self.repository = repository
        self.recent_message_limit = max(1, recent_message_limit)

    def build(self, account_id: str, session_id: str, user_message: str) -> AgentContext:
        session = self.repository.get_chat_session(account_id, session_id)
        if session is None:
            raise KeyError(session_id)
        profile = self.repository.get_profile(account_id)
        memory = self.repository.get_chat_memory(session_id)
        return AgentContext(
            account_id=account_id,
            session_id=session_id,
            user_message=user_message,
            state=session["state"],
            profile={
                "display_name": profile["display_name"],
                "gender_code": profile["gender_code"],
                "completed_courses": profile["completed_courses"],
                "profile_version": profile["profile_version"],
                "status": profile["status"],
            },
            profile_draft=session["profile_draft"],
            pending_resolutions=[
                resolution
                for resolution in session["course_resolutions"]
                if resolution["status"] == "PENDING"
            ],
            memory=memory,
            recent_messages=session["messages"][-self.recent_message_limit :],
        )


def compact_context_memory(context: AgentContext) -> tuple[str, dict[str, Any]]:
    draft = context.profile_draft
    courses = draft.get("completed_courses", [])
    course_names = [str(item.get("course_name", item.get("course_id", ""))) for item in courses]
    pending_queries = [str(item.get("query", "")) for item in context.pending_resolutions]
    facts = {
        "session_state": context.state,
        "display_name": draft.get("display_name"),
        "gender_code": draft.get("gender_code"),
        "confirmed_course_ids": [item.get("course_id") for item in courses],
        "pending_queries": pending_queries,
    }
    summary = (
        f"阶段：{context.state}；姓名：{draft.get('display_name') or '未填写'}；"
        f"性别：{draft.get('gender_code') or '未填写'}；"
        f"已整理课程：{'、'.join(course_names) or '暂无'}；"
        f"待确认描述：{'、'.join(pending_queries) or '无'}"
    )
    return summary[:500], facts

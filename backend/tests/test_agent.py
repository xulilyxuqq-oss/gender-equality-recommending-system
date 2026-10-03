from __future__ import annotations

import asyncio
import json

import httpx

from backend.app.agent import generate_agent_reply
from backend.app.config import AgentSettings


def settings(**overrides) -> AgentSettings:
    values = {
        "provider": "zhipuai",
        "enabled": True,
        "api_key": "secret-for-test",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "chat_completions_path": "/chat/completions",
        "model": "glm-5.3-flash",
        "temperature": 0.2,
        "max_tokens": 256,
        "timeout_seconds": 10,
        "max_retries": 0,
    }
    values.update(overrides)
    return AgentSettings(**values)


def test_zhipu_chat_completion_request_and_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        assert request.headers["Authorization"] == "Bearer secret-for-test"
        body = json.loads(request.content)
        assert body["model"] == "glm-5.3-flash"
        assert body["stream"] is False
        assert body["messages"][0]["role"] == "system"
        assert "后端指令" in body["messages"][1]["content"]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "好的，请描述你学过的课程。"}}]},
        )

    reply = asyncio.run(
        generate_agent_reply(
            state="COLLECTING_COURSES",
            user_message="女",
            authoritative_reply="已记录。现在请描述你学过的课程，可以一次说多门。",
            settings=settings(),
            transport=httpx.MockTransport(handler),
        )
    )
    assert reply == "好的，请描述你学过的课程。"


def test_zhipu_failure_returns_rules_fallback_signal():
    transport = httpx.MockTransport(lambda request: httpx.Response(503, json={"error": "unavailable"}))
    reply = asyncio.run(
        generate_agent_reply(
            state="COLLECTING_NAME",
            user_message="你好",
            authoritative_reply="请告诉我你的姓名。",
            settings=settings(),
            transport=transport,
        )
    )
    assert reply is None


def test_agent_is_not_called_without_api_key():
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    reply = asyncio.run(
        generate_agent_reply(
            state="COLLECTING_NAME",
            user_message="你好",
            authoritative_reply="请告诉我你的姓名。",
            settings=settings(api_key=""),
            transport=httpx.MockTransport(handler),
        )
    )
    assert reply is None
    assert called is False

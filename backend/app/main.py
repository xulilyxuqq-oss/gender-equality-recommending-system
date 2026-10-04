from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, AsyncIterator

import jwt
from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from .agent import generate_agent_reply
from .admin import AdminService, build_admin_router
from .agent_runtime.context import AgentContextBuilder
from .agent_runtime.executor import ToolExecutor
from .agent_runtime.loop import AgentLoop, AgentRunResult
from .agent_runtime.policy import PolicyGuard
from .agent_runtime.tools import build_course_tool_registry, register_write_tools
from .catalog import CourseCatalog
from .config import get_agent_settings, get_app_settings
from .database import Database, validate_core_schema
from .migrations import apply_migrations
from .repositories import AcceptedChatTurn, ApplicationRepository, ChatTransition
from .store import AuthService, new_id, now


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AppServices:
    database: Database
    repository: ApplicationRepository
    auth: AuthService
    catalog: CourseCatalog
    agent_runtime: AgentLoop | None = None
    admin: AdminService | None = None


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def error_payload(request: Request, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {},
                      "request_id": getattr(request.state, "request_id", new_id("req"))}}


async def request_context(request: Request, call_next):
    request.state.request_id = new_id("req")
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content=error_payload(request, exc.code, exc.message, exc.details))


async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content=jsonable_encoder(
        error_payload(request, "VALIDATION_ERROR", "请求字段不符合要求。", {"issues": exc.errors()})))


async def database_error_handler(request: Request, exc: Exception):
    if isinstance(exc, sqlite3.IntegrityError):
        return JSONResponse(status_code=409, content=error_payload(
            request, "DATA_CONFLICT", "数据状态冲突，请刷新后重试。"))
    return JSONResponse(status_code=503, headers={"Retry-After": "1"}, content=error_payload(
        request, "DATABASE_UNAVAILABLE", "数据库暂时不可用，请稍后重试。"))


router = APIRouter()


def get_services(request: Request) -> AppServices:
    return request.app.state.services


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=72)


class RefreshRequest(BaseModel):
    refresh_token: str


class ProfilePatch(BaseModel):
    profile_version: int = Field(ge=0)
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    gender_code: int | None = Field(default=None)


class ProfileConfirm(BaseModel):
    profile_version: int = Field(ge=0)
    chat_session_id: str


class DraftProfilePatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    gender_code: int | None = None


class SessionCreate(BaseModel):
    purpose: str = "RECOMMENDATION"


class MessageCreate(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    client_message_id: str = Field(min_length=1, max_length=100)


class ResolveRequest(BaseModel):
    chat_session_id: str
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=5, ge=1, le=5)


class ResolutionDecision(BaseModel):
    course_id: str | None = None
    rejected: bool | None = None

    @model_validator(mode="after")
    def validate_choice(self):
        if bool(self.course_id) == bool(self.rejected):
            raise ValueError("course_id 和 rejected 必须二选一")
        return self


class RecommendationCreate(BaseModel):
    profile_version: int = Field(ge=0)
    top_n: int = Field(default=10, ge=1, le=50)


def auth_response(services: AppServices, account: dict[str, Any]) -> dict[str, Any]:
    return {"account": services.auth.public_account(account), **services.auth.issue_tokens(account["account_id"])}


def account_id_from_header(authorization: str | None = Header(default=None),
                           services: AppServices = Depends(get_services)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise AppError(401, "AUTH_REQUIRED", "请先登录。")
    try:
        return services.auth.decode_access_token(authorization.removeprefix("Bearer ").strip())
    except jwt.ExpiredSignatureError as exc:
        raise AppError(401, "TOKEN_EXPIRED", "登录状态已过期，请重新登录。") from exc
    except jwt.InvalidTokenError as exc:
        raise AppError(401, "AUTH_REQUIRED", "登录凭据无效。") from exc


def public_profile(profile: dict[str, Any]) -> dict[str, Any]:
    return {key: profile[key] for key in (
        "display_name", "gender_code", "completed_courses", "profile_version", "status", "confirmed_at")}


def session_for(services: AppServices, account_id: str, session_id: str) -> dict[str, Any]:
    session = services.repository.get_chat_session(account_id, session_id)
    if session is None:
        raise AppError(404, "CHAT_SESSION_NOT_FOUND", "对话不存在。")
    return session


def public_session(session: dict[str, Any]) -> dict[str, Any]:
    return {key: session[key] for key in (
        "chat_session_id", "state", "profile_version", "profile_draft", "messages", "course_resolutions", "created_at")}


def version_conflict(services: AppServices, account_id: str, received: int) -> AppError:
    profile = services.repository.get_profile(account_id)
    return AppError(409, "PROFILE_VERSION_CONFLICT", "用户画像已被更新，请刷新后重试。",
                    {"expected_version": profile["profile_version"], "received_version": received})


def domain_error(exc: ValueError, services: AppServices, account_id: str, version: int = 0) -> AppError:
    if str(exc) == "profile version conflict":
        return version_conflict(services, account_id, version)
    errors = {
        "pending course resolutions": (409, "PENDING_COURSE_RESOLUTIONS", "请先处理全部课程匹配。"),
        "profile incomplete": (409, "PROFILE_INCOMPLETE", "姓名或性别尚未填写完整。"),
        "invalid draft course": (404, "COURSE_NOT_FOUND", "课程不存在。"),
        "invalid display name": (422, "VALIDATION_ERROR", "姓名不能为空。"),
        "invalid gender code": (422, "VALIDATION_ERROR", "性别字段只支持 1 或 2。"),
        "resolution expired": (409, "RESOLUTION_EXPIRED", "课程候选已过期，请重新描述。"),
        "resolution already finalized": (409, "RESOLUTION_ALREADY_FINALIZED", "该课程描述已经处理。"),
        "invalid resolution candidate": (400, "INVALID_RESOLUTION_CANDIDATE", "确认的课程不在候选集合中。"),
    }
    if str(exc) not in errors:
        raise exc
    return AppError(*errors[str(exc)])


def create_resolution(services: AppServices, account_id: str, session_id: str,
                      query: str, limit: int = 5) -> dict[str, Any]:
    return services.repository.create_resolution(
        account_id, session_id, query, services.catalog.resolve(query, limit), now() + timedelta(minutes=30))


def split_course_descriptions(message: str) -> list[str]:
    cleaned = re.sub(r"^(我)?(还)?学过", "", message.strip())
    parts = re.split(r"(?:、|，|,|；|;|以及|还有|和)", cleaned)
    return [part.strip(" 。.!！") for part in parts if part.strip(" 。.!！")][:8]


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()))
    except (ValueError, UnicodeDecodeError):
        raise AppError(400, "INVALID_CURSOR", "分页游标无效。")


def page_response(items: list[dict[str, Any]], has_more: bool, offset: int, limit: int) -> dict[str, Any]:
    return {"items": items, "next_cursor": encode_cursor(offset + limit) if has_more else None, "has_more": has_more}


@router.get("/api/v1/health")
def health(services: AppServices = Depends(get_services)) -> dict[str, Any]:
    with services.database.connect() as connection:
        connection.execute("SELECT 1").fetchone()
        version = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
    agent_settings = get_agent_settings()
    return {"status": "ok", "storage": "sqlite", "database_check": "ok", "database_schema_version": version,
            "agent_mode": "zhipuai" if agent_settings.configured else "rules_fallback",
            "agent_model": agent_settings.model if agent_settings.configured else "rules"}


@router.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: Credentials, services: AppServices = Depends(get_services)):
    if not payload.username.strip():
        raise AppError(422, "VALIDATION_ERROR", "用户名不能为空。")
    try:
        account = services.auth.create_account(payload.username.strip(), payload.password)
    except ValueError as exc:
        if str(exc) != "duplicate username":
            raise
        raise AppError(409, "USERNAME_EXISTS", "该用户名已被使用。") from exc
    return auth_response(services, account)


@router.post("/api/v1/auth/login")
def login(payload: Credentials, services: AppServices = Depends(get_services)):
    account = services.auth.authenticate(payload.username.strip(), payload.password)
    if not account:
        raise AppError(401, "AUTH_INVALID_CREDENTIALS", "用户名或密码错误。")
    return auth_response(services, account)


@router.post("/api/v1/auth/refresh")
def refresh(payload: RefreshRequest, services: AppServices = Depends(get_services)):
    tokens = services.auth.refresh(payload.refresh_token)
    if tokens is None:
        raise AppError(401, "TOKEN_EXPIRED", "刷新令牌无效或已过期。")
    account_id = services.auth.decode_access_token(tokens["access_token"])
    account = services.repository.get_account(account_id)
    return {"account": services.auth.public_account(account), **tokens}


@router.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshRequest, services: AppServices = Depends(get_services)):
    services.auth.logout(payload.refresh_token)
    return Response(status_code=204)


@router.get("/api/v1/me/profile")
def get_profile(account_id: str = Depends(account_id_from_header), services: AppServices = Depends(get_services)):
    return public_profile(services.repository.get_profile(account_id))


@router.patch("/api/v1/me/profile")
def patch_profile(payload: ProfilePatch, account_id: str = Depends(account_id_from_header),
                  services: AppServices = Depends(get_services)):
    profile = services.repository.get_profile(account_id)
    if payload.profile_version != profile["profile_version"]:
        raise version_conflict(services, account_id, payload.profile_version)
    if payload.gender_code is not None and payload.gender_code not in (1, 2):
        raise AppError(422, "VALIDATION_ERROR", "性别字段只支持 1 或 2。")
    name = payload.display_name.strip() if payload.display_name is not None else None
    if name == "":
        raise AppError(422, "VALIDATION_ERROR", "姓名不能为空。")
    try:
        return public_profile(services.repository.patch_profile(account_id, payload.profile_version, name, payload.gender_code))
    except ValueError as exc:
        raise domain_error(exc, services, account_id, payload.profile_version) from exc


@router.get("/api/v1/me/completed-courses")
def completed_courses(account_id: str = Depends(account_id_from_header), services: AppServices = Depends(get_services)):
    return {"items": services.repository.get_profile(account_id)["completed_courses"]}


@router.post("/api/v1/me/profile/confirm")
def confirm_profile(payload: ProfileConfirm, account_id: str = Depends(account_id_from_header),
                    services: AppServices = Depends(get_services)):
    session_for(services, account_id, payload.chat_session_id)
    try:
        return public_profile(services.repository.confirm_profile(account_id, payload.chat_session_id, payload.profile_version))
    except ValueError as exc:
        raise domain_error(exc, services, account_id, payload.profile_version) from exc


@router.get("/api/v1/courses")
def list_courses(q: str = "", cursor: str | None = None, limit: int = Query(default=20, ge=1, le=50),
                 account_id: str = Depends(account_id_from_header), services: AppServices = Depends(get_services)):
    offset = decode_cursor(cursor)
    results = services.catalog.search(q, offset + limit + 1)[offset:]
    return page_response(results[:limit], len(results) > limit, offset, limit)


@router.get("/api/v1/courses/{course_id}")
def course_details(course_id: str, account_id: str = Depends(account_id_from_header),
                   services: AppServices = Depends(get_services)):
    course = services.catalog.get(course_id)
    if not course:
        raise AppError(404, "COURSE_NOT_FOUND", "课程不存在。")
    completed = {item["course_id"] for item in services.repository.get_profile(account_id)["completed_courses"]}
    favorites, _ = services.repository.list_favorites(account_id, 0, len(services.catalog.courses))
    return {**services.catalog.public_course(course), "advanced_label_rule": course["advanced_label_rule"],
            "prerequisites": course["prerequisites"],
            "prerequisites_satisfied": all(item["course_id"] in completed for item in course["prerequisites"]),
            "is_favorite": any(item["course"]["course_id"] == course_id for item in favorites)}


@router.post("/api/v1/courses/resolve", status_code=status.HTTP_201_CREATED)
def resolve_course(payload: ResolveRequest, account_id: str = Depends(account_id_from_header),
                   services: AppServices = Depends(get_services)):
    session_for(services, account_id, payload.chat_session_id)
    return create_resolution(services, account_id, payload.chat_session_id, payload.query, payload.limit)


@router.post("/api/v1/chat/sessions", status_code=status.HTTP_201_CREATED)
def create_session(payload: SessionCreate, account_id: str = Depends(account_id_from_header),
                   services: AppServices = Depends(get_services)):
    if payload.purpose != "RECOMMENDATION":
        raise AppError(400, "INVALID_SESSION_PURPOSE", "暂不支持该会话类型。")
    return public_session(services.repository.create_chat_session(account_id, payload.purpose))


@router.get("/api/v1/chat/sessions/{chat_session_id}")
def get_session(chat_session_id: str, account_id: str = Depends(account_id_from_header),
                services: AppServices = Depends(get_services)):
    return public_session(session_for(services, account_id, chat_session_id))


@router.patch("/api/v1/chat/sessions/{chat_session_id}/profile-draft")
def patch_profile_draft(chat_session_id: str, payload: DraftProfilePatch,
                        account_id: str = Depends(account_id_from_header), services: AppServices = Depends(get_services)):
    session_for(services, account_id, chat_session_id)
    try:
        return public_session(services.repository.patch_session_draft(
            account_id, chat_session_id, payload.display_name, payload.gender_code))
    except ValueError as exc:
        raise domain_error(exc, services, account_id) from exc


@router.delete("/api/v1/chat/sessions/{chat_session_id}/profile-draft/completed-courses/{course_id}",
               status_code=status.HTTP_200_OK)
def remove_draft_course(chat_session_id: str, course_id: str, account_id: str = Depends(account_id_from_header),
                        services: AppServices = Depends(get_services)):
    session_for(services, account_id, chat_session_id)
    return public_session(services.repository.remove_session_course(account_id, chat_session_id, course_id))


def sse_event(sequence: int, event: str, data: dict[str, Any]) -> str:
    return f"id: evt_{sequence:06d}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def prepare_chat_transition(catalog: CourseCatalog, session: dict[str, Any], content: str) -> ChatTransition:
    """Plan from the repository's locked session snapshot using only cached metadata."""
    message = content.strip()
    state = session["state"]
    name = session["profile_draft"]["display_name"]
    gender = session["profile_draft"]["gender_code"]
    profile_event = None
    resolutions = []
    if state == "COLLECTING_NAME":
        name, state = message[:80], "COLLECTING_GENDER"
        response_text = f"好的，{message[:80]}。请选择性别数据组，系统不会根据姓名推断。"
        profile_event = "profile.updated"
    elif state == "COLLECTING_GENDER":
        selected_gender = 1 if message in {"1", "男", "男性"} else 2 if message in {"2", "女", "女性"} else None
        if selected_gender is None:
            response_text = "请明确选择男或女。这个字段只用于当前数据集与公平算法。"
        else:
            gender, state = selected_gender, "COLLECTING_COURSES"
            response_text = "已记录。现在请描述你学过的课程，可以一次说多门。"
            profile_event = "profile.updated"
    elif state in {"COLLECTING_COURSES", "PROFILE_REVIEW"}:
        if any(term in message for term in ("没有学过", "没有其他", "暂时没有", "跳过课程", "完成课程")):
            if any(item["status"] == "PENDING" for item in session["course_resolutions"]):
                response_text = "还有课程候选尚未处理，请先确认或选择都不是。"
            else:
                state = "PROFILE_REVIEW"
                response_text = "课程信息已经整理完成，请检查画像后确认生成推荐。"
                profile_event = "profile.ready"
        else:
            resolutions = [(query, catalog.resolve(query)) for query in split_course_descriptions(message)]
            state = "WAITING_COURSE_CONFIRMATION"
            response_text = f"我识别出 {len(resolutions)} 项课程描述，请逐项核对候选。"
    elif state == "WAITING_COURSE_CONFIRMATION":
        response_text = "请先使用课程确认卡处理全部候选。"
    else:
        response_text = "本轮画像已经确认，可以查看推荐结果或创建新会话。"
    return ChatTransition(state, name, gender, response_text, profile_event, resolutions)


def prepare_stream_frames(turn: AcceptedChatTurn, response_text: str) -> list[str]:
    session, transition = turn.session, turn.transition
    session_id = session["chat_session_id"]
    if transition is None:
        return [sse_event(turn.event_sequences[0], "done", {"chat_session_id": session_id, "duplicate": True})]
    midpoint = max(1, len(response_text) // 2)
    events = [("message.delta", {"text": chunk})
              for chunk in (response_text[:midpoint], response_text[midpoint:]) if chunk]
    events.extend(("course.match_required", record) for record in turn.resolutions)
    if transition.profile_event:
        events.append((transition.profile_event, {"profile_draft": session["profile_draft"], "state": session["state"]}))
    events.append(("done", {"chat_session_id": session_id}))
    return [sse_event(sequence, event, data) for sequence, (event, data) in zip(turn.event_sequences, events)]


async def stream_reply(frames: list[str]) -> AsyncIterator[str]:
    """All persistence and event formatting finish before response headers are sent."""
    for frame in frames:
        yield frame
        await asyncio.sleep(0.03)


async def stream_agent_reply(
    services: AppServices,
    account_id: str,
    session_id: str,
    message: str,
    client_message_id: str,
) -> AsyncIterator[str]:
    """Stream safe Agent progress first, then the final assistant response."""
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()
    outcome: dict[str, Any] = {}

    async def emit(event: str, data: dict[str, Any]) -> None:
        await queue.put((event, data))

    async def run_agent() -> None:
        try:
            outcome["result"] = await services.agent_runtime.run(
                account_id, session_id, message, client_message_id, emit=emit
            )
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            await queue.put(None)

    task = asyncio.create_task(run_agent())
    while True:
        item = await queue.get()
        if item is None:
            break
        event, data = item
        sequence = await run_in_threadpool(
            services.repository.next_event_sequence, account_id, session_id
        )
        yield sse_event(sequence, event, data)

    await task
    if "error" in outcome:
        raise outcome["error"]
    result: AgentRunResult = outcome["result"]
    if result.duplicate:
        for event, data in result.events or [("done", {"chat_session_id": session_id, "duplicate": True})]:
            sequence = await run_in_threadpool(
                services.repository.next_event_sequence, account_id, session_id
            )
            yield sse_event(sequence, event, data)
        return
    if result.text:
        midpoint = max(1, len(result.text) // 2)
        for chunk in (result.text[:midpoint], result.text[midpoint:]):
            if chunk:
                sequence = await run_in_threadpool(
                    services.repository.next_event_sequence, account_id, session_id
                )
                yield sse_event(sequence, "message.delta", {"text": chunk})
                await asyncio.sleep(0.03)
    for event, data in result.events or []:
        sequence = await run_in_threadpool(
            services.repository.next_event_sequence, account_id, session_id
        )
        yield sse_event(sequence, event, data)
    sequence = await run_in_threadpool(
        services.repository.next_event_sequence, account_id, session_id
    )
    yield sse_event(sequence, "done", {"chat_session_id": session_id})


def prepare_agent_stream_frames(
    services: AppServices, account_id: str, session_id: str, result: AgentRunResult
) -> list[str]:
    if result.duplicate:
        events = result.events or [("done", {"chat_session_id": session_id, "duplicate": True})]
    else:
        events: list[tuple[str, dict[str, Any]]] = []
        if result.text:
            midpoint = max(1, len(result.text) // 2)
            events.extend(
                ("message.delta", {"text": chunk})
                for chunk in (result.text[:midpoint], result.text[midpoint:])
                if chunk
            )
        events.extend(result.events or [])
        events.append(("done", {"chat_session_id": session_id}))
    sequences = [services.repository.next_event_sequence(account_id, session_id) for _ in events]
    return [sse_event(sequence, event, data) for sequence, (event, data) in zip(sequences, events)]


def autonomous_runtime_enabled(services: AppServices) -> bool:
    runtime = services.agent_runtime
    settings = get_agent_settings()
    return runtime is not None and settings.mode == "autonomous"


@router.post("/api/v1/chat/sessions/{chat_session_id}/messages:stream")
async def send_message(chat_session_id: str, payload: MessageCreate, account_id: str = Depends(account_id_from_header),
                       services: AppServices = Depends(get_services)):
    if autonomous_runtime_enabled(services):
        return StreamingResponse(
            stream_agent_reply(
                services, account_id, chat_session_id, payload.message, payload.client_message_id
            ),
            media_type="text/event-stream",
        )
    try:
        turn = await run_in_threadpool(services.repository.accept_chat_message,
            account_id, chat_session_id, payload.message, payload.client_message_id,
            lambda current: prepare_chat_transition(services.catalog, current, payload.message),
        )
    except KeyError as exc:
        raise AppError(404, "CHAT_SESSION_NOT_FOUND", "对话不存在。") from exc
    response_text = turn.transition.reply if turn.transition is not None else ""
    if turn.transition is not None:
        generated_reply = await generate_agent_reply(state=turn.session["state"], user_message=payload.message.strip(),
                                                     authoritative_reply=response_text)
        if generated_reply and generated_reply != response_text:
            try:
                await run_in_threadpool(services.repository.update_assistant_reply,
                    account_id, chat_session_id, turn.assistant_message["message_id"], generated_reply)
            except (sqlite3.Error, OSError) as exc:
                # The accepted fallback and event IDs already committed together.
                logger.warning("回复润色未能保存，使用已持久化的规则回复。error_type=%s", type(exc).__name__)
            else:
                response_text = generated_reply
    frames = prepare_stream_frames(turn, response_text)
    return StreamingResponse(stream_reply(frames), media_type="text/event-stream")


@router.post("/api/v1/chat/sessions/{chat_session_id}/course-resolutions/{resolution_id}")
def decide_resolution(chat_session_id: str, resolution_id: str, payload: ResolutionDecision,
                      account_id: str = Depends(account_id_from_header), services: AppServices = Depends(get_services)):
    session_for(services, account_id, chat_session_id)
    try:
        return public_session(services.repository.decide_resolution(
            account_id, chat_session_id, resolution_id, payload.course_id, bool(payload.rejected)))
    except KeyError as exc:
        raise AppError(404, "RESOLUTION_NOT_FOUND", "课程解析不存在。") from exc
    except ValueError as exc:
        raise domain_error(exc, services, account_id) from exc


@router.post("/api/v1/recommendations", status_code=status.HTTP_201_CREATED)
def create_recommendation(payload: RecommendationCreate, account_id: str = Depends(account_id_from_header),
                          services: AppServices = Depends(get_services)):
    profile = services.repository.get_profile(account_id)
    if profile["status"] != "CONFIRMED":
        raise AppError(409, "PROFILE_NOT_CONFIRMED", "请先确认用户画像。")
    if payload.profile_version != profile["profile_version"]:
        raise version_conflict(services, account_id, payload.profile_version)
    source, items = services.catalog.recommendations(profile["user_id"], payload.top_n)
    fairness_policy_version = None
    if services.admin is not None:
        items, fairness_policy_version = services.admin.apply_active_policy(items, profile["gender_code"])
    try:
        return services.repository.save_recommendation(
            account_id, profile["user_id"], profile["profile_version"], source, items,
            fairness_policy_version=fairness_policy_version,
        )
    except ValueError as exc:
        raise domain_error(exc, services, account_id, payload.profile_version) from exc


@router.get("/api/v1/recommendations")
def list_recommendations(cursor: str | None = None, limit: int = Query(default=20, ge=1, le=50),
                         account_id: str = Depends(account_id_from_header), services: AppServices = Depends(get_services)):
    offset = decode_cursor(cursor)
    items, has_more = services.repository.list_recommendations(account_id, offset, limit)
    return page_response(items, has_more, offset, limit)


@router.get("/api/v1/recommendations/{recommendation_id}")
def get_recommendation(recommendation_id: str, account_id: str = Depends(account_id_from_header),
                       services: AppServices = Depends(get_services)):
    record = services.repository.get_recommendation(account_id, recommendation_id)
    if record is None:
        raise AppError(404, "RECOMMENDATION_NOT_FOUND", "推荐记录不存在。")
    return record


@router.delete("/api/v1/recommendations/{recommendation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recommendation(recommendation_id: str, account_id: str = Depends(account_id_from_header),
                          services: AppServices = Depends(get_services)):
    try:
        services.repository.soft_delete_recommendation(account_id, recommendation_id, reject_foreign=True)
    except KeyError as exc:
        raise AppError(404, "RECOMMENDATION_NOT_FOUND", "推荐记录不存在。") from exc
    return Response(status_code=204)


@router.get("/api/v1/favorites")
def list_favorites(cursor: str | None = None, limit: int = Query(default=20, ge=1, le=50),
                   account_id: str = Depends(account_id_from_header), services: AppServices = Depends(get_services)):
    offset = decode_cursor(cursor)
    items, has_more = services.repository.list_favorites(account_id, offset, limit)
    return page_response(items, has_more, offset, limit)


@router.put("/api/v1/favorites/{course_id}")
def add_favorite(course_id: str, account_id: str = Depends(account_id_from_header),
                  services: AppServices = Depends(get_services)):
    try:
        return services.repository.add_favorite(account_id, course_id)
    except KeyError as exc:
        raise AppError(404, "COURSE_NOT_FOUND", "课程不存在。") from exc


@router.delete("/api/v1/favorites/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_favorite(course_id: str, account_id: str = Depends(account_id_from_header),
                     services: AppServices = Depends(get_services)):
    services.repository.remove_favorite(account_id, course_id)
    return Response(status_code=204)


def create_app(database_path: Path | str | None = None, jwt_secret: str | None = None) -> FastAPI:
    settings = get_app_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        database = Database(Path(database_path) if database_path is not None else settings.database_path)
        validate_core_schema(database)
        apply_migrations(database, Path(__file__).resolve().parents[1] / "migrations")
        with database.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL").fetchone()
        repository = ApplicationRepository(database)
        auth = AuthService(repository, jwt_secret if jwt_secret is not None else settings.jwt_secret)
        catalog = CourseCatalog(database)
        base_services = AppServices(database, repository, auth, catalog)
        tool_registry = build_course_tool_registry(base_services)
        register_write_tools(tool_registry, base_services)

        def rules_fallback(account_id: str, session_id: str, message: str) -> dict[str, Any]:
            current = repository.get_chat_session(account_id, session_id)
            if current is None:
                raise KeyError(session_id)
            transition = prepare_chat_transition(catalog, current, message)
            committed = repository.commit_agent_transition(account_id, session_id, transition)
            events: list[tuple[str, dict[str, Any]]] = [
                ("course.match_required", resolution) for resolution in committed["resolutions"]
            ]
            if transition.profile_event:
                events.append((transition.profile_event, {
                    "profile_draft": committed["session"]["profile_draft"],
                    "state": committed["session"]["state"],
                }))
            return {"text": transition.reply, "events": events}

        agent_runtime = AgentLoop(
            repository=repository,
            context_builder=AgentContextBuilder(repository),
            registry=tool_registry,
            policy=PolicyGuard(tool_registry),
            executor=ToolExecutor(tool_registry, repository),
            fallback_handler=rules_fallback,
        )
        admin_service = AdminService(database, jwt_secret if jwt_secret is not None else settings.jwt_secret, catalog)
        admin_service.ensure_seed(settings.admin_username, settings.admin_password, settings.admin_display_name)
        services = AppServices(database, repository, auth, catalog, agent_runtime, admin_service)
        if repository.get_account_by_username("course_demo") is None:
            try:
                auth.create_account("course_demo", "demo1234")
            except ValueError as exc:
                # Concurrent workers may race to seed; repository uniqueness protects the winner.
                if str(exc) != "duplicate username":
                    raise
        application.state.services = services
        try:
            yield
        finally:
            del application.state.services

    application = FastAPI(title="Course Compass API", version="0.1.0", lifespan=lifespan)
    application.add_middleware(CORSMiddleware,
                               allow_origins=["http://127.0.0.1:4173", "http://localhost:4173"],
                               allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    application.middleware("http")(request_context)
    application.add_exception_handler(AppError, app_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.add_exception_handler(sqlite3.Error, database_error_handler)
    application.add_exception_handler(FileNotFoundError, database_error_handler)
    application.include_router(router)
    application.include_router(build_admin_router(get_services, AppError))
    return application


app = create_app()

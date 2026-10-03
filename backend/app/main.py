from __future__ import annotations

import asyncio
import base64
import copy
import json
import re
from datetime import timedelta
from typing import Any, AsyncIterator

import jwt
from fastapi import Depends, FastAPI, Header, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from .agent import generate_agent_reply
from .catalog import catalog
from .config import get_agent_settings
from .store import iso, new_id, now, store


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: dict[str, Any] | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def error_payload(request: Request, code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": getattr(request.state, "request_id", new_id("req")),
        }
    }


app = FastAPI(title="Course Compass API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:4173", "http://localhost:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = new_id("req")
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(request, exc.code, exc.message, exc.details),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(error_payload(request, "VALIDATION_ERROR", "请求字段不符合要求。", {"issues": exc.errors()})),
    )


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


def auth_response(account: dict[str, Any]) -> dict[str, Any]:
    return {"account": store.public_account(account), **store.issue_tokens(account["account_id"])}


def account_id_from_header(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise AppError(401, "AUTH_REQUIRED", "请先登录。")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return store.decode_access_token(token)
    except jwt.ExpiredSignatureError as exc:
        raise AppError(401, "TOKEN_EXPIRED", "登录状态已过期，请重新登录。") from exc
    except jwt.InvalidTokenError as exc:
        raise AppError(401, "AUTH_REQUIRED", "登录凭据无效。") from exc


def profile_for(account_id: str) -> dict[str, Any]:
    return copy.deepcopy(store.profiles[account_id])


def session_for(account_id: str, session_id: str) -> dict[str, Any]:
    session = store.sessions.get(session_id)
    if not session or session["account_id"] != account_id:
        raise AppError(404, "CHAT_SESSION_NOT_FOUND", "对话不存在。")
    return session


def recommendation_for(account_id: str, recommendation_id: str) -> dict[str, Any]:
    recommendation = store.recommendations.get(recommendation_id)
    if not recommendation or recommendation["account_id"] != account_id or recommendation.get("deleted_at"):
        raise AppError(404, "RECOMMENDATION_NOT_FOUND", "推荐记录不存在。")
    return recommendation


def course_summary(course_id: str) -> dict[str, Any]:
    course = catalog.get(course_id)
    if not course:
        raise AppError(404, "COURSE_NOT_FOUND", "课程不存在。")
    return {"course_id": course_id, "course_name": course["course_name"]}


def create_resolution(account_id: str, session: dict[str, Any], query: str, limit: int = 5) -> dict[str, Any]:
    resolution_id = new_id("res")
    record = {
        "resolution_id": resolution_id,
        "account_id": account_id,
        "chat_session_id": session["chat_session_id"],
        "query": query,
        "status": "PENDING",
        "selected_course_id": None,
        "expires_at": now() + timedelta(minutes=30),
        "candidates": catalog.resolve(query, limit),
    }
    store.resolutions[resolution_id] = record
    session["resolution_ids"].append(resolution_id)
    return public_resolution(record)


def public_resolution(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "resolution_id": record["resolution_id"],
        "query": record["query"],
        "status": record["status"],
        "selected_course_id": record.get("selected_course_id"),
        "expires_at": iso(record["expires_at"]),
        "candidates": copy.deepcopy(record["candidates"]),
    }


def public_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "chat_session_id": session["chat_session_id"],
        "state": session["state"],
        "profile_version": session["profile_version"],
        "profile_draft": copy.deepcopy(session["profile_draft"]),
        "messages": copy.deepcopy(session["messages"]),
        "course_resolutions": [
            public_resolution(store.resolutions[item])
            for item in session["resolution_ids"]
            if item in store.resolutions
        ],
        "created_at": session["created_at"],
    }


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
        padded = cursor + "=" * (-len(cursor) % 4)
        return max(0, int(base64.urlsafe_b64decode(padded).decode()))
    except (ValueError, UnicodeDecodeError):
        raise AppError(400, "INVALID_CURSOR", "分页游标无效。")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    agent_settings = get_agent_settings()
    return {
        "status": "ok",
        "storage": "memory",
        "agent_mode": "zhipuai" if agent_settings.configured else "rules_fallback",
        "agent_model": agent_settings.model if agent_settings.configured else "rules",
    }


@app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: Credentials):
    if not payload.username.strip():
        raise AppError(422, "VALIDATION_ERROR", "用户名不能为空。")
    try:
        account = store.create_account(payload.username.strip(), payload.password)
    except ValueError as exc:
        raise AppError(409, "USERNAME_EXISTS", "该用户名已被使用。") from exc
    return auth_response(account)


@app.post("/api/v1/auth/login")
def login(payload: Credentials):
    account = store.authenticate(payload.username.strip(), payload.password)
    if not account:
        raise AppError(401, "AUTH_INVALID_CREDENTIALS", "用户名或密码错误。")
    return auth_response(account)


@app.post("/api/v1/auth/refresh")
def refresh(payload: RefreshRequest):
    record = store.refresh_tokens.get(payload.refresh_token)
    if not record or record["revoked"] or record["expires_at"] <= now():
        raise AppError(401, "TOKEN_EXPIRED", "刷新令牌无效或已过期。")
    record["revoked"] = True
    account = store.accounts[record["account_id"]]
    return auth_response(account)


@app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshRequest):
    record = store.refresh_tokens.get(payload.refresh_token)
    if record:
        record["revoked"] = True
    return Response(status_code=204)


@app.get("/api/v1/me/profile")
def get_profile(account_id: str = Depends(account_id_from_header)):
    return profile_for(account_id)


@app.patch("/api/v1/me/profile")
def patch_profile(payload: ProfilePatch, account_id: str = Depends(account_id_from_header)):
    profile = store.profiles[account_id]
    if payload.profile_version != profile["profile_version"]:
        raise AppError(
            409,
            "PROFILE_VERSION_CONFLICT",
            "用户画像已被更新，请刷新后重试。",
            {"expected_version": profile["profile_version"], "received_version": payload.profile_version},
        )
    if payload.gender_code is not None and payload.gender_code not in (1, 2):
        raise AppError(422, "VALIDATION_ERROR", "性别字段只支持 1 或 2。")
    if payload.display_name is not None:
        display_name = payload.display_name.strip()
        if not display_name:
            raise AppError(422, "VALIDATION_ERROR", "姓名不能为空。")
        profile["display_name"] = display_name
    if payload.gender_code is not None:
        profile["gender_code"] = payload.gender_code
    profile["profile_version"] += 1
    profile["status"] = "DRAFT"
    return profile_for(account_id)


@app.get("/api/v1/me/completed-courses")
def completed_courses(account_id: str = Depends(account_id_from_header)):
    return {"items": profile_for(account_id)["completed_courses"]}


@app.post("/api/v1/me/profile/confirm")
def confirm_profile(payload: ProfileConfirm, account_id: str = Depends(account_id_from_header)):
    session = session_for(account_id, payload.chat_session_id)
    profile = store.profiles[account_id]
    if payload.profile_version != profile["profile_version"]:
        raise AppError(
            409,
            "PROFILE_VERSION_CONFLICT",
            "用户画像已被更新，请刷新后重试。",
            {"expected_version": profile["profile_version"], "received_version": payload.profile_version},
        )
    pending = [item for item in session["resolution_ids"] if store.resolutions[item]["status"] == "PENDING"]
    if pending or session["state"] != "PROFILE_REVIEW":
        raise AppError(409, "PENDING_COURSE_RESOLUTIONS", "请先处理全部课程匹配。")
    draft = session["profile_draft"]
    if not draft["display_name"] or draft["gender_code"] not in (1, 2):
        raise AppError(409, "PROFILE_INCOMPLETE", "姓名或性别尚未填写完整。")
    profile.update(copy.deepcopy(draft))
    profile["profile_version"] += 1
    profile["status"] = "CONFIRMED"
    profile["confirmed_at"] = iso()
    session["profile_version"] = profile["profile_version"]
    session["state"] = "COMPLETED"
    return profile_for(account_id)


@app.get("/api/v1/courses")
def list_courses(
    q: str = "",
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    account_id: str = Depends(account_id_from_header),
):
    del account_id
    offset = decode_cursor(cursor)
    results = catalog.search(q, offset + limit + 1)[offset:]
    items = results[:limit]
    has_more = len(results) > limit
    return {"items": items, "next_cursor": encode_cursor(offset + limit) if has_more else None, "has_more": has_more}


@app.get("/api/v1/courses/{course_id}")
def course_details(course_id: str, account_id: str = Depends(account_id_from_header)):
    course = catalog.get(course_id)
    if not course:
        raise AppError(404, "COURSE_NOT_FOUND", "课程不存在。")
    completed = {item["course_id"] for item in store.profiles[account_id]["completed_courses"]}
    return {
        **catalog.public_course(course),
        "advanced_label_rule": course["advanced_label_rule"],
        "prerequisites": course["prerequisites"],
        "prerequisites_satisfied": all(item["course_id"] in completed for item in course["prerequisites"]),
        "is_favorite": course_id in store.favorites[account_id],
    }


@app.post("/api/v1/courses/resolve", status_code=status.HTTP_201_CREATED)
def resolve_course(payload: ResolveRequest, account_id: str = Depends(account_id_from_header)):
    session = session_for(account_id, payload.chat_session_id)
    return create_resolution(account_id, session, payload.query, payload.limit)


@app.post("/api/v1/chat/sessions", status_code=status.HTTP_201_CREATED)
def create_session(payload: SessionCreate, account_id: str = Depends(account_id_from_header)):
    if payload.purpose != "RECOMMENDATION":
        raise AppError(400, "INVALID_SESSION_PURPOSE", "暂不支持该会话类型。")
    profile = profile_for(account_id)
    session_id = new_id("chat")
    draft = {
        "display_name": profile["display_name"],
        "gender_code": profile["gender_code"],
        "completed_courses": profile["completed_courses"],
    }
    if not draft["display_name"]:
        state_name = "COLLECTING_NAME"
        greeting = "你好，我是课程路径助手。先告诉我应该怎么称呼你。"
    elif draft["gender_code"] not in (1, 2):
        state_name = "COLLECTING_GENDER"
        greeting = "继续完善画像，请选择当前模型支持的性别数据组。"
    else:
        state_name = "COLLECTING_COURSES"
        greeting = "请用自然语言描述你学过的课程，可以一次说一门或多门。"
    session = {
        "chat_session_id": session_id,
        "account_id": account_id,
        "state": state_name,
        "profile_version": profile["profile_version"],
        "profile_draft": draft,
        "messages": [{"message_id": new_id("msg"), "role": "assistant", "content": greeting, "created_at": iso()}],
        "resolution_ids": [],
        "client_message_ids": set(),
        "event_seq": 0,
        "created_at": iso(),
    }
    store.sessions[session_id] = session
    return public_session(session)


@app.get("/api/v1/chat/sessions/{chat_session_id}")
def get_session(chat_session_id: str, account_id: str = Depends(account_id_from_header)):
    return public_session(session_for(account_id, chat_session_id))


@app.patch("/api/v1/chat/sessions/{chat_session_id}/profile-draft")
def patch_profile_draft(
    chat_session_id: str,
    payload: DraftProfilePatch,
    account_id: str = Depends(account_id_from_header),
):
    session = session_for(account_id, chat_session_id)
    if payload.gender_code is not None and payload.gender_code not in (1, 2):
        raise AppError(422, "VALIDATION_ERROR", "性别字段只支持 1 或 2。")
    if payload.display_name is not None:
        display_name = payload.display_name.strip()
        if not display_name:
            raise AppError(422, "VALIDATION_ERROR", "姓名不能为空。")
        session["profile_draft"]["display_name"] = display_name
    if payload.gender_code is not None:
        session["profile_draft"]["gender_code"] = payload.gender_code
    if session["profile_draft"]["display_name"] and session["profile_draft"]["gender_code"] in (1, 2):
        pending = [item for item in session["resolution_ids"] if store.resolutions[item]["status"] == "PENDING"]
        session["state"] = "WAITING_COURSE_CONFIRMATION" if pending else "PROFILE_REVIEW"
    return public_session(session)


@app.delete(
    "/api/v1/chat/sessions/{chat_session_id}/profile-draft/completed-courses/{course_id}",
    status_code=status.HTTP_200_OK,
)
def remove_draft_course(
    chat_session_id: str,
    course_id: str,
    account_id: str = Depends(account_id_from_header),
):
    session = session_for(account_id, chat_session_id)
    session["profile_draft"]["completed_courses"] = [
        item for item in session["profile_draft"]["completed_courses"] if item["course_id"] != course_id
    ]
    return public_session(session)


def sse_event(session: dict[str, Any], event: str, data: dict[str, Any]) -> str:
    session["event_seq"] += 1
    return f"id: evt_{session['event_seq']:06d}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_reply(account_id: str, session: dict[str, Any], payload: MessageCreate) -> AsyncIterator[str]:
    if payload.client_message_id in session["client_message_ids"]:
        yield sse_event(session, "done", {"chat_session_id": session["chat_session_id"], "duplicate": True})
        return
    session["client_message_ids"].add(payload.client_message_id)
    session["messages"].append(
        {"message_id": new_id("msg"), "role": "user", "content": payload.message, "created_at": iso()}
    )
    message = payload.message.strip()
    response_text = ""
    extra_events: list[tuple[str, dict[str, Any]]] = []

    if session["state"] == "COLLECTING_NAME":
        session["profile_draft"]["display_name"] = message[:80]
        session["state"] = "COLLECTING_GENDER"
        response_text = f"好的，{message[:80]}。请选择性别数据组，系统不会根据姓名推断。"
        extra_events.append(("profile.updated", {"profile_draft": session["profile_draft"], "state": session["state"]}))
    elif session["state"] == "COLLECTING_GENDER":
        gender = 1 if message in {"1", "男", "男性"} else 2 if message in {"2", "女", "女性"} else None
        if gender is None:
            response_text = "请明确选择男或女。这个字段只用于当前数据集与公平算法。"
        else:
            session["profile_draft"]["gender_code"] = gender
            session["state"] = "COLLECTING_COURSES"
            response_text = "已记录。现在请描述你学过的课程，可以一次说多门。"
            extra_events.append(("profile.updated", {"profile_draft": session["profile_draft"], "state": session["state"]}))
    elif session["state"] in {"COLLECTING_COURSES", "PROFILE_REVIEW"}:
        if any(term in message for term in ("没有学过", "没有其他", "暂时没有", "跳过课程", "完成课程")):
            pending = [item for item in session["resolution_ids"] if store.resolutions[item]["status"] == "PENDING"]
            if pending:
                response_text = "还有课程候选尚未处理，请先确认或选择都不是。"
            else:
                session["state"] = "PROFILE_REVIEW"
                response_text = "课程信息已经整理完成，请检查画像后确认生成推荐。"
                extra_events.append(("profile.ready", {"profile_draft": session["profile_draft"], "state": session["state"]}))
        else:
            descriptions = split_course_descriptions(message)
            records = [create_resolution(account_id, session, item) for item in descriptions]
            session["state"] = "WAITING_COURSE_CONFIRMATION"
            response_text = f"我识别出 {len(records)} 项课程描述，请逐项核对候选。"
            extra_events.extend(("course.match_required", record) for record in records)
    elif session["state"] == "WAITING_COURSE_CONFIRMATION":
        response_text = "请先使用课程确认卡处理全部候选。"
    else:
        response_text = "本轮画像已经确认，可以查看推荐结果或创建新会话。"

    generated_reply = await generate_agent_reply(
        state=session["state"],
        user_message=message,
        authoritative_reply=response_text,
    )
    response_text = generated_reply or response_text
    session["messages"].append(
        {"message_id": new_id("msg"), "role": "assistant", "content": response_text, "created_at": iso()}
    )
    midpoint = max(1, len(response_text) // 2)
    for chunk in (response_text[:midpoint], response_text[midpoint:]):
        if chunk:
            yield sse_event(session, "message.delta", {"text": chunk})
            await asyncio.sleep(0.03)
    for event, data in extra_events:
        yield sse_event(session, event, data)
    yield sse_event(session, "done", {"chat_session_id": session["chat_session_id"]})


@app.post("/api/v1/chat/sessions/{chat_session_id}/messages:stream")
def send_message(
    chat_session_id: str,
    payload: MessageCreate,
    account_id: str = Depends(account_id_from_header),
):
    session = session_for(account_id, chat_session_id)
    return StreamingResponse(stream_reply(account_id, session, payload), media_type="text/event-stream")


@app.post("/api/v1/chat/sessions/{chat_session_id}/course-resolutions/{resolution_id}")
def decide_resolution(
    chat_session_id: str,
    resolution_id: str,
    payload: ResolutionDecision,
    account_id: str = Depends(account_id_from_header),
):
    session = session_for(account_id, chat_session_id)
    record = store.resolutions.get(resolution_id)
    if not record or record["account_id"] != account_id or record["chat_session_id"] != chat_session_id:
        raise AppError(404, "RESOLUTION_NOT_FOUND", "课程解析不存在。")
    if record["expires_at"] <= now():
        record["status"] = "EXPIRED"
        raise AppError(409, "RESOLUTION_EXPIRED", "课程候选已过期，请重新描述。")
    if record["status"] != "PENDING":
        same = payload.course_id and payload.course_id == record.get("selected_course_id")
        if same or (payload.rejected and record["status"] == "REJECTED"):
            return public_session(session)
        raise AppError(409, "RESOLUTION_ALREADY_FINALIZED", "该课程描述已经处理。")

    if payload.course_id:
        candidate_ids = {item["course_id"] for item in record["candidates"]}
        if payload.course_id not in candidate_ids:
            raise AppError(400, "INVALID_RESOLUTION_CANDIDATE", "确认的课程不在候选集合中。")
        record["status"] = "CONFIRMED"
        record["selected_course_id"] = payload.course_id
        course = course_summary(payload.course_id)
        existing = {item["course_id"] for item in session["profile_draft"]["completed_courses"]}
        if payload.course_id not in existing:
            session["profile_draft"]["completed_courses"].append(course)
    else:
        record["status"] = "REJECTED"

    pending = [item for item in session["resolution_ids"] if store.resolutions[item]["status"] == "PENDING"]
    rejected = [item for item in session["resolution_ids"] if store.resolutions[item]["status"] == "REJECTED"]
    if pending:
        session["state"] = "WAITING_COURSE_CONFIRMATION"
    elif rejected:
        session["state"] = "COLLECTING_COURSES"
        session["messages"].append(
            {"message_id": new_id("msg"), "role": "assistant", "content": "未匹配的课程可以换一种说法，或选择完成课程描述。", "created_at": iso()}
        )
    else:
        session["state"] = "PROFILE_REVIEW"
        session["messages"].append(
            {"message_id": new_id("msg"), "role": "assistant", "content": "候选都已确认，请检查画像并生成推荐。", "created_at": iso()}
        )
    return public_session(session)


@app.post("/api/v1/recommendations", status_code=status.HTTP_201_CREATED)
def create_recommendation(payload: RecommendationCreate, account_id: str = Depends(account_id_from_header)):
    profile = store.profiles[account_id]
    if profile["status"] != "CONFIRMED":
        raise AppError(409, "PROFILE_NOT_CONFIRMED", "请先确认用户画像。")
    if payload.profile_version != profile["profile_version"]:
        raise AppError(
            409,
            "PROFILE_VERSION_CONFLICT",
            "用户画像已被更新，请刷新后重试。",
            {"expected_version": profile["profile_version"], "received_version": payload.profile_version},
        )
    completed = [item["course_id"] for item in profile["completed_courses"]]
    source, items = catalog.recommendations(completed, payload.top_n)
    recommendation_id = new_id("rec")
    record = {
        "recommendation_id": recommendation_id,
        "account_id": account_id,
        "source": source,
        "algorithm_version": "user-cf-memory-v1",
        "fairness_applied": False,
        "fairness_policy_version": None,
        "generated_at": iso(),
        "items": items,
        "deleted_at": None,
    }
    store.recommendations[recommendation_id] = record
    return copy.deepcopy({key: value for key, value in record.items() if key not in {"account_id", "deleted_at"}})


@app.get("/api/v1/recommendations")
def list_recommendations(
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    account_id: str = Depends(account_id_from_header),
):
    offset = decode_cursor(cursor)
    records = [
        item
        for item in store.recommendations.values()
        if item["account_id"] == account_id and not item.get("deleted_at")
    ]
    records.sort(key=lambda item: item["generated_at"], reverse=True)
    page = records[offset : offset + limit]
    items = [
        {
            "recommendation_id": item["recommendation_id"],
            "source": item["source"],
            "generated_at": item["generated_at"],
            "course_count": len(item["items"]),
        }
        for item in page
    ]
    has_more = offset + limit < len(records)
    return {"items": items, "next_cursor": encode_cursor(offset + limit) if has_more else None, "has_more": has_more}


@app.get("/api/v1/recommendations/{recommendation_id}")
def get_recommendation(recommendation_id: str, account_id: str = Depends(account_id_from_header)):
    record = recommendation_for(account_id, recommendation_id)
    return copy.deepcopy({key: value for key, value in record.items() if key not in {"account_id", "deleted_at"}})


@app.delete("/api/v1/recommendations/{recommendation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recommendation(recommendation_id: str, account_id: str = Depends(account_id_from_header)):
    record = store.recommendations.get(recommendation_id)
    if record and record["account_id"] != account_id:
        raise AppError(404, "RECOMMENDATION_NOT_FOUND", "推荐记录不存在。")
    if record:
        record["deleted_at"] = iso()
    return Response(status_code=204)


@app.get("/api/v1/favorites")
def list_favorites(
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    account_id: str = Depends(account_id_from_header),
):
    offset = decode_cursor(cursor)
    records = sorted(store.favorites[account_id].items(), key=lambda item: item[1], reverse=True)
    page = records[offset : offset + limit]
    items = [
        {"course": catalog.public_course(catalog.get(course_id)), "created_at": created_at}
        for course_id, created_at in page
        if catalog.get(course_id)
    ]
    has_more = offset + limit < len(records)
    return {"items": items, "next_cursor": encode_cursor(offset + limit) if has_more else None, "has_more": has_more}


@app.put("/api/v1/favorites/{course_id}")
def add_favorite(course_id: str, account_id: str = Depends(account_id_from_header)):
    course = catalog.get(course_id)
    if not course:
        raise AppError(404, "COURSE_NOT_FOUND", "课程不存在。")
    created_at = store.favorites[account_id].setdefault(course_id, iso())
    return {"course": catalog.public_course(course), "created_at": created_at}


@app.delete("/api/v1/favorites/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_favorite(course_id: str, account_id: str = Depends(account_id_from_header)):
    store.favorites[account_id].pop(course_id, None)
    return Response(status_code=204)

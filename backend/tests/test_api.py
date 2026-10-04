from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.app.config import get_agent_settings
from backend.tests.db_support import build_test_database


@pytest.fixture
def database_path(tmp_path):
    path = build_test_database(tmp_path / "api.sqlite3")
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("INSERT INTO courses VALUES ('C_PY', 'Python程序设计', 'basic', 0, 'prerequisite_count>=2')")
        connection.execute("INSERT INTO course_fields VALUES ('C_PY', '计算机科学', 0)")
        connection.execute("INSERT INTO course_information VALUES ('C_PY', ?, 'Python入门', 0, '[]', '[]')", ('["计算机科学"]',))
        connection.executemany("INSERT INTO interactions VALUES ('U_APP_NEIGHBOR', ?, 0)", [("C_PY",), ("C_LA",)])
    return path


@pytest.fixture
def app_factory(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_ENABLED", "false")
    monkeypatch.setenv("AGENT_MODE", "rules")
    get_agent_settings.cache_clear()
    from backend.app.main import create_app

    yield lambda path: create_app(database_path=path, jwt_secret="api-test-secret-with-at-least-32-bytes")
    get_agent_settings.cache_clear()


@pytest.fixture
def client(app_factory, database_path):
    with TestClient(app_factory(database_path)) as instance:
        yield instance


def register(client, username=None) -> tuple[dict, dict[str, str]]:
    username = username or f"student_{uuid4().hex[:8]}"
    response = client.post("/api/v1/auth/register", json={"username": username, "password": "learning123"})
    assert response.status_code == 201
    payload = response.json()
    return payload, {"Authorization": f"Bearer {payload['access_token']}"}


def sse_events(response) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name = "message"
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
        if line.startswith("data: "):
            events.append((event_name, json.loads(line.removeprefix("data: "))))
    return events


def send(client, session_id: str, headers: dict[str, str], message: str, client_message_id=None):
    return client.post(
        f"/api/v1/chat/sessions/{session_id}/messages:stream",
        headers=headers,
        json={"message": message, "client_message_id": client_message_id or f"client_{uuid4().hex}"},
    )


def test_complete_profile_course_confirmation_recommendation_and_favorite(client):
    auth, headers = register(client)
    profile = client.get("/api/v1/me/profile", headers=headers).json()
    assert profile["status"] == "DRAFT"
    assert profile["display_name"] is None
    assert set(profile) == {"display_name", "gender_code", "completed_courses", "profile_version", "status", "confirmed_at"}

    session_response = client.post("/api/v1/chat/sessions", headers=headers, json={"purpose": "RECOMMENDATION"})
    assert session_response.status_code == 201
    session_id = session_response.json()["chat_session_id"]

    assert send(client, session_id, headers, "小莉").status_code == 200
    assert send(client, session_id, headers, "女").status_code == 200
    course_stream = send(client, session_id, headers, "我学过 Python 入门和线性代数")
    events = sse_events(course_stream)
    resolutions = [data for event, data in events if event == "course.match_required"]
    assert len(resolutions) == 2
    assert all(item["candidates"] for item in resolutions)

    first_resolution = resolutions[0]
    forged = client.post(
        f"/api/v1/chat/sessions/{session_id}/course-resolutions/{first_resolution['resolution_id']}",
        headers=headers,
        json={"course_id": "C_NOT_A_CANDIDATE"},
    )
    assert forged.status_code == 400
    assert forged.json()["error"]["code"] == "INVALID_RESOLUTION_CANDIDATE"

    for resolution in resolutions:
        result = client.post(
            f"/api/v1/chat/sessions/{session_id}/course-resolutions/{resolution['resolution_id']}",
            headers=headers,
            json={"course_id": resolution["candidates"][0]["course_id"]},
        )
        assert result.status_code == 200

    latest_session = client.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).json()
    assert latest_session["state"] == "PROFILE_REVIEW"
    assert len(latest_session["profile_draft"]["completed_courses"]) == 2

    confirmed = client.post(
        "/api/v1/me/profile/confirm",
        headers=headers,
        json={"profile_version": 0, "chat_session_id": session_id},
    )
    assert confirmed.status_code == 200
    confirmed_profile = confirmed.json()
    assert confirmed_profile["display_name"] == "小莉"
    assert confirmed_profile["gender_code"] == 2
    assert confirmed_profile["status"] == "CONFIRMED"

    recommendation = client.post(
        "/api/v1/recommendations",
        headers=headers,
        json={"profile_version": confirmed_profile["profile_version"], "top_n": 5},
    )
    assert recommendation.status_code == 201
    recommendation_payload = recommendation.json()
    assert recommendation_payload["source"] in {"collaborative", "popular_fallback"}
    assert recommendation_payload["items"]
    assert recommendation_payload["fairness_applied"] is False

    course_id = recommendation_payload["items"][0]["course"]["course_id"]
    favorite = client.put(f"/api/v1/favorites/{course_id}", headers=headers)
    assert favorite.status_code == 200
    assert client.get("/api/v1/favorites", headers=headers).json()["items"]

    history = client.get("/api/v1/recommendations", headers=headers).json()
    assert history["items"][0]["recommendation_id"] == recommendation_payload["recommendation_id"]
    deleted = client.delete(
        f"/api/v1/recommendations/{recommendation_payload['recommendation_id']}", headers=headers
    )
    assert deleted.status_code == 204
    assert client.get("/api/v1/recommendations", headers=headers).json()["items"] == []


def test_empty_course_profile_uses_popular_fallback(client):
    _, headers = register(client)
    session = client.post("/api/v1/chat/sessions", headers=headers, json={"purpose": "RECOMMENDATION"}).json()
    session_id = session["chat_session_id"]
    send(client, session_id, headers, "晨曦")
    send(client, session_id, headers, "男")
    ready = send(client, session_id, headers, "我没有学过课程")
    assert any(event == "profile.ready" for event, _ in sse_events(ready))

    confirmed = client.post(
        "/api/v1/me/profile/confirm",
        headers=headers,
        json={"profile_version": 0, "chat_session_id": session_id},
    ).json()
    recommendation = client.post(
        "/api/v1/recommendations",
        headers=headers,
        json={"profile_version": confirmed["profile_version"], "top_n": 4},
    )
    assert recommendation.status_code == 201
    payload = recommendation.json()
    assert payload["source"] == "popular_fallback"
    assert len(payload["items"]) == 4
    assert all("NO_PREREQUISITES_REQUIRED" in item["reason_codes"] for item in payload["items"])


def test_user_cannot_read_another_users_session(client):
    _, first_headers = register(client)
    _, second_headers = register(client)
    session_id = client.post(
        "/api/v1/chat/sessions", headers=first_headers, json={"purpose": "RECOMMENDATION"}
    ).json()["chat_session_id"]
    response = client.get(f"/api/v1/chat/sessions/{session_id}", headers=second_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CHAT_SESSION_NOT_FOUND"


def prepare_profile(client, headers):
    session = client.post("/api/v1/chat/sessions", headers=headers, json={}).json()
    session_id = session["chat_session_id"]
    patched = client.patch(f"/api/v1/chat/sessions/{session_id}/profile-draft", headers=headers,
                           json={"display_name": "学习者", "gender_code": 2})
    assert patched.status_code == 200
    resolution = client.post("/api/v1/courses/resolve", headers=headers,
                             json={"chat_session_id": session_id, "query": "编程基础"}).json()
    decision = client.post(f"/api/v1/chat/sessions/{session_id}/course-resolutions/{resolution['resolution_id']}",
                           headers=headers, json={"course_id": "C_BASE"})
    assert decision.status_code == 200
    profile = client.post("/api/v1/me/profile/confirm", headers=headers,
                          json={"profile_version": session["profile_version"], "chat_session_id": session_id})
    assert profile.status_code == 200
    return profile.json(), session_id


def test_health_exposes_database_schema_version(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["storage"] == "sqlite"
    assert payload["database_schema_version"] == 3
    assert payload["database_check"] == "ok"
    assert payload["agent_mode"] == "rules_fallback"
    assert "path" not in payload
    with client.app.state.services.database.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_health_reports_unavailable_database_without_exposing_its_path(client, database_path):
    moved = database_path.with_suffix(".unavailable")
    database_path.rename(moved)
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
        assert response.headers["Retry-After"] == "1"
        assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
        assert str(database_path) not in response.text
    finally:
        moved.rename(database_path)


def test_locked_database_returns_retriable_error_without_changing_profile(client):
    _, headers = register(client)
    with client.app.state.services.database.transaction(immediate=True):
        response = client.patch("/api/v1/me/profile", headers=headers,
                                json={"profile_version": 0, "display_name": "not committed"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert response.headers["Retry-After"] == "1"
    assert client.get("/api/v1/me/profile", headers=headers).json()["profile_version"] == 0


def test_settings_control_database_and_jwt_signing(monkeypatch, database_path):
    from backend.app.config import get_app_settings
    from backend.app.main import create_app

    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    monkeypatch.setenv("JWT_SECRET", "configured-test-secret-with-at-least-32-bytes")
    monkeypatch.setenv("AGENT_LLM_ENABLED", "false")
    get_agent_settings.cache_clear()
    settings = get_app_settings()
    assert settings.database_path == database_path
    with TestClient(create_app()) as configured:
        auth, _ = register(configured)
    with TestClient(create_app()) as restarted:
        assert restarted.get("/api/v1/me/profile", headers={"Authorization": f"Bearer {auth['access_token']}"}).status_code == 200
    with TestClient(create_app(jwt_secret="different-test-secret-with-at-least-32-bytes")) as changed:
        assert changed.get("/api/v1/me/profile", headers={"Authorization": f"Bearer {auth['access_token']}"}).status_code == 401
    get_agent_settings.cache_clear()


def test_full_flow_survives_application_restart(app_factory, database_path):
    with TestClient(app_factory(database_path)) as first:
        auth, headers = register(first, "restart_user")
        profile, session_id = prepare_profile(first, headers)
        response = first.post("/api/v1/recommendations", headers=headers,
                              json={"profile_version": profile["profile_version"], "top_n": 5})
        assert response.status_code == 201
        recommendation = response.json()
        assert recommendation["source"] == "collaborative"
        assert "C_BASE" not in {item["course"]["course_id"] for item in recommendation["items"]}
        assert "C_DATA" in {item["course"]["course_id"] for item in recommendation["items"]}
        favorite = first.put("/api/v1/favorites/C_DATA", headers=headers).json()
        saved_session = first.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).json()

    with TestClient(app_factory(database_path)) as second:
        logged_in = second.post("/api/v1/auth/login", json={"username": "RESTART_USER", "password": "learning123"})
        assert logged_in.status_code == 200
        assert logged_in.json()["account"] == auth["account"]
        renewed = second.post("/api/v1/auth/refresh", json={"refresh_token": auth["refresh_token"]})
        assert renewed.status_code == 200
        assert renewed.json()["account"] == auth["account"]
        assert second.post("/api/v1/auth/refresh", json={"refresh_token": auth["refresh_token"]}).status_code == 401
        headers = {"Authorization": f"Bearer {renewed.json()['access_token']}"}
        assert second.get("/api/v1/me/profile", headers=headers).json() == profile
        assert second.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).json() == saved_session
        assert second.get(f"/api/v1/recommendations/{recommendation['recommendation_id']}", headers=headers).json() == recommendation
        assert second.get("/api/v1/favorites", headers=headers).json()["items"] == [favorite]
        assert second.get("/api/v1/courses/C_DATA", headers=headers).json()["prerequisites_satisfied"] is True
        assert second.get("/api/v1/courses/C_DATA", headers=headers).json()["is_favorite"] is True


def event_ids(response):
    return [int(line.removeprefix("id: evt_")) for line in response.text.splitlines() if line.startswith("id: evt_")]


def test_sse_idempotency_and_event_sequence_survive_restart(app_factory, database_path):
    with TestClient(app_factory(database_path)) as first:
        _, headers = register(first)
        session_id = first.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
        initial = send(first, session_id, headers, "晨曦", "same-message")
        assert [name for name, _ in sse_events(initial)] == ["message.delta", "message.delta", "profile.updated", "done"]
        assert event_ids(initial) == [1, 2, 3, 4]

    with TestClient(app_factory(database_path)) as second:
        duplicate = send(second, session_id, headers, "晨曦", "same-message")
        assert sse_events(duplicate) == [("done", {"chat_session_id": session_id, "duplicate": True})]
        assert event_ids(duplicate) == [5]
        reply = send(second, session_id, headers, "女")
        assert event_ids(reply) == [6, 7, 8, 9]
        session = second.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).json()
        assert len(session["messages"]) == 5
        stored = second.app.state.services.repository.get_chat_session(
            second.app.state.services.auth.decode_access_token(headers["Authorization"][7:]), session_id)
        assert stored["event_seq"] == 9


def test_whitespace_message_keeps_legacy_draft_and_incomplete_profile_error(client):
    _, headers = register(client)
    session_id = client.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
    response = send(client, session_id, headers, "   ")
    updated = next(data for event, data in sse_events(response) if event == "profile.updated")
    assert updated["profile_draft"]["display_name"] == ""
    assert updated["state"] == "COLLECTING_GENDER"
    send(client, session_id, headers, "女")
    send(client, session_id, headers, "我没有学过课程")
    rejected = client.post("/api/v1/me/profile/confirm", headers=headers,
                           json={"profile_version": 0, "chat_session_id": session_id})
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "PROFILE_INCOMPLETE"


def test_cross_account_mutations_are_non_disclosing(client):
    _, owner = register(client)
    _, stranger = register(client)
    profile, session_id = prepare_profile(client, owner)
    before = client.get(f"/api/v1/chat/sessions/{session_id}", headers=owner).json()
    response = send(client, session_id, stranger, "malicious append")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CHAT_SESSION_NOT_FOUND"
    assert client.get(f"/api/v1/chat/sessions/{session_id}", headers=owner).json() == before
    rec = client.post("/api/v1/recommendations", headers=owner,
                      json={"profile_version": profile["profile_version"]}).json()
    path = f"/api/v1/recommendations/{rec['recommendation_id']}"
    for action in (client.get, client.delete):
        forbidden = action(path, headers=stranger)
        assert forbidden.status_code == 404
        assert forbidden.json()["error"]["code"] == "RECOMMENDATION_NOT_FOUND"
    assert client.delete(path, headers=owner).status_code == 204
    assert client.delete(path, headers=owner).status_code == 204
    assert client.get(path, headers=owner).status_code == 404
    assert client.delete(path, headers=stranger).status_code == 404
    assert client.delete("/api/v1/recommendations/missing", headers=owner).status_code == 204
    client.put("/api/v1/favorites/C_BASE", headers=owner)
    assert client.get("/api/v1/favorites", headers=stranger).json()["items"] == []


def test_profile_conflicts_and_resolution_errors_keep_contract(client):
    _, headers = register(client)
    session_id = client.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
    path = f"/api/v1/chat/sessions/{session_id}"
    assert client.post("/api/v1/recommendations", headers=headers, json={"profile_version": 0}).json()["error"]["code"] == "PROFILE_NOT_CONFIRMED"
    assert client.post("/api/v1/me/profile/confirm", headers=headers, json={"profile_version": 0, "chat_session_id": session_id}).json()["error"]["code"] == "PENDING_COURSE_RESOLUTIONS"
    assert client.patch(path + "/profile-draft", headers=headers, json={"gender_code": 3}).status_code == 422
    assert client.patch(path + "/profile-draft", headers=headers, json={"display_name": " "}).status_code == 422
    client.patch(path + "/profile-draft", headers=headers, json={"display_name": "Alice", "gender_code": 2})
    resolution = client.post("/api/v1/courses/resolve", headers=headers,
                             json={"chat_session_id": session_id, "query": "编程基础"}).json()
    decision_path = path + f"/course-resolutions/{resolution['resolution_id']}"
    assert client.post(path + "/course-resolutions/missing", headers=headers, json={"rejected": True}).json()["error"]["code"] == "RESOLUTION_NOT_FOUND"
    assert client.post(decision_path, headers=headers, json={"course_id": "forged"}).status_code == 400
    decided = client.post(decision_path, headers=headers, json={"course_id": "C_BASE"})
    assert client.post(decision_path, headers=headers, json={"course_id": "C_BASE"}).json() == decided.json()
    finalized = client.post(decision_path, headers=headers, json={"rejected": True})
    assert finalized.status_code == 409
    assert finalized.json()["error"]["code"] == "RESOLUTION_ALREADY_FINALIZED"
    removed = client.delete(path + "/profile-draft/completed-courses/C_BASE", headers=headers)
    assert removed.json()["profile_draft"]["completed_courses"] == []
    patched = client.patch("/api/v1/me/profile", headers=headers, json={"profile_version": 0, "display_name": "Alicia"})
    assert patched.json()["profile_version"] == 1
    conflict = client.patch("/api/v1/me/profile", headers=headers, json={"profile_version": 0})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["details"] == {"expected_version": 1, "received_version": 0}
    stale_session = client.post("/api/v1/me/profile/confirm", headers=headers, json={"profile_version": 1, "chat_session_id": session_id})
    assert stale_session.status_code == 409
    assert stale_session.json()["error"]["code"] == "PROFILE_VERSION_CONFLICT"


def test_expired_resolution_is_persisted(client):
    _, headers = register(client)
    session_id = client.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
    resolution = client.post("/api/v1/courses/resolve", headers=headers,
                             json={"chat_session_id": session_id, "query": "编程基础"}).json()
    with client.app.state.services.database.transaction() as connection:
        connection.execute("UPDATE app_course_resolutions SET expires_at = '2000-01-01T00:00:00+00:00'")
    expired = client.post(f"/api/v1/chat/sessions/{session_id}/course-resolutions/{resolution['resolution_id']}",
                          headers=headers, json={"course_id": "C_BASE"})
    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "RESOLUTION_EXPIRED"
    session = client.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).json()
    assert session["course_resolutions"][0]["status"] == "EXPIRED"


def test_demo_account_is_seeded_once_and_never_reset(app_factory, database_path):
    with TestClient(app_factory(database_path)) as first:
        auth = first.post("/api/v1/auth/login", json={"username": "course_demo", "password": "demo1234"}).json()
        headers = {"Authorization": f"Bearer {auth['access_token']}"}
        changed = first.patch("/api/v1/me/profile", headers=headers, json={"profile_version": 0, "display_name": "保留修改"}).json()
        password_hash = first.app.state.services.repository.get_account(auth["account"]["account_id"])["password_hash"]
        with first.app.state.services.database.transaction() as connection:
            connection.execute("UPDATE app_accounts SET username = 'COURSE_DEMO' WHERE account_id = ?", (auth["account"]["account_id"],))
    with TestClient(app_factory(database_path)) as second:
        logged_in = second.post("/api/v1/auth/login", json={"username": "course_demo", "password": "demo1234"}).json()
        assert logged_in["account"]["account_id"] == auth["account"]["account_id"]
        assert second.get("/api/v1/me/profile", headers=headers).json() == changed
        assert second.app.state.services.repository.get_account(auth["account"]["account_id"])["password_hash"] == password_hash
        with second.app.state.services.database.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM app_accounts").fetchone()[0] == 1


def test_startup_rejects_missing_or_invalid_database_without_creating_app_tables(app_factory, tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(FileNotFoundError):
        with TestClient(app_factory(missing)):
            pass
    assert not missing.exists()
    invalid = tmp_path / "invalid.sqlite3"
    with closing(sqlite3.connect(invalid)) as connection, connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER)")
    with pytest.raises(RuntimeError, match="缺少推荐数据表"):
        with TestClient(app_factory(invalid)):
            pass
    with closing(sqlite3.connect(invalid)) as connection:
        assert connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table'").fetchall() == [("unrelated",)]


def test_unicode_authentication_and_logout_contract(client):
    auth, headers = register(client, "Älice")
    duplicate = client.post("/api/v1/auth/register", json={"username": "äLICE", "password": "learning123"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "USERNAME_EXISTS"
    assert client.post("/api/v1/auth/login", json={"username": "älice", "password": "learning123"}).status_code == 200
    assert client.get("/api/v1/me/profile").json()["error"]["code"] == "AUTH_REQUIRED"
    assert client.get("/api/v1/me/profile", headers={"Authorization": "Bearer malformed"}).status_code == 401
    for _ in range(2):
        assert client.post("/api/v1/auth/logout", json={"refresh_token": auth["refresh_token"]}).status_code == 204
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": auth["refresh_token"]}).json()["error"]["code"] == "TOKEN_EXPIRED"


def test_catalog_and_favorite_pagination_and_errors(client):
    _, headers = register(client)
    first = client.get("/api/v1/courses?limit=2", headers=headers).json()
    second = client.get("/api/v1/courses", headers=headers, params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert first["has_more"] is True
    assert {item["course_id"] for item in first["items"]}.isdisjoint(item["course_id"] for item in second["items"])
    assert client.get("/api/v1/courses?cursor=invalid!", headers=headers).json()["error"]["code"] == "INVALID_CURSOR"
    assert client.get("/api/v1/courses/missing", headers=headers).json()["error"]["code"] == "COURSE_NOT_FOUND"
    assert client.put("/api/v1/favorites/missing", headers=headers).json()["error"]["code"] == "COURSE_NOT_FOUND"
    original = client.put("/api/v1/favorites/C_BASE", headers=headers).json()
    assert client.put("/api/v1/favorites/C_BASE", headers=headers).json() == original
    client.put("/api/v1/favorites/C_DB", headers=headers)
    page = client.get("/api/v1/favorites?limit=1", headers=headers).json()
    assert page["has_more"] is True
    assert len(client.get("/api/v1/favorites", headers=headers, params={"cursor": page["next_cursor"]}).json()["items"]) == 1
    assert client.delete("/api/v1/favorites/C_BASE", headers=headers).status_code == 204
    assert client.delete("/api/v1/favorites/C_BASE", headers=headers).status_code == 204


def test_recommendation_rejects_profile_reconfirmed_during_generation(client, monkeypatch):
    from backend.app.database import Database
    from backend.app.repositories import ApplicationRepository

    auth, headers = register(client)
    profile, _ = prepare_profile(client, headers)
    services = client.app.state.services
    other_worker = ApplicationRepository(Database(services.database.path))
    account_id = auth["account"]["account_id"]
    original = services.catalog.recommendations

    def reconfirm_then_generate(user_id, top_n):
        session = other_worker.create_chat_session(account_id, "RECOMMENDATION")
        session_id = session["chat_session_id"]
        other_worker.remove_session_course(account_id, session_id, "C_BASE")
        other_worker.patch_session_draft(account_id, session_id, state="PROFILE_REVIEW")
        other_worker.confirm_profile(account_id, session_id, profile["profile_version"])
        return original(user_id, top_n)

    monkeypatch.setattr(services.catalog, "recommendations", reconfirm_then_generate)
    response = client.post("/api/v1/recommendations", headers=headers,
                           json={"profile_version": profile["profile_version"]})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROFILE_VERSION_CONFLICT"
    assert response.json()["error"]["details"] == {"expected_version": 2, "received_version": 1}
    with services.database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM app_recommendations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM app_recommendation_items").fetchone()[0] == 0


def test_ordered_chat_requests_choose_state_before_deferred_streams(client):
    from backend.app.database import Database
    from backend.app.main import AppServices, MessageCreate, send_message
    from backend.app.repositories import ApplicationRepository

    auth, headers = register(client)
    session_id = client.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
    first_worker = client.app.state.services
    second_worker = AppServices(first_worker.database, ApplicationRepository(Database(first_worker.database.path)),
                                first_worker.auth, first_worker.catalog)
    # Both requests are accepted before either lazy StreamingResponse is consumed.
    first = asyncio.run(send_message(session_id, MessageCreate(message="Alice", client_message_id="alice"),
                                    auth["account"]["account_id"], first_worker))
    second = asyncio.run(send_message(session_id, MessageCreate(message="2", client_message_id="gender"),
                                     auth["account"]["account_id"], second_worker))

    async def consume(response):
        return "".join([chunk async for chunk in response.body_iterator])

    assert "profile.updated" in asyncio.run(consume(first))
    assert "profile.updated" in asyncio.run(consume(second))
    session = client.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).json()
    assert session["profile_draft"]["display_name"] == "Alice"
    assert session["profile_draft"]["gender_code"] == 2
    assert session["state"] == "COLLECTING_COURSES"
    assert [item["content"] for item in session["messages"] if item["role"] == "user"] == ["Alice", "2"]


def test_empty_draft_patch_preserves_completed_session_state(client):
    _, headers = register(client)
    _, session_id = prepare_profile(client, headers)
    response = client.patch(f"/api/v1/chat/sessions/{session_id}/profile-draft", headers=headers, json={})
    assert response.status_code == 200
    assert response.json()["state"] == "COMPLETED"


def test_name_only_draft_patch_preserves_collecting_name_state(client):
    _, headers = register(client)
    session_id = client.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
    response = client.patch(f"/api/v1/chat/sessions/{session_id}/profile-draft", headers=headers,
                            json={"display_name": "Alice"})
    assert response.status_code == 200
    assert response.json()["profile_draft"]["display_name"] == "Alice"
    assert response.json()["state"] == "COLLECTING_NAME"


def test_draft_patch_validates_gender_before_blank_name(client):
    _, headers = register(client)
    session_id = client.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
    response = client.patch(f"/api/v1/chat/sessions/{session_id}/profile-draft", headers=headers,
                            json={"display_name": " ", "gender_code": 3})
    assert response.status_code == 422
    assert response.json()["error"]["message"] == "性别字段只支持 1 或 2。"


def test_standalone_resolution_preserves_collecting_gender_state(client):
    _, headers = register(client)
    session_id = client.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
    send(client, session_id, headers, "Alice")
    response = client.post("/api/v1/courses/resolve", headers=headers,
                            json={"chat_session_id": session_id, "query": "编程基础"})
    assert response.status_code == 201
    session = client.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).json()
    assert session["state"] == "COLLECTING_GENDER"
    assert session["course_resolutions"][0]["resolution_id"] == response.json()["resolution_id"]
    reply = send(client, session_id, headers, "2")
    updated = next(data for event, data in sse_events(reply) if event == "profile.updated")
    assert updated["profile_draft"]["gender_code"] == 2
    assert updated["state"] == "COLLECTING_COURSES"


def test_accepted_chat_reply_survives_restart_before_stream_consumption(app_factory, database_path):
    from backend.app.main import prepare_chat_transition

    with TestClient(app_factory(database_path)) as first:
        auth, headers = register(first)
        session_id = first.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
        services = first.app.state.services
        services.repository.accept_chat_message(
            auth["account"]["account_id"], session_id, "Alice", "interrupted",
            lambda session: prepare_chat_transition(services.catalog, session, "Alice"),
        )
        # Simulate a process stopping immediately after acceptance, with no stream consumed.
    with TestClient(app_factory(database_path)) as restarted:
        session = restarted.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).json()
        assert [message["role"] for message in session["messages"]] == ["assistant", "user", "assistant"]
        assert session["messages"][-1]["content"] == "好的，Alice。请选择性别数据组，系统不会根据姓名推断。"
        duplicate = send(restarted, session_id, headers, "Alice", "interrupted")
        assert sse_events(duplicate) == [("done", {"chat_session_id": session_id, "duplicate": True})]
        assert restarted.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).json()["messages"] == session["messages"]


def test_writer_contention_after_acceptance_streams_durable_authoritative_reply(client, monkeypatch):
    import backend.app.main as main

    _, headers = register(client)
    session_id = client.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
    blocker = sqlite3.connect(client.app.state.services.database.path, check_same_thread=False)

    async def contended_wording(**kwargs):
        blocker.execute("BEGIN IMMEDIATE")
        return "请继续选择性别数据组。"

    monkeypatch.setattr(main, "generate_agent_reply", contended_wording)
    try:
        response = send(client, session_id, headers, "Alice", "contended")
    finally:
        blocker.rollback()
        blocker.close()
    assert response.status_code == 200
    events = sse_events(response)
    text = "".join(data["text"] for event, data in events if event == "message.delta")
    assert text == "好的，Alice。请选择性别数据组，系统不会根据姓名推断。"
    assert [event for event, _ in events] == ["message.delta", "message.delta", "profile.updated", "done"]
    assert event_ids(response) == [1, 2, 3, 4]
    session = client.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).json()
    assert session["messages"][-1]["content"] == text
    assert len(session["messages"]) == 3
    assert sse_events(send(client, session_id, headers, "Alice", "contended"))[0][1]["duplicate"] is True


def test_streaming_body_never_accesses_database(client, monkeypatch):
    import backend.app.main as main

    _, headers = register(client)
    session_id = client.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
    original_stream = main.stream_reply

    def unavailable_connection():
        raise AssertionError("database accessed after streaming headers")

    async def disconnected_storage(*args, **kwargs):
        with monkeypatch.context() as streaming_patch:
            streaming_patch.setattr(client.app.state.services.database, "connect", unavailable_connection)
            async for frame in original_stream(*args, **kwargs):
                yield frame

    monkeypatch.setattr(main, "stream_reply", disconnected_storage)
    response = send(client, session_id, headers, "Alice")
    assert response.status_code == 200
    assert [event for event, _ in sse_events(response)] == ["message.delta", "message.delta", "profile.updated", "done"]


def test_autonomous_runtime_uses_tools_and_preserves_confirmation_boundary(monkeypatch, database_path):
    import asyncio

    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_MODE", "autonomous")
    get_agent_settings.cache_clear()


def test_autonomous_provider_failure_falls_back_to_rules(monkeypatch, database_path):
    monkeypatch.setenv("AGENT_LLM_ENABLED", "true")
    monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_MODE", "autonomous")
    get_agent_settings.cache_clear()
    from backend.app.main import create_app

    with TestClient(create_app(database_path, jwt_secret="autonomous-fallback-secret")) as client:
        _, headers = register(client)
        session_id = client.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
        runtime = client.app.state.services.agent_runtime

        async def unavailable(context, tools):
            return None

        runtime.decision_provider = unavailable
        response = send(client, session_id, headers, "Alice", "fallback-api")

        assert response.status_code == 200
        assert "profile.updated" in [event for event, _ in sse_events(response)]
        session = client.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).json()
        assert session["state"] == "COLLECTING_GENDER"
        assert session["messages"][-1]["content"].startswith("好的，Alice")
    get_agent_settings.cache_clear()
    from backend.app.main import create_app
    from backend.app.agent_runtime.types import ToolCall

    with TestClient(create_app(database_path, jwt_secret="autonomous-test-secret")) as client:
        auth, headers = register(client)
        session_id = client.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
        runtime = client.app.state.services.agent_runtime
        decisions = iter([
            {"tool_calls": [ToolCall("name", "update_draft_name", {"name": "Alice"})]},
            {"text": "Alice，请选择性别数据组。", "tool_calls": []},
            {"tool_calls": [ToolCall("gender", "update_draft_gender", {"gender_code": 2})]},
            {"text": "已记录，请描述你学过的课程。", "tool_calls": []},
            {"tool_calls": [ToolCall("course", "create_course_resolution", {"query": "编程基础"})]},
            {"text": "我已找到课程候选，请逐项确认。", "tool_calls": []},
        ])

        async def provider(context, tools):
            return next(decisions)

        runtime.decision_provider = provider
        first = send(client, session_id, headers, "Alice", "auto-name")
        first_events = sse_events(first)
        assert "profile.updated" in [event for event, _ in first_events]
        assert any(event == "agent.step" and data["phase"] == "tool_call" for event, data in first_events)
        second = send(client, session_id, headers, "女", "auto-gender")
        assert "profile.updated" in [event for event, _ in sse_events(second)]
        third = send(client, session_id, headers, "我学过编程基础", "auto-course")
        events = sse_events(third)
        assert "course.match_required" in [event for event, _ in events]
        assert client.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).json()["state"] == "WAITING_COURSE_CONFIRMATION"
        resolution = next(data for event, data in events if event == "course.match_required")
        assert client.post(
            f"/api/v1/chat/sessions/{session_id}/course-resolutions/{resolution['resolution_id']}",
            headers=headers, json={"course_id": resolution["candidates"][0]["course_id"]},
        ).status_code == 200
    get_agent_settings.cache_clear()


def test_llm_wording_updates_existing_durable_reply_before_streaming(client, monkeypatch):
    import backend.app.main as main

    _, headers = register(client)
    session_id = client.post("/api/v1/chat/sessions", headers=headers, json={}).json()["chat_session_id"]
    original_stream = main.stream_reply
    accepted_message_ids = []

    async def wording(**kwargs):
        session = client.app.state.services.repository.get_chat_session(
            client.app.state.services.auth.decode_access_token(headers["Authorization"][7:]), session_id)
        assert session["messages"][-1]["role"] == "assistant"
        accepted_message_ids.append(session["messages"][-1]["message_id"])
        return "Alice，请选择性别数据组。"

    async def verified_stream(*args, **kwargs):
        session = client.app.state.services.repository.get_chat_session(
            client.app.state.services.auth.decode_access_token(headers["Authorization"][7:]), session_id)
        assert accepted_message_ids
        assert session["messages"][-1]["message_id"] == accepted_message_ids[0]
        assert session["messages"][-1]["content"] == "Alice，请选择性别数据组。"
        async for frame in original_stream(*args, **kwargs):
            yield frame

    monkeypatch.setattr(main, "generate_agent_reply", wording)
    monkeypatch.setattr(main, "stream_reply", verified_stream)
    response = send(client, session_id, headers, "Alice")
    assert response.status_code == 200
    assert "".join(data["text"] for event, data in sse_events(response) if event == "message.delta") == "Alice，请选择性别数据组。"


def test_startup_integrity_failure_prevents_all_migration_and_seed_writes(app_factory, database_path):
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("UPDATE courses SET is_advanced = 2 WHERE course_id = 'C_BASE'")
    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
        original_schema = connection.execute("SELECT type, name, sql FROM sqlite_schema ORDER BY name").fetchall()
    with pytest.raises(RuntimeError, match="完整性检查失败"):
        with TestClient(app_factory(database_path)):
            pass
    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute("SELECT type, name, sql FROM sqlite_schema ORDER BY name").fetchall() == original_schema
        assert connection.execute("SELECT name FROM sqlite_schema WHERE name = 'schema_migrations' OR name LIKE 'app_%'").fetchall() == []


from __future__ import annotations

import json
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app.main import app


client = TestClient(app)


def register() -> tuple[dict, dict[str, str]]:
    username = f"student_{uuid4().hex[:8]}"
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


def send(session_id: str, headers: dict[str, str], message: str):
    return client.post(
        f"/api/v1/chat/sessions/{session_id}/messages:stream",
        headers=headers,
        json={"message": message, "client_message_id": f"client_{uuid4().hex}"},
    )


def test_complete_profile_course_confirmation_recommendation_and_favorite():
    auth, headers = register()
    profile = client.get("/api/v1/me/profile", headers=headers).json()
    assert profile["status"] == "DRAFT"
    assert profile["display_name"] is None

    session_response = client.post("/api/v1/chat/sessions", headers=headers, json={"purpose": "RECOMMENDATION"})
    assert session_response.status_code == 201
    session_id = session_response.json()["chat_session_id"]

    assert send(session_id, headers, "小莉").status_code == 200
    assert send(session_id, headers, "女").status_code == 200
    course_stream = send(session_id, headers, "我学过 Python 入门和线性代数")
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


def test_empty_course_profile_uses_popular_fallback():
    _, headers = register()
    session = client.post("/api/v1/chat/sessions", headers=headers, json={"purpose": "RECOMMENDATION"}).json()
    session_id = session["chat_session_id"]
    send(session_id, headers, "晨曦")
    send(session_id, headers, "男")
    ready = send(session_id, headers, "我没有学过课程")
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


def test_user_cannot_read_another_users_session():
    _, first_headers = register()
    _, second_headers = register()
    session_id = client.post(
        "/api/v1/chat/sessions", headers=first_headers, json={"purpose": "RECOMMENDATION"}
    ).json()["chat_session_id"]
    response = client.get(f"/api/v1/chat/sessions/{session_id}", headers=second_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CHAT_SESSION_NOT_FOUND"


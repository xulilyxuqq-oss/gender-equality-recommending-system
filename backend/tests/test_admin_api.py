from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.tests.db_support import build_test_database


@pytest.fixture
def client(tmp_path: Path):
    path = build_test_database(tmp_path / "admin-api.sqlite3")
    with TestClient(create_app(path, jwt_secret="admin-api-test-secret-with-at-least-32-bytes")) as instance:
        yield instance


def admin_login(client: TestClient) -> tuple[dict, dict[str, str]]:
    response = client.post("/api/v1/admin/auth/login", json={"username": "admin", "password": "admin1234"})
    assert response.status_code == 200
    payload = response.json()
    return payload, {"Authorization": f"Bearer {payload['access_token']}"}


def test_admin_identity_is_separate_from_user_identity(client: TestClient):
    admin, admin_headers = admin_login(client)
    user = client.post("/api/v1/auth/register", json={"username": "student", "password": "student123"}).json()
    user_headers = {"Authorization": f"Bearer {user['access_token']}"}

    assert admin["admin"]["role"] == "admin"
    assert client.get("/api/v1/admin/dashboard", headers=user_headers).status_code == 401
    assert client.get("/api/v1/me/profile", headers=admin_headers).status_code == 401
    assert client.get("/api/v1/admin/dashboard", headers=admin_headers).status_code == 200


def test_course_change_uses_row_version_and_updates_resolution_alias(client: TestClient):
    _, headers = admin_login(client)
    created = client.post("/api/v1/admin/courses", headers=headers, json={
        "course_id": "C_GOV_AI",
        "course_name": "智能治理导论",
        "difficulty_level": "入门",
        "fields": ["公共管理", "人工智能"],
        "is_advanced": False,
        "prerequisite_course_ids": [],
    })
    assert created.status_code == 201
    assert created.json()["row_version"] == 1

    alias = client.post("/api/v1/admin/courses/C_GOV_AI/aliases", headers=headers, json={"alias_text": "智慧政府"})
    assert alias.status_code == 201
    assert client.app.state.services.catalog.resolve("智慧政府")[0]["course_id"] == "C_GOV_AI"

    updated = client.patch("/api/v1/admin/courses/C_GOV_AI", headers=headers, json={
        "row_version": 1,
        "course_name": "智能治理基础",
    })
    assert updated.status_code == 200
    assert updated.json()["row_version"] == 2
    stale = client.patch("/api/v1/admin/courses/C_GOV_AI", headers=headers, json={
        "row_version": 1,
        "course_name": "过期修改",
    })
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "ROW_VERSION_CONFLICT"


def test_hyperparameter_grid_limit_results_and_policy_activation(client: TestClient):
    _, headers = admin_login(client)
    base_parameters = {
        "k_neighbors": [10, 20], "candidate_size": [50], "top_n": [10],
        "implicit_score": [1.0], "enforce_prerequisites": [True],
        "fairness_lambda": [0.0, 0.15, 0.3], "target_gap": [0.05],
        "max_total_cost": [0.08],
    }
    experiment = client.post("/api/v1/admin/hyperparameter-experiments", headers=headers, json={
        "name": "公平参数网格", "mode": "GRID", "parameters": base_parameters,
    })
    assert experiment.status_code == 202
    assert experiment.json()["combination_count"] == 6
    experiment_id = experiment.json()["experiment_id"]
    results = client.get(
        f"/api/v1/admin/hyperparameter-experiments/{experiment_id}/results", headers=headers
    ).json()["items"]
    assert len(results) == 6
    assert any(item["is_pareto_optimal"] for item in results)

    policy = client.post(
        f"/api/v1/admin/hyperparameter-results/{results[-1]['result_id']}:create-policy-draft",
        headers=headers,
        json={"reason": "位于质量与公平差距的候选边界"},
    )
    assert policy.status_code == 202
    activated = client.post(
        f"/api/v1/admin/fairness-policies/{policy.json()['policy_id']}:activate",
        headers=headers,
        json={"row_version": 1, "expected_active_policy_id": None, "reason": "离线检查通过"},
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"
    assert client.get("/api/v1/admin/system/health", headers=headers).json()["status"] == "HEALTHY"

    too_many = dict(base_parameters)
    too_many["k_neighbors"] = list(range(1, 102))
    limited = client.post("/api/v1/admin/hyperparameter-experiments", headers=headers, json={
        "name": "超过组合上限", "mode": "GRID", "parameters": too_many,
    })
    assert limited.status_code == 422
    assert limited.json()["error"]["code"] == "HYPERPARAMETER_COMBINATION_LIMIT"


def test_admin_account_guards_and_audit_log(client: TestClient):
    admin, headers = admin_login(client)
    current = admin["admin"]
    self_disable = client.post(
        f"/api/v1/admin/admins/{current['admin_id']}:disable", headers=headers,
        json={"row_version": current["row_version"]},
    )
    assert self_disable.status_code == 409

    second = client.post("/api/v1/admin/admins", headers=headers, json={
        "username": "catalog_admin", "display_name": "目录管理员", "password": "catalog1234",
    })
    assert second.status_code == 201
    assert second.json()["role"] == "admin"
    disabled = client.post(
        f"/api/v1/admin/admins/{second.json()['admin_id']}:disable", headers=headers,
        json={"row_version": second.json()["row_version"]},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "DISABLED"
    audits = client.get("/api/v1/admin/audit-logs", headers=headers).json()["items"]
    assert any(item["action"] == "ADMIN_DISABLE" and item["priority"] == "HIGH" for item in audits)


def test_course_import_validates_before_transactional_commit(client: TestClient):
    _, headers = admin_login(client)
    content = "course_id,course_name,difficulty_level,fields\nC_IMPORT,数据伦理,入门,人工智能\n"
    validated = client.post("/api/v1/admin/course-imports", headers=headers, json={
        "filename": "courses.csv", "file_format": "CSV", "content": content,
    })
    assert validated.status_code == 202
    assert validated.json()["status"] == "VALIDATED"
    assert client.get("/api/v1/admin/courses/C_IMPORT", headers=headers).status_code == 404

    committed = client.post(
        f"/api/v1/admin/course-imports/{validated.json()['import_id']}:commit",
        headers=headers,
        json={"row_version": validated.json()["row_version"]},
    )
    assert committed.status_code == 200
    assert committed.json()["status"] == "COMMITTED"
    course = client.get("/api/v1/admin/courses/C_IMPORT", headers=headers).json()
    assert course["course_name"] == "数据伦理"
    assert client.app.state.services.catalog.get("C_IMPORT") is not None

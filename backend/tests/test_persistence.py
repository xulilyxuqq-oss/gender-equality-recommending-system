from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from dataclasses import dataclass
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest

from backend.app.database import Database
from backend.app.migrations import apply_migrations
from backend.app.repositories import ApplicationRepository, ChatTransition
from backend.app.store import AuthService, now
from backend.tests.db_support import build_test_database


@dataclass(frozen=True)
class Services:
    repository: ApplicationRepository
    auth: AuthService


@pytest.fixture
def database(tmp_path: Path) -> Database:
    database = Database(build_test_database(tmp_path / "persistence.sqlite3"))
    apply_migrations(database, Path(__file__).parents[1] / "migrations")
    return database


def services(database: Database) -> Services:
    repository = ApplicationRepository(database)
    return Services(repository, AuthService(repository, "test-jwt-secret"))


def test_account_profile_and_refresh_token_survive_service_restart(database: Database):
    first = services(database)
    account = first.auth.create_account("alice", "password123")
    tokens = first.auth.issue_tokens(account["account_id"])
    first.repository.patch_profile(account["account_id"], 0, "Alice", 2)

    second = services(Database(database.path))

    assert second.auth.authenticate("ALICE", "password123")["account_id"] == account["account_id"]
    assert second.repository.get_profile(account["account_id"])["display_name"] == "Alice"
    replacement = second.auth.refresh(tokens["refresh_token"])
    assert replacement is not None
    assert replacement["refresh_token"] != tokens["refresh_token"]
    assert second.auth.refresh(tokens["refresh_token"]) is None


def test_duplicate_case_insensitive_usernames_are_rejected(database: Database):
    auth = services(database).auth
    auth.create_account("Alice", "password123")

    with pytest.raises(ValueError, match="duplicate"):
        auth.create_account("aLiCe", "password123")


def test_unicode_casefolded_usernames_are_unique_and_authenticate(database: Database):
    auth = services(database).auth
    account = auth.create_account("Älice", "password123")

    assert auth.authenticate("äLICE", "password123")["account_id"] == account["account_id"]
    with pytest.raises(ValueError, match="duplicate"):
        auth.create_account("älice", "password123")


def test_profile_patch_rejects_stale_version(database: Database):
    service = services(database)
    account = service.auth.create_account("alice", "password123")
    service.repository.patch_profile(account["account_id"], 0, "Alice", 2)

    with pytest.raises(ValueError, match="profile version conflict"):
        service.repository.patch_profile(account["account_id"], 0, "Alicia", 2)


def test_expired_refresh_token_cannot_be_rotated(database: Database):
    service = services(database)
    account = service.auth.create_account("alice", "password123")
    token = "expired-refresh-token"
    service.repository.store_refresh_token(
        account["account_id"],
        hashlib.sha256(token.encode("utf-8")).hexdigest(),
        now() - timedelta(seconds=1),
    )

    assert service.auth.refresh(token) is None


def test_refresh_token_expiring_while_waiting_for_writer_cannot_rotate(database: Database):
    service = services(database)
    account = service.auth.create_account("alice", "password123")
    token = "contended-refresh-token"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    replacement_hash = hashlib.sha256(b"replacement-refresh-token").hexdigest()
    service.repository.store_refresh_token(
        account["account_id"], token_hash, now() + timedelta(milliseconds=100)
    )
    started = threading.Event()
    result: list[dict[str, str] | None] = []

    def rotate() -> None:
        started.set()
        result.append(
            service.repository.rotate_refresh_token(
                token_hash, replacement_hash, now() + timedelta(days=7)
            )
        )

    with database.transaction(immediate=True):
        worker = threading.Thread(target=rotate)
        worker.start()
        assert started.wait(timeout=1)
        time.sleep(0.25)
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert result == [None]


def test_logout_is_idempotent(database: Database):
    auth = services(database).auth
    account = auth.create_account("alice", "password123")
    tokens = auth.issue_tokens(account["account_id"])

    assert auth.logout(tokens["refresh_token"]) is None
    assert auth.logout(tokens["refresh_token"]) is None
    assert auth.refresh(tokens["refresh_token"]) is None


def test_event_sequence_is_owned_atomic_and_durable(database: Database):
    from concurrent.futures import ThreadPoolExecutor

    service = services(database)
    owner = service.auth.create_account("sequence_owner", "password123")["account_id"]
    stranger = service.auth.create_account("sequence_stranger", "password123")["account_id"]
    session_id = service.repository.create_chat_session(owner, "RECOMMENDATION")["chat_session_id"]
    with pytest.raises(KeyError):
        service.repository.next_event_sequence(stranger, session_id)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: services(database).repository.next_event_sequence(owner, session_id), range(12)))
    assert sorted(results) == list(range(1, 13))
    assert services(database).repository.get_chat_session(owner, session_id)["event_seq"] == 12


def test_refresh_token_table_contains_only_digest(database: Database):
    service = services(database)
    account = service.auth.create_account("alice", "password123")
    tokens = service.auth.issue_tokens(account["account_id"])

    with database.connect() as connection:
        row = connection.execute("SELECT token_hash FROM app_refresh_tokens").fetchone()

    assert row is not None
    assert row["token_hash"] == hashlib.sha256(tokens["refresh_token"].encode("utf-8")).hexdigest()
    assert row["token_hash"] != tokens["refresh_token"]


def ready_session(database: Database):
    service = services(database)
    account = service.repository.create_account("bob", "unused-test-hash")
    account_id = account["account_id"]
    session = service.repository.create_chat_session(account_id, "RECOMMENDATION")
    session_id = session["chat_session_id"]
    service.repository.patch_session_draft(account_id, session_id, display_name="Bob", gender_code=1)
    return service.repository, account_id, session_id


def candidate(course_id="C_BASE"):
    return {"course_id": course_id, "match_score": 0.9, "match_reason": "FUZZY_NAME"}


def test_agent_memory_round_trip_is_versioned(database: Database):
    repo, account_id, session_id = ready_session(database)

    initial = repo.get_chat_memory(session_id)
    assert initial == {
        "chat_session_id": session_id,
        "summary_text": "",
        "extracted_facts": {},
        "memory_version": 0,
    }

    saved = repo.save_chat_memory(
        account_id,
        session_id,
        "用户已提供姓名，正在整理课程。",
        {"display_name": "Bob", "gender_code": 1},
        expected_version=0,
    )
    assert saved["memory_version"] == 1
    assert saved["extracted_facts"]["display_name"] == "Bob"

    with pytest.raises(ValueError, match="memory version conflict"):
        repo.save_chat_memory(account_id, session_id, "stale", {}, expected_version=0)

    restarted = ApplicationRepository(Database(database.path))
    assert restarted.get_chat_memory(session_id) == saved


def test_agent_run_steps_are_durable_and_only_one_run_is_active(database: Database):
    repo, account_id, session_id = ready_session(database)
    message, _ = repo.append_message(session_id, "user", "我叫 Bob", "agent-message")

    run = repo.begin_agent_run(account_id, session_id, message["message_id"])
    assert run["status"] == "RUNNING"
    step = repo.record_agent_step(
        account_id,
        run["run_id"],
        1,
        "TOOL_CALL",
        "get_profile_context",
        {"session_id": session_id},
        {"state": "COLLECTING_NAME"},
        "COMPLETED",
    )
    assert step["step_index"] == 1
    assert repo.finish_agent_run(account_id, run["run_id"], "COMPLETED")["status"] == "COMPLETED"

    second = repo.begin_agent_run(account_id, session_id, message["message_id"])
    assert second["run_id"] != run["run_id"]

    with pytest.raises(ValueError, match="active agent run"):
        repo.begin_agent_run(account_id, session_id, message["message_id"])

    restarted = ApplicationRepository(Database(database.path))
    loaded = restarted.get_agent_run(account_id, second["run_id"])
    assert loaded is not None
    assert loaded["steps"] == []


def test_agent_fallback_transition_commits_after_user_message(database: Database):
    service = services(database)
    account = service.auth.create_account("fallback-transition", "password123")
    session = service.repository.create_chat_session(account["account_id"], "RECOMMENDATION")
    user_message, _ = service.repository.append_message(
        session["chat_session_id"], "user", "Alice", "fallback-transition-message"
    )
    transition = ChatTransition(
        "COLLECTING_GENDER", "Alice", None,
        "好的，Alice。请选择性别数据组。", "profile.updated", []
    )

    committed = service.repository.commit_agent_transition(
        account["account_id"], session["chat_session_id"], transition
    )

    assert committed["session"]["state"] == "COLLECTING_GENDER"
    assert committed["session"]["messages"][-1]["role"] == "assistant"
    assert committed["session"]["messages"][-1]["content"] == transition.reply


def test_course_confirmation_closes_waiting_agent_run(database: Database):
    repo, account_id, session_id = ready_session(database)
    resolution = repo.create_resolution(
        account_id, session_id, "编程基础", [candidate("C_BASE")], now() + timedelta(minutes=30)
    )
    repo.patch_session_draft(account_id, session_id, state="WAITING_COURSE_CONFIRMATION")
    message, _ = repo.append_message(session_id, "user", "确认课程", "waiting-run-message")
    run = repo.begin_agent_run(account_id, session_id, message["message_id"])
    repo.finish_agent_run(account_id, run["run_id"], "WAITING_USER")

    repo.decide_resolution(account_id, session_id, resolution["resolution_id"], "C_BASE", False)

    assert repo.get_agent_run(account_id, run["run_id"])["status"] == "COMPLETED"


def test_agent_message_and_run_are_accepted_atomically(database: Database):
    service = services(database)
    account = service.auth.create_account("atomic-agent", "password123")
    session = service.repository.create_chat_session(account["account_id"], "RECOMMENDATION")

    message, run, created = service.repository.accept_agent_message(
        account["account_id"], session["chat_session_id"], "Alice", "atomic-message"
    )
    assert created is True
    assert message["role"] == "user"
    assert run["trigger_message_id"] == message["message_id"]

    duplicate, no_run, created = service.repository.accept_agent_message(
        account["account_id"], session["chat_session_id"], "changed", "atomic-message"
    )
    assert created is False
    assert no_run is None
    assert duplicate == message

    service.repository.finish_agent_run(account["account_id"], run["run_id"], "WAITING_USER")
    with pytest.raises(ValueError, match="active agent run"):
        service.repository.accept_agent_message(
            account["account_id"], session["chat_session_id"], "second", "second-message"
        )
    assert len(service.repository.get_chat_session(account["account_id"], session["chat_session_id"])["messages"]) == 2


def test_chat_messages_are_durable_ordered_and_client_ids_are_idempotent(database: Database):
    repo, account_id, session_id = ready_session(database)
    first, created = repo.append_message(session_id, "user", "first", "client-1")
    assert created is True
    repo.append_message(session_id, "assistant", "reply")
    restarted = ApplicationRepository(Database(database.path))
    duplicate, created = restarted.append_message(session_id, "user", "changed", "client-1")
    assert created is False
    assert duplicate == first
    session = restarted.get_chat_session(account_id, session_id)
    assert [item["content"] for item in session["messages"]][-2:] == ["first", "reply"]
    assert session["profile_draft"]["display_name"] == "Bob"


def test_chat_transition_reads_state_under_cross_worker_write_lock(database: Database, monkeypatch):
    from backend.app.catalog import CourseCatalog
    from backend.app.main import prepare_chat_transition

    first = ApplicationRepository(database)
    account_id = first.create_account("concurrent-chat", "unused")["account_id"]
    session_id = first.create_chat_session(account_id, "RECOMMENDATION")["chat_session_id"]
    second_database = Database(database.path)
    second = ApplicationRepository(second_database)
    catalog = CourseCatalog(database)
    first_planning, second_begin, second_planning, release_first = [threading.Event() for _ in range(4)]
    original_connect = second_database.connect

    @contextmanager
    def observed_connect():
        with original_connect() as connection:
            connection.set_trace_callback(lambda sql: second_begin.set() if sql == "BEGIN IMMEDIATE" else None)
            yield connection

    monkeypatch.setattr(second_database, "connect", observed_connect)

    def first_plan(session):
        first_planning.set()
        assert release_first.wait(timeout=5)
        return prepare_chat_transition(catalog, session, "Alice")

    def second_plan(session):
        second_planning.set()
        return prepare_chat_transition(catalog, session, "2")

    with ThreadPoolExecutor(max_workers=2) as executor:
        alice = executor.submit(first.accept_chat_message, account_id, session_id, "Alice", "alice", first_plan)
        try:
            assert first_planning.wait(timeout=3)
            gender = executor.submit(second.accept_chat_message, account_id, session_id, "2", "gender", second_plan)
            assert second_begin.wait(timeout=3)
            assert not second_planning.is_set()
        finally:
            release_first.set()
        alice.result(timeout=5)
        gender.result(timeout=5)

    session = first.get_chat_session(account_id, session_id)
    assert session["profile_draft"]["display_name"] == "Alice"
    assert session["profile_draft"]["gender_code"] == 2
    assert session["state"] == "COLLECTING_COURSES"
    assert [message["content"] for message in session["messages"] if message["role"] == "user"] == ["Alice", "2"]


def test_cross_account_sessions_and_mutations_are_hidden(database: Database):
    repo, account_id, session_id = ready_session(database)
    other_id = repo.create_account("other", "unused")["account_id"]
    resolution = repo.create_resolution(account_id, session_id, "base", [candidate()], now() + timedelta(minutes=30))
    assert repo.get_chat_session(other_id, session_id) is None
    for operation in (
        lambda: repo.patch_session_draft(other_id, session_id, display_name="forged"),
        lambda: repo.add_session_course(other_id, session_id, "C_BASE"),
        lambda: repo.remove_session_course(other_id, session_id, "C_BASE"),
        lambda: repo.create_resolution(other_id, session_id, "base", [candidate()], now()),
        lambda: repo.decide_resolution(other_id, session_id, resolution["resolution_id"], "C_BASE", False),
        lambda: repo.confirm_profile(other_id, session_id, 0),
    ):
        with pytest.raises(KeyError):
            operation()
    assert repo.get_chat_session(account_id, session_id)["profile_draft"]["display_name"] == "Bob"


def test_resolution_candidates_are_ordered_durable_and_forgery_is_rejected(database: Database):
    repo, account_id, session_id = ready_session(database)
    resolution = repo.create_resolution(account_id, session_id, "base", [candidate("C_DATA"), candidate()], now() + timedelta(minutes=30))
    restarted = ApplicationRepository(Database(database.path))
    session = restarted.get_chat_session(account_id, session_id)
    assert [item["course_id"] for item in session["course_resolutions"][0]["candidates"]] == ["C_DATA", "C_BASE"]
    assert session["course_resolutions"][0]["candidates"][1]["course_name"] == "编程基础"
    assert session["course_resolutions"][0]["candidates"][1]["fields"] == ["计算机科学"]
    with pytest.raises(ValueError, match="invalid resolution candidate"):
        restarted.decide_resolution(account_id, session_id, resolution["resolution_id"], "C_DB", False)
    decided = restarted.decide_resolution(account_id, session_id, resolution["resolution_id"], "C_BASE", False)
    repeated = restarted.decide_resolution(account_id, session_id, resolution["resolution_id"], "C_BASE", False)
    assert repeated == decided
    assert decided["state"] == "PROFILE_REVIEW"
    assert decided["profile_draft"]["completed_courses"] == [{"course_id": "C_BASE", "course_name": "编程基础"}]
    with pytest.raises(ValueError, match="already finalized"):
        restarted.decide_resolution(account_id, session_id, resolution["resolution_id"], "C_DATA", False)


def test_rejected_and_expired_resolutions_are_durable(database: Database):
    repo, account_id, session_id = ready_session(database)
    rejected = repo.create_resolution(account_id, session_id, "unknown", [], now() + timedelta(minutes=30))
    repo.decide_resolution(account_id, session_id, rejected["resolution_id"], None, True)
    expired = repo.create_resolution(account_id, session_id, "old", [candidate()], now() - timedelta(seconds=1))
    with pytest.raises(ValueError, match="resolution expired"):
        repo.decide_resolution(account_id, session_id, expired["resolution_id"], "C_BASE", False)
    session = ApplicationRepository(Database(database.path)).get_chat_session(account_id, session_id)
    assert [item["status"] for item in session["course_resolutions"]] == ["REJECTED", "EXPIRED"]
    assert session["profile_draft"]["completed_courses"] == []
    assert session["state"] == "COLLECTING_COURSES"


@pytest.mark.parametrize("later_state", ["PROFILE_REVIEW", "COMPLETED"])
def test_expired_resolution_retry_preserves_later_session_state(database: Database, later_state: str):
    repo, account_id, session_id = ready_session(database)
    resolution = repo.create_resolution(
        account_id, session_id, "old", [candidate()], now() - timedelta(seconds=1)
    )
    with pytest.raises(ValueError, match="resolution expired"):
        repo.decide_resolution(account_id, session_id, resolution["resolution_id"], "C_BASE", False)
    repo.patch_session_draft(account_id, session_id, state="PROFILE_REVIEW")
    if later_state == "COMPLETED":
        repo.confirm_profile(account_id, session_id, 0)
    before = repo.get_chat_session(account_id, session_id)
    assert before["state"] == later_state
    with database.connect() as connection:
        session_row_before = dict(connection.execute(
            "SELECT * FROM app_chat_sessions WHERE chat_session_id = ?", (session_id,)
        ).fetchone())

    restarted = ApplicationRepository(Database(database.path))
    with pytest.raises(ValueError, match="resolution expired"):
        restarted.decide_resolution(account_id, session_id, resolution["resolution_id"], "C_BASE", False)

    assert restarted.get_chat_session(account_id, session_id) == before
    with database.connect() as connection:
        session_row_after = dict(connection.execute(
            "SELECT * FROM app_chat_sessions WHERE chat_session_id = ?", (session_id,)
        ).fetchone())
    assert session_row_after == session_row_before


def test_pending_resolutions_block_confirmation_even_if_state_is_review(database: Database):
    repo, account_id, session_id = ready_session(database)
    repo.create_resolution(account_id, session_id, "base", [candidate()], now() + timedelta(minutes=30))
    repo.patch_session_draft(account_id, session_id, state="PROFILE_REVIEW")
    with pytest.raises(ValueError, match="pending course resolutions"):
        repo.confirm_profile(account_id, session_id, 0)
    assert repo.get_profile(account_id)["user_id"] is None


def test_confirm_profile_promotes_draft_atomically(database: Database):
    repo, account_id, session_id = ready_session(database)
    repo.add_session_course(account_id, session_id, "C_DATA")
    repo.add_session_course(account_id, session_id, "C_BASE")
    repo.add_session_course(account_id, session_id, "C_DATA")
    profile = repo.confirm_profile(account_id, session_id, expected_version=0)
    assert profile["status"] == "CONFIRMED"
    assert profile["profile_version"] == 1
    assert profile["confirmed_at"]
    assert profile["user_id"].startswith("U_APP_") and len(profile["user_id"]) == 38
    assert [item["course_id"] for item in profile["completed_courses"]] == ["C_DATA", "C_BASE"]
    assert ApplicationRepository(Database(database.path)).get_profile(account_id) == profile
    with database.connect() as connection:
        assert tuple(connection.execute("SELECT user_name, gender_code FROM users WHERE user_id = ?", (profile["user_id"],)).fetchone()) == ("Bob", 1)
        assert [tuple(row) for row in connection.execute("SELECT course_id, comment FROM interactions WHERE user_id = ? ORDER BY course_id", (profile["user_id"],))] == [("C_BASE", 0), ("C_DATA", 0)]
    session = repo.get_chat_session(account_id, session_id)
    assert session["state"] == "COMPLETED"
    assert session["profile_version"] == 1
    new_session = repo.create_chat_session(account_id, "RECOMMENDATION")
    assert new_session["profile_draft"]["completed_courses"] == profile["completed_courses"]


def test_reconfirmation_preserves_explicit_ratings_and_removes_only_obsolete_implicit_courses(database: Database):
    repo, account_id, session_id = ready_session(database)
    for course_id in ["C_BASE", "C_DATA", "C_DB"]:
        repo.add_session_course(account_id, session_id, course_id)
    profile = repo.confirm_profile(account_id, session_id, 0)
    with database.transaction() as connection:
        connection.execute("UPDATE interactions SET comment = 4.5 WHERE user_id = ? AND course_id IN ('C_BASE', 'C_DB')", (profile["user_id"],))
    session_id = repo.create_chat_session(account_id, "RECOMMENDATION")["chat_session_id"]
    repo.remove_session_course(account_id, session_id, "C_DATA")
    repo.remove_session_course(account_id, session_id, "C_DB")
    repo.add_session_course(account_id, session_id, "C_LA")
    repo.patch_session_draft(account_id, session_id, display_name="Bobby", gender_code=2)
    updated = repo.confirm_profile(account_id, session_id, 1)
    assert updated["user_id"] == profile["user_id"]
    assert updated["profile_version"] == 2
    with database.connect() as connection:
        assert [tuple(row) for row in connection.execute("SELECT course_id, comment FROM interactions WHERE user_id = ? ORDER BY course_id", (profile["user_id"],))] == [("C_BASE", 4.5), ("C_DB", 4.5), ("C_LA", 0)]
        assert [tuple(row) for row in connection.execute("SELECT course_id, completed_position FROM user_completed_courses WHERE user_id = ? ORDER BY completed_position", (profile["user_id"],))] == [("C_BASE", 0), ("C_LA", 1)]
        assert tuple(connection.execute("SELECT user_name, gender_code FROM users WHERE user_id = ?", (profile["user_id"],)).fetchone()) == ("Bobby", 2)


def test_confirmation_rejects_stale_version_and_incomplete_or_unready_draft(database: Database):
    repo, account_id, session_id = ready_session(database)
    repo.patch_profile(account_id, 0, "updated elsewhere", 1)
    with pytest.raises(ValueError, match="profile version conflict"):
        repo.confirm_profile(account_id, session_id, 0)
    with pytest.raises(ValueError, match="profile version conflict"):
        repo.confirm_profile(account_id, session_id, 1)
    fresh = repo.create_chat_session(account_id, "RECOMMENDATION")["chat_session_id"]
    with pytest.raises(ValueError, match="pending course resolutions"):
        repo.confirm_profile(account_id, fresh, 1)
    with database.transaction() as connection:
        connection.execute("UPDATE app_chat_sessions SET state = 'PROFILE_REVIEW', draft_display_name = '' WHERE chat_session_id = ?", (fresh,))
    with pytest.raises(ValueError, match="profile incomplete"):
        repo.confirm_profile(account_id, fresh, 1)


@pytest.mark.parametrize("reconfirm", [False, True])
def test_confirmation_failure_rolls_back_every_table(database: Database, reconfirm: bool):
    repo, account_id, session_id = ready_session(database)
    repo.add_session_course(account_id, session_id, "C_BASE")
    if reconfirm:
        repo.confirm_profile(account_id, session_id, 0)
        session_id = repo.create_chat_session(account_id, "RECOMMENDATION")["chat_session_id"]
        repo.remove_session_course(account_id, session_id, "C_BASE")
        repo.add_session_course(account_id, session_id, "C_DATA")
        repo.patch_session_draft(account_id, session_id, display_name="changed", gender_code=2)
    tables = ["users", "user_completed_courses", "interactions", "app_profiles", "app_chat_sessions", "app_chat_session_courses"]
    with database.transaction() as connection:
        before = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")] for table in tables}
        connection.execute("""CREATE TRIGGER fail_confirmation BEFORE UPDATE OF state ON app_chat_sessions
            WHEN NEW.state = 'COMPLETED' BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        repo.confirm_profile(account_id, session_id, int(reconfirm))
    with database.connect() as connection:
        after = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")] for table in tables}
    assert after == before


@dataclass(frozen=True)
class ConfirmedAccount:
    account_id: str
    user_id: str
    profile_version: int


@pytest.fixture
def confirmed_account(database: Database) -> ConfirmedAccount:
    repository, account_id, session_id = ready_session(database)
    repository.add_session_course(account_id, session_id, "C_BASE")
    profile = repository.confirm_profile(account_id, session_id, 0)
    assert profile["user_id"] is not None
    return ConfirmedAccount(account_id, profile["user_id"], profile["profile_version"])


def recommendation_items(*course_ids: str) -> list[dict[str, object]]:
    courses = {
        "C_ADV": {
            "course_id": "C_ADV", "course_name": "高级算法", "fields": ["计算机科学"],
            "difficulty_level": "高阶", "is_advanced": True, "prerequisites_satisfied": True,
        },
        "C_DATA": {
            "course_id": "C_DATA", "course_name": "数据结构", "fields": ["计算机科学"],
            "difficulty_level": "中级", "is_advanced": False, "prerequisites_satisfied": True,
        },
        "C_DB": {
            "course_id": "C_DB", "course_name": "数据库基础", "fields": ["数据库"],
            "difficulty_level": "入门", "is_advanced": False, "prerequisites_satisfied": True,
        },
    }
    return [
        {
            "rank": rank,
            "course": courses[course_id],
            "reason_codes": ["SIMILAR_USERS_COMPLETED", "PREREQUISITES_SATISFIED"],
            "reason_text": "推荐理由",
        }
        for rank, course_id in enumerate(course_ids, start=1)
    ]


def test_snapshot_and_favorite_survive_restart(database: Database, confirmed_account: ConfirmedAccount):
    first = services(database)
    saved = first.repository.save_recommendation(
        confirmed_account.account_id, confirmed_account.user_id, confirmed_account.profile_version,
        "collaborative", recommendation_items("C_ADV"),
    )
    first.repository.add_favorite(confirmed_account.account_id, "C_ADV")

    second = services(Database(database.path))

    assert second.repository.get_recommendation(
        confirmed_account.account_id, saved["recommendation_id"]
    )["items"][0]["course"]["course_id"] == "C_ADV"
    assert saved["algorithm_version"] == "jaccard-v1"
    assert second.repository.list_favorites(
        confirmed_account.account_id, 0, 20
    )[0][0]["course"]["course_id"] == "C_ADV"


@pytest.mark.parametrize("change", ["version", "status", "user"])
def test_recommendation_save_rejects_changed_profile(database: Database, confirmed_account: ConfirmedAccount, change):
    with database.transaction(immediate=True) as connection:
        if change == "version":
            connection.execute("UPDATE app_profiles SET profile_version = profile_version + 1 WHERE account_id = ?",
                               (confirmed_account.account_id,))
        elif change == "status":
            connection.execute("UPDATE app_profiles SET status = 'DRAFT' WHERE account_id = ?",
                               (confirmed_account.account_id,))
        else:
            connection.execute("UPDATE app_profiles SET user_id = 'U_APP_TARGET' WHERE account_id = ?",
                               (confirmed_account.account_id,))
    with pytest.raises(ValueError, match="profile version conflict"):
        services(database).repository.save_recommendation(
            confirmed_account.account_id, confirmed_account.user_id, confirmed_account.profile_version,
            "collaborative", recommendation_items("C_ADV"),
        )
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM app_recommendations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM app_recommendation_items").fetchone()[0] == 0


def test_recommendation_keeps_immutable_course_snapshot(database: Database, confirmed_account: ConfirmedAccount):
    repository = services(database).repository
    saved = repository.save_recommendation(
        confirmed_account.account_id, confirmed_account.user_id, confirmed_account.profile_version,
        "collaborative", recommendation_items("C_ADV"),
    )
    with database.transaction() as connection:
        connection.execute("UPDATE courses SET course_name = '已改名' WHERE course_id = 'C_ADV'")

    restored = repository.get_recommendation(confirmed_account.account_id, saved["recommendation_id"])

    assert restored is not None
    assert restored["items"][0]["course"]["course_name"] == "高级算法"
    assert restored["items"][0]["reason_codes"] == ["SIMILAR_USERS_COMPLETED", "PREREQUISITES_SATISFIED"]


def test_recommendation_save_rolls_back_header_when_an_item_fails(database: Database, confirmed_account: ConfirmedAccount):
    repository = services(database).repository
    duplicate = recommendation_items("C_ADV", "C_ADV")

    with pytest.raises(sqlite3.IntegrityError):
        repository.save_recommendation(
            confirmed_account.account_id, confirmed_account.user_id, confirmed_account.profile_version,
            "collaborative", duplicate,
        )

    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM app_recommendations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM app_recommendation_items").fetchone()[0] == 0


def test_recommendation_listing_is_cursor_ready_and_soft_delete_is_hidden(database: Database, confirmed_account: ConfirmedAccount):
    repository = services(database).repository
    first = repository.save_recommendation(
        confirmed_account.account_id, confirmed_account.user_id, confirmed_account.profile_version,
        "collaborative", recommendation_items("C_ADV"),
    )
    second = repository.save_recommendation(
        confirmed_account.account_id, confirmed_account.user_id, confirmed_account.profile_version,
        "collaborative", recommendation_items("C_DATA"),
    )
    third = repository.save_recommendation(
        confirmed_account.account_id, confirmed_account.user_id, confirmed_account.profile_version,
        "collaborative", recommendation_items("C_DB"),
    )

    page, has_more = repository.list_recommendations(confirmed_account.account_id, 0, 2)
    assert [item["recommendation_id"] for item in page] == [third["recommendation_id"], second["recommendation_id"]]
    assert has_more is True
    page, has_more = repository.list_recommendations(confirmed_account.account_id, 2, 2)
    assert [item["recommendation_id"] for item in page] == [first["recommendation_id"]]
    assert has_more is False

    assert repository.soft_delete_recommendation(confirmed_account.account_id, second["recommendation_id"]) is True
    assert repository.get_recommendation(confirmed_account.account_id, second["recommendation_id"]) is None
    assert [item["recommendation_id"] for item in repository.list_recommendations(confirmed_account.account_id, 0, 20)[0]] == [third["recommendation_id"], first["recommendation_id"]]


def test_favorites_are_idempotent_paginated_and_account_isolated(database: Database, confirmed_account: ConfirmedAccount):
    repository = services(database).repository
    initial = repository.add_favorite(confirmed_account.account_id, "C_ADV")
    repeated = repository.add_favorite(confirmed_account.account_id, "C_ADV")
    repository.add_favorite(confirmed_account.account_id, "C_DATA")
    repository.add_favorite(confirmed_account.account_id, "C_DB")
    other = repository.create_account("favorite-other", "unused")["account_id"]
    repository.add_favorite(other, "C_ADV")

    assert repeated["created_at"] == initial["created_at"]
    page, has_more = repository.list_favorites(confirmed_account.account_id, 0, 2)
    assert [item["course"]["course_id"] for item in page] == ["C_DB", "C_DATA"]
    assert has_more is True
    page, has_more = repository.list_favorites(confirmed_account.account_id, 2, 2)
    assert [item["course"]["course_id"] for item in page] == ["C_ADV"]
    assert has_more is False
    assert [item["course"]["course_id"] for item in repository.list_favorites(other, 0, 20)[0]] == ["C_ADV"]

    assert repository.remove_favorite(confirmed_account.account_id, "C_ADV") is True
    assert repository.remove_favorite(confirmed_account.account_id, "C_ADV") is False
    assert repository.list_favorites(other, 0, 20)[0][0]["course"]["course_id"] == "C_ADV"


def test_recommendations_are_non_disclosing_across_accounts(database: Database, confirmed_account: ConfirmedAccount):
    repository = services(database).repository
    saved = repository.save_recommendation(
        confirmed_account.account_id, confirmed_account.user_id, confirmed_account.profile_version,
        "collaborative", recommendation_items("C_ADV"),
    )
    other = repository.create_account("recommendation-other", "unused")["account_id"]

    assert repository.get_recommendation(other, saved["recommendation_id"]) is None
    assert repository.soft_delete_recommendation(other, saved["recommendation_id"]) is False
    assert repository.list_recommendations(other, 0, 20) == ([], False)
    assert repository.get_recommendation(confirmed_account.account_id, saved["recommendation_id"]) is not None

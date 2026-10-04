from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from .database import Database
from .catalog import display_difficulty


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def _new_account_id() -> str:
    return f"acct_{uuid4().hex[:16]}"


def _canonical_username(username: str) -> str:
    return username.casefold()


def _account_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _profile_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    profile = dict(row)
    profile["completed_courses"] = []
    return profile


def _read_profile(connection: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    profile = _profile_from_row(connection.execute(
        "SELECT * FROM app_profiles WHERE account_id = ?", (account_id,)
    ).fetchone())
    if profile is None:
        raise KeyError(account_id)
    profile["completed_courses"] = [dict(row) for row in connection.execute(
        """SELECT c.course_id, c.course_name FROM user_completed_courses AS uc
           JOIN courses AS c ON c.course_id = uc.course_id
           WHERE uc.user_id = ? ORDER BY uc.completed_position, uc.course_id""",
        (profile["user_id"],),
    )]
    return profile


def _read_catalog_course(connection: sqlite3.Connection, course_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT course_id, course_name, difficulty_level, is_advanced
           FROM courses WHERE course_id = ?""",
        (course_id,),
    ).fetchone()
    if row is None:
        return None
    course = dict(row)
    course["difficulty_level"] = display_difficulty(course["difficulty_level"])
    course["is_advanced"] = bool(course["is_advanced"])
    course["fields"] = [field[0] for field in connection.execute(
        "SELECT field FROM course_fields WHERE course_id = ? ORDER BY field_position, field",
        (course_id,),
    )]
    return course


def _json_object(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("invalid course snapshot")
    return decoded


def _json_string_list(value: str) -> list[str]:
    decoded = json.loads(value)
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise ValueError("invalid recommendation reason codes")
    return decoded


def _read_recommendation(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    items = []
    for item in connection.execute(
        """SELECT rank, course_snapshot_json, reason_codes_json, reason_text
           FROM app_recommendation_items WHERE recommendation_id = ? ORDER BY rank""",
        (row["recommendation_id"],),
    ):
        items.append({
            "rank": item["rank"],
            "course": _json_object(item["course_snapshot_json"]),
            "reason_codes": _json_string_list(item["reason_codes_json"]),
            "reason_text": item["reason_text"],
        })
    return {
        "recommendation_id": row["recommendation_id"],
        "source": row["source"],
        "algorithm_version": row["algorithm_version"],
        "fairness_applied": bool(row["fairness_applied"]),
        "fairness_policy_version": row["fairness_policy_version"],
        "generated_at": row["generated_at"],
        "items": items,
    }


def _owned_session(connection: sqlite3.Connection, account_id: str, session_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM app_chat_sessions WHERE account_id = ? AND chat_session_id = ?",
        (account_id, session_id),
    ).fetchone()
    if row is None:
        raise KeyError(session_id)
    return row


def _read_resolution(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    candidates = []
    for candidate in connection.execute(
        """SELECT c.course_id, c.course_name, c.difficulty_level, c.is_advanced,
                  rc.match_score, rc.match_reason
           FROM app_resolution_candidates AS rc JOIN courses AS c ON c.course_id = rc.course_id
           WHERE rc.resolution_id = ? ORDER BY rc.candidate_rank""", (row["resolution_id"],)
    ):
        item = dict(candidate)
        item["is_advanced"] = bool(item["is_advanced"])
        item["difficulty_level"] = display_difficulty(item["difficulty_level"])
        item["fields"] = [field[0] for field in connection.execute(
            "SELECT field FROM course_fields WHERE course_id = ? ORDER BY field_position, field",
            (item["course_id"],),
        )]
        candidates.append(item)
    return {
        "resolution_id": row["resolution_id"], "query": row["query_text"],
        "status": row["status"], "selected_course_id": row["selected_course_id"],
        "expires_at": row["expires_at"], "candidates": candidates,
    }


def _read_session(connection: sqlite3.Connection, account_id: str, session_id: str) -> dict[str, Any]:
    row = _owned_session(connection, account_id, session_id)
    resolutions = [_read_resolution(connection, resolution) for resolution in connection.execute(
        "SELECT * FROM app_course_resolutions WHERE chat_session_id = ? ORDER BY rowid", (session_id,)
    )]
    return {
        "chat_session_id": session_id, "account_id": account_id, "purpose": row["purpose"],
        "state": row["state"], "profile_version": row["profile_version"],
        "event_seq": row["event_seq"], "created_at": row["created_at"],
        "profile_draft": {
            "display_name": row["draft_display_name"], "gender_code": row["draft_gender_code"],
            "completed_courses": [dict(course) for course in connection.execute(
                """SELECT c.course_id, c.course_name FROM app_chat_session_courses AS sc
                   JOIN courses AS c ON c.course_id = sc.course_id
                   WHERE sc.chat_session_id = ? ORDER BY sc.course_position, sc.course_id""", (session_id,)
            )],
        },
        "messages": [dict(message) for message in connection.execute(
            "SELECT message_id, role, content, created_at FROM app_chat_messages WHERE chat_session_id = ? ORDER BY rowid",
            (session_id,),
        )],
        "course_resolutions": resolutions,
        "resolution_ids": [resolution["resolution_id"] for resolution in resolutions],
    }


def _default_chat_memory(session_id: str) -> dict[str, Any]:
    return {
        "chat_session_id": session_id,
        "summary_text": "",
        "extracted_facts": {},
        "memory_version": 0,
    }


def _read_chat_memory(connection: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT chat_session_id, summary_text, extracted_facts_json, memory_version "
        "FROM app_chat_memory WHERE chat_session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return _default_chat_memory(session_id)
    facts = json.loads(row["extracted_facts_json"])
    if not isinstance(facts, dict):
        raise ValueError("invalid chat memory facts")
    return {
        "chat_session_id": row["chat_session_id"],
        "summary_text": row["summary_text"],
        "extracted_facts": facts,
        "memory_version": row["memory_version"],
    }


def _read_agent_run(connection: sqlite3.Connection, account_id: str, run_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM app_agent_runs WHERE account_id = ? AND run_id = ?",
        (account_id, run_id),
    ).fetchone()
    if row is None:
        raise KeyError(run_id)
    steps = [
        {
            "step_id": step["step_id"],
            "run_id": step["run_id"],
            "step_index": step["step_index"],
            "action_type": step["action_type"],
            "tool_name": step["tool_name"],
            "input": json.loads(step["input_json"]),
            "output": json.loads(step["output_json"]),
            "status": step["status"],
            "idempotency_key": step["idempotency_key"],
            "created_at": step["created_at"],
        }
        for step in connection.execute(
            "SELECT * FROM app_agent_steps WHERE run_id = ? ORDER BY step_index",
            (run_id,),
        )
    ]
    result = dict(row)
    result["steps"] = steps
    return result


def _insert_message(connection: sqlite3.Connection, session_id: str, role: str, content: str,
                    client_message_id: str | None = None) -> dict[str, Any]:
    message = {"message_id": f"msg_{uuid4().hex}", "role": role, "content": content, "created_at": _timestamp(_now())}
    connection.execute(
        """INSERT INTO app_chat_messages(message_id, chat_session_id, role, content, client_message_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (message["message_id"], session_id, role, content, client_message_id, message["created_at"]),
    )
    return message


def _insert_session_course(connection: sqlite3.Connection, session_id: str, course_id: str) -> None:
    if connection.execute("SELECT 1 FROM courses WHERE course_id = ?", (course_id,)).fetchone() is None:
        raise KeyError(course_id)
    connection.execute(
        """INSERT INTO app_chat_session_courses(chat_session_id, course_id, course_position)
           VALUES (?, ?, (SELECT COALESCE(MAX(course_position), -1) + 1 FROM app_chat_session_courses WHERE chat_session_id = ?))
           ON CONFLICT(chat_session_id, course_id) DO NOTHING""", (session_id, course_id, session_id)
    )


def _set_state(connection: sqlite3.Connection, session_id: str, state: str) -> None:
    connection.execute("UPDATE app_chat_sessions SET state = ?, updated_at = ? WHERE chat_session_id = ?",
                       (state, _timestamp(_now()), session_id))


def _close_waiting_agent_runs(connection: sqlite3.Connection, account_id: str, session_id: str) -> None:
    connection.execute(
        """UPDATE app_agent_runs
           SET status = 'COMPLETED', finished_at = COALESCE(finished_at, ?)
           WHERE account_id = ? AND chat_session_id = ? AND status = 'WAITING_USER'""",
        (_timestamp(_now()), account_id, session_id),
    )


def _resolution_state(connection: sqlite3.Connection, session_id: str) -> str:
    statuses = {row[0] for row in connection.execute(
        "SELECT status FROM app_course_resolutions WHERE chat_session_id = ?", (session_id,)
    )}
    if "PENDING" in statuses:
        return "WAITING_COURSE_CONFIRMATION"
    if statuses & {"REJECTED", "EXPIRED"}:
        return "COLLECTING_COURSES"
    return "PROFILE_REVIEW"


def _insert_resolution(connection: sqlite3.Connection, account_id: str, session_id: str, query: str,
                       candidates: Sequence[Mapping[str, Any]], expires_at: datetime) -> dict[str, Any]:
    resolution_id = f"res_{uuid4().hex}"
    connection.execute(
        """INSERT INTO app_course_resolutions(resolution_id, chat_session_id, account_id, query_text, status, created_at, expires_at)
           VALUES (?, ?, ?, ?, 'PENDING', ?, ?)""",
        (resolution_id, session_id, account_id, query, _timestamp(_now()), _timestamp(expires_at)),
    )
    for rank, candidate in enumerate(candidates, 1):
        connection.execute(
            """INSERT INTO app_resolution_candidates(resolution_id, course_id, candidate_rank, match_score, match_reason)
               VALUES (?, ?, ?, ?, ?)""",
            (resolution_id, candidate["course_id"], rank, candidate["match_score"], candidate["match_reason"]),
        )
    return _read_resolution(connection, connection.execute(
        "SELECT * FROM app_course_resolutions WHERE resolution_id = ?", (resolution_id,)
    ).fetchone())


@dataclass(frozen=True)
class ChatTransition:
    state: str
    display_name: str | None
    gender_code: int | None
    reply: str
    profile_event: str | None = None
    resolutions: list[tuple[str, list[dict[str, Any]]]] = field(default_factory=list)


@dataclass(frozen=True)
class AcceptedChatTurn:
    session: dict[str, Any]
    transition: ChatTransition | None
    resolutions: list[dict[str, Any]]
    assistant_message: dict[str, Any] | None
    event_sequences: tuple[int, ...]


def _reserve_event_sequences(connection: sqlite3.Connection, session_id: str,
                             current: int, count: int) -> tuple[int, ...]:
    connection.execute(
        "UPDATE app_chat_sessions SET event_seq = ?, updated_at = ? WHERE chat_session_id = ?",
        (current + count, _timestamp(_now()), session_id),
    )
    return tuple(range(current + 1, current + count + 1))


class ApplicationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_account(self, username: str, password_hash: str) -> dict[str, Any]:
        account = {
            "account_id": _new_account_id(),
            "username": username,
            "password_hash": password_hash,
            "created_at": _timestamp(_now()),
        }
        try:
            with self.database.transaction(immediate=True) as connection:
                username_key = _canonical_username(username)
                existing_usernames = connection.execute("SELECT username FROM app_accounts")
                if any(_canonical_username(row["username"]) == username_key for row in existing_usernames):
                    raise ValueError("duplicate username")
                connection.execute(
                    """
                    INSERT INTO app_accounts(account_id, username, password_hash, created_at)
                    VALUES (:account_id, :username, :password_hash, :created_at)
                    """,
                    account,
                )
                connection.execute(
                    "INSERT INTO app_profiles(account_id) VALUES (?)", (account["account_id"],)
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("duplicate username") from exc
        return account

    def get_account_by_username(self, username: str) -> dict[str, Any] | None:
        username_key = _canonical_username(username)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT account_id, username, password_hash, created_at FROM app_accounts"
            )
            for row in rows:
                if _canonical_username(row["username"]) == username_key:
                    return _account_from_row(row)
        return None

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            return _account_from_row(
                connection.execute(
                    "SELECT account_id, username, password_hash, created_at "
                    "FROM app_accounts WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
            )

    def is_account_active(self, account_id: str) -> bool:
        with self.database.connect() as connection:
            has_status_table = connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'admin_user_status'"
            ).fetchone() is not None
            if not has_status_table:
                return self.get_account(account_id) is not None
            row = connection.execute(
                """SELECT COALESCE(s.status, 'ACTIVE') AS status
                   FROM app_accounts AS a LEFT JOIN admin_user_status AS s USING(account_id)
                   WHERE a.account_id = ?""", (account_id,),
            ).fetchone()
            return row is not None and row["status"] == "ACTIVE"

    def get_profile(self, account_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            return _read_profile(connection, account_id)

    def patch_profile(
        self,
        account_id: str,
        expected_version: int,
        display_name: str | None,
        gender_code: int | None,
    ) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            result = connection.execute(
                """
                UPDATE app_profiles
                SET display_name = COALESCE(?, display_name),
                    gender_code = COALESCE(?, gender_code),
                    profile_version = profile_version + 1,
                    status = 'DRAFT'
                WHERE account_id = ? AND profile_version = ?
                """,
                (display_name, gender_code, account_id, expected_version),
            )
            if result.rowcount == 0:
                exists = connection.execute(
                    "SELECT 1 FROM app_profiles WHERE account_id = ?", (account_id,)
                ).fetchone()
                if exists is None:
                    raise KeyError(account_id)
                raise ValueError("profile version conflict")
            return _read_profile(connection, account_id)

    def create_chat_session(self, account_id: str, purpose: str) -> dict[str, Any]:
        if purpose != "RECOMMENDATION":
            raise ValueError("invalid session purpose")
        with self.database.transaction(immediate=True) as connection:
            profile = _read_profile(connection, account_id)
            if not profile["display_name"]:
                state, greeting = "COLLECTING_NAME", "你好，我是课程路径助手。先告诉我应该怎么称呼你。"
            elif profile["gender_code"] not in (1, 2):
                state, greeting = "COLLECTING_GENDER", "继续完善画像，请选择当前模型支持的性别数据组。"
            else:
                state, greeting = "COLLECTING_COURSES", "请用自然语言描述你学过的课程，可以一次说一门或多门。"
            session_id, timestamp = f"chat_{uuid4().hex}", _timestamp(_now())
            connection.execute(
                """INSERT INTO app_chat_sessions(chat_session_id, account_id, purpose, state, profile_version,
                       draft_display_name, draft_gender_code, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, account_id, purpose, state, profile["profile_version"],
                 profile["display_name"], profile["gender_code"], timestamp, timestamp),
            )
            for course in profile["completed_courses"]:
                _insert_session_course(connection, session_id, course["course_id"])
            _insert_message(connection, session_id, "assistant", greeting)
            return _read_session(connection, account_id, session_id)

    def get_chat_session(self, account_id: str, session_id: str) -> dict[str, Any] | None:
        with self.database.transaction() as connection:
            try:
                return _read_session(connection, account_id, session_id)
            except KeyError:
                return None

    def get_chat_memory(self, session_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM app_chat_sessions WHERE chat_session_id = ?", (session_id,)
            ).fetchone() is None:
                raise KeyError(session_id)
            return _read_chat_memory(connection, session_id)

    def accept_agent_message(
        self, account_id: str, session_id: str, content: str, client_message_id: str
    ) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
        """Insert a user message and its Agent run under one writer lock."""
        run_id = f"run_{uuid4().hex}"
        timestamp = _timestamp(_now())
        try:
            with self.database.transaction(immediate=True) as connection:
                _owned_session(connection, account_id, session_id)
                existing = connection.execute(
                    """SELECT message_id, role, content, created_at
                       FROM app_chat_messages
                       WHERE chat_session_id = ? AND client_message_id = ?""",
                    (session_id, client_message_id),
                ).fetchone()
                if existing is not None:
                    return dict(existing), None, False
                active = connection.execute(
                    """SELECT 1 FROM app_agent_runs
                       WHERE chat_session_id = ? AND status IN ('RUNNING', 'WAITING_USER')""",
                    (session_id,),
                ).fetchone()
                if active is not None:
                    raise ValueError("active agent run")
                message = _insert_message(connection, session_id, "user", content, client_message_id)
                connection.execute(
                    "UPDATE app_chat_sessions SET updated_at = ? WHERE chat_session_id = ?",
                    (message["created_at"], session_id),
                )
                connection.execute(
                    """INSERT INTO app_agent_runs(
                           run_id, chat_session_id, account_id, trigger_message_id,
                           status, step_count, started_at
                       ) VALUES (?, ?, ?, ?, 'RUNNING', 0, ?)""",
                    (run_id, session_id, account_id, message["message_id"], timestamp),
                )
                return message, _read_agent_run(connection, account_id, run_id), True
        except sqlite3.IntegrityError as exc:
            raise ValueError("active agent run") from exc

    def save_chat_memory(
        self,
        account_id: str,
        session_id: str,
        summary_text: str,
        extracted_facts: Mapping[str, Any],
        expected_version: int,
    ) -> dict[str, Any]:
        facts_json = json.dumps(dict(extracted_facts), ensure_ascii=False, allow_nan=False)
        with self.database.transaction(immediate=True) as connection:
            _owned_session(connection, account_id, session_id)
            current = connection.execute(
                "SELECT memory_version FROM app_chat_memory WHERE chat_session_id = ?",
                (session_id,),
            ).fetchone()
            current_version = current["memory_version"] if current is not None else 0
            if current_version != expected_version:
                raise ValueError("memory version conflict")
            timestamp = _timestamp(_now())
            connection.execute(
                """INSERT INTO app_chat_memory(
                       chat_session_id, summary_text, extracted_facts_json, memory_version, updated_at
                   ) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(chat_session_id) DO UPDATE SET
                       summary_text = excluded.summary_text,
                       extracted_facts_json = excluded.extracted_facts_json,
                       memory_version = excluded.memory_version,
                       updated_at = excluded.updated_at""",
                (session_id, summary_text, facts_json, expected_version + 1, timestamp),
            )
            return _read_chat_memory(connection, session_id)

    def begin_agent_run(self, account_id: str, session_id: str, trigger_message_id: str) -> dict[str, Any]:
        run_id = f"run_{uuid4().hex}"
        timestamp = _timestamp(_now())
        try:
            with self.database.transaction(immediate=True) as connection:
                _owned_session(connection, account_id, session_id)
                trigger = connection.execute(
                    """SELECT 1 FROM app_chat_messages
                       WHERE message_id = ? AND chat_session_id = ? AND role = 'user'""",
                    (trigger_message_id, session_id),
                ).fetchone()
                if trigger is None:
                    raise KeyError(trigger_message_id)
                active = connection.execute(
                    """SELECT 1 FROM app_agent_runs
                       WHERE chat_session_id = ? AND status IN ('RUNNING', 'WAITING_USER')""",
                    (session_id,),
                ).fetchone()
                if active is not None:
                    raise ValueError("active agent run")
                connection.execute(
                    """INSERT INTO app_agent_runs(
                           run_id, chat_session_id, account_id, trigger_message_id,
                           status, step_count, started_at
                       ) VALUES (?, ?, ?, ?, 'RUNNING', 0, ?)""",
                    (run_id, session_id, account_id, trigger_message_id, timestamp),
                )
                return _read_agent_run(connection, account_id, run_id)
        except sqlite3.IntegrityError as exc:
            raise ValueError("active agent run") from exc

    def record_agent_step(
        self,
        account_id: str,
        run_id: str,
        step_index: int,
        action_type: str,
        tool_name: str | None,
        input_payload: Mapping[str, Any],
        output_payload: Mapping[str, Any],
        status: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        input_json = json.dumps(dict(input_payload), ensure_ascii=False, allow_nan=False)
        output_json = json.dumps(dict(output_payload), ensure_ascii=False, allow_nan=False)
        idempotency_key = idempotency_key or f"{run_id}:{step_index}"
        with self.database.transaction(immediate=True) as connection:
            run = connection.execute(
                "SELECT * FROM app_agent_runs WHERE run_id = ? AND account_id = ?",
                (run_id, account_id),
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            existing = connection.execute(
                "SELECT * FROM app_agent_steps WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                return {
                    "step_id": existing["step_id"],
                    "run_id": existing["run_id"],
                    "step_index": existing["step_index"],
                    "action_type": existing["action_type"],
                    "tool_name": existing["tool_name"],
                    "input": json.loads(existing["input_json"]),
                    "output": json.loads(existing["output_json"]),
                    "status": existing["status"],
                    "idempotency_key": existing["idempotency_key"],
                    "created_at": existing["created_at"],
                }
            if run["status"] not in {"RUNNING", "WAITING_USER"}:
                raise ValueError("agent run is not active")
            timestamp = _timestamp(_now())
            step_id = f"step_{uuid4().hex}"
            connection.execute(
                """INSERT INTO app_agent_steps(
                       step_id, run_id, step_index, action_type, tool_name,
                       input_json, output_json, status, idempotency_key, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (step_id, run_id, step_index, action_type, tool_name, input_json,
                 output_json, status, idempotency_key, timestamp),
            )
            connection.execute(
                "UPDATE app_agent_runs SET step_count = MAX(step_count, ?) WHERE run_id = ?",
                (step_index, run_id),
            )
            return {
                "step_id": step_id,
                "run_id": run_id,
                "step_index": step_index,
                "action_type": action_type,
                "tool_name": tool_name,
                "input": dict(input_payload),
                "output": dict(output_payload),
                "status": status,
                "idempotency_key": idempotency_key,
                "created_at": timestamp,
            }

    def finish_agent_run(
        self, account_id: str, run_id: str, status: str, error_code: str | None = None
    ) -> dict[str, Any]:
        if status not in {"WAITING_USER", "COMPLETED", "FAILED"}:
            raise ValueError("invalid agent run status")
        with self.database.transaction(immediate=True) as connection:
            result = connection.execute(
                """UPDATE app_agent_runs
                   SET status = ?, finished_at = ?, error_code = ?
                   WHERE run_id = ? AND account_id = ?""",
                (status, _timestamp(_now()), error_code, run_id, account_id),
            )
            if result.rowcount != 1:
                raise KeyError(run_id)
            return _read_agent_run(connection, account_id, run_id)

    def get_agent_run(self, account_id: str, run_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            try:
                return _read_agent_run(connection, account_id, run_id)
            except KeyError:
                return None

    def append_message(self, session_id: str, role: str, content: str,
                       client_message_id: str | None = None) -> tuple[dict[str, Any], bool]:
        """Append after the caller's ownership check; return (message, inserted)."""
        with self.database.transaction(immediate=True) as connection:
            if connection.execute("SELECT 1 FROM app_chat_sessions WHERE chat_session_id = ?", (session_id,)).fetchone() is None:
                raise KeyError(session_id)
            if client_message_id is not None:
                existing = connection.execute(
                    """SELECT message_id, role, content, created_at FROM app_chat_messages
                       WHERE chat_session_id = ? AND client_message_id = ?""", (session_id, client_message_id)
                ).fetchone()
                if existing is not None:
                    return dict(existing), False
            message = _insert_message(connection, session_id, role, content, client_message_id)
            connection.execute("UPDATE app_chat_sessions SET updated_at = ? WHERE chat_session_id = ?",
                               (message["created_at"], session_id))
            return message, True

    def next_event_sequence(self, account_id: str, session_id: str) -> int:
        """Allocate an owned session's event ID before it is sent to the client."""
        with self.database.transaction(immediate=True) as connection:
            row = _owned_session(connection, account_id, session_id)
            return _reserve_event_sequences(connection, session_id, row["event_seq"], 1)[0]

    def update_assistant_reply(self, account_id: str, session_id: str, message_id: str, content: str) -> None:
        """Optionally refine an already durable reply without adding another message."""
        with self.database.transaction(immediate=True) as connection:
            _owned_session(connection, account_id, session_id)
            updated = connection.execute(
                """UPDATE app_chat_messages SET content = ?
                   WHERE chat_session_id = ? AND message_id = ? AND role = 'assistant'""",
                (content, session_id, message_id),
            )
            if updated.rowcount != 1:
                raise KeyError(message_id)

    def accept_chat_message(
        self, account_id: str, session_id: str, content: str, client_message_id: str,
        prepare: Callable[[dict[str, Any]], ChatTransition],
    ) -> AcceptedChatTurn:
        """Commit state, both messages, candidates and event IDs before network awaits.

        The planner must only inspect the supplied session and cached course metadata;
        it must not open another transaction or call an external service.
        """
        with self.database.transaction(immediate=True) as connection:
            session = _read_session(connection, account_id, session_id)
            if connection.execute(
                "SELECT 1 FROM app_chat_messages WHERE chat_session_id = ? AND client_message_id = ?",
                (session_id, client_message_id),
            ).fetchone() is not None:
                sequences = _reserve_event_sequences(connection, session_id, session["event_seq"], 1)
                return AcceptedChatTurn(_read_session(connection, account_id, session_id), None, [], None, sequences)
            transition = prepare(session)
            _insert_message(connection, session_id, "user", content, client_message_id)
            connection.execute(
                """UPDATE app_chat_sessions SET state = ?, draft_display_name = ?, draft_gender_code = ?, updated_at = ?
                   WHERE chat_session_id = ?""",
                (transition.state, transition.display_name, transition.gender_code, _timestamp(_now()), session_id),
            )
            resolutions = [
                _insert_resolution(connection, account_id, session_id, query, candidates, _now() + timedelta(minutes=30))
                for query, candidates in transition.resolutions
            ]
            assistant = _insert_message(connection, session_id, "assistant", transition.reply)
            # Reserve two delta slots plus profile/resolution events and done. A
            # disconnect or a one-character optional rewrite may leave an unused ID.
            count = 3 + len(resolutions) + int(transition.profile_event is not None)
            sequences = _reserve_event_sequences(connection, session_id, session["event_seq"], count)
            return AcceptedChatTurn(_read_session(connection, account_id, session_id), transition,
                                    resolutions, assistant, sequences)

    def commit_agent_transition(
        self, account_id: str, session_id: str, transition: ChatTransition
    ) -> dict[str, Any]:
        """Commit a rules fallback after the Agent has already inserted the user message."""
        with self.database.transaction(immediate=True) as connection:
            _owned_session(connection, account_id, session_id)
            connection.execute(
                """UPDATE app_chat_sessions
                   SET state = ?, draft_display_name = ?, draft_gender_code = ?, updated_at = ?
                   WHERE chat_session_id = ?""",
                (transition.state, transition.display_name, transition.gender_code,
                 _timestamp(_now()), session_id),
            )
            resolutions = [
                _insert_resolution(connection, account_id, session_id, query, candidates,
                                   _now() + timedelta(minutes=30))
                for query, candidates in transition.resolutions
            ]
            _insert_message(connection, session_id, "assistant", transition.reply)
            return {
                "session": _read_session(connection, account_id, session_id),
                "resolutions": resolutions,
            }

    def patch_session_draft(self, account_id: str, session_id: str, display_name: str | None = None,
                            gender_code: int | None = None, state: str | None = None) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            row = _owned_session(connection, account_id, session_id)
            name = row["draft_display_name"] if display_name is None else display_name.strip()
            gender = row["draft_gender_code"] if gender_code is None else gender_code
            if gender_code is not None and gender_code not in (1, 2):
                raise ValueError("invalid gender code")
            if display_name is not None and not name:
                raise ValueError("invalid display name")
            if state is None:
                if display_name is None and gender_code is None:
                    return _read_session(connection, account_id, session_id)
                if name and gender in (1, 2):
                    pending = connection.execute(
                        "SELECT 1 FROM app_course_resolutions WHERE chat_session_id = ? AND status = 'PENDING'", (session_id,)
                    ).fetchone()
                    state = "WAITING_COURSE_CONFIRMATION" if pending else "PROFILE_REVIEW"
                else:
                    state = row["state"]
            if state not in {"COLLECTING_NAME", "COLLECTING_GENDER", "COLLECTING_COURSES", "WAITING_COURSE_CONFIRMATION", "PROFILE_REVIEW", "COMPLETED"}:
                raise ValueError("invalid session state")
            connection.execute(
                """UPDATE app_chat_sessions SET draft_display_name = ?, draft_gender_code = ?, state = ?, updated_at = ?
                   WHERE chat_session_id = ?""", (name, gender, state, _timestamp(_now()), session_id)
            )
            return _read_session(connection, account_id, session_id)

    def add_session_course(self, account_id: str, session_id: str, course_id: str) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            row = _owned_session(connection, account_id, session_id)
            _insert_session_course(connection, session_id, course_id)
            _set_state(connection, session_id, row["state"])
            return _read_session(connection, account_id, session_id)

    def remove_session_course(self, account_id: str, session_id: str, course_id: str) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            row = _owned_session(connection, account_id, session_id)
            connection.execute("DELETE FROM app_chat_session_courses WHERE chat_session_id = ? AND course_id = ?", (session_id, course_id))
            _set_state(connection, session_id, row["state"])
            return _read_session(connection, account_id, session_id)

    def create_resolution(self, account_id: str, session_id: str, query: str,
                          candidates: Sequence[Mapping[str, Any]], expires_at: datetime) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            _owned_session(connection, account_id, session_id)
            return _insert_resolution(connection, account_id, session_id, query, candidates, expires_at)

    def decide_resolution(self, account_id: str, session_id: str, resolution_id: str,
                          course_id: str | None, rejected: bool) -> dict[str, Any]:
        expired = False
        with self.database.transaction(immediate=True) as connection:
            _owned_session(connection, account_id, session_id)
            row = connection.execute(
                "SELECT * FROM app_course_resolutions WHERE account_id = ? AND chat_session_id = ? AND resolution_id = ?",
                (account_id, session_id, resolution_id),
            ).fetchone()
            if row is None:
                raise KeyError(resolution_id)
            if bool(course_id) == bool(rejected):
                raise ValueError("invalid resolution decision")
            if row["status"] == "EXPIRED":
                raise ValueError("resolution expired")
            if row["status"] == "PENDING" and datetime.fromisoformat(row["expires_at"]) <= _now():
                connection.execute("UPDATE app_course_resolutions SET status = 'EXPIRED' WHERE resolution_id = ?", (resolution_id,))
                _set_state(connection, session_id, _resolution_state(connection, session_id))
                _close_waiting_agent_runs(connection, account_id, session_id)
                expired = True
            elif row["status"] != "PENDING":
                if (row["status"] == "CONFIRMED" and row["selected_course_id"] == course_id) or (row["status"] == "REJECTED" and rejected):
                    return _read_session(connection, account_id, session_id)
                raise ValueError("resolution already finalized")
            else:
                if course_id:
                    if connection.execute("SELECT 1 FROM app_resolution_candidates WHERE resolution_id = ? AND course_id = ?", (resolution_id, course_id)).fetchone() is None:
                        raise ValueError("invalid resolution candidate")
                    _insert_session_course(connection, session_id, course_id)
                connection.execute("UPDATE app_course_resolutions SET status = ?, selected_course_id = ? WHERE resolution_id = ?",
                                   ("REJECTED" if rejected else "CONFIRMED", course_id, resolution_id))
                state = _resolution_state(connection, session_id)
                _set_state(connection, session_id, state)
                _close_waiting_agent_runs(connection, account_id, session_id)
                if state == "COLLECTING_COURSES":
                    _insert_message(connection, session_id, "assistant", "未匹配的课程可以换一种说法，或选择完成课程描述。")
                elif state == "PROFILE_REVIEW":
                    _insert_message(connection, session_id, "assistant", "候选都已确认，请检查画像并生成推荐。")
            session = _read_session(connection, account_id, session_id)
        # Expiry is a durable lifecycle transition, so commit it before reporting the conflict.
        if expired:
            raise ValueError("resolution expired")
        return session

    def confirm_profile(self, account_id: str, session_id: str, expected_version: int) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            # 1. Verify ownership and the versions captured by both profile and draft.
            session = _owned_session(connection, account_id, session_id)
            profile = _read_profile(connection, account_id)
            if expected_version != profile["profile_version"] or session["profile_version"] != expected_version:
                raise ValueError("profile version conflict")
            pending = connection.execute(
                "SELECT 1 FROM app_course_resolutions WHERE chat_session_id = ? AND status = 'PENDING'", (session_id,)
            ).fetchone()
            if pending or session["state"] != "PROFILE_REVIEW":
                raise ValueError("pending course resolutions")
            # 2. Validate the scalar draft and every course, including broken external writes.
            name, gender = session["draft_display_name"], session["draft_gender_code"]
            if not name or not name.strip() or gender not in (1, 2):
                raise ValueError("profile incomplete")
            courses = list(connection.execute(
                """SELECT sc.course_id, c.course_id AS valid_course_id FROM app_chat_session_courses AS sc
                   LEFT JOIN courses AS c ON c.course_id = sc.course_id
                   WHERE sc.chat_session_id = ? ORDER BY sc.course_position, sc.course_id""", (session_id,)
            ))
            if any(course["valid_course_id"] is None for course in courses):
                raise ValueError("invalid draft course")
            user_id = profile["user_id"]
            # 3. Create and link the recommendation identity only on first confirmation.
            if user_id is None:
                user_id = f"U_APP_{uuid4().hex}"
                connection.execute("INSERT INTO users(user_id, user_name, gender_code) VALUES (?, ?, ?)", (user_id, name, gender))
                connection.execute("UPDATE app_profiles SET user_id = ? WHERE account_id = ?", (user_id, account_id))
            # 4. Synchronize the canonical identity.
            connection.execute("UPDATE users SET user_name = ?, gender_code = ? WHERE user_id = ?", (name, gender, user_id))
            # 5. Replace canonical completion rows, preserving draft order.
            previous = {course["course_id"] for course in profile["completed_courses"]}
            current = {course["course_id"] for course in courses}
            connection.execute("DELETE FROM user_completed_courses WHERE user_id = ?", (user_id,))
            connection.executemany("INSERT INTO user_completed_courses(user_id, course_id, completed_position) VALUES (?, ?, ?)",
                                   [(user_id, course["course_id"], position) for position, course in enumerate(courses)])
            # 6. Existing explicit ratings win over an implicit completion.
            connection.executemany(
                "INSERT INTO interactions(user_id, course_id, comment) VALUES (?, ?, 0) ON CONFLICT(user_id, course_id) DO NOTHING",
                [(user_id, course["course_id"]) for course in courses],
            )
            # 7. Only obsolete implicit rows belong to this synchronization operation.
            connection.executemany("DELETE FROM interactions WHERE user_id = ? AND course_id = ? AND comment = 0",
                                   [(user_id, course_id) for course_id in previous - current])
            # 8. Publish the confirmed profile and its new version.
            connection.execute(
                """UPDATE app_profiles SET display_name = ?, gender_code = ?, profile_version = profile_version + 1,
                       status = 'CONFIRMED', confirmed_at = ? WHERE account_id = ?""", (name, gender, _timestamp(_now()), account_id)
            )
            # 9. Complete the session in the same transaction.
            connection.execute(
                "UPDATE app_chat_sessions SET state = 'COMPLETED', profile_version = ?, updated_at = ? WHERE chat_session_id = ?",
                (expected_version + 1, _timestamp(_now()), session_id),
            )
            return _read_profile(connection, account_id)

    def save_recommendation(
        self,
        account_id: str,
        user_id: str,
        profile_version: int,
        source: str,
        items: Sequence[Mapping[str, Any]],
        fairness_policy_version: str | None = None,
    ) -> dict[str, Any]:
        recommendation_id = f"rec_{uuid4().hex}"
        generated_at = _timestamp(_now())
        with self.database.transaction(immediate=True) as connection:
            profile = connection.execute(
                "SELECT user_id, profile_version, status FROM app_profiles WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if profile is None:
                raise KeyError(account_id)
            if (profile["user_id"] != user_id or profile["profile_version"] != profile_version
                    or profile["status"] != "CONFIRMED"):
                raise ValueError("profile version conflict")
            connection.execute(
                """INSERT INTO app_recommendations(
                       recommendation_id, account_id, user_id, profile_version, source,
                       algorithm_version, fairness_applied, fairness_policy_version, generated_at, deleted_at
                   ) VALUES (?, ?, ?, ?, ?, 'jaccard-v1', ?, ?, ?, NULL)""",
                (recommendation_id, account_id, user_id, profile_version, source,
                 int(fairness_policy_version is not None), fairness_policy_version, generated_at),
            )
            for item in items:
                course = item["course"]
                if not isinstance(course, Mapping):
                    raise ValueError("invalid course snapshot")
                reason_codes = item["reason_codes"]
                if not isinstance(reason_codes, Sequence) or isinstance(reason_codes, (str, bytes)):
                    raise ValueError("invalid recommendation reason codes")
                course_snapshot = json.dumps(dict(course), ensure_ascii=False, allow_nan=False)
                reason_codes_json = json.dumps(list(reason_codes), ensure_ascii=False, allow_nan=False)
                connection.execute(
                    """INSERT INTO app_recommendation_items(
                           recommendation_id, rank, course_id, course_snapshot_json, reason_codes_json, reason_text
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        recommendation_id,
                        item["rank"],
                        course["course_id"],
                        course_snapshot,
                        reason_codes_json,
                        item["reason_text"],
                    ),
                )
            row = connection.execute(
                "SELECT * FROM app_recommendations WHERE recommendation_id = ?",
                (recommendation_id,),
            ).fetchone()
            assert row is not None
            return _read_recommendation(connection, row)

    def list_recommendations(
        self, account_id: str, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        with self.database.connect() as connection:
            rows = list(connection.execute(
                """SELECT recommendation_id, source, generated_at,
                          (SELECT COUNT(*) FROM app_recommendation_items AS ri
                           WHERE ri.recommendation_id = r.recommendation_id) AS course_count
                   FROM app_recommendations AS r
                   WHERE account_id = ? AND deleted_at IS NULL
                   ORDER BY generated_at DESC, rowid DESC LIMIT ? OFFSET ?""",
                (account_id, limit + 1, offset),
            ))
        has_more = len(rows) > limit
        return [dict(row) for row in rows[:limit]], has_more

    def get_recommendation(self, account_id: str, recommendation_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM app_recommendations
                   WHERE account_id = ? AND recommendation_id = ? AND deleted_at IS NULL""",
                (account_id, recommendation_id),
            ).fetchone()
            return _read_recommendation(connection, row) if row is not None else None

    def soft_delete_recommendation(
        self, account_id: str, recommendation_id: str, *, reject_foreign: bool = False
    ) -> bool:
        with self.database.transaction(immediate=True) as connection:
            if reject_foreign:
                owner = connection.execute(
                    "SELECT account_id FROM app_recommendations WHERE recommendation_id = ?",
                    (recommendation_id,),
                ).fetchone()
                if owner is not None and owner["account_id"] != account_id:
                    raise KeyError(recommendation_id)
            result = connection.execute(
                """UPDATE app_recommendations SET deleted_at = ?
                   WHERE account_id = ? AND recommendation_id = ? AND deleted_at IS NULL""",
                (_timestamp(_now()), account_id, recommendation_id),
            )
            return result.rowcount == 1

    def list_favorites(
        self, account_id: str, offset: int, limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        with self.database.connect() as connection:
            rows = list(connection.execute(
                """SELECT course_id, created_at FROM app_favorites
                   WHERE account_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?""",
                (account_id, limit + 1, offset),
            ))
            page = []
            for row in rows[:limit]:
                course = _read_catalog_course(connection, row["course_id"])
                if course is not None:
                    page.append({"course": course, "created_at": row["created_at"]})
        return page, len(rows) > limit

    def add_favorite(self, account_id: str, course_id: str) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            course = _read_catalog_course(connection, course_id)
            if course is None:
                raise KeyError(course_id)
            created_at = _timestamp(_now())
            connection.execute(
                """INSERT INTO app_favorites(account_id, course_id, created_at) VALUES (?, ?, ?)
                   ON CONFLICT(account_id, course_id) DO NOTHING""",
                (account_id, course_id, created_at),
            )
            favorite = connection.execute(
                "SELECT created_at FROM app_favorites WHERE account_id = ? AND course_id = ?",
                (account_id, course_id),
            ).fetchone()
            assert favorite is not None
            return {"course": course, "created_at": favorite["created_at"]}

    def remove_favorite(self, account_id: str, course_id: str) -> bool:
        with self.database.transaction(immediate=True) as connection:
            result = connection.execute(
                "DELETE FROM app_favorites WHERE account_id = ? AND course_id = ?",
                (account_id, course_id),
            )
            return result.rowcount == 1

    def store_refresh_token(self, account_id: str, token_hash: str, expires_at: datetime) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO app_refresh_tokens(token_hash, account_id, expires_at, revoked_at, created_at)
                VALUES (?, ?, ?, NULL, ?)
                """,
                (token_hash, account_id, _timestamp(expires_at), _timestamp(_now())),
            )

    def rotate_refresh_token(
        self,
        token_hash: str,
        replacement_hash: str,
        replacement_expiry: datetime,
    ) -> dict[str, Any] | None:
        with self.database.transaction(immediate=True) as connection:
            current_time = _timestamp(_now())
            token = connection.execute(
                """
                SELECT account_id
                FROM app_refresh_tokens
                WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?
                """,
                (token_hash, current_time),
            ).fetchone()
            if token is None:
                return None
            revoked = connection.execute(
                """
                UPDATE app_refresh_tokens
                SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > ?
                """,
                (current_time, token_hash, current_time),
            )
            if revoked.rowcount != 1:
                return None
            connection.execute(
                """
                INSERT INTO app_refresh_tokens(token_hash, account_id, expires_at, revoked_at, created_at)
                VALUES (?, ?, ?, NULL, ?)
                """,
                (replacement_hash, token["account_id"], _timestamp(replacement_expiry), current_time),
            )
            return {"account_id": token["account_id"]}

    def revoke_refresh_token(self, token_hash: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE app_refresh_tokens
                SET revoked_at = COALESCE(revoked_at, ?)
                WHERE token_hash = ?
                """,
                (_timestamp(_now()), token_hash),
            )

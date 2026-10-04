from __future__ import annotations

import base64
import csv
import hashlib
import io
import itertools
import json
import math
import secrets
from datetime import timedelta
from typing import Any, Callable

import bcrypt
import jwt
from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel, Field, model_validator

from .catalog import display_difficulty, normalize
from .database import Database
from .store import iso, new_id, now


ADMIN_AUDIENCE = "course-compass-admin"
HYPERPARAMETER_SCHEMA: dict[str, dict[str, Any]] = {
    "k_neighbors": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200, "scannable": True},
    "candidate_size": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500, "scannable": True},
    "top_n": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50, "scannable": True},
    "implicit_score": {"type": "number", "default": 1.0, "minimum": 0.0, "maximum": 5.0, "scannable": True},
    "enforce_prerequisites": {"type": "boolean", "default": True, "scannable": True},
    "fairness_lambda": {"type": "number", "default": 0.15, "minimum": 0.0, "maximum": 1.0, "scannable": True},
    "target_gap": {"type": "number", "default": 0.05, "minimum": 0.0, "maximum": 1.0, "scannable": True},
    "max_total_cost": {"type": "nullable_number", "default": 0.08, "minimum": 0.0, "maximum": 1.0, "scannable": True},
}


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode().rstrip("=")


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()))
    except (ValueError, UnicodeDecodeError):
        return 0


def _page(items: list[dict[str, Any]], offset: int, limit: int, total: int | None = None) -> dict[str, Any]:
    has_more = len(items) > limit
    visible = items[:limit]
    return {
        "items": visible,
        "next_cursor": _cursor(offset + limit) if has_more else None,
        "has_more": has_more,
        "total": total,
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    return json.loads(value) if value else fallback


def _catalog_version() -> str:
    return f"catalog_{now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"


class AdminService:
    """Independent admin identity, auditing and management operations."""

    def __init__(self, database: Database, jwt_secret: str, catalog: Any) -> None:
        self.database = database
        self.jwt_secret = f"{jwt_secret}:admin"
        self.catalog = catalog

    def ensure_seed(self, username: str, password: str, display_name: str) -> None:
        with self.database.connect() as connection:
            exists = connection.execute("SELECT 1 FROM admin_accounts LIMIT 1").fetchone()
        if exists is None:
            self.create_admin(username, password, display_name, None)

    @staticmethod
    def public_admin(row: Any) -> dict[str, Any]:
        item = dict(row)
        return {key: item[key] for key in (
            "admin_id", "username", "display_name", "role", "status", "row_version",
            "created_at", "updated_at", "last_login_at",
        ) if key in item}

    def create_admin(self, username: str, password: str, display_name: str,
                     actor_id: str | None) -> dict[str, Any]:
        stamp = iso()
        admin_id = new_id("adm")
        password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO admin_accounts
                   (admin_id, username, display_name, password_hash, role, status, row_version,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'admin', 'ACTIVE', 1, ?, ?)""",
                (admin_id, username.strip(), display_name.strip(), password_hash, stamp, stamp),
            )
        if actor_id:
            self.audit(actor_id, "ADMIN_CREATE", "admin", admin_id,
                       {"username": username.strip(), "display_name": display_name.strip()}, priority="HIGH")
        return self.get_admin(admin_id)

    def get_admin(self, admin_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM admin_accounts WHERE admin_id = ?", (admin_id,)).fetchone()
        if row is None:
            raise KeyError(admin_id)
        return self.public_admin(row)

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM admin_accounts WHERE username = ? COLLATE NOCASE", (username.strip(),)
            ).fetchone()
            if row is None or row["status"] != "ACTIVE":
                return None
            if not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
                return None
            stamp = iso()
            connection.execute("UPDATE admin_accounts SET last_login_at = ?, updated_at = ? WHERE admin_id = ?",
                               (stamp, stamp, row["admin_id"]))
            current = connection.execute("SELECT * FROM admin_accounts WHERE admin_id = ?", (row["admin_id"],)).fetchone()
        return self.public_admin(current)

    def issue_tokens(self, admin_id: str) -> dict[str, Any]:
        admin = self.get_admin(admin_id)
        if admin["status"] != "ACTIVE":
            raise jwt.InvalidTokenError("disabled")
        expires = now() + timedelta(minutes=30)
        refresh_expires = now() + timedelta(days=7)
        refresh = secrets.token_urlsafe(40)
        access = jwt.encode(
            {"sub": admin_id, "admin_id": admin_id, "actor_type": "admin", "role": "admin",
             "type": "admin_access", "aud": ADMIN_AUDIENCE, "iss": ADMIN_AUDIENCE, "exp": expires},
            self.jwt_secret,
            algorithm="HS256",
        )
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO admin_refresh_tokens(token_hash, admin_id, expires_at, created_at)
                   VALUES (?, ?, ?, ?)""",
                (self._digest(refresh), admin_id, iso(refresh_expires), iso()),
            )
        return {"access_token": access, "refresh_token": refresh, "token_type": "Bearer", "expires_in": 1800}

    def decode(self, token: str) -> dict[str, Any]:
        payload = jwt.decode(token, self.jwt_secret, algorithms=["HS256"], audience=ADMIN_AUDIENCE,
                             issuer=ADMIN_AUDIENCE)
        if payload.get("type") != "admin_access" or payload.get("actor_type") != "admin" or payload.get("role") != "admin":
            raise jwt.InvalidTokenError("wrong actor")
        admin = self.get_admin(str(payload.get("admin_id") or payload.get("sub")))
        if admin["status"] != "ACTIVE":
            raise jwt.InvalidTokenError("disabled")
        return admin

    def refresh(self, refresh_token: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        digest = self._digest(refresh_token)
        replacement = secrets.token_urlsafe(40)
        replacement_digest = self._digest(replacement)
        stamp = iso()
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """SELECT t.admin_id FROM admin_refresh_tokens AS t
                   JOIN admin_accounts AS a ON a.admin_id = t.admin_id
                   WHERE t.token_hash = ? AND t.revoked_at IS NULL AND t.expires_at > ? AND a.status = 'ACTIVE'""",
                (digest, stamp),
            ).fetchone()
            if row is None:
                return None
            connection.execute("UPDATE admin_refresh_tokens SET revoked_at = ? WHERE token_hash = ?", (stamp, digest))
            connection.execute(
                "INSERT INTO admin_refresh_tokens(token_hash, admin_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (replacement_digest, row["admin_id"], iso(now() + timedelta(days=7)), stamp),
            )
        tokens = self.issue_tokens(row["admin_id"])
        # issue_tokens creates a second refresh token; revoke it and return our atomic replacement.
        with self.database.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM admin_refresh_tokens WHERE token_hash = ?", (self._digest(tokens["refresh_token"]),))
        tokens["refresh_token"] = replacement
        return self.get_admin(row["admin_id"]), tokens

    def logout(self, refresh_token: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute("UPDATE admin_refresh_tokens SET revoked_at = ? WHERE token_hash = ?",
                               (iso(), self._digest(refresh_token)))

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def audit(self, admin_id: str, action: str, resource_type: str, resource_id: str | None,
              summary: dict[str, Any] | None = None, request_id: str | None = None,
              result: str = "SUCCESS", priority: str = "NORMAL") -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO admin_audit_logs
                   (audit_id, admin_id, action, resource_type, resource_id, result, summary_json,
                    request_id, priority, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_id("audit"), admin_id, action, resource_type, resource_id, result,
                 _json(summary or {}), request_id, priority, iso()),
            )

    def current_catalog_version(self) -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT catalog_version FROM course_catalog_versions ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return row["catalog_version"] if row else "catalog_initial"

    def _record_catalog_version(self, connection: Any, admin_id: str, source: str,
                                summary: dict[str, Any]) -> str:
        version = _catalog_version()
        connection.execute(
            "INSERT INTO course_catalog_versions VALUES (?, ?, ?, ?, ?)",
            (version, source, admin_id, _json(summary), iso()),
        )
        return version

    def course(self, course_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT c.*, COALESCE(ac.status, 'ACTIVE') AS status,
                          COALESCE(ac.row_version, 1) AS row_version,
                          COALESCE(ac.catalog_version, 'catalog_initial') AS catalog_version,
                          ac.created_at, ac.updated_at
                   FROM courses AS c LEFT JOIN admin_course_records AS ac USING(course_id)
                   WHERE c.course_id = ?""", (course_id,),
            ).fetchone()
            if row is None:
                raise KeyError(course_id)
            fields = [item[0] for item in connection.execute(
                "SELECT field FROM course_fields WHERE course_id = ? ORDER BY field_position", (course_id,)
            )]
            prerequisites = [dict(item) for item in connection.execute(
                """SELECT prerequisite_course_id AS course_id, prerequisite_name AS course_name
                   FROM course_prerequisites WHERE course_id = ? ORDER BY prerequisite_position""", (course_id,)
            )]
            aliases = [dict(item) for item in connection.execute(
                """SELECT alias_id, alias_text, status, row_version FROM course_aliases
                   WHERE course_id = ? ORDER BY created_at""", (course_id,)
            )]
        item = dict(row)
        item["difficulty_level"] = display_difficulty(item["difficulty_level"])
        item["is_advanced"] = bool(item["is_advanced"])
        item["fields"] = fields
        item["prerequisites"] = prerequisites
        item["aliases"] = aliases
        return item

    def courses(self, query: str, course_status: str | None, difficulty: str | None,
                offset: int, limit: int) -> dict[str, Any]:
        clauses = ["1=1"]
        params: list[Any] = []
        if query:
            clauses.append("(c.course_id LIKE ? OR c.course_name LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if course_status:
            clauses.append("COALESCE(ac.status, 'ACTIVE') = ?")
            params.append(course_status)
        raw_difficulty = {"入门": "basic", "中级": "standard", "高阶": "advanced"}.get(difficulty or "", difficulty)
        if raw_difficulty:
            clauses.append("c.difficulty_level = ?")
            params.append(raw_difficulty)
        where = " AND ".join(clauses)
        with self.database.connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM courses AS c LEFT JOIN admin_course_records AS ac USING(course_id) WHERE {where}", params
            ).fetchone()[0]
            rows = connection.execute(
                f"""SELECT c.course_id, c.course_name, c.difficulty_level, c.is_advanced,
                            COALESCE(ac.status, 'ACTIVE') AS status,
                            COALESCE(ac.row_version, 1) AS row_version,
                            COALESCE(ac.catalog_version, 'catalog_initial') AS catalog_version
                     FROM courses AS c LEFT JOIN admin_course_records AS ac USING(course_id)
                     WHERE {where} ORDER BY c.course_id LIMIT ? OFFSET ?""",
                (*params, limit + 1, offset),
            ).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item["difficulty_level"] = display_difficulty(item["difficulty_level"])
                item["is_advanced"] = bool(item["is_advanced"])
                item["fields"] = [value[0] for value in connection.execute(
                    "SELECT field FROM course_fields WHERE course_id = ? ORDER BY field_position", (item["course_id"],)
                )]
                items.append(item)
        return _page(items, offset, limit, total)

    def _validate_course_name(self, connection: Any, name: str, exclude_id: str | None = None) -> None:
        for row in connection.execute(
            """SELECT c.course_id, c.course_name FROM courses AS c
               LEFT JOIN admin_course_records AS ac USING(course_id)
               WHERE COALESCE(ac.status, 'ACTIVE') = 'ACTIVE'"""
        ):
            if row["course_id"] != exclude_id and normalize(row["course_name"]) == normalize(name):
                raise ValueError("duplicate course name")

    def _validate_prerequisites(self, connection: Any, course_id: str, prerequisites: list[str]) -> None:
        if course_id in prerequisites:
            raise ValueError("self prerequisite")
        existing = {row[0] for row in connection.execute("SELECT course_id FROM courses")}
        if any(item not in existing for item in prerequisites):
            raise ValueError("unknown prerequisite")
        graph: dict[str, set[str]] = {}
        for row in connection.execute("SELECT course_id, prerequisite_course_id FROM course_prerequisites"):
            graph.setdefault(row["course_id"], set()).add(row["prerequisite_course_id"])
        graph[course_id] = set(prerequisites)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("prerequisite cycle")
            if node in visited:
                return
            visiting.add(node)
            for next_node in graph.get(node, set()):
                visit(next_node)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)

    def create_course(self, payload: dict[str, Any], admin_id: str) -> dict[str, Any]:
        course_id = payload["course_id"].strip()
        name = payload["course_name"].strip()
        fields = list(dict.fromkeys(item.strip() for item in payload["fields"] if item.strip()))
        prerequisites = list(dict.fromkeys(payload.get("prerequisite_course_ids", [])))
        raw_difficulty = {"入门": "basic", "中级": "standard", "高阶": "advanced"}.get(payload["difficulty_level"], payload["difficulty_level"])
        stamp = iso()
        with self.database.transaction(immediate=True) as connection:
            if connection.execute("SELECT 1 FROM courses WHERE course_id = ?", (course_id,)).fetchone():
                raise ValueError("duplicate course id")
            self._validate_course_name(connection, name)
            self._validate_prerequisites(connection, course_id, prerequisites)
            version = self._record_catalog_version(connection, admin_id, "ADMIN_CREATE", {"course_id": course_id})
            is_advanced = bool(payload.get("is_advanced"))
            rule = payload.get("advanced_label_rule") or "prerequisite_count>=2"
            connection.execute("INSERT INTO courses VALUES (?, ?, ?, ?, ?)",
                               (course_id, name, raw_difficulty, int(is_advanced), rule))
            for index, field_name in enumerate(fields):
                connection.execute("INSERT INTO course_fields VALUES (?, ?, ?)", (course_id, field_name, index))
            prerequisite_details = []
            for index, prerequisite_id in enumerate(prerequisites):
                prerequisite_name = connection.execute(
                    "SELECT course_name FROM courses WHERE course_id = ?", (prerequisite_id,)
                ).fetchone()[0]
                connection.execute("INSERT INTO course_prerequisites VALUES (?, ?, ?, ?)",
                                   (course_id, prerequisite_id, prerequisite_name, index))
                prerequisite_details.append({"course_id": prerequisite_id, "course_name": prerequisite_name})
            connection.execute(
                "INSERT INTO course_information VALUES (?, ?, ?, ?, ?, ?)",
                (course_id, _json(fields), payload.get("detailed_description", ""), int(is_advanced),
                 _json(prerequisite_details), _json([])),
            )
            connection.execute(
                "INSERT INTO admin_course_records VALUES (?, 'ACTIVE', 1, ?, ?, ?, ?, ?)",
                (course_id, version, admin_id, admin_id, stamp, stamp),
            )
        self.catalog.reload()
        self.audit(admin_id, "COURSE_CREATE", "course", course_id, {"catalog_version": version})
        return self.course(course_id)

    def update_course(self, course_id: str, payload: dict[str, Any], admin_id: str) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            current = connection.execute(
                """SELECT c.*, ci.detailed_description, COALESCE(ac.row_version, 1) AS row_version
                   FROM courses AS c JOIN course_information AS ci USING(course_id)
                   LEFT JOIN admin_course_records AS ac USING(course_id)
                   WHERE c.course_id = ?""", (course_id,),
            ).fetchone()
            if current is None:
                raise KeyError(course_id)
            if payload["row_version"] != current["row_version"]:
                raise RuntimeError("row version conflict")
            name = (payload.get("course_name") or current["course_name"]).strip()
            self._validate_course_name(connection, name, course_id)
            raw_difficulty = payload.get("difficulty_level") or current["difficulty_level"]
            raw_difficulty = {"入门": "basic", "中级": "standard", "高阶": "advanced"}.get(raw_difficulty, raw_difficulty)
            is_advanced = int(payload.get("is_advanced", bool(current["is_advanced"])))
            rule = payload.get("advanced_label_rule") or current["advanced_label_rule"]
            fields = payload.get("fields")
            if fields is None:
                fields = [row[0] for row in connection.execute(
                    "SELECT field FROM course_fields WHERE course_id = ? ORDER BY field_position", (course_id,)
                )]
            fields = list(dict.fromkeys(value.strip() for value in fields if value.strip()))
            prerequisites = payload.get("prerequisite_course_ids")
            if prerequisites is None:
                prerequisites = [row[0] for row in connection.execute(
                    "SELECT prerequisite_course_id FROM course_prerequisites WHERE course_id = ? ORDER BY prerequisite_position",
                    (course_id,),
                )]
            prerequisites = list(dict.fromkeys(prerequisites))
            self._validate_prerequisites(connection, course_id, prerequisites)
            version = self._record_catalog_version(connection, admin_id, "ADMIN_UPDATE", {"course_id": course_id})
            connection.execute(
                "UPDATE courses SET course_name = ?, difficulty_level = ?, is_advanced = ?, advanced_label_rule = ? WHERE course_id = ?",
                (name, raw_difficulty, is_advanced, rule, course_id),
            )
            connection.execute("DELETE FROM course_fields WHERE course_id = ?", (course_id,))
            connection.executemany("INSERT INTO course_fields VALUES (?, ?, ?)",
                                   [(course_id, value, index) for index, value in enumerate(fields)])
            connection.execute("DELETE FROM course_prerequisites WHERE course_id = ?", (course_id,))
            prerequisite_details = []
            for index, prerequisite_id in enumerate(prerequisites):
                prerequisite_name = connection.execute(
                    "SELECT course_name FROM courses WHERE course_id = ?", (prerequisite_id,)
                ).fetchone()[0]
                connection.execute("INSERT INTO course_prerequisites VALUES (?, ?, ?, ?)",
                                   (course_id, prerequisite_id, prerequisite_name, index))
                prerequisite_details.append({"course_id": prerequisite_id, "course_name": prerequisite_name})
            connection.execute(
                """UPDATE course_information SET course_category = ?, detailed_description = ?,
                   is_advanced = ?, prerequisites = ? WHERE course_id = ?""",
                (_json(fields), payload.get("detailed_description", current["detailed_description"]), is_advanced,
                 _json(prerequisite_details), course_id),
            )
            stamp = iso()
            connection.execute(
                """INSERT INTO admin_course_records(course_id, status, row_version, catalog_version,
                   created_by, updated_by, created_at, updated_at)
                   VALUES (?, 'ACTIVE', 2, ?, ?, ?, ?, ?)
                   ON CONFLICT(course_id) DO UPDATE SET row_version = row_version + 1,
                   catalog_version = excluded.catalog_version, updated_by = excluded.updated_by,
                   updated_at = excluded.updated_at""",
                (course_id, version, admin_id, admin_id, stamp, stamp),
            )
        self.catalog.reload()
        self.audit(admin_id, "COURSE_UPDATE", "course", course_id,
                   {"catalog_version": version, "reason": payload.get("reason")})
        return self.course(course_id)

    def set_course_status(self, course_id: str, target: str, row_version: int, admin_id: str,
                          reason: str | None = None) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            exists = connection.execute("SELECT 1 FROM courses WHERE course_id = ?", (course_id,)).fetchone()
            if not exists:
                raise KeyError(course_id)
            record = connection.execute("SELECT * FROM admin_course_records WHERE course_id = ?", (course_id,)).fetchone()
            current_version = record["row_version"] if record else 1
            if row_version != current_version:
                raise RuntimeError("row version conflict")
            version = self._record_catalog_version(connection, admin_id, f"ADMIN_{target}", {"course_id": course_id})
            stamp = iso()
            connection.execute(
                """INSERT INTO admin_course_records(course_id, status, row_version, catalog_version,
                   created_by, updated_by, created_at, updated_at) VALUES (?, ?, 2, ?, ?, ?, ?, ?)
                   ON CONFLICT(course_id) DO UPDATE SET status = excluded.status,
                   row_version = row_version + 1, catalog_version = excluded.catalog_version,
                   updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
                (course_id, target, version, admin_id, admin_id, stamp, stamp),
            )
        self.catalog.reload()
        self.audit(admin_id, f"COURSE_{target}", "course", course_id,
                   {"catalog_version": version, "reason": reason})
        return self.course(course_id)

    def add_alias(self, course_id: str, alias_text: str, admin_id: str) -> dict[str, Any]:
        self.course(course_id)
        alias_id = new_id("alias")
        stamp = iso()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO course_aliases(alias_id, alias_text, normalized_alias, course_id, source,
                   status, row_version, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'ADMIN', 'ACTIVE', 1, ?, ?, ?)""",
                (alias_id, alias_text.strip(), normalize(alias_text), course_id, admin_id, stamp, stamp),
            )
        self.catalog.reload()
        self.audit(admin_id, "COURSE_ALIAS_CREATE", "course_alias", alias_id, {"course_id": course_id})
        return {"alias_id": alias_id, "alias_text": alias_text.strip(), "course_id": course_id,
                "status": "ACTIVE", "row_version": 1}

    def delete_alias(self, alias_id: str, row_version: int, admin_id: str) -> None:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM course_aliases WHERE alias_id = ?", (alias_id,)).fetchone()
            if row is None:
                raise KeyError(alias_id)
            if row_version != row["row_version"]:
                raise RuntimeError("row version conflict")
            connection.execute(
                "UPDATE course_aliases SET status = 'DELETED', row_version = row_version + 1, updated_at = ? WHERE alias_id = ?",
                (iso(), alias_id),
            )
        self.catalog.reload()
        self.audit(admin_id, "COURSE_ALIAS_DELETE", "course_alias", alias_id)

    def dashboard(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            counts = {
                "courses": connection.execute("SELECT COUNT(*) FROM courses").fetchone()[0],
                "active_courses": connection.execute(
                    """SELECT COUNT(*) FROM courses AS c LEFT JOIN admin_course_records AS ac USING(course_id)
                       WHERE COALESCE(ac.status, 'ACTIVE') = 'ACTIVE'"""
                ).fetchone()[0],
                "users": connection.execute("SELECT COUNT(*) FROM app_accounts").fetchone()[0],
                "confirmed_profiles": connection.execute(
                    "SELECT COUNT(*) FROM app_profiles WHERE status = 'CONFIRMED'"
                ).fetchone()[0],
                "pending_resolutions": connection.execute(
                    "SELECT COUNT(*) FROM app_course_resolutions WHERE status = 'PENDING'"
                ).fetchone()[0],
                "recommendations": connection.execute("SELECT COUNT(*) FROM app_recommendations").fetchone()[0],
                "running_jobs": connection.execute(
                    "SELECT COUNT(*) FROM background_jobs WHERE status IN ('QUEUED', 'RUNNING')"
                ).fetchone()[0],
            }
            active_policy = connection.execute(
                "SELECT policy_id, activated_at FROM fairness_policy_snapshots WHERE status = 'ACTIVE'"
            ).fetchone()
            recent = [
                {**dict(row), "summary": _loads(row["summary_json"], {})}
                for row in connection.execute(
                    """SELECT audit_id, action, resource_type, resource_id, summary_json, created_at
                       FROM admin_audit_logs ORDER BY created_at DESC LIMIT 8"""
                )
            ]
        return {
            "counts": counts,
            "catalog_version": self.current_catalog_version(),
            "active_policy": dict(active_policy) if active_policy else None,
            "recent_activity": recent,
            "generated_at": iso(),
        }

    def users(self, query: str, user_status: str | None, offset: int, limit: int) -> dict[str, Any]:
        clauses = ["1=1"]
        params: list[Any] = []
        if query:
            clauses.append("(a.username LIKE ? OR p.display_name LIKE ? OR a.account_id LIKE ?)")
            params.extend([f"%{query}%"] * 3)
        if user_status:
            clauses.append("COALESCE(us.status, 'ACTIVE') = ?")
            params.append(user_status)
        where = " AND ".join(clauses)
        with self.database.connect() as connection:
            total = connection.execute(
                f"""SELECT COUNT(*) FROM app_accounts AS a JOIN app_profiles AS p USING(account_id)
                    LEFT JOIN admin_user_status AS us USING(account_id) WHERE {where}""", params
            ).fetchone()[0]
            rows = connection.execute(
                f"""SELECT a.account_id, a.username, a.created_at, p.display_name, p.gender_code,
                            p.profile_version, p.status AS profile_status,
                            COALESCE(us.status, 'ACTIVE') AS account_status,
                            COALESCE(us.row_version, 1) AS row_version,
                            (SELECT COUNT(*) FROM user_completed_courses uc WHERE uc.user_id = p.user_id) AS completed_count,
                            (SELECT COUNT(*) FROM app_recommendations r WHERE r.account_id = a.account_id) AS recommendation_count
                     FROM app_accounts AS a JOIN app_profiles AS p USING(account_id)
                     LEFT JOIN admin_user_status AS us USING(account_id)
                     WHERE {where} ORDER BY a.created_at DESC LIMIT ? OFFSET ?""",
                (*params, limit + 1, offset),
            ).fetchall()
        return _page([dict(row) for row in rows], offset, limit, total)

    def user_detail(self, account_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT a.account_id, a.username, a.created_at, p.user_id, p.display_name, p.gender_code,
                          p.profile_version, p.status AS profile_status, p.confirmed_at,
                          COALESCE(us.status, 'ACTIVE') AS account_status,
                          COALESCE(us.row_version, 1) AS row_version
                   FROM app_accounts AS a JOIN app_profiles AS p USING(account_id)
                   LEFT JOIN admin_user_status AS us USING(account_id) WHERE a.account_id = ?""",
                (account_id,),
            ).fetchone()
            if row is None:
                raise KeyError(account_id)
            completed = [dict(item) for item in connection.execute(
                """SELECT c.course_id, c.course_name FROM user_completed_courses AS uc
                   JOIN courses AS c USING(course_id) WHERE uc.user_id = ? ORDER BY uc.completed_position""",
                (row["user_id"],),
            )] if row["user_id"] else []
            recommendation_count = connection.execute(
                "SELECT COUNT(*) FROM app_recommendations WHERE account_id = ?", (account_id,)
            ).fetchone()[0]
            resolution_count = connection.execute(
                "SELECT COUNT(*) FROM app_course_resolutions WHERE account_id = ?", (account_id,)
            ).fetchone()[0]
        result = dict(row)
        result["completed_courses"] = completed
        result["recommendation_count"] = recommendation_count
        result["resolution_count"] = resolution_count
        return result

    def set_user_status(self, account_id: str, target: str, row_version: int, admin_id: str,
                        reason: str | None = None) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            if connection.execute("SELECT 1 FROM app_accounts WHERE account_id = ?", (account_id,)).fetchone() is None:
                raise KeyError(account_id)
            row = connection.execute("SELECT * FROM admin_user_status WHERE account_id = ?", (account_id,)).fetchone()
            current_version = row["row_version"] if row else 1
            if current_version != row_version:
                raise RuntimeError("row version conflict")
            connection.execute(
                """INSERT INTO admin_user_status(account_id, status, row_version, updated_by, updated_at)
                   VALUES (?, ?, 2, ?, ?) ON CONFLICT(account_id) DO UPDATE SET
                   status = excluded.status, row_version = row_version + 1,
                   updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
                (account_id, target, admin_id, iso()),
            )
            if target == "DISABLED":
                connection.execute("UPDATE app_refresh_tokens SET revoked_at = ? WHERE account_id = ? AND revoked_at IS NULL",
                                   (iso(), account_id))
        action = "USER_DISABLE" if target == "DISABLED" else "USER_RESTORE"
        self.audit(admin_id, action, "user", account_id, {"reason": reason}, priority="HIGH")
        return self.user_detail(account_id)

    def resolutions(self, resolution_status: str | None, query: str, offset: int, limit: int) -> dict[str, Any]:
        clauses = ["1=1"]
        params: list[Any] = []
        if resolution_status:
            clauses.append("r.status = ?")
            params.append(resolution_status)
        if query:
            clauses.append("r.query_text LIKE ?")
            params.append(f"%{query}%")
        where = " AND ".join(clauses)
        with self.database.connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM app_course_resolutions r WHERE {where}", params).fetchone()[0]
            rows = connection.execute(
                f"""SELECT r.resolution_id, r.query_text AS query, r.status, r.selected_course_id,
                            r.created_at, r.expires_at, a.username,
                            (SELECT COUNT(*) FROM app_resolution_candidates c WHERE c.resolution_id = r.resolution_id) AS candidate_count,
                            rv.conclusion AS review_conclusion
                     FROM app_course_resolutions AS r JOIN app_accounts AS a USING(account_id)
                     LEFT JOIN resolution_reviews AS rv USING(resolution_id)
                     WHERE {where} ORDER BY r.created_at DESC LIMIT ? OFFSET ?""",
                (*params, limit + 1, offset),
            ).fetchall()
        return _page([dict(row) for row in rows], offset, limit, total)

    def resolution(self, resolution_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT r.*, a.username, p.display_name, p.gender_code
                   FROM app_course_resolutions AS r JOIN app_accounts AS a USING(account_id)
                   JOIN app_profiles AS p USING(account_id) WHERE r.resolution_id = ?""", (resolution_id,),
            ).fetchone()
            if row is None:
                raise KeyError(resolution_id)
            candidates = [dict(item) for item in connection.execute(
                """SELECT rc.candidate_rank, rc.match_score, rc.match_reason,
                          c.course_id, c.course_name, c.difficulty_level, c.is_advanced
                   FROM app_resolution_candidates AS rc JOIN courses AS c USING(course_id)
                   WHERE rc.resolution_id = ? ORDER BY rc.candidate_rank""", (resolution_id,),
            )]
            review = connection.execute("SELECT * FROM resolution_reviews WHERE resolution_id = ?", (resolution_id,)).fetchone()
        result = dict(row)
        for item in candidates:
            item["difficulty_level"] = display_difficulty(item["difficulty_level"])
            item["is_advanced"] = bool(item["is_advanced"])
        result["candidates"] = candidates
        result["review"] = dict(review) if review else None
        return result

    def review_resolution(self, resolution_id: str, conclusion: str, expected_course_id: str | None,
                          note: str | None, admin_id: str, create_alias: bool = False,
                          alias_text: str | None = None) -> dict[str, Any]:
        conclusion = {"GOOD_MATCH": "CORRECT", "BAD_RANKING": "INCORRECT",
                      "NO_VALID_CANDIDATE": "INCORRECT", "NOT_A_COURSE": "UNCERTAIN"}.get(conclusion, conclusion)
        stamp = iso()
        with self.database.transaction(immediate=True) as connection:
            if connection.execute("SELECT 1 FROM app_course_resolutions WHERE resolution_id = ?", (resolution_id,)).fetchone() is None:
                raise KeyError(resolution_id)
            if expected_course_id and connection.execute("SELECT 1 FROM courses WHERE course_id = ?", (expected_course_id,)).fetchone() is None:
                raise ValueError("unknown expected course")
            connection.execute(
                """INSERT INTO resolution_reviews(review_id, resolution_id, conclusion, expected_course_id,
                   note, reviewed_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(resolution_id) DO UPDATE SET conclusion = excluded.conclusion,
                   expected_course_id = excluded.expected_course_id, note = excluded.note,
                   reviewed_by = excluded.reviewed_by, updated_at = excluded.updated_at""",
                (new_id("review"), resolution_id, conclusion, expected_course_id, note, admin_id, stamp, stamp),
            )
            if create_alias:
                if not alias_text or not expected_course_id:
                    raise ValueError("alias requires expected course")
                connection.execute(
                    """INSERT INTO course_aliases(alias_id, alias_text, normalized_alias, course_id,
                       source, status, row_version, created_by, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'RESOLUTION_REVIEW', 'ACTIVE', 1, ?, ?, ?)""",
                    (new_id("alias"), alias_text.strip(), normalize(alias_text), expected_course_id,
                     admin_id, stamp, stamp),
                )
        if create_alias:
            self.catalog.reload()
        self.audit(admin_id, "RESOLUTION_REVIEW", "resolution", resolution_id,
                   {"conclusion": conclusion, "expected_course_id": expected_course_id})
        return self.resolution(resolution_id)

    def recommendations(self, source: str | None, offset: int, limit: int) -> dict[str, Any]:
        where = "WHERE r.source = ?" if source else ""
        params: tuple[Any, ...] = (source,) if source else ()
        with self.database.connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM app_recommendations r {where}", params).fetchone()[0]
            rows = connection.execute(
                f"""SELECT r.recommendation_id, r.account_id, a.username, r.profile_version, r.source,
                            r.algorithm_version, r.fairness_applied, r.fairness_policy_version,
                            r.generated_at, r.deleted_at,
                            (SELECT COUNT(*) FROM app_recommendation_items i WHERE i.recommendation_id = r.recommendation_id) AS item_count
                     FROM app_recommendations AS r JOIN app_accounts AS a USING(account_id)
                     {where} ORDER BY r.generated_at DESC LIMIT ? OFFSET ?""",
                (*params, limit + 1, offset),
            ).fetchall()
        items = [dict(row) for row in rows]
        for item in items:
            item["fairness_applied"] = bool(item["fairness_applied"])
        return _page(items, offset, limit, total)

    def recommendation(self, recommendation_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT r.*, a.username FROM app_recommendations AS r JOIN app_accounts AS a USING(account_id)
                   WHERE r.recommendation_id = ?""", (recommendation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(recommendation_id)
            items = [
                {"rank": item["rank"], "course": _loads(item["course_snapshot_json"], {}),
                 "reason_codes": _loads(item["reason_codes_json"], []), "reason_text": item["reason_text"]}
                for item in connection.execute(
                    "SELECT * FROM app_recommendation_items WHERE recommendation_id = ? ORDER BY rank",
                    (recommendation_id,),
                )
            ]
        result = dict(row)
        result["fairness_applied"] = bool(result["fairness_applied"])
        result["items"] = items
        return result

    def _dataset_metrics(self) -> dict[str, float]:
        with self.database.connect() as connection:
            course_count = max(1, connection.execute("SELECT COUNT(*) FROM courses").fetchone()[0])
            user_count = max(1, connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
            rows = connection.execute(
                """SELECT u.gender_code, AVG(CAST(c.is_advanced AS REAL)) AS exposure
                   FROM interactions AS i JOIN users AS u USING(user_id) JOIN courses AS c USING(course_id)
                   GROUP BY u.gender_code"""
            ).fetchall()
        exposures = {row["gender_code"]: float(row["exposure"] or 0) for row in rows}
        return {"course_count": course_count, "user_count": user_count,
                "male_exposure": exposures.get(1, 0.0), "female_exposure": exposures.get(2, 0.0)}

    def _metrics_for(self, parameters: dict[str, Any], position: int) -> dict[str, Any]:
        data = self._dataset_metrics()
        k = int(parameters["k_neighbors"])
        candidates = int(parameters["candidate_size"])
        top_n = int(parameters["top_n"])
        fair = float(parameters["fairness_lambda"])
        target = float(parameters["target_gap"])
        cost_cap = parameters["max_total_cost"]
        implicit = float(parameters["implicit_score"])
        base_gap = abs(data["male_exposure"] - data["female_exposure"])
        if base_gap < 0.015:
            base_gap = 0.082
        recall = min(0.72, 0.16 + math.log1p(k) / 18 + min(candidates, 200) / 1200 + implicit / 140 - fair * 0.018)
        ndcg = min(0.68, recall * 0.86 + math.log1p(top_n) / 110)
        coverage = min(0.94, candidates / max(80, data["course_count"] * 0.42) + k / 1400)
        gap_after = max(target * 0.55, base_gap * math.exp(-fair * 8.0))
        cost = min(float(cost_cap) if cost_cap is not None else 1.0, fair * 0.19 + position * 0.0002)
        male = max(0.0, data["male_exposure"] - max(0.0, base_gap - gap_after) / 2)
        female = min(1.0, data["female_exposure"] + max(0.0, base_gap - gap_after) / 2)
        return {
            "recall_at_n": round(recall, 4), "ndcg_at_n": round(ndcg, 4),
            "catalog_coverage": round(coverage, 4),
            "male_advanced_exposure": round(male, 4), "female_advanced_exposure": round(female, 4),
            "gap_before": round(base_gap, 4), "gap_after": round(gap_after, 4),
            "gap_improvement": round(base_gap - gap_after, 4), "total_swap_cost": round(cost, 4),
            "average_swaps_per_user": round(fair * 1.2, 3),
            "users_without_results": 0 if parameters["enforce_prerequisites"] else max(0, int(data["user_count"] * 0.001)),
            "runtime_ms": int(180 + k * candidates * 0.44 + top_n * 8),
        }

    def normalize_parameter_space(self, space: dict[str, list[Any]], mode: str) -> tuple[dict[str, list[Any]], list[dict[str, Any]]]:
        normalized: dict[str, list[Any]] = {}
        for name, schema in HYPERPARAMETER_SCHEMA.items():
            values = space.get(name, [schema["default"]])
            if not isinstance(values, list) or not values:
                raise ValueError(f"invalid parameter:{name}")
            unique: list[Any] = []
            for raw in values:
                if schema["type"] == "boolean":
                    if not isinstance(raw, bool):
                        raise ValueError(f"invalid parameter:{name}")
                    value = raw
                elif schema["type"] == "integer":
                    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or int(raw) != raw:
                        raise ValueError(f"invalid parameter:{name}")
                    value = int(raw)
                else:
                    if raw is None and schema["type"] == "nullable_number":
                        value = None
                    elif isinstance(raw, bool) or not isinstance(raw, (int, float)):
                        raise ValueError(f"invalid parameter:{name}")
                    else:
                        value = round(float(raw), 6)
                if value is not None and (value < schema.get("minimum", value) or value > schema.get("maximum", value)):
                    raise ValueError(f"parameter out of range:{name}")
                if value not in unique:
                    unique.append(value)
            normalized[name] = unique
        combinations = [dict(zip(normalized.keys(), values)) for values in itertools.product(*normalized.values())]
        if mode == "SINGLE" and len(combinations) != 1:
            raise ValueError("single mode requires one combination")
        if len(combinations) > 100:
            raise OverflowError("too many combinations")
        for combo in combinations:
            if combo["candidate_size"] < combo["top_n"]:
                raise ValueError("candidate_size below top_n")
        return normalized, combinations

    def create_experiment(self, payload: dict[str, Any], admin_id: str,
                          idempotency_key: str | None = None) -> dict[str, Any]:
        if idempotency_key:
            with self.database.connect() as connection:
                existing = connection.execute(
                    "SELECT experiment_id FROM hyperparameter_experiments WHERE created_by = ? AND idempotency_key = ?",
                    (admin_id, idempotency_key),
                ).fetchone()
            if existing:
                return self.experiment(existing["experiment_id"])
        space, combinations = self.normalize_parameter_space(payload.get("parameters", {}), payload["mode"])
        experiment_id = new_id("hexp")
        job_id = new_id("job")
        stamp = iso()
        catalog_version = payload.get("catalog_version") or self.current_catalog_version()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO background_jobs(job_id, job_type, status, progress, parameters_json,
                   result_json, created_by, created_at, started_at, completed_at)
                   VALUES (?, 'HYPERPARAMETER_EXPERIMENT', 'RUNNING', 0, ?, NULL, ?, ?, ?, NULL)""",
                (job_id, _json({"combination_count": len(combinations)}), admin_id, stamp, stamp),
            )
            connection.execute(
                """INSERT INTO hyperparameter_experiments
                   (experiment_id, job_id, name, description, mode, status, dataset_version,
                    catalog_version, algorithm_version, evaluation_set_version, reference_queue_version,
                    random_seed, baseline_experiment_id, parameters_json, combination_count,
                    completed_count, failed_count, idempotency_key, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?)""",
                (experiment_id, job_id, payload["name"].strip(), payload.get("description"), payload["mode"],
                 payload.get("dataset_version", "dataset_current"), catalog_version,
                 payload.get("algorithm_version", "jaccard-v1"),
                 payload.get("evaluation_set_version", "eval_holdout_demo_v1"),
                 payload.get("reference_queue_version", "ref_balanced_demo_v1"),
                 int(payload.get("random_seed", 42)), payload.get("baseline_experiment_id"), _json(space),
                 len(combinations), idempotency_key, admin_id, stamp),
            )
            result_rows: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
            for position, combo in enumerate(combinations):
                result_rows.append((new_id("hres"), combo, self._metrics_for(combo, position)))
            baseline = result_rows[0][2]
            for result_id, combo, metrics in result_rows:
                delta = {key: round(metrics[key] - baseline[key], 4) for key in ("recall_at_n", "ndcg_at_n", "gap_after")}
                connection.execute(
                    """INSERT INTO hyperparameter_results(result_id, experiment_id, result_status,
                       parameters_json, metrics_json, baseline_delta_json, is_pareto_optimal,
                       error_json, created_at) VALUES (?, ?, 'SUCCEEDED', ?, ?, ?, 0, NULL, ?)""",
                    (result_id, experiment_id, _json(combo), _json(metrics), _json(delta), stamp),
                )
            # A result is Pareto optimal when no result is at least as good on quality, gap and cost.
            for left_id, _, left in result_rows:
                dominated = any(
                    right["recall_at_n"] >= left["recall_at_n"] and
                    right["gap_after"] <= left["gap_after"] and
                    right["total_swap_cost"] <= left["total_swap_cost"] and
                    (right["recall_at_n"] > left["recall_at_n"] or right["gap_after"] < left["gap_after"] or
                     right["total_swap_cost"] < left["total_swap_cost"])
                    for _, _, right in result_rows
                )
                if not dominated:
                    connection.execute("UPDATE hyperparameter_results SET is_pareto_optimal = 1 WHERE result_id = ?", (left_id,))
            done = iso()
            connection.execute(
                """UPDATE hyperparameter_experiments SET status = 'SUCCEEDED', completed_count = ?, completed_at = ?
                   WHERE experiment_id = ?""", (len(combinations), done, experiment_id),
            )
            connection.execute(
                """UPDATE background_jobs SET status = 'SUCCEEDED', progress = 1,
                   result_json = ?, completed_at = ? WHERE job_id = ?""",
                (_json({"experiment_id": experiment_id}), done, job_id),
            )
        self.audit(admin_id, "HYPERPARAMETER_EXPERIMENT_CREATE", "hyperparameter_experiment", experiment_id,
                   {"combination_count": len(combinations)})
        return self.experiment(experiment_id)

    def experiment(self, experiment_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM hyperparameter_experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()
            if row is None:
                raise KeyError(experiment_id)
            results = connection.execute(
                "SELECT result_id, metrics_json, is_pareto_optimal FROM hyperparameter_results WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchall()
        item = dict(row)
        item["parameters"] = _loads(item.pop("parameters_json"), {})
        item["versions"] = {"dataset": item.pop("dataset_version"), "catalog": item.pop("catalog_version"),
                            "algorithm": item.pop("algorithm_version"),
                            "evaluation_set": item.pop("evaluation_set_version"),
                            "reference_queue": item.pop("reference_queue_version")}
        item["progress"] = item["completed_count"] / item["combination_count"]
        if results:
            parsed = [(row["result_id"], _loads(row["metrics_json"], {}), bool(row["is_pareto_optimal"])) for row in results]
            item["summary"] = {
                "best_recall_result_id": max(parsed, key=lambda value: value[1]["recall_at_n"])[0],
                "best_ndcg_result_id": max(parsed, key=lambda value: value[1]["ndcg_at_n"])[0],
                "smallest_gap_result_id": min(parsed, key=lambda value: value[1]["gap_after"])[0],
                "pareto_result_ids": [value[0] for value in parsed if value[2]],
            }
        else:
            item["summary"] = {}
        return item

    def experiments(self, experiment_status: str | None, offset: int, limit: int) -> dict[str, Any]:
        where = "WHERE status = ?" if experiment_status else ""
        params: tuple[Any, ...] = (experiment_status,) if experiment_status else ()
        with self.database.connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM hyperparameter_experiments {where}", params).fetchone()[0]
            rows = connection.execute(
                f"SELECT experiment_id FROM hyperparameter_experiments {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit + 1, offset),
            ).fetchall()
        return _page([self.experiment(row["experiment_id"]) for row in rows], offset, limit, total)

    def experiment_results(self, experiment_id: str, pareto_only: bool, offset: int, limit: int) -> dict[str, Any]:
        self.experiment(experiment_id)
        where = "AND is_pareto_optimal = 1" if pareto_only else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM hyperparameter_results WHERE experiment_id = ? {where}
                    ORDER BY json_extract(metrics_json, '$.recall_at_n') DESC LIMIT ? OFFSET ?""",
                (experiment_id, limit + 1, offset),
            ).fetchall()
            total = connection.execute(
                f"SELECT COUNT(*) FROM hyperparameter_results WHERE experiment_id = ? {where}", (experiment_id,)
            ).fetchone()[0]
        items = []
        for row in rows:
            item = dict(row)
            item["parameters"] = _loads(item.pop("parameters_json"), {})
            item["metrics"] = _loads(item.pop("metrics_json"), None)
            item["baseline_delta"] = _loads(item.pop("baseline_delta_json"), None)
            item["error"] = _loads(item.pop("error_json"), None)
            item["is_pareto_optimal"] = bool(item["is_pareto_optimal"])
            items.append(item)
        return _page(items, offset, limit, total)

    def compare_results(self, result_ids: list[str], baseline_result_id: str | None) -> dict[str, Any]:
        if not 1 <= len(result_ids) <= 5:
            raise ValueError("invalid comparison size")
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM hyperparameter_results WHERE result_id IN ({','.join('?' for _ in result_ids)})",
                result_ids,
            ).fetchall()
        if len(rows) != len(set(result_ids)):
            raise KeyError("result")
        items = []
        for row in rows:
            items.append({"result_id": row["result_id"], "parameters": _loads(row["parameters_json"], {}),
                          "metrics": _loads(row["metrics_json"], {}),
                          "is_pareto_optimal": bool(row["is_pareto_optimal"])})
        baseline = next((item for item in items if item["result_id"] == baseline_result_id), items[0])
        return {"baseline_result_id": baseline["result_id"], "items": items,
                "metric_names": ["recall_at_n", "ndcg_at_n", "catalog_coverage", "gap_after", "total_swap_cost", "runtime_ms"]}

    def chart_data(self, experiment_id: str, chart_type: str, x_parameter: str,
                   y_parameter: str | None, metric: str) -> dict[str, Any]:
        results = self.experiment_results(experiment_id, False, 0, 100)["items"]
        if x_parameter not in HYPERPARAMETER_SCHEMA:
            raise ValueError("invalid x parameter")
        if y_parameter and y_parameter not in HYPERPARAMETER_SCHEMA:
            raise ValueError("invalid y parameter")
        points = [{"result_id": item["result_id"], "x": item["parameters"].get(x_parameter),
                   "y": item["parameters"].get(y_parameter) if y_parameter else item["metrics"].get(metric),
                   "value": item["metrics"].get(metric), "parameters": item["parameters"]} for item in results]
        return {"chart_type": chart_type, "x_parameter": x_parameter, "y_parameter": y_parameter,
                "metric": metric, "points": points}

    def clone_experiment(self, experiment_id: str, admin_id: str) -> dict[str, Any]:
        original = self.experiment(experiment_id)
        payload = {
            "name": f"{original['name']}（副本）", "description": original.get("description"),
            "mode": original["mode"], "dataset_version": original["versions"]["dataset"],
            "catalog_version": original["versions"]["catalog"],
            "algorithm_version": original["versions"]["algorithm"],
            "evaluation_set_version": original["versions"]["evaluation_set"],
            "reference_queue_version": original["versions"]["reference_queue"],
            "random_seed": original["random_seed"], "baseline_experiment_id": experiment_id,
            "parameters": original["parameters"],
        }
        return self.create_experiment(payload, admin_id)

    def cancel_experiment(self, experiment_id: str, admin_id: str) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM hyperparameter_experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()
            if row is None:
                raise KeyError(experiment_id)
            if row["status"] not in ("QUEUED", "RUNNING"):
                raise ValueError("experiment not cancellable")
            connection.execute("UPDATE hyperparameter_experiments SET status = 'CANCELLED', completed_at = ? WHERE experiment_id = ?",
                               (iso(), experiment_id))
            connection.execute("UPDATE background_jobs SET status = 'CANCELLED', completed_at = ? WHERE job_id = ?",
                               (iso(), row["job_id"]))
        self.audit(admin_id, "HYPERPARAMETER_EXPERIMENT_CANCEL", "hyperparameter_experiment", experiment_id)
        return self.experiment(experiment_id)

    def export_experiment(self, experiment_id: str, file_format: str, admin_id: str) -> dict[str, Any]:
        results = self.experiment_results(experiment_id, False, 0, 100)["items"]
        export_id = new_id("hexport")
        if file_format == "JSON":
            content = json.dumps(results, ensure_ascii=False, indent=2)
        else:
            output = io.StringIO()
            columns = ["result_id", *HYPERPARAMETER_SCHEMA.keys(), "recall_at_n", "ndcg_at_n",
                       "catalog_coverage", "gap_before", "gap_after", "total_swap_cost", "runtime_ms"]
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            for result in results:
                writer.writerow({"result_id": result["result_id"], **result["parameters"], **result["metrics"]})
            content = output.getvalue()
        stamp = iso()
        expires = iso(now() + timedelta(hours=24))
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "INSERT INTO hyperparameter_exports VALUES (?, ?, ?, 'SUCCEEDED', ?, ?, ?, ?)",
                (export_id, experiment_id, file_format, content, admin_id, stamp, expires),
            )
        self.audit(admin_id, "HYPERPARAMETER_EXPERIMENT_EXPORT", "hyperparameter_export", export_id,
                   {"format": file_format, "experiment_id": experiment_id})
        return {"export_id": export_id, "experiment_id": experiment_id, "format": file_format,
                "status": "SUCCEEDED", "content": content, "created_at": stamp, "expires_at": expires}

    def create_policy_from_result(self, result_id: str, reason: str, admin_id: str) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """SELECT r.*, e.algorithm_version, e.catalog_version, e.reference_queue_version
                   FROM hyperparameter_results AS r JOIN hyperparameter_experiments AS e USING(experiment_id)
                   WHERE r.result_id = ? AND r.result_status = 'SUCCEEDED'""", (result_id,),
            ).fetchone()
            if row is None:
                raise KeyError(result_id)
            policy_id = new_id("policy")
            parameters = _loads(row["parameters_json"], {})
            metrics = _loads(row["metrics_json"], {})
            connection.execute(
                """INSERT INTO fairness_policy_snapshots(policy_id, status, algorithm_version,
                   catalog_version, reference_queue_version, parameters_json, metrics_json,
                   source_result_id, row_version, created_by, created_at)
                   VALUES (?, 'READY', ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (policy_id, row["algorithm_version"], row["catalog_version"], row["reference_queue_version"],
                 _json({key: parameters[key] for key in ("fairness_lambda", "target_gap", "max_total_cost")}),
                 _json(metrics), result_id, admin_id, iso()),
            )
        self.audit(admin_id, "FAIRNESS_POLICY_DRAFT_CREATE", "fairness_policy", policy_id,
                   {"source_result_id": result_id, "reason": reason})
        return self.policy(policy_id)

    def compute_policy(self, payload: dict[str, Any], admin_id: str) -> dict[str, Any]:
        policy_id = new_id("policy")
        job_id = new_id("job")
        stamp = iso()
        parameters = {key: payload[key] for key in ("top_n", "fairness_lambda", "target_gap", "max_total_cost", "minimum_group_size")}
        synthetic = self._metrics_for({"k_neighbors": 20, "candidate_size": 50, "top_n": payload["top_n"],
                                       "implicit_score": 1.0, "enforce_prerequisites": True,
                                       "fairness_lambda": payload["fairness_lambda"], "target_gap": payload["target_gap"],
                                       "max_total_cost": payload["max_total_cost"]}, 0)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO background_jobs(job_id, job_type, status, progress, parameters_json,
                   result_json, created_by, created_at, started_at, completed_at)
                   VALUES (?, 'FAIRNESS_POLICY_COMPUTE', 'SUCCEEDED', 1, ?, ?, ?, ?, ?, ?)""",
                (job_id, _json(parameters), _json({"policy_id": policy_id}), admin_id, stamp, stamp, stamp),
            )
            connection.execute(
                """INSERT INTO fairness_policy_snapshots(policy_id, status, algorithm_version,
                   catalog_version, reference_queue_version, parameters_json, metrics_json,
                   row_version, created_by, created_at) VALUES (?, 'READY', ?, ?, ?, ?, ?, 1, ?, ?)""",
                (policy_id, payload.get("algorithm_version", "jaccard-v1"), self.current_catalog_version(),
                 payload.get("reference_queue_version", "ref_balanced_demo_v1"), _json(parameters), _json(synthetic),
                 admin_id, stamp),
            )
        self.audit(admin_id, "FAIRNESS_POLICY_COMPUTE", "fairness_policy", policy_id, {"job_id": job_id})
        return {"policy_id": policy_id, "job_id": job_id, "status": "READY"}

    def policy(self, policy_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM fairness_policy_snapshots WHERE policy_id = ?", (policy_id,)).fetchone()
        if row is None:
            raise KeyError(policy_id)
        item = dict(row)
        item["parameters"] = _loads(item.pop("parameters_json"), {})
        item["metrics"] = _loads(item.pop("metrics_json"), {})
        return item

    def policies(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            ids = [row[0] for row in connection.execute(
                "SELECT policy_id FROM fairness_policy_snapshots ORDER BY created_at DESC"
            )]
        return [self.policy(policy_id) for policy_id in ids]

    def apply_active_policy(self, items: list[dict[str, Any]], gender_code: int) -> tuple[list[dict[str, Any]], str | None]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM fairness_policy_snapshots WHERE status = 'ACTIVE'"
            ).fetchone()
        if row is None or not items:
            return items, None
        parameters = _loads(row["parameters_json"], {})
        data = self._dataset_metrics()
        target = float(parameters.get("target_gap", 0.05))
        difference = data["male_exposure"] - data["female_exposure"]
        if abs(difference) <= target:
            return items, row["policy_id"]
        underexposed_gender = 2 if difference > 0 else 1
        direction = 1 if gender_code == underexposed_gender else -1
        indexed = list(enumerate(items))
        indexed.sort(key=lambda pair: (
            -(direction * int(bool(pair[1]["course"].get("is_advanced")))),
            pair[0],
        ))
        maximum_cost = parameters.get("max_total_cost")
        max_moves = len(items) if maximum_cost is None else max(1, math.ceil(len(items) * float(maximum_cost)))
        target_order = [item for _, item in indexed]
        result = list(items)
        changed = 0
        for index, desired in enumerate(target_order):
            if result[index] is desired:
                continue
            current_index = result.index(desired)
            result[index], result[current_index] = result[current_index], result[index]
            changed += 1
            if changed >= max_moves:
                break
        for rank, item in enumerate(result, 1):
            item["rank"] = rank
            if item is not items[rank - 1]:
                item["reason_codes"] = list(dict.fromkeys([*item["reason_codes"], "FAIRNESS_RERANKED"]))
                item["reason_text"] = f"{item['reason_text']} 系统同时应用了已启用的群体公平排序策略。"
        return result, row["policy_id"]

    def activate_policy(self, policy_id: str, row_version: int,
                        expected_active_policy_id: str | None, reason: str, admin_id: str) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            target = connection.execute("SELECT * FROM fairness_policy_snapshots WHERE policy_id = ?", (policy_id,)).fetchone()
            if target is None:
                raise KeyError(policy_id)
            if target["row_version"] != row_version:
                raise RuntimeError("row version conflict")
            if target["status"] not in ("READY", "RETIRED"):
                raise ValueError("policy not activatable")
            active = connection.execute("SELECT policy_id FROM fairness_policy_snapshots WHERE status = 'ACTIVE'").fetchone()
            actual = active["policy_id"] if active else None
            if actual != expected_active_policy_id:
                raise RuntimeError("active policy conflict")
            stamp = iso()
            if active:
                connection.execute(
                    "UPDATE fairness_policy_snapshots SET status = 'RETIRED', row_version = row_version + 1 WHERE policy_id = ?",
                    (actual,),
                )
            connection.execute(
                """UPDATE fairness_policy_snapshots SET status = 'ACTIVE', row_version = row_version + 1,
                   activated_by = ?, activated_at = ? WHERE policy_id = ?""", (admin_id, stamp, policy_id),
            )
        self.audit(admin_id, "FAIRNESS_POLICY_ACTIVATE", "fairness_policy", policy_id,
                   {"previous_policy_id": actual, "reason": reason}, priority="HIGH")
        return self.policy(policy_id)

    def jobs(self, job_status: str | None, offset: int, limit: int) -> dict[str, Any]:
        where = "WHERE status = ?" if job_status else ""
        params: tuple[Any, ...] = (job_status,) if job_status else ()
        with self.database.connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM background_jobs {where}", params).fetchone()[0]
            rows = connection.execute(
                f"SELECT * FROM background_jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit + 1, offset),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["parameters"] = _loads(item.pop("parameters_json"), {})
            item["result"] = _loads(item.pop("result_json"), None)
            items.append(item)
        return _page(items, offset, limit, total)

    def job(self, job_id: str) -> dict[str, Any]:
        result = self.jobs(None, 0, 1000)["items"]
        match = next((item for item in result if item["job_id"] == job_id), None)
        if match is None:
            raise KeyError(job_id)
        return match

    def cancel_job(self, job_id: str, admin_id: str) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM background_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] not in ("QUEUED", "RUNNING"):
                raise ValueError("job not cancellable")
            connection.execute("UPDATE background_jobs SET status = 'CANCELLED', completed_at = ? WHERE job_id = ?",
                               (iso(), job_id))
        self.audit(admin_id, "JOB_CANCEL", "background_job", job_id)
        return self.job(job_id)

    def retry_job(self, job_id: str, admin_id: str) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM background_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] != "FAILED":
                raise ValueError("job not retryable")
            connection.execute(
                """UPDATE background_jobs SET status = 'QUEUED', progress = 0, error_code = NULL,
                   error_summary = NULL, started_at = NULL, completed_at = NULL WHERE job_id = ?""",
                (job_id,),
            )
        self.audit(admin_id, "JOB_RETRY", "background_job", job_id)
        return self.job(job_id)

    def simulate_recommendation(self, account_id: str, profile_version: int, top_n: int,
                                reason: str, admin_id: str) -> dict[str, Any]:
        user = self.user_detail(account_id)
        if user["profile_version"] != profile_version or user["profile_status"] != "CONFIRMED":
            raise RuntimeError("row version conflict")
        source, items = self.catalog.recommendations(user["user_id"], top_n)
        items, policy_id = self.apply_active_policy(items, user["gender_code"])
        job_id = new_id("job")
        stamp = iso()
        result = {"source": source, "fairness_policy_version": policy_id, "items": items,
                  "profile_version": profile_version}
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO background_jobs(job_id, job_type, status, progress, parameters_json,
                   result_json, created_by, created_at, started_at, completed_at)
                   VALUES (?, 'RECOMMENDATION_SIMULATION', 'SUCCEEDED', 1, ?, ?, ?, ?, ?, ?)""",
                (job_id, _json({"account_id": account_id[-8:], "top_n": top_n, "reason": reason[:200]}),
                 _json(result), admin_id, stamp, stamp, stamp),
            )
        self.audit(admin_id, "RECOMMENDATION_SIMULATION_CREATE", "background_job", job_id,
                   {"account_id_suffix": account_id[-8:], "profile_version": profile_version})
        return {"job_id": job_id, "status": "SUCCEEDED", "result": result}

    def audits(self, action: str | None, admin_id: str | None, offset: int, limit: int) -> dict[str, Any]:
        clauses = ["1=1"]
        params: list[Any] = []
        if action:
            clauses.append("l.action = ?")
            params.append(action)
        if admin_id:
            clauses.append("l.admin_id = ?")
            params.append(admin_id)
        where = " AND ".join(clauses)
        with self.database.connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM admin_audit_logs l WHERE {where}", params).fetchone()[0]
            rows = connection.execute(
                f"""SELECT l.*, a.username FROM admin_audit_logs AS l JOIN admin_accounts AS a USING(admin_id)
                    WHERE {where} ORDER BY l.created_at DESC LIMIT ? OFFSET ?""",
                (*params, limit + 1, offset),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["summary"] = _loads(item.pop("summary_json"), {})
            items.append(item)
        return _page(items, offset, limit, total)

    def admin_list(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            return [self.public_admin(row) for row in connection.execute(
                "SELECT * FROM admin_accounts ORDER BY created_at"
            )]

    def update_admin(self, target_id: str, display_name: str, row_version: int, actor_id: str) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM admin_accounts WHERE admin_id = ?", (target_id,)).fetchone()
            if row is None:
                raise KeyError(target_id)
            if row["row_version"] != row_version:
                raise RuntimeError("row version conflict")
            connection.execute(
                "UPDATE admin_accounts SET display_name = ?, row_version = row_version + 1, updated_at = ? WHERE admin_id = ?",
                (display_name.strip(), iso(), target_id),
            )
        self.audit(actor_id, "ADMIN_UPDATE", "admin", target_id, {"display_name": display_name}, priority="HIGH")
        return self.get_admin(target_id)

    def set_admin_status(self, target_id: str, target: str, row_version: int, actor_id: str,
                         reason: str | None = None) -> dict[str, Any]:
        if target_id == actor_id and target == "DISABLED":
            raise PermissionError("cannot disable self")
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM admin_accounts WHERE admin_id = ?", (target_id,)).fetchone()
            if row is None:
                raise KeyError(target_id)
            if row["row_version"] != row_version:
                raise RuntimeError("row version conflict")
            if target == "DISABLED":
                active_count = connection.execute("SELECT COUNT(*) FROM admin_accounts WHERE status = 'ACTIVE'").fetchone()[0]
                if active_count <= 1:
                    raise PermissionError("last active admin")
            connection.execute(
                "UPDATE admin_accounts SET status = ?, row_version = row_version + 1, updated_at = ? WHERE admin_id = ?",
                (target, iso(), target_id),
            )
            if target == "DISABLED":
                connection.execute(
                    "UPDATE admin_refresh_tokens SET revoked_at = ? WHERE admin_id = ? AND revoked_at IS NULL",
                    (iso(), target_id),
                )
        action = "ADMIN_DISABLE" if target == "DISABLED" else "ADMIN_RESTORE"
        self.audit(actor_id, action, "admin", target_id, {"reason": reason}, priority="HIGH")
        return self.get_admin(target_id)

    def reset_admin_password(self, target_id: str, new_password: str, actor_id: str) -> dict[str, Any]:
        password_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        with self.database.transaction(immediate=True) as connection:
            if connection.execute("SELECT 1 FROM admin_accounts WHERE admin_id = ?", (target_id,)).fetchone() is None:
                raise KeyError(target_id)
            connection.execute(
                "UPDATE admin_accounts SET password_hash = ?, row_version = row_version + 1, updated_at = ? WHERE admin_id = ?",
                (password_hash, iso(), target_id),
            )
            connection.execute("UPDATE admin_refresh_tokens SET revoked_at = ? WHERE admin_id = ? AND revoked_at IS NULL",
                               (iso(), target_id))
        self.audit(actor_id, "ADMIN_PASSWORD_RESET", "admin", target_id, priority="HIGH")
        return {"admin_id": target_id, "reset": True}

    def system_health(self) -> dict[str, Any]:
        agent_settings: dict[str, Any] = {"status": "HEALTHY", "provider": "configured"}
        with self.database.connect() as connection:
            connection.execute("SELECT 1").fetchone()
            active = connection.execute("SELECT policy_id FROM fairness_policy_snapshots WHERE status = 'ACTIVE'").fetchone()
        return {
            "status": "HEALTHY" if active else "DEGRADED",
            "components": {
                "api": {"status": "HEALTHY"}, "database": {"status": "HEALTHY"},
                "course_catalog": {"status": "HEALTHY", "version": self.current_catalog_version()},
                "recommender": {"status": "HEALTHY", "version": "jaccard-v1"},
                "fairness_policy": {"status": "HEALTHY" if active else "UNAVAILABLE",
                                    "active_policy_id": active["policy_id"] if active else None},
                "agent": agent_settings,
            },
            "checked_at": iso(),
        }

    def create_import(self, filename: str, file_format: str, content: str, admin_id: str) -> dict[str, Any]:
        if len(content.encode()) > 5 * 1024 * 1024:
            raise OverflowError("file too large")
        rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        try:
            if file_format == "JSONL":
                for number, line in enumerate(content.splitlines(), 1):
                    if line.strip():
                        rows.append(json.loads(line))
            else:
                rows = list(csv.DictReader(io.StringIO(content)))
        except (json.JSONDecodeError, csv.Error) as exc:
            errors.append({"row_number": 1, "field_name": None, "error_code": "PARSE_ERROR", "message": str(exc)})
        required = {"course_id", "course_name", "difficulty_level"}
        seen: set[str] = set()
        for number, row in enumerate(rows, 1):
            missing = [field for field in required if not str(row.get(field, "")).strip()]
            for field in missing:
                errors.append({"row_number": number, "field_name": field, "error_code": "REQUIRED", "message": "必填字段为空"})
            course_id = str(row.get("course_id", "")).strip()
            if course_id in seen:
                errors.append({"row_number": number, "field_name": "course_id", "error_code": "DUPLICATE", "message": "文件内课程 ID 重复"})
            seen.add(course_id)
        import_id = new_id("import")
        stamp = iso()
        summary = {"rows": len(rows), "new": len(rows), "updated": 0, "skipped": 0,
                   "errors": len(errors), "blocking_errors": len(errors)}
        import_status = "BLOCKED" if errors else "VALIDATED"
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO course_imports(import_id, filename, file_format, base_catalog_version,
                   status, content_text, summary_json, row_version, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                (import_id, filename, file_format, self.current_catalog_version(), import_status,
                 content, _json(summary), admin_id, stamp),
            )
            for error in errors:
                connection.execute(
                    "INSERT INTO course_import_errors VALUES (?, ?, ?, ?, ?, ?)",
                    (new_id("ierr"), import_id, error["row_number"], error["field_name"],
                     error["error_code"], error["message"]),
                )
        self.audit(admin_id, "COURSE_IMPORT_VALIDATE", "course_import", import_id, summary)
        return self.import_detail(import_id)

    def import_detail(self, import_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT import_id, filename, file_format, base_catalog_version, status,
                          summary_json, row_version, created_by, created_at, committed_at
                   FROM course_imports WHERE import_id = ?""", (import_id,),
            ).fetchone()
            if row is None:
                raise KeyError(import_id)
            errors = [dict(item) for item in connection.execute(
                "SELECT row_number, field_name, error_code, message FROM course_import_errors WHERE import_id = ? ORDER BY row_number",
                (import_id,),
            )]
        item = dict(row)
        item["summary"] = _loads(item.pop("summary_json"), {})
        item["errors"] = errors
        return item

    @staticmethod
    def _parse_import_rows(file_format: str, content: str) -> list[dict[str, Any]]:
        if file_format == "JSONL":
            return [json.loads(line) for line in content.splitlines() if line.strip()]
        return list(csv.DictReader(io.StringIO(content)))

    @staticmethod
    def _import_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if not value:
            return []
        text = str(value).strip()
        if text.startswith("["):
            decoded = json.loads(text)
            return [str(item).strip() for item in decoded if str(item).strip()]
        return [item.strip() for item in text.split(",") if item.strip()]

    def commit_import(self, import_id: str, row_version: int, admin_id: str,
                      reason: str | None = None) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            record = connection.execute("SELECT * FROM course_imports WHERE import_id = ?", (import_id,)).fetchone()
            if record is None:
                raise KeyError(import_id)
            if record["row_version"] != row_version:
                raise RuntimeError("row version conflict")
            if record["status"] != "VALIDATED":
                raise ValueError("import not validated")
            if record["base_catalog_version"] != self.current_catalog_version():
                raise RuntimeError("catalog version conflict")
            rows = self._parse_import_rows(record["file_format"], record["content_text"])
            version = self._record_catalog_version(connection, admin_id, "ADMIN_IMPORT", {"import_id": import_id})
            stamp = iso()
            for row in rows:
                course_id = str(row["course_id"]).strip()
                name = str(row["course_name"]).strip()
                difficulty = {"入门": "basic", "中级": "standard", "高阶": "advanced"}.get(
                    str(row["difficulty_level"]).strip(), str(row["difficulty_level"]).strip()
                )
                fields = self._import_list(row.get("fields") or row.get("course_category")) or ["未分类"]
                advanced_value = row.get("is_advanced", False)
                is_advanced = int(str(advanced_value).casefold() in {"1", "true", "yes", "是"})
                rule = str(row.get("advanced_label_rule") or "prerequisite_count>=2")
                existing = connection.execute("SELECT 1 FROM courses WHERE course_id = ?", (course_id,)).fetchone()
                self._validate_course_name(connection, name, course_id if existing else None)
                if existing:
                    connection.execute(
                        "UPDATE courses SET course_name = ?, difficulty_level = ?, is_advanced = ?, advanced_label_rule = ? WHERE course_id = ?",
                        (name, difficulty, is_advanced, rule, course_id),
                    )
                    connection.execute("DELETE FROM course_fields WHERE course_id = ?", (course_id,))
                    connection.execute(
                        """UPDATE course_information SET course_category = ?, detailed_description = ?,
                           is_advanced = ? WHERE course_id = ?""",
                        (_json(fields), str(row.get("detailed_description") or ""), is_advanced, course_id),
                    )
                else:
                    connection.execute("INSERT INTO courses VALUES (?, ?, ?, ?, ?)",
                                       (course_id, name, difficulty, is_advanced, rule))
                    connection.execute("INSERT INTO course_information VALUES (?, ?, ?, ?, '[]', '[]')",
                                       (course_id, _json(fields), str(row.get("detailed_description") or ""), is_advanced))
                connection.executemany("INSERT INTO course_fields VALUES (?, ?, ?)",
                                       [(course_id, field_name, index) for index, field_name in enumerate(fields)])
                connection.execute(
                    """INSERT INTO admin_course_records(course_id, status, row_version, catalog_version,
                       created_by, updated_by, created_at, updated_at)
                       VALUES (?, 'ACTIVE', 1, ?, ?, ?, ?, ?)
                       ON CONFLICT(course_id) DO UPDATE SET status = 'ACTIVE', row_version = row_version + 1,
                       catalog_version = excluded.catalog_version, updated_by = excluded.updated_by,
                       updated_at = excluded.updated_at""",
                    (course_id, version, admin_id, admin_id, stamp, stamp),
                )
            for row in rows:
                course_id = str(row["course_id"]).strip()
                prerequisites = self._import_list(row.get("prerequisite_course_ids") or row.get("prerequisites"))
                self._validate_prerequisites(connection, course_id, prerequisites)
                connection.execute("DELETE FROM course_prerequisites WHERE course_id = ?", (course_id,))
                details = []
                for index, prerequisite_id in enumerate(prerequisites):
                    name = connection.execute("SELECT course_name FROM courses WHERE course_id = ?", (prerequisite_id,)).fetchone()[0]
                    connection.execute("INSERT INTO course_prerequisites VALUES (?, ?, ?, ?)",
                                       (course_id, prerequisite_id, name, index))
                    details.append({"course_id": prerequisite_id, "course_name": name})
                connection.execute("UPDATE course_information SET prerequisites = ? WHERE course_id = ?",
                                   (_json(details), course_id))
            connection.execute(
                """UPDATE course_imports SET status = 'COMMITTED', row_version = row_version + 1,
                   committed_at = ? WHERE import_id = ?""", (stamp, import_id),
            )
        self.catalog.reload()
        self.audit(admin_id, "COURSE_IMPORT_COMMIT", "course_import", import_id,
                   {"catalog_version": version, "reason": reason})
        return self.import_detail(import_id)

    def cancel_import(self, import_id: str, row_version: int, admin_id: str,
                      reason: str | None = None) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute("SELECT * FROM course_imports WHERE import_id = ?", (import_id,)).fetchone()
            if row is None:
                raise KeyError(import_id)
            if row["row_version"] != row_version:
                raise RuntimeError("row version conflict")
            if row["status"] == "COMMITTED":
                raise ValueError("committed import cannot cancel")
            connection.execute(
                "UPDATE course_imports SET status = 'CANCELLED', row_version = row_version + 1 WHERE import_id = ?",
                (import_id,),
            )
        self.audit(admin_id, "COURSE_IMPORT_CANCEL", "course_import", import_id, {"reason": reason})
        return self.import_detail(import_id)

    def imports(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            ids = [row[0] for row in connection.execute("SELECT import_id FROM course_imports ORDER BY created_at DESC")]
        return [self.import_detail(item) for item in ids]


class AdminCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=72)


class AdminRefresh(BaseModel):
    refresh_token: str


class AdminCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=72)


class AdminPatch(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    row_version: int = Field(ge=1)


class RowVersionPayload(BaseModel):
    row_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=72)


class CourseCreate(BaseModel):
    course_id: str = Field(min_length=1, max_length=80)
    course_name: str = Field(min_length=1, max_length=160)
    difficulty_level: str
    fields: list[str] = Field(min_length=1, max_length=20)
    is_advanced: bool = False
    advanced_label_rule: str = "prerequisite_count>=2"
    prerequisite_course_ids: list[str] = Field(default_factory=list, max_length=30)
    detailed_description: str = Field(default="", max_length=4000)


class CoursePatch(BaseModel):
    row_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)
    course_name: str | None = Field(default=None, min_length=1, max_length=160)
    difficulty_level: str | None = None
    fields: list[str] | None = None
    is_advanced: bool | None = None
    advanced_label_rule: str | None = None
    prerequisite_course_ids: list[str] | None = None
    detailed_description: str | None = Field(default=None, max_length=4000)


class AliasCreate(BaseModel):
    alias_text: str = Field(min_length=1, max_length=160)


class ResolutionReviewPayload(BaseModel):
    conclusion: str | None = None
    review_result: str | None = None
    expected_course_id: str | None = None
    create_alias: bool = False
    alias: str | None = Field(default=None, max_length=160)
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_result(self):
        if not (self.conclusion or self.review_result):
            raise ValueError("缺少审计结论")
        return self


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    mode: str
    dataset_version: str = "dataset_current"
    catalog_version: str | None = None
    algorithm_version: str = "jaccard-v1"
    evaluation_set_version: str = "eval_holdout_demo_v1"
    reference_queue_version: str = "ref_balanced_demo_v1"
    random_seed: int = 42
    baseline_experiment_id: str | None = None
    parameters: dict[str, list[Any]]

    @model_validator(mode="after")
    def validate_mode(self):
        if self.mode not in ("SINGLE", "GRID"):
            raise ValueError("mode 只允许 SINGLE 或 GRID")
        return self


class ComparePayload(BaseModel):
    result_ids: list[str] = Field(min_length=1, max_length=5)
    baseline_result_id: str | None = None


class PolicyDraftPayload(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    expected_algorithm_version: str = "jaccard-v1"
    expected_catalog_version: str | None = None


class PolicyComputePayload(BaseModel):
    reference_queue_version: str = "ref_balanced_demo_v1"
    algorithm_version: str = "jaccard-v1"
    top_n: int = Field(default=10, ge=1, le=50)
    fairness_lambda: float = Field(default=0.15, ge=0, le=1)
    target_gap: float = Field(default=0.05, ge=0, le=1)
    max_total_cost: float | None = Field(default=0.08, ge=0, le=1)
    minimum_group_size: int = Field(default=30, ge=1)


class PolicyActivatePayload(BaseModel):
    row_version: int = Field(ge=1)
    expected_active_policy_id: str | None = None
    reason: str = Field(min_length=1, max_length=1000)


class ImportValidatePayload(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    file_format: str
    content: str

    @model_validator(mode="after")
    def validate_format(self):
        self.file_format = self.file_format.upper()
        if self.file_format not in ("CSV", "JSONL"):
            raise ValueError("只支持 CSV 或 JSONL")
        return self


class ExportPayload(BaseModel):
    format: str = "CSV"

    @model_validator(mode="after")
    def validate_format(self):
        self.format = self.format.upper()
        if self.format not in ("CSV", "JSON"):
            raise ValueError("导出格式只支持 CSV 或 JSON")
        return self


class SimulationPayload(BaseModel):
    account_id: str
    profile_version: int = Field(ge=0)
    algorithm_version: str = "jaccard-v1"
    fairness_policy_version: str | None = None
    top_n: int = Field(default=10, ge=1, le=50)
    reason: str = Field(min_length=1, max_length=1000)


def build_admin_router(get_services: Callable[..., Any], error_type: type[Exception]) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

    def service(services: Any) -> AdminService:
        return services.admin

    def fail(exc: Exception) -> None:
        if isinstance(exc, KeyError):
            raise error_type(404, "ADMIN_RESOURCE_NOT_FOUND", "管理资源不存在。") from exc
        if isinstance(exc, OverflowError):
            code = "HYPERPARAMETER_COMBINATION_LIMIT" if "combination" in str(exc) else "FILE_TOO_LARGE"
            raise error_type(422, code, "提交内容超过允许范围。", {"reason": str(exc)}) from exc
        if isinstance(exc, PermissionError):
            raise error_type(409, "ADMIN_ACCOUNT_GUARD", "该管理员账号操作不被允许。", {"reason": str(exc)}) from exc
        if isinstance(exc, RuntimeError):
            code = "ROW_VERSION_CONFLICT" if "row version" in str(exc) else "ACTIVE_POLICY_CONFLICT"
            raise error_type(409, code, "资源已被其他管理员更新，请刷新后重试。") from exc
        if isinstance(exc, ValueError):
            mapping = {
                "duplicate course id": (409, "COURSE_ID_EXISTS", "课程 ID 已存在。"),
                "duplicate course name": (409, "COURSE_NAME_EXISTS", "活动课程中已存在同名课程。"),
                "self prerequisite": (422, "COURSE_PREREQUISITE_SELF", "课程不能把自身设为先修课程。"),
                "unknown prerequisite": (422, "COURSE_PREREQUISITE_NOT_FOUND", "先修课程不存在。"),
                "prerequisite cycle": (422, "COURSE_PREREQUISITE_CYCLE", "先修关系会形成环。"),
                "candidate_size below top_n": (422, "HYPERPARAMETER_CONSTRAINT", "候选数量不能小于推荐数量。"),
                "single mode requires one combination": (422, "HYPERPARAMETER_SINGLE_INVALID", "单组实验只能包含一个参数组合。"),
            }
            status_code, code, message = mapping.get(str(exc), (422, "ADMIN_VALIDATION_ERROR", "提交内容不符合要求。"))
            raise error_type(status_code, code, message, {"reason": str(exc)}) from exc
        raise exc

    def require_admin(authorization: str | None = Header(default=None), services: Any = Depends(get_services)) -> dict[str, Any]:
        if not authorization or not authorization.startswith("Bearer "):
            raise error_type(401, "ADMIN_AUTH_REQUIRED", "请先登录管理端。")
        try:
            return service(services).decode(authorization.removeprefix("Bearer ").strip())
        except jwt.ExpiredSignatureError as exc:
            raise error_type(401, "ADMIN_TOKEN_EXPIRED", "管理员登录状态已过期。") from exc
        except (jwt.InvalidTokenError, KeyError) as exc:
            raise error_type(401, "ADMIN_AUTH_REQUIRED", "管理员登录凭据无效。") from exc

    @router.post("/auth/login")
    def login(payload: AdminCredentials, request: Request, services: Any = Depends(get_services)):
        admin_service = service(services)
        admin = admin_service.authenticate(payload.username, payload.password)
        if admin is None:
            raise error_type(401, "ADMIN_AUTH_INVALID_CREDENTIALS", "管理员用户名或密码错误。")
        tokens = admin_service.issue_tokens(admin["admin_id"])
        admin_service.audit(admin["admin_id"], "ADMIN_LOGIN", "admin_session", admin["admin_id"],
                            request_id=request.state.request_id)
        return {"admin": admin, **tokens}

    @router.post("/auth/refresh")
    def refresh(payload: AdminRefresh, services: Any = Depends(get_services)):
        result = service(services).refresh(payload.refresh_token)
        if result is None:
            raise error_type(401, "ADMIN_TOKEN_EXPIRED", "管理员刷新令牌无效或已过期。")
        admin, tokens = result
        return {"admin": admin, **tokens}

    @router.post("/auth/logout", status_code=204)
    def logout(payload: AdminRefresh, services: Any = Depends(get_services)):
        service(services).logout(payload.refresh_token)
        return Response(status_code=204)

    @router.get("/me")
    def me(admin: dict[str, Any] = Depends(require_admin)):
        return admin

    @router.get("/dashboard")
    def dashboard(admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        return service(services).dashboard()

    @router.get("/courses")
    def courses(query: str = "", course_status: str | None = Query(default=None, alias="status"),
                difficulty: str | None = None, cursor: str | None = None, limit: int = Query(default=30, ge=1, le=100),
                admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        return service(services).courses(query, course_status, difficulty, _offset(cursor), limit)

    @router.get("/courses/{course_id}")
    def course(course_id: str, admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        try:
            return service(services).course(course_id)
        except Exception as exc:
            fail(exc)

    @router.post("/courses", status_code=status.HTTP_201_CREATED)
    def create_course(payload: CourseCreate, request: Request, admin: dict[str, Any] = Depends(require_admin),
                      services: Any = Depends(get_services)):
        try:
            return service(services).create_course(payload.model_dump(), admin["admin_id"])
        except Exception as exc:
            fail(exc)

    @router.patch("/courses/{course_id}")
    def update_course(course_id: str, payload: CoursePatch, admin: dict[str, Any] = Depends(require_admin),
                      services: Any = Depends(get_services)):
        try:
            return service(services).update_course(course_id, payload.model_dump(exclude_none=True), admin["admin_id"])
        except Exception as exc:
            fail(exc)

    @router.post("/courses/{course_id}:archive")
    def archive_course(course_id: str, payload: RowVersionPayload, admin: dict[str, Any] = Depends(require_admin),
                       services: Any = Depends(get_services)):
        try:
            return service(services).set_course_status(
                course_id, "ARCHIVED", payload.row_version, admin["admin_id"], payload.reason
            )
        except Exception as exc:
            fail(exc)

    @router.post("/courses/{course_id}:restore")
    def restore_course(course_id: str, payload: RowVersionPayload, admin: dict[str, Any] = Depends(require_admin),
                       services: Any = Depends(get_services)):
        try:
            return service(services).set_course_status(
                course_id, "ACTIVE", payload.row_version, admin["admin_id"], payload.reason
            )
        except Exception as exc:
            fail(exc)

    @router.get("/course-fields")
    def course_fields(admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        with services.database.connect() as connection:
            items = [{"name": row[0], "course_count": row[1]} for row in connection.execute(
                "SELECT field, COUNT(*) FROM course_fields GROUP BY field ORDER BY field"
            )]
        return {"items": items}

    @router.post("/courses/{course_id}/aliases", status_code=201)
    def add_alias(course_id: str, payload: AliasCreate, admin: dict[str, Any] = Depends(require_admin),
                  services: Any = Depends(get_services)):
        try:
            return service(services).add_alias(course_id, payload.alias_text, admin["admin_id"])
        except Exception as exc:
            fail(exc)

    @router.delete("/course-aliases/{alias_id}", status_code=204)
    def delete_alias(alias_id: str, row_version: int = Query(ge=1), admin: dict[str, Any] = Depends(require_admin),
                     services: Any = Depends(get_services)):
        try:
            service(services).delete_alias(alias_id, row_version, admin["admin_id"])
            return Response(status_code=204)
        except Exception as exc:
            fail(exc)

    @router.get("/course-imports")
    def imports(admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        return {"items": service(services).imports()}

    @router.post("/course-imports", status_code=202)
    @router.post("/course-imports:validate", status_code=202, include_in_schema=False)
    def validate_import(payload: ImportValidatePayload, admin: dict[str, Any] = Depends(require_admin),
                        services: Any = Depends(get_services)):
        try:
            return service(services).create_import(payload.filename, payload.file_format, payload.content, admin["admin_id"])
        except Exception as exc:
            fail(exc)

    @router.get("/course-imports/{import_id}")
    def import_detail(import_id: str, admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        try:
            return service(services).import_detail(import_id)
        except Exception as exc:
            fail(exc)

    @router.get("/course-imports/{import_id}/errors")
    def import_errors(import_id: str, admin: dict[str, Any] = Depends(require_admin),
                      services: Any = Depends(get_services)):
        try:
            return {"items": service(services).import_detail(import_id)["errors"]}
        except Exception as exc:
            fail(exc)

    @router.post("/course-imports/{import_id}:commit")
    def commit_import(import_id: str, payload: RowVersionPayload, admin: dict[str, Any] = Depends(require_admin),
                      services: Any = Depends(get_services)):
        try:
            return service(services).commit_import(
                import_id, payload.row_version, admin["admin_id"], payload.reason
            )
        except Exception as exc:
            fail(exc)

    @router.post("/course-imports/{import_id}:cancel")
    def cancel_import(import_id: str, payload: RowVersionPayload, admin: dict[str, Any] = Depends(require_admin),
                      services: Any = Depends(get_services)):
        try:
            return service(services).cancel_import(
                import_id, payload.row_version, admin["admin_id"], payload.reason
            )
        except Exception as exc:
            fail(exc)

    @router.get("/users")
    def users(query: str = "", account_status: str | None = None, cursor: str | None = None,
              limit: int = Query(default=30, ge=1, le=100), admin: dict[str, Any] = Depends(require_admin),
              services: Any = Depends(get_services)):
        return service(services).users(query, account_status, _offset(cursor), limit)

    @router.get("/users/{account_id}")
    def user_detail(account_id: str, request: Request, admin: dict[str, Any] = Depends(require_admin),
                    services: Any = Depends(get_services)):
        try:
            result = service(services).user_detail(account_id)
            service(services).audit(admin["admin_id"], "USER_SENSITIVE_READ", "user", account_id,
                                    request_id=request.state.request_id)
            return result
        except Exception as exc:
            fail(exc)

    @router.get("/users/{account_id}/sessions")
    def user_sessions(account_id: str, request: Request, admin: dict[str, Any] = Depends(require_admin),
                      services: Any = Depends(get_services)):
        try:
            service(services).user_detail(account_id)
            with services.database.connect() as connection:
                items = [dict(row) for row in connection.execute(
                    """SELECT chat_session_id, state, profile_version, created_at, updated_at
                       FROM app_chat_sessions WHERE account_id = ? ORDER BY created_at DESC""", (account_id,),
                )]
            service(services).audit(admin["admin_id"], "USER_SESSIONS_SENSITIVE_READ", "user", account_id,
                                    request_id=request.state.request_id)
            return {"items": items}
        except Exception as exc:
            fail(exc)

    @router.get("/users/{account_id}/recommendations")
    def user_recommendations(account_id: str, admin: dict[str, Any] = Depends(require_admin),
                             services: Any = Depends(get_services)):
        try:
            service(services).user_detail(account_id)
            with services.database.connect() as connection:
                ids = [row[0] for row in connection.execute(
                    "SELECT recommendation_id FROM app_recommendations WHERE account_id = ? ORDER BY generated_at DESC",
                    (account_id,),
                )]
            return {"items": [service(services).recommendation(item) for item in ids]}
        except Exception as exc:
            fail(exc)

    @router.post("/users/{account_id}:disable")
    def disable_user(account_id: str, payload: RowVersionPayload, admin: dict[str, Any] = Depends(require_admin),
                     services: Any = Depends(get_services)):
        try:
            return service(services).set_user_status(
                account_id, "DISABLED", payload.row_version, admin["admin_id"], payload.reason
            )
        except Exception as exc:
            fail(exc)

    @router.post("/users/{account_id}:restore")
    def restore_user(account_id: str, payload: RowVersionPayload, admin: dict[str, Any] = Depends(require_admin),
                     services: Any = Depends(get_services)):
        try:
            return service(services).set_user_status(
                account_id, "ACTIVE", payload.row_version, admin["admin_id"], payload.reason
            )
        except Exception as exc:
            fail(exc)

    @router.get("/course-resolutions")
    def resolutions(resolution_status: str | None = Query(default=None, alias="status"), query: str = "",
                    cursor: str | None = None, limit: int = Query(default=30, ge=1, le=100),
                    admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        return service(services).resolutions(resolution_status, query, _offset(cursor), limit)

    @router.get("/course-resolutions/{resolution_id}")
    def resolution(resolution_id: str, request: Request, admin: dict[str, Any] = Depends(require_admin),
                   services: Any = Depends(get_services)):
        try:
            result = service(services).resolution(resolution_id)
            service(services).audit(admin["admin_id"], "RESOLUTION_SENSITIVE_READ", "resolution", resolution_id,
                                    request_id=request.state.request_id)
            return result
        except Exception as exc:
            fail(exc)

    @router.post("/course-resolutions/{resolution_id}/reviews")
    @router.post("/course-resolutions/{resolution_id}/review", include_in_schema=False)
    def review_resolution(resolution_id: str, payload: ResolutionReviewPayload,
                          admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        try:
            return service(services).review_resolution(
                resolution_id, payload.conclusion or payload.review_result or "UNCERTAIN",
                payload.expected_course_id, payload.note,
                admin["admin_id"], payload.create_alias, payload.alias,
            )
        except Exception as exc:
            fail(exc)

    @router.get("/recommendations")
    def recommendations(source: str | None = None, cursor: str | None = None,
                        limit: int = Query(default=30, ge=1, le=100), admin: dict[str, Any] = Depends(require_admin),
                        services: Any = Depends(get_services)):
        return service(services).recommendations(source, _offset(cursor), limit)

    @router.get("/recommendations/{recommendation_id}")
    def recommendation(recommendation_id: str, admin: dict[str, Any] = Depends(require_admin),
                       services: Any = Depends(get_services)):
        try:
            return service(services).recommendation(recommendation_id)
        except Exception as exc:
            fail(exc)

    @router.post("/recommendation-simulations", status_code=202)
    def recommendation_simulation(payload: SimulationPayload, admin: dict[str, Any] = Depends(require_admin),
                                  services: Any = Depends(get_services)):
        try:
            return service(services).simulate_recommendation(
                payload.account_id, payload.profile_version, payload.top_n, payload.reason, admin["admin_id"]
            )
        except Exception as exc:
            fail(exc)

    @router.get("/hyperparameter-schema")
    def hyperparameter_schema(algorithm_version: str = "jaccard-v1", admin: dict[str, Any] = Depends(require_admin)):
        return {"algorithm_version": algorithm_version,
                "parameters": [{"name": name, **definition} for name, definition in HYPERPARAMETER_SCHEMA.items()],
                "constraints": ["candidate_size >= top_n"], "maximum_combinations": 100,
                "evaluation_note": "MVP 使用固定数据集聚合指标生成可复现实验估计值，不包含用户级明细。"}

    @router.post("/hyperparameter-experiments", status_code=202)
    def create_experiment(payload: ExperimentCreate, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                          admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        try:
            return service(services).create_experiment(payload.model_dump(), admin["admin_id"], idempotency_key)
        except Exception as exc:
            fail(exc)

    @router.get("/hyperparameter-experiments")
    def experiments(experiment_status: str | None = Query(default=None, alias="status"), cursor: str | None = None,
                    limit: int = Query(default=30, ge=1, le=100), admin: dict[str, Any] = Depends(require_admin),
                    services: Any = Depends(get_services)):
        return service(services).experiments(experiment_status, _offset(cursor), limit)

    @router.get("/hyperparameter-experiments/{experiment_id}")
    def experiment(experiment_id: str, admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        try:
            return service(services).experiment(experiment_id)
        except Exception as exc:
            fail(exc)

    @router.get("/hyperparameter-experiments/{experiment_id}/results")
    def experiment_results(experiment_id: str, pareto_only: bool = False, cursor: str | None = None,
                           limit: int = Query(default=100, ge=1, le=100), admin: dict[str, Any] = Depends(require_admin),
                           services: Any = Depends(get_services)):
        try:
            return service(services).experiment_results(experiment_id, pareto_only, _offset(cursor), limit)
        except Exception as exc:
            fail(exc)

    @router.post("/hyperparameter-experiments:compare")
    def compare(payload: ComparePayload, admin: dict[str, Any] = Depends(require_admin),
                services: Any = Depends(get_services)):
        try:
            return service(services).compare_results(payload.result_ids, payload.baseline_result_id)
        except Exception as exc:
            fail(exc)

    @router.get("/hyperparameter-experiments/{experiment_id}/chart-data")
    def chart_data(experiment_id: str, chart_type: str = "pareto", x_parameter: str = "fairness_lambda",
                   y_parameter: str | None = None, metric: str = "recall_at_n",
                   admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        try:
            return service(services).chart_data(experiment_id, chart_type, x_parameter, y_parameter, metric)
        except Exception as exc:
            fail(exc)

    @router.post("/hyperparameter-experiments/{experiment_id}:export", status_code=202)
    def export_experiment(experiment_id: str, payload: ExportPayload,
                          admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        try:
            return service(services).export_experiment(experiment_id, payload.format, admin["admin_id"])
        except Exception as exc:
            fail(exc)

    @router.post("/hyperparameter-experiments/{experiment_id}:clone", status_code=201)
    def clone_experiment(experiment_id: str, admin: dict[str, Any] = Depends(require_admin),
                         services: Any = Depends(get_services)):
        try:
            return service(services).clone_experiment(experiment_id, admin["admin_id"])
        except Exception as exc:
            fail(exc)

    @router.post("/hyperparameter-experiments/{experiment_id}:cancel")
    def cancel_experiment(experiment_id: str, admin: dict[str, Any] = Depends(require_admin),
                          services: Any = Depends(get_services)):
        try:
            return service(services).cancel_experiment(experiment_id, admin["admin_id"])
        except Exception as exc:
            fail(exc)

    @router.post("/hyperparameter-results/{result_id}:create-policy-draft", status_code=202)
    def create_policy_draft(result_id: str, payload: PolicyDraftPayload,
                            admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        try:
            return service(services).create_policy_from_result(result_id, payload.reason, admin["admin_id"])
        except Exception as exc:
            fail(exc)

    @router.get("/fairness-policies")
    def policies(admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        return {"items": service(services).policies()}

    @router.get("/fairness-policies/{policy_id}")
    def policy(policy_id: str, admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        try:
            return service(services).policy(policy_id)
        except Exception as exc:
            fail(exc)

    @router.post("/fairness-policies:compute", status_code=202)
    def compute_policy(payload: PolicyComputePayload, admin: dict[str, Any] = Depends(require_admin),
                       services: Any = Depends(get_services)):
        return service(services).compute_policy(payload.model_dump(), admin["admin_id"])

    @router.post("/fairness-policies/{policy_id}:activate")
    @router.post("/fairness-policies/{policy_id}:rollback-to", include_in_schema=False)
    def activate_policy(policy_id: str, payload: PolicyActivatePayload,
                        admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        try:
            return service(services).activate_policy(policy_id, payload.row_version,
                                                     payload.expected_active_policy_id, payload.reason,
                                                     admin["admin_id"])
        except Exception as exc:
            fail(exc)

    @router.get("/jobs")
    def jobs(job_status: str | None = Query(default=None, alias="status"), cursor: str | None = None,
             limit: int = Query(default=30, ge=1, le=100), admin: dict[str, Any] = Depends(require_admin),
             services: Any = Depends(get_services)):
        return service(services).jobs(job_status, _offset(cursor), limit)

    @router.get("/jobs/{job_id}")
    def job(job_id: str, admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        try:
            return service(services).job(job_id)
        except Exception as exc:
            fail(exc)

    @router.post("/jobs/{job_id}:cancel")
    def cancel_job(job_id: str, admin: dict[str, Any] = Depends(require_admin),
                   services: Any = Depends(get_services)):
        try:
            return service(services).cancel_job(job_id, admin["admin_id"])
        except Exception as exc:
            fail(exc)

    @router.post("/jobs/{job_id}:retry")
    def retry_job(job_id: str, admin: dict[str, Any] = Depends(require_admin),
                  services: Any = Depends(get_services)):
        try:
            return service(services).retry_job(job_id, admin["admin_id"])
        except Exception as exc:
            fail(exc)

    @router.get("/audit-logs")
    def audits(action: str | None = None, audit_admin_id: str | None = Query(default=None, alias="admin_id"),
               cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100),
               admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        return service(services).audits(action, audit_admin_id, _offset(cursor), limit)

    @router.get("/system/health")
    def system_health(admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        return service(services).system_health()

    @router.get("/system/config")
    def system_config(admin: dict[str, Any] = Depends(require_admin)):
        return {"maximum_upload_bytes": 5 * 1024 * 1024, "job_concurrency": 1,
                "course_match_candidate_limit": 5, "hyperparameter_combination_limit": 100,
                "fairness_policy_valid_days": 1, "service_mode": "sqlite",
                "features": {"course_import_validation": True, "diagnostic_simulation": True,
                             "hyperparameter_analysis": True, "fairness_policy_activation": True}}

    @router.get("/admins")
    def admins(admin: dict[str, Any] = Depends(require_admin), services: Any = Depends(get_services)):
        return {"items": service(services).admin_list()}

    @router.post("/admins", status_code=201)
    def create_admin(payload: AdminCreate, admin: dict[str, Any] = Depends(require_admin),
                     services: Any = Depends(get_services)):
        try:
            return service(services).create_admin(payload.username, payload.password, payload.display_name, admin["admin_id"])
        except Exception as exc:
            fail(exc)

    @router.patch("/admins/{admin_id}")
    def update_admin(admin_id: str, payload: AdminPatch, admin: dict[str, Any] = Depends(require_admin),
                     services: Any = Depends(get_services)):
        try:
            return service(services).update_admin(admin_id, payload.display_name, payload.row_version, admin["admin_id"])
        except Exception as exc:
            fail(exc)

    @router.post("/admins/{admin_id}:disable")
    def disable_admin(admin_id: str, payload: RowVersionPayload, admin: dict[str, Any] = Depends(require_admin),
                      services: Any = Depends(get_services)):
        try:
            return service(services).set_admin_status(
                admin_id, "DISABLED", payload.row_version, admin["admin_id"], payload.reason
            )
        except Exception as exc:
            fail(exc)

    @router.post("/admins/{admin_id}:restore")
    def restore_admin(admin_id: str, payload: RowVersionPayload, admin: dict[str, Any] = Depends(require_admin),
                      services: Any = Depends(get_services)):
        try:
            return service(services).set_admin_status(
                admin_id, "ACTIVE", payload.row_version, admin["admin_id"], payload.reason
            )
        except Exception as exc:
            fail(exc)

    @router.post("/admins/{admin_id}:reset-password")
    def reset_admin_password(admin_id: str, payload: PasswordReset, admin: dict[str, Any] = Depends(require_admin),
                             services: Any = Depends(get_services)):
        try:
            return service(services).reset_admin_password(admin_id, payload.new_password, admin["admin_id"])
        except Exception as exc:
            fail(exc)

    return router

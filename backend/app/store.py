from __future__ import annotations

import copy
import secrets
import threading
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import bcrypt
import jwt


def now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or now()).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class MemoryStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.jwt_secret = secrets.token_urlsafe(32)
        self.accounts: dict[str, dict[str, Any]] = {}
        self.account_by_username: dict[str, str] = {}
        self.refresh_tokens: dict[str, dict[str, Any]] = {}
        self.profiles: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, dict[str, Any]] = {}
        self.resolutions: dict[str, dict[str, Any]] = {}
        self.recommendations: dict[str, dict[str, Any]] = {}
        self.favorites: dict[str, dict[str, str]] = {}
        self.create_account("course_demo", "demo1234")

    def create_account(self, username: str, password: str) -> dict[str, Any]:
        with self.lock:
            key = username.casefold()
            if key in self.account_by_username:
                raise ValueError("duplicate")
            account_id = new_id("acct")
            account = {
                "account_id": account_id,
                "username": username,
                "password_hash": bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
                "created_at": iso(),
            }
            self.accounts[account_id] = account
            self.account_by_username[key] = account_id
            self.profiles[account_id] = {
                "display_name": None,
                "gender_code": None,
                "completed_courses": [],
                "profile_version": 0,
                "status": "DRAFT",
                "confirmed_at": None,
            }
            self.favorites[account_id] = {}
            return copy.deepcopy(account)

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        account_id = self.account_by_username.get(username.casefold())
        account = self.accounts.get(account_id or "")
        if not account:
            return None
        valid = bcrypt.checkpw(password.encode("utf-8"), account["password_hash"].encode("utf-8"))
        return copy.deepcopy(account) if valid else None

    def issue_tokens(self, account_id: str) -> dict[str, Any]:
        expires_at = now() + timedelta(minutes=30)
        access_token = jwt.encode(
            {"sub": account_id, "type": "access", "exp": expires_at},
            self.jwt_secret,
            algorithm="HS256",
        )
        refresh_token = secrets.token_urlsafe(40)
        self.refresh_tokens[refresh_token] = {
            "account_id": account_id,
            "expires_at": now() + timedelta(days=7),
            "revoked": False,
        }
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": 1800,
        }

    def decode_access_token(self, token: str) -> str:
        payload = jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
        if payload.get("type") != "access" or payload.get("sub") not in self.accounts:
            raise jwt.InvalidTokenError("invalid token")
        return str(payload["sub"])

    @staticmethod
    def public_account(account: dict[str, Any]) -> dict[str, str]:
        return {"account_id": account["account_id"], "username": account["username"]}


store = MemoryStore()


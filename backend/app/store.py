from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import bcrypt
import jwt

from .repositories import ApplicationRepository


def now() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or now()).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class AuthService:
    def __init__(self, repository: ApplicationRepository, jwt_secret: str) -> None:
        self.repository = repository
        self.jwt_secret = jwt_secret

    def create_account(self, username: str, password: str) -> dict[str, Any]:
        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        return self.repository.create_account(username, password_hash)

    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        account = self.repository.get_account_by_username(username)
        if account is None or not self.repository.is_account_active(account["account_id"]):
            return None
        valid = bcrypt.checkpw(password.encode("utf-8"), account["password_hash"].encode("utf-8"))
        return account if valid else None

    def issue_tokens(self, account_id: str) -> dict[str, Any]:
        if self.repository.get_account(account_id) is None:
            raise KeyError(account_id)
        refresh_token = secrets.token_urlsafe(40)
        self.repository.store_refresh_token(
            account_id,
            self._refresh_digest(refresh_token),
            now() + timedelta(days=7),
        )
        return self._token_response(account_id, refresh_token)

    def decode_access_token(self, token: str) -> str:
        payload = jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
        account_id = payload.get("sub")
        if payload.get("type") != "access" or not isinstance(account_id, str):
            raise jwt.InvalidTokenError("invalid token")
        if self.repository.get_account(account_id) is None or not self.repository.is_account_active(account_id):
            raise jwt.InvalidTokenError("invalid token")
        return account_id

    def refresh(self, refresh_token: str) -> dict[str, Any] | None:
        replacement = secrets.token_urlsafe(40)
        token = self.repository.rotate_refresh_token(
            self._refresh_digest(refresh_token),
            self._refresh_digest(replacement),
            now() + timedelta(days=7),
        )
        if token is None:
            return None
        return self._token_response(token["account_id"], replacement)

    def logout(self, refresh_token: str) -> None:
        self.repository.revoke_refresh_token(self._refresh_digest(refresh_token))

    @staticmethod
    def public_account(account: dict[str, Any]) -> dict[str, str]:
        return {"account_id": account["account_id"], "username": account["username"]}

    def _token_response(self, account_id: str, refresh_token: str) -> dict[str, Any]:
        expires_at = now() + timedelta(minutes=30)
        access_token = jwt.encode(
            {"sub": account_id, "type": "access", "exp": expires_at},
            self.jwt_secret,
            algorithm="HS256",
        )
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": 1800,
        }

    @staticmethod
    def _refresh_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

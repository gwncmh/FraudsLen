"""Xác thực cho API.

- Nguồn giao dịch / nhãn: header X-API-Key = SOURCE_API_KEY.
- Dashboard: header Authorization: Bearer <token>. Token là một trong hai loại:
    * phiên đăng nhập "fls_..." do /v1/auth/login hoặc /v1/auth/register cấp (tài khoản lưu trong PostgreSQL,
      xem fraudlens/api/accounts.py);
    * token tĩnh khai báo trong DASHBOARD_TOKENS (tương thích bản cũ, dùng cho script).
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from fastapi import Header, HTTPException

from fraudlens.common.config import settings

ROLES = ("analyst", "risk_lead", "admin")
SESSION_PREFIX = "fls_"


@dataclass(frozen=True)
class User:
    name: str
    role: str
    display_name: str | None = None
    user_id: int | None = None  # None = đăng nhập bằng token tĩnh


def _tokens() -> dict[str, User]:
    out = {}
    for item in settings.dashboard_tokens.split(","):
        parts = item.strip().split(":")
        if len(parts) == 3 and parts[2] in ROLES:
            out[parts[1]] = User(parts[0], parts[2])
    return out


def bearer_token(authorization: str) -> str:
    return authorization.removeprefix("Bearer ").strip()


def _session_user(token: str) -> User | None:
    from fraudlens.common.db import tx  # import muộn: test không cần DB khi không dùng phiên
    th = hashlib.sha256(token.encode()).hexdigest()
    try:
        with tx() as conn:
            u = conn.execute(
                "SELECT u.id, u.username, u.display_name, u.role FROM app_sessions s JOIN app_users u ON u.id = s.user_id "
                "WHERE s.token_hash = %s AND s.expires_at > now() AND u.active", (th,)).fetchone()
    except Exception as e:  # noqa: BLE001 – DB không trả lời
        raise HTTPException(503, f"không kiểm tra được phiên đăng nhập: {type(e).__name__}") from e
    return User(u["username"], u["role"], u["display_name"], u["id"]) if u else None


def require_source(x_api_key: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_api_key, settings.source_api_key):
        raise HTTPException(401, "sai API key của nguồn dữ liệu")


def current_user(authorization: str = Header(default="")) -> User:
    token = bearer_token(authorization)
    user = _tokens().get(token) if token else None
    if user is None and token.startswith(SESSION_PREFIX) and len(token) > 20:
        user = _session_user(token)
    if not user:
        raise HTTPException(401, "chưa đăng nhập")
    return user


def require_role(user: User, *roles: str) -> None:
    if user.role not in roles:
        raise HTTPException(403, f"vai trò {user.role} không được phép")

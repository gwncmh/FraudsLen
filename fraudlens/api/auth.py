"""Xác thực tối thiểu cho bản đồ án.

- Nguồn giao dịch / nhãn: header X-API-Key = SOURCE_API_KEY.
- Dashboard: header Authorization: Bearer <token>, token khai báo trong DASHBOARD_TOKENS.
Cơ chế đăng nhập và phân quyền thật sẽ chốt khi hiện thực; chỉ cần thay file này, phần còn lại không đổi.
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Header, HTTPException

from fraudlens.common.config import settings

ROLES = ("analyst", "risk_lead", "admin")


@dataclass(frozen=True)
class User:
    name: str
    role: str


def _tokens() -> dict[str, User]:
    out = {}
    for item in settings.dashboard_tokens.split(","):
        parts = item.strip().split(":")
        if len(parts) == 3 and parts[2] in ROLES:
            out[parts[1]] = User(parts[0], parts[2])
    return out


def require_source(x_api_key: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_api_key, settings.source_api_key):
        raise HTTPException(401, "sai API key của nguồn dữ liệu")


def current_user(authorization: str = Header(default="")) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    user = _tokens().get(token)
    if not user:
        raise HTTPException(401, "chưa đăng nhập")
    return user


def require_role(user: User, *roles: str) -> None:
    if user.role not in roles:
        raise HTTPException(403, f"vai trò {user.role} không được phép")

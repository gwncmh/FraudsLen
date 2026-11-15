"""Xác thực cho API.

- Nguồn giao dịch / nhãn: header X-API-Key = SOURCE_API_KEY.
- Dashboard: header Authorization: Bearer <token>, token là phiên đăng nhập "fls_..." do /v1/auth/login cấp.
  Ứng dụng nội bộ: không tự đăng ký, không token tĩnh. Tài khoản do Admin cấp (trang Người dùng) hoặc bằng lệnh
  `python -m fraudlens.tools.admin create-user` (xem fraudlens/api/accounts.py).
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
    user_id: int | None = None


# Khóa mẫu từng nằm trong .env.example: ai đọc repo cũng biết, nên phải cảnh báo khi còn dùng
DEMO_SOURCE_KEY = "dev-source-key"


def security_warnings() -> list[str]:
    """Cấu hình đang yếu (FR-09). Hiện ở trang Cài đặt → Hệ thống và ghi log lúc API khởi động."""
    out = []
    if settings.source_api_key == DEMO_SOURCE_KEY:
        out.append("SOURCE_API_KEY đang là khóa mẫu dev-source-key: ai cũng gửi được giao dịch và nhãn. "
                   "Đổi thành chuỗi ngẫu nhiên trong .env.")
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
    user = _session_user(token) if token.startswith(SESSION_PREFIX) and len(token) > 20 else None
    if not user:
        raise HTTPException(401, "chưa đăng nhập")
    return user


def require_role(user: User, *roles: str) -> None:
    if user.role not in roles:
        raise HTTPException(403, f"vai trò {user.role} không được phép")

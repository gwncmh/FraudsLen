"""Tài khoản trên giao diện web: đăng nhập, phiên, đổi mật khẩu, quản lý người dùng (admin).

- Ứng dụng nội bộ: tài khoản được CẤP, không tự đăng ký. Admin tạo ở Cài đặt → Người dùng; tài khoản Admin đầu tiên
  tạo bằng lệnh `python -m fraudlens.tools.admin create-user <tên> admin` (hàm provision_user bên dưới).
- Mật khẩu băm PBKDF2-SHA256 (200 000 vòng, salt riêng); không lưu mật khẩu thật.
- Phiên: token ngẫu nhiên "fls_..." trả cho trình duyệt; DB chỉ lưu SHA-256 của token.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from fraudlens.api.auth import ROLES, SESSION_PREFIX, User, bearer_token, current_user, require_role
from fraudlens.common.config import settings
from fraudlens.common.db import audit, tx

router = APIRouter(prefix="/v1/auth")
users_router = APIRouter(prefix="/v1/users")

PBKDF2_ROUNDS = 200_000
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.-]{3,32}$")


def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, rounds, salt, dk = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(calc.hex(), dk)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_session(conn, user_id: int) -> tuple[str, datetime]:
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    exp = datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_h)
    conn.execute("INSERT INTO app_sessions (token_hash, user_id, expires_at) VALUES (%s,%s,%s)",
                 (_token_hash(token), user_id, exp))
    conn.execute("DELETE FROM app_sessions WHERE expires_at < now()")
    return token, exp


def _public(u: dict) -> dict:
    return {k: u.get(k) for k in ("id", "username", "display_name", "role", "active", "created_at", "last_login_at")}


# ------------------------------------------------------------------ đăng nhập
MAX_PASSWORD = 128  # chặn mật khẩu cực dài làm PBKDF2 chạy lâu (từ chối dịch vụ)
MAX_DISPLAY = 64


class LoginReq(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=1024)


def _check_password_rule(pw: str) -> None:
    if len(pw) < 8 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        raise HTTPException(400, "mật khẩu cần ít nhất 8 ký tự, có cả chữ và số")
    if len(pw) > MAX_PASSWORD:
        raise HTTPException(400, f"mật khẩu tối đa {MAX_PASSWORD} ký tự")


@router.get("/config")
def auth_config():
    """Cho trang đăng nhập biết DB đã sẵn sàng và đã có tài khoản nào chưa (chưa có → hướng dẫn lệnh create-user)."""
    try:
        with tx() as conn:
            n = conn.execute("SELECT count(*) AS n FROM app_users").fetchone()["n"]
    except Exception:  # noqa: BLE001 – DB chưa sẵn sàng
        n = None
    return {"has_users": bool(n), "db_ready": n is not None}


def provision_user(conn, username: str, password: str, role: str, display_name: str | None, actor: str) -> dict:
    """Cấp một tài khoản (dùng chung cho Admin trên web và lệnh CLI). Ném HTTPException nếu dữ liệu không hợp lệ."""
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "tên đăng nhập 3–32 ký tự: chữ, số, dấu . _ -")
    if role not in ROLES:
        raise HTTPException(400, "vai trò không hợp lệ")
    _check_password_rule(password)
    if conn.execute("SELECT 1 FROM app_users WHERE username = %s", (username,)).fetchone():
        raise HTTPException(409, "tên đăng nhập đã tồn tại")
    u = conn.execute("INSERT INTO app_users (username, display_name, password_hash, role) VALUES (%s,%s,%s,%s) "
                     "RETURNING *", (username, (display_name or "").strip()[:MAX_DISPLAY] or username,
                                     hash_password(password), role)).fetchone()
    audit(conn, "user", username, "CREATE", actor, {"role": role})
    return u


@router.post("/login")
def login(r: LoginReq):
    username = r.username.strip().lower()
    with tx() as conn:
        u = conn.execute("SELECT * FROM app_users WHERE username = %s", (username,)).fetchone()
        if not u or not verify_password(r.password, u["password_hash"]):
            raise HTTPException(401, "sai tên đăng nhập hoặc mật khẩu")
        if not u["active"]:
            raise HTTPException(403, "tài khoản đã bị khóa")
        conn.execute("UPDATE app_users SET last_login_at = now() WHERE id = %s", (u["id"],))
        token, exp = _new_session(conn, u["id"])
        audit(conn, "user", username, "LOGIN", f"user:{username}", {})
    return {"token": token, "expires_at": exp, "user": _public(u)}


@router.post("/logout")
def logout(authorization: str = Header(default="")):
    token = bearer_token(authorization)
    if token.startswith(SESSION_PREFIX):
        with tx() as conn:
            conn.execute("DELETE FROM app_sessions WHERE token_hash = %s", (_token_hash(token),))
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"username": user.name, "display_name": user.display_name or user.name, "role": user.role}


class PasswordReq(BaseModel):
    old_password: str
    new_password: str


@router.post("/password")
def change_password(r: PasswordReq, user: User = Depends(current_user), authorization: str = Header(default="")):
    _check_password_rule(r.new_password)
    with tx() as conn:
        u = conn.execute("SELECT password_hash FROM app_users WHERE id = %s", (user.user_id,)).fetchone()
        if not verify_password(r.old_password, u["password_hash"]):
            raise HTTPException(400, "mật khẩu hiện tại không đúng")
        conn.execute("UPDATE app_users SET password_hash = %s WHERE id = %s", (hash_password(r.new_password), user.user_id))
        # đăng xuất các phiên khác
        conn.execute("DELETE FROM app_sessions WHERE user_id = %s AND token_hash <> %s",
                     (user.user_id, _token_hash(bearer_token(authorization))))
        audit(conn, "user", user.name, "CHANGE_PASSWORD", f"user:{user.name}", {})
    return {"ok": True}


# ------------------------------------------------------------------ quản lý người dùng (admin)
@users_router.get("")
def list_users(user: User = Depends(current_user)):
    require_role(user, "admin")
    with tx() as conn:
        rows = conn.execute("SELECT * FROM app_users ORDER BY id").fetchall()
    return {"items": [_public(r) for r in rows]}


class UserUpdate(BaseModel):
    role: str | None = None
    active: bool | None = None
    display_name: str | None = None
    password: str | None = None  # admin đặt lại mật khẩu


class UserCreate(BaseModel):
    username: str
    display_name: str | None = None
    password: str
    role: str = "analyst"


@users_router.post("")
def create_user(r: UserCreate, user: User = Depends(current_user)):
    require_role(user, "admin")
    with tx() as conn:
        u = provision_user(conn, r.username, r.password, r.role, r.display_name, f"user:{user.name}")
    return _public(u)


@users_router.patch("/{user_id}")
def update_user(user_id: int, r: UserUpdate, user: User = Depends(current_user)):
    require_role(user, "admin")
    with tx() as conn:
        u = conn.execute("SELECT * FROM app_users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
        if not u:
            raise HTTPException(404, "không có người dùng")
        changes: dict = {}
        if r.role is not None:
            if r.role not in ROLES:
                raise HTTPException(400, "vai trò không hợp lệ")
            changes["role"] = r.role
        if r.active is not None:
            changes["active"] = r.active
        if r.display_name is not None and r.display_name.strip():
            changes["display_name"] = r.display_name.strip()[:MAX_DISPLAY]
        if r.password:
            _check_password_rule(r.password)
            changes["password_hash"] = hash_password(r.password)
        demote = changes.get("role", "admin") != "admin" or changes.get("active") is False
        if u["role"] == "admin" and demote:
            n = conn.execute("SELECT count(*) AS n FROM app_users WHERE role = 'admin' AND active AND id <> %s",
                             (user_id,)).fetchone()["n"]
            if n == 0:
                raise HTTPException(400, "phải còn ít nhất một admin đang hoạt động")
        if changes:
            sets = ", ".join(f"{k} = %s" for k in changes)
            conn.execute(f"UPDATE app_users SET {sets} WHERE id = %s", (*changes.values(), user_id))
            if changes.get("active") is False or "password_hash" in changes:  # vai trò đọc lại mỗi request
                conn.execute("DELETE FROM app_sessions WHERE user_id = %s", (user_id,))
            audit(conn, "user", u["username"], "UPDATE", f"user:{user.name}",
                  {k: v for k, v in changes.items() if k != "password_hash"} | ({"password_reset": True} if r.password else {}))
        u = conn.execute("SELECT * FROM app_users WHERE id = %s", (user_id,)).fetchone()
    return _public(u)

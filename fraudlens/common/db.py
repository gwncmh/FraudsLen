"""Kết nối PostgreSQL (nguồn sự thật duy nhất) và ghi nhật ký audit có chuỗi băm."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from fraudlens.common.config import settings

_pool: ConnectionPool | None = None
AUDIT_LOCK_KEY = 4242  # khóa advisory để nối chuỗi băm theo đúng thứ tự


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(settings.database_url, min_size=1, max_size=10,
                               kwargs={"row_factory": dict_row, "autocommit": False}, open=True)
    return _pool


def close_pool() -> None:
    """Đóng pool khi lệnh dòng lệnh kết thúc (tránh cảnh báo 'couldn't stop thread')."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx():
    """Một transaction: commit khi thoát bình thường, rollback khi có lỗi."""
    with pool().connection() as conn:
        with conn.transaction():
            yield conn


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def audit(conn, entity: str, entity_id: str, action: str, actor: str, payload: dict) -> None:
    """Thêm một dòng audit. Mỗi dòng mang băm của dòng trước nên sửa lén sẽ bị phát hiện.
    Gọi bên trong transaction đang mở để audit và dữ liệu nghiệp vụ ghi cùng lúc."""
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (AUDIT_LOCK_KEY,))
    prev = conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = prev["hash"] if prev else "GENESIS"
    body = {"entity": entity, "entity_id": entity_id, "action": action, "actor": actor, "payload": payload}
    h = hashlib.sha256((prev_hash + _canonical(body)).encode()).hexdigest()
    conn.execute(
        "INSERT INTO audit_log (entity, entity_id, action, actor, payload, prev_hash, hash) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (entity, entity_id, action, actor, Jsonb(payload), prev_hash, h),
    )


def verify_audit_chain(conn) -> tuple[bool, int | None]:
    """Kiểm tra lại toàn bộ chuỗi băm. Trả về (hợp lệ?, seq đầu tiên bị sai)."""
    prev = "GENESIS"
    for r in conn.execute("SELECT seq, entity, entity_id, action, actor, payload, prev_hash, hash FROM audit_log ORDER BY seq"):
        body = {"entity": r["entity"], "entity_id": r["entity_id"], "action": r["action"], "actor": r["actor"], "payload": r["payload"]}
        if r["prev_hash"] != prev or hashlib.sha256((prev + _canonical(body)).encode()).hexdigest() != r["hash"]:
            return False, r["seq"]
        prev = r["hash"]
    return True, None


J = Jsonb  # tiện dùng ở nơi khác

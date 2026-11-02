"""Lịch sử 30 ngày của từng thẻ trong Redis – chỉ là BẢN SAO, dựng lại được từ PostgreSQL.

Mỗi thẻ là một sorted set: key = hist:<card_hash>, score = event_ts, member = JSON của bản ghi.
Cửa sổ tính theo thời gian giao dịch, không theo đồng hồ máy.
"""
from __future__ import annotations

import json

import redis

from fraudlens.common.config import settings
from fraudlens.common.features import HistRecord

WARM_FLAG = "fl:warm"


class HistoryStore:
    def __init__(self, url: str | None = None):
        self.r = redis.Redis.from_url(url or settings.redis_url, socket_timeout=0.1, socket_connect_timeout=0.5)
        self.window = settings.history_window_days * 86400

    @staticmethod
    def _key(card_hash: str) -> str:
        return f"hist:{card_hash}"

    def get(self, card_hash: str, event_ts: int) -> list[HistRecord]:
        # lấy cả bản ghi "tương lai" (nếu dữ liệu đến lệch) – compute() sẽ tự lọc
        members = self.r.zrangebyscore(self._key(card_hash), event_ts - self.window, "+inf")
        return [tuple(json.loads(m)) for m in members]

    def add(self, rec: HistRecord, card_hash: str) -> None:
        key = self._key(card_hash)
        p = self.r.pipeline(transaction=False)
        p.zadd(key, {json.dumps(list(rec), ensure_ascii=False): rec[0]})
        p.zremrangebyscore(key, "-inf", rec[0] - self.window - 86400)  # dọn bản ghi quá cũ, chừa 1 ngày đệm
        p.execute()

    def lock(self, card_hash: str):
        """Khóa theo thẻ: đọc lịch sử -> chấm -> ghi PostgreSQL -> cập nhật lịch sử chạy TUẦN TỰ cho cùng một thẻ.
        Không có khóa, hai giao dịch cùng thẻ xử lý song song sẽ không thấy nhau (n_txn_1h/24h bị đếm thiếu,
        lệch với offline). TTL để khóa tự nhả nếu tiến trình chết giữa chừng."""
        return self.r.lock(f"lock:{card_hash}", timeout=settings.card_lock_ttl_s,
                           blocking_timeout=settings.card_lock_wait_s)

    def is_warm(self) -> bool:
        return bool(self.r.exists(WARM_FLAG))

    def rebuild_from_db(self, conn) -> int:
        """Nạp lại lịch sử 30 ngày gần nhất (tính theo thời gian giao dịch mới nhất) từ PostgreSQL."""
        row = conn.execute("SELECT max(event_ts) AS t FROM transactions WHERE status IN ('VALID','SEED')").fetchone()
        if not row or row["t"] is None:
            self.r.set(WARM_FLAG, "empty")
            return 0
        since = int(row["t"]) - self.window
        n = 0
        p = self.r.pipeline(transaction=False)
        cur = conn.execute(
            "SELECT card_hash, event_ts, trans_num, amt, category, merchant FROM transactions "
            "WHERE status IN ('VALID','SEED') AND event_ts >= %s ORDER BY event_ts", (since,))
        for r in cur:
            rec = (int(r["event_ts"]), r["trans_num"], float(r["amt"]), r["category"], r["merchant"])
            p.zadd(self._key(r["card_hash"]), {json.dumps(list(rec), ensure_ascii=False): rec[0]})
            n += 1
            if n % 5000 == 0:
                p.execute()
        p.set(WARM_FLAG, str(n))
        p.execute()
        return n

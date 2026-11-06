"""Nạp lịch sử 30 ngày trước thời điểm bắt đầu phát lại, để feature của những giao dịch đầu tiên không bị sai.

  python -m fraudlens.tools.seed_history --csv data/fraudTrain.csv --days 30
  (hoặc bấm "Nạp lịch sử" ở trang Hệ thống trên giao diện web)

Các bản ghi được lưu với status = 'SEED' (chỉ dùng làm lịch sử, không chấm điểm, không tạo hồ sơ),
sau đó Redis được dựng lại từ PostgreSQL. CSV được đọc theo từng khối nên chạy được trong container ít RAM.
"""
from __future__ import annotations

import argparse

import pandas as pd

from fraudlens.api.history import WARM_FLAG, HistoryStore
from fraudlens.common import pii
from fraudlens.common.config import settings
from fraudlens.common.db import close_pool, tx
from fraudlens.ml.data import USECOLS

CHUNK = 200_000
INSERT_SQL = (
    "INSERT INTO transactions (trans_num, event_ts, card_hash, card_last4, merchant, category, amt, city_pop, "
    "state, age, distance_km, status) VALUES (%(trans_num)s,%(event_ts)s,%(card_hash)s,%(card_last4)s,"
    "%(merchant)s,%(category)s,%(amt)s,%(city_pop)s,%(state)s,%(age)s,%(distance_km)s,'SEED') "
    "ON CONFLICT (trans_num) DO NOTHING")


def _epoch(s: pd.Series) -> pd.Series:
    # đổi sang giây Unix theo cách không phụ thuộc độ phân giải thời gian của pandas (pandas 3 dùng micro giây)
    dt = pd.to_datetime(s, format="%Y-%m-%d %H:%M:%S", utc=True)
    return (dt - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)


def seed(csv: str, days: int = 30, progress=None) -> dict:
    """Nạp `days` ngày cuối của csv làm lịch sử. progress(dict) được gọi sau mỗi khối."""
    key = settings.require_hmac()
    say = progress or (lambda d: None)
    say({"phase": "scan", "message": "đang tìm thời điểm cuối của file"})
    t_max = None
    for ch in pd.read_csv(csv, usecols=["trans_date_trans_time"], chunksize=CHUNK):
        m = int(_epoch(ch["trans_date_trans_time"]).max())
        t_max = m if t_max is None else max(t_max, m)
    if t_max is None:
        raise RuntimeError(f"{csv} rỗng")
    cutoff = t_max - days * 86400
    n_rows, n_read = 0, 0
    for ch in pd.read_csv(csv, usecols=USECOLS, dtype={"cc_num": str, "trans_num": str}, chunksize=CHUNK):
        n_read += len(ch)
        ch = ch[_epoch(ch["trans_date_trans_time"]) >= cutoff]
        if len(ch):
            rows = [pii.minimize(r, key) for r in ch.to_dict("records")]
            with tx() as conn:
                with conn.cursor() as cur:
                    cur.executemany(INSERT_SQL, rows)
            n_rows += len(rows)
        say({"phase": "insert", "rows_read": n_read, "rows_seeded": n_rows,
             "message": f"đã đọc {n_read:,} dòng, nạp {n_rows:,} giao dịch"})
    say({"phase": "redis", "rows_seeded": n_rows, "message": "đang dựng lại lịch sử thẻ trong Redis"})
    h = HistoryStore()
    h.r.delete(WARM_FLAG)
    with tx() as conn:
        n_redis = h.rebuild_from_db(conn)
    return {"csv": str(csv), "days": days, "rows_seeded": n_rows, "redis_records": n_redis}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/fraudTrain.csv")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    rep = seed(a.csv, a.days, progress=lambda d: print(d["message"]))
    print(f"nạp {rep['rows_seeded']:,} giao dịch của {a.days} ngày cuối trong {a.csv}")
    print(f"đã dựng Redis: {rep['redis_records']:,} bản ghi lịch sử")


if __name__ == "__main__":
    try:
        main()
    finally:
        close_pool()

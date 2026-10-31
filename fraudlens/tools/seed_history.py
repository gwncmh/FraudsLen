"""Nạp lịch sử 30 ngày trước thời điểm bắt đầu phát lại, để feature của những giao dịch đầu tiên không bị sai.

  python -m fraudlens.tools.seed_history --csv data/fraudTrain.csv --days 30

Các bản ghi được lưu với status = 'SEED' (chỉ dùng làm lịch sử, không chấm điểm, không tạo hồ sơ),
sau đó Redis được dựng lại từ PostgreSQL.
"""
from __future__ import annotations

import argparse

import pandas as pd

from fraudlens.api.history import WARM_FLAG, HistoryStore
from fraudlens.common import pii
from fraudlens.common.config import settings
from fraudlens.common.db import close_pool, tx
from fraudlens.ml.data import USECOLS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/fraudTrain.csv")
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    key = settings.require_hmac()
    df = pd.read_csv(a.csv, usecols=USECOLS, dtype={"cc_num": str, "trans_num": str})
    # đổi sang giây Unix theo cách không phụ thuộc độ phân giải thời gian của pandas (pandas 3 dùng micro giây)
    dt = pd.to_datetime(df["trans_date_trans_time"], format="%Y-%m-%d %H:%M:%S", utc=True)
    ts = (dt - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)
    df = df[ts >= ts.max() - a.days * 86400]
    print(f"nạp {len(df):,} giao dịch của {a.days} ngày cuối trong {a.csv}")
    rows = [pii.minimize(r, key) for r in df.to_dict("records")]
    with tx() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO transactions (trans_num, event_ts, card_hash, card_last4, merchant, category, amt, city_pop, "
                "state, age, distance_km, status) VALUES (%(trans_num)s,%(event_ts)s,%(card_hash)s,%(card_last4)s,"
                "%(merchant)s,%(category)s,%(amt)s,%(city_pop)s,%(state)s,%(age)s,%(distance_km)s,'SEED') "
                "ON CONFLICT (trans_num) DO NOTHING", rows)
    h = HistoryStore()
    h.r.delete(WARM_FLAG)
    with tx() as conn:
        n = h.rebuild_from_db(conn)
    print(f"đã dựng Redis: {n:,} bản ghi lịch sử")


if __name__ == "__main__":
    try:
        main()
    finally:
        close_pool()

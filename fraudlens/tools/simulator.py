"""Simulator: phát lại giao dịch Sparkov vào API theo thứ tự thời gian, tiêm lỗi, gửi nhãn trễ, đo độ trễ.

  python -m fraudlens.tools.simulator --csv data/fraudTest.csv --limit 2000 --tps 20
  python -m fraudlens.tools.simulator --limit 20000 --tps 200 --threads 8      # kiểm thử tải (Mức 2)

Nhãn is_fraud KHÔNG gửi kèm giao dịch; chỉ gửi qua /v1/labels sau --label-delay-days (tính theo thời gian giao dịch).
Giao dịch của cùng một thẻ luôn đi qua cùng một luồng gửi (chia theo thẻ) nên đến API đúng thứ tự thời gian,
giống nguồn thật phát theo từng tài khoản; API còn khóa theo thẻ để hai request cùng thẻ không chạy song song.
"""
from __future__ import annotations

import argparse
import heapq
import random
import threading
import time
import zlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx
import numpy as np
import pandas as pd

from fraudlens.common.config import settings
from fraudlens.common.pii import parse_event_ts

RAW_COLS = ["trans_date_trans_time", "cc_num", "merchant", "category", "amt", "first", "last", "gender", "street", "city",
            "state", "zip", "lat", "long", "city_pop", "job", "dob", "trans_num", "merch_lat", "merch_long", "is_fraud"]
FAULTS = ["missing_field", "bad_amt", "negative_amt", "bad_time", "bool_amt", "bad_lat", "duplicate"]


def inject(row: dict, rng: random.Random) -> tuple[dict, str]:
    kind = rng.choice(FAULTS)
    r = dict(row)
    if kind == "missing_field":
        r.pop(rng.choice(["amt", "cc_num", "category", "dob"]), None)
    elif kind == "bad_amt":
        r["amt"] = "abc"
    elif kind == "negative_amt":
        r["amt"] = -5
    elif kind == "bad_time":
        r["trans_date_trans_time"] = "31/02/2020 25:61"
    elif kind == "bool_amt":
        r["amt"] = True
    elif kind == "bad_lat":
        r["lat"] = 123.4
    return r, kind


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.lat: list[float] = []
        self.routes = Counter()
        self.errors = Counter()

    def add(self, ms: float, route: str | None, err: str | None):
        with self.lock:
            self.lat.append(ms)
            if route:
                self.routes[route] += 1
            if err:
                self.errors[err] += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/fraudTest.csv")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--key", default=settings.source_api_key)
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--offset", type=int, default=0, help="bỏ qua N giao dịch đầu (để chạy tiếp lần sau)")
    ap.add_argument("--tps", type=float, default=20, help="số giao dịch/giây gửi đi")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--fault-rate", type=float, default=0.02)
    ap.add_argument("--label-delay-days", type=float, default=7)
    ap.add_argument("--no-labels", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    df = pd.read_csv(a.csv, usecols=lambda c: c in RAW_COLS, dtype={"cc_num": str, "trans_num": str, "zip": str},
                     skiprows=range(1, a.offset + 1), nrows=a.limit)
    df = df.sort_values(["trans_date_trans_time", "trans_num"], kind="mergesort")
    rng = random.Random(a.seed)
    stats = Stats()
    labels_heap: list = []
    sent_rows: list[dict] = []
    client = httpx.Client(timeout=10.0, limits=httpx.Limits(max_connections=a.threads * 2))
    headers = {"X-API-Key": a.key}

    def send(payload: dict, fault: str | None):
        t0 = time.perf_counter()
        try:
            r = client.post(f"{a.api}/v1/transactions", json=payload, headers=headers)
            ms = (time.perf_counter() - t0) * 1000
            if r.status_code == 200:
                stats.add(ms, r.json().get("route"), f"fault:{fault}" if fault else None)
            else:
                stats.add(ms, None, f"http_{r.status_code}")
        except Exception as e:  # noqa: BLE001
            stats.add((time.perf_counter() - t0) * 1000, None, type(e).__name__)

    def send_label(tn: str, y: int, ts: int):
        try:
            client.post(f"{a.api}/v1/labels", json={"trans_num": tn, "is_fraud": bool(y), "label_ts": ts}, headers=headers)
        except Exception:  # noqa: BLE001
            pass

    delay = int(a.label_delay_days * 86400)
    interval = 1.0 / a.tps if a.tps > 0 else 0
    t_start = time.perf_counter()
    shards = [ThreadPoolExecutor(1) for _ in range(max(1, a.threads))]
    with ThreadPoolExecutor(2) as pool:
        for i, row in enumerate(df.to_dict("records")):
            y = int(row.pop("is_fraud"))
            ev = parse_event_ts(row["trans_date_trans_time"])
            # nhãn của các giao dịch đã "đủ trễ" theo thời gian giao dịch hiện tại
            while labels_heap and labels_heap[0][0] <= ev:
                due, tn, yy = heapq.heappop(labels_heap)
                if not a.no_labels:
                    pool.submit(send_label, tn, yy, due)
            fault = None
            payload = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in row.items()}
            if rng.random() < a.fault_rate:
                if rng.random() < 0.15 and sent_rows:
                    payload, fault = rng.choice(sent_rows), "duplicate"
                else:
                    payload, fault = inject(payload, rng)
            else:
                heapq.heappush(labels_heap, (ev + delay, row["trans_num"], y))
                if len(sent_rows) < 1000:
                    sent_rows.append(payload)
            cc = str(payload.get("cc_num") or i)
            shards[zlib.crc32(cc.encode()) % len(shards)].submit(send, payload, fault)
            target = t_start + (i + 1) * interval
            sleep = target - time.perf_counter()
            if sleep > 0:
                time.sleep(sleep)
        for sh in shards:
            sh.shutdown(wait=True)
    dur = time.perf_counter() - t_start
    lat = np.array(stats.lat) if stats.lat else np.array([0.0])
    print(f"\nĐã gửi {len(df):,} giao dịch trong {dur:.1f} giây ({len(df) / dur:.1f} giao dịch/giây thực tế)")
    print(f"Độ trễ phía client (ms): p50={np.percentile(lat, 50):.1f}  p95={np.percentile(lat, 95):.1f}  "
          f"p99={np.percentile(lat, 99):.1f}  max={lat.max():.1f}")
    print("Phân luồng:", dict(stats.routes))
    print("Lỗi / lỗi tiêm vào:", dict(stats.errors))
    print(f"Lần sau chạy tiếp với --offset {a.offset + len(df)}")


if __name__ == "__main__":
    main()

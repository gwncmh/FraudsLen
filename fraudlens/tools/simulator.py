"""Simulator: phát lại giao dịch Sparkov vào API theo thứ tự thời gian, tiêm lỗi, gửi nhãn trễ, đo độ trễ.

  python -m fraudlens.tools.simulator --csv data/fraudTest.csv --limit 2000 --tps 20
  python -m fraudlens.tools.simulator --limit 20000 --tps 200 --threads 8      # kiểm thử tải (Mức 2)

Kết quả ghi vào --report (mặc định replay_report.json): độ trễ phía client và phía server (p50/p95/p99), phân luồng,
lỗi, bảng lỗi tiêm × luồng. Tự kiểm T-FAIL-01: không payload lỗi nào được thành LOW (trừ "duplicate" – gửi lại
giao dịch hợp lệ đã có, API trả kết quả cũ). Vi phạm thì thoát với mã 1.

Nhãn is_fraud KHÔNG gửi kèm giao dịch; chỉ gửi qua /v1/labels sau --label-delay-days (tính theo thời gian giao dịch).
Một lần chạy thường ngắn hơn độ trễ của nhãn, nên nhãn chưa tới hạn khi lần chạy kết thúc do LẦN CHẠY SAU gửi: nó tính
lại phần còn nợ từ chính file CSV (earlier_labels), không cần lưu trạng thái. Hết file thì gửi nốt. Mọi giao dịch gốc
đều có nhãn, kể cả dòng bị tiêm lỗi; các số đo trên nhãn chỉ tính giao dịch VALID. --label-backfill gửi lại nhãn của
MỌI dòng trước --offset (dùng một lần cho dữ liệu đã phát lại bằng bản cũ, bản đó làm rơi nhãn).
Giao dịch của cùng một thẻ luôn đi qua cùng một luồng gửi (chia theo thẻ) nên đến API đúng thứ tự thời gian,
giống nguồn thật phát theo từng tài khoản; API còn khóa theo thẻ để hai request cùng thẻ không chạy song song.
"""
from __future__ import annotations

import argparse
import heapq
import json
import random
import threading
import time
import zlib
from collections import Counter
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import httpx
import numpy as np
import pandas as pd

from fraudlens.common.config import settings
from fraudlens.common.pii import parse_event_ts

RAW_COLS = ["trans_date_trans_time", "cc_num", "merchant", "category", "amt", "first", "last", "gender", "street", "city",
            "state", "zip", "lat", "long", "city_pop", "job", "dob", "trans_num", "merch_lat", "merch_long", "is_fraud"]
FAULTS = ["missing_field", "bad_amt", "negative_amt", "bad_time", "bool_amt", "bad_lat", "duplicate"]
# lỗi không được phép thành LOW; "duplicate" là giao dịch hợp lệ gửi lại nên được trả đúng luồng cũ
FAIL_SAFE_FAULTS = set(FAULTS) - {"duplicate"}
LABEL_THREADS = 4


def epoch_s(s: pd.Series) -> np.ndarray:
    """Cột thời gian Sparkov -> epoch giây (UTC), không phụ thuộc độ phân giải thời gian của pandas."""
    dt = pd.to_datetime(s, format="%Y-%m-%d %H:%M:%S", utc=True)
    return ((dt - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)).to_numpy(np.int64)


def earlier_labels(csv: str, offset: int, delay: int, backfill: bool = False) -> list[tuple[int, str, int]]:
    """Nhãn của các dòng TRƯỚC offset mà lần chạy này phải gửi: (hạn gửi, trans_num, is_fraud), xếp theo hạn gửi.

    Các lần chạy trước đã gửi mọi nhãn tới hạn trước giao dịch cuối cùng của chúng, nên phần còn nợ là các dòng có
    hạn gửi (thời gian giao dịch + delay) sau thời điểm của dòng cuối trước offset. backfill: lấy mọi dòng trước offset
    (API ghi nhãn theo kiểu upsert nên gửi lại không tạo bản trùng)."""
    if offset <= 0:
        return []
    prev = pd.read_csv(csv, usecols=["trans_date_trans_time", "trans_num", "is_fraud"], dtype={"trans_num": str}, nrows=offset)
    if prev.empty:
        return []
    ts = epoch_s(prev["trans_date_trans_time"])
    keep = np.ones(len(prev), dtype=bool) if backfill else ts + delay > ts.max()
    return sorted(zip((ts[keep] + delay).tolist(), prev["trans_num"][keep].tolist(), prev["is_fraud"][keep].astype(int).tolist()))


def inject(row: dict, rng: random.Random) -> tuple[dict, str]:
    # "duplicate" do replay() tự tạo (gửi lại một giao dịch đã gửi); ở đây chỉ chọn lỗi làm hỏng payload,
    # nếu không giao dịch còn nguyên vẹn sẽ bị đếm là lỗi tiêm và không bao giờ được gửi nhãn
    kind = rng.choice([f for f in FAULTS if f != "duplicate"])
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
        self.server_lat: list[float] = []
        self.routes = Counter()
        self.errors = Counter()
        self.fault_routes = Counter()  # (lỗi tiêm, luồng) -> số giao dịch
        self.labels_sent = 0
        self.labels_failed = 0

    def add(self, ms: float, route: str | None, err: str | None, fault: str | None = None,
            server_ms: float | None = None):
        with self.lock:
            self.lat.append(ms)
            if server_ms is not None:
                self.server_lat.append(server_ms)
            if route:
                self.routes[route] += 1
            if err:
                self.errors[err] += 1
            if fault:
                self.fault_routes[(fault, route or "NO_RESPONSE")] += 1


def percentiles(ms: list[float]) -> dict:
    if not ms:
        return {"n": 0}
    v = np.array(ms)
    return {"n": len(ms), "p50": float(np.percentile(v, 50)), "p95": float(np.percentile(v, 95)),
            "p99": float(np.percentile(v, 99)), "max": float(v.max())}


def fail_safe_violations(fault_routes: Counter) -> dict[str, int]:
    """T-FAIL-01: số payload lỗi bị thành LOW, theo loại lỗi. Rỗng = đạt."""
    return {f: n for (f, rt), n in fault_routes.items() if rt == "LOW" and f in FAIL_SAFE_FAULTS}


def build_report(stats: Stats, n_sent: int, dur: float, args: dict) -> dict:
    faults: dict[str, dict[str, int]] = {}
    for (f, rt), n in sorted(stats.fault_routes.items()):
        faults.setdefault(f, {})[rt] = n
    viol = fail_safe_violations(stats.fault_routes)
    return {
        "run_at": datetime.now(timezone.utc).isoformat(), "args": args,
        "sent": n_sent, "duration_s": dur, "throughput_tps": n_sent / dur if dur else None,
        "latency_client_ms": percentiles(stats.lat),
        "latency_server_ms": percentiles(stats.server_lat),
        "routes": dict(stats.routes), "errors": dict(stats.errors), "faults_by_route": faults,
        "t_fail_01": {"passed": not viol, "faults_injected": sum(n for (f, _), n in stats.fault_routes.items()
                                                               if f in FAIL_SAFE_FAULTS),
                      "faults_to_low": viol},
    }


DEFAULTS = {"csv": "data/fraudTest.csv", "api": "http://localhost:8000", "key": None, "limit": 2000, "offset": 0,
            "tps": 20.0, "threads": 4, "fault_rate": 0.02, "label_delay_days": 7.0, "no_labels": False,
            "label_backfill": False, "seed": 7}


def replay(opts: dict, progress=None, should_stop=None, progress_every_s: float = 1.0) -> dict:
    """Phát lại giao dịch; dùng chung cho dòng lệnh và nút "Simulator" trên web.

    progress(snapshot: dict) được gọi khoảng mỗi progress_every_s giây; should_stop() trả True để dừng sớm.
    Trả về báo cáo (build_report) kèm "stopped_early", "next_offset" và "labels" (đã gửi / lỗi / còn nợ lần sau)."""
    a = {**DEFAULTS, **{k: v for k, v in opts.items() if v is not None}}
    key = a["key"] or settings.source_api_key
    df = pd.read_csv(a["csv"], usecols=lambda c: c in RAW_COLS, dtype={"cc_num": str, "trans_num": str, "zip": str},
                     skiprows=range(1, int(a["offset"]) + 1), nrows=int(a["limit"]))
    df = df.sort_values(["trans_date_trans_time", "trans_num"], kind="mergesort")
    total = len(df)
    rng = random.Random(a["seed"])
    stats = Stats()
    delay = int(a["label_delay_days"] * 86400)
    # hàng đợi nhãn theo hạn gửi; mở đầu bằng phần các lần chạy trước còn nợ
    labels_heap = [] if a["no_labels"] else earlier_labels(a["csv"], int(a["offset"]), delay, bool(a["label_backfill"]))
    labels_carried = len(labels_heap)
    sent_rows: list[dict] = []
    threads = max(1, int(a["threads"]))
    client = httpx.Client(timeout=10.0, limits=httpx.Limits(max_connections=threads + LABEL_THREADS + 2))
    headers = {"X-API-Key": key}
    api = a["api"].rstrip("/")
    # Giới hạn số giao dịch đang chờ gửi. Không có giới hạn thì khi --tps 0 (hoặc tps vượt sức API) cả file nằm sẵn trong
    # hàng đợi: các luồng gửi lệch nhau nhiều ngày thời gian giao dịch (API đánh STALE oan) và nút Dừng không còn tác dụng.
    backlog = threading.BoundedSemaphore(threads * 4)

    def send(payload: dict, fault: str | None):
        t0 = time.perf_counter()
        try:
            r = client.post(f"{api}/v1/transactions", json=payload, headers=headers)
            ms = (time.perf_counter() - t0) * 1000
            if r.status_code == 200:
                body = r.json()
                stats.add(ms, body.get("route"), f"fault:{fault}" if fault else None, fault, body.get("latency_ms"))
            else:
                stats.add(ms, None, f"http_{r.status_code}", fault)
        except Exception as e:  # noqa: BLE001
            stats.add((time.perf_counter() - t0) * 1000, None, type(e).__name__, fault)
        finally:
            backlog.release()

    def send_label(tn: str, y: int, ts: int):
        try:
            r = client.post(f"{api}/v1/labels", json={"trans_num": tn, "is_fraud": bool(y), "label_ts": ts}, headers=headers)
            ok = r.status_code == 200
        except Exception:  # noqa: BLE001
            ok = False
        with stats.lock:
            if ok:
                stats.labels_sent += 1
            else:
                stats.labels_failed += 1

    def snapshot(i: int) -> dict:
        with stats.lock:
            lat = list(stats.lat[-2000:])
            return {"queued": i, "total": total, "done": len(stats.lat), "routes": dict(stats.routes),
                    "errors": dict(stats.errors), "labels_sent": stats.labels_sent, "labels_failed": stats.labels_failed,
                    "elapsed_s": time.perf_counter() - t_start, "latency_recent_ms": percentiles(lat)}

    interval = 1.0 / a["tps"] if a["tps"] and a["tps"] > 0 else 0
    t_start = time.perf_counter()
    last_progress = t_start
    stopped = False
    n_sent = 0
    shards = [ThreadPoolExecutor(1) for _ in range(threads)]
    label_pool = ThreadPoolExecutor(LABEL_THREADS)

    def send_due_labels(until: float) -> None:
        while labels_heap and labels_heap[0][0] <= until:
            due, tn, yy = heapq.heappop(labels_heap)
            label_pool.submit(send_label, tn, yy, due)

    for i, row in enumerate(df.to_dict("records")):
        if should_stop and i % 20 == 0 and should_stop():
            stopped = True
            break
        y = int(row.pop("is_fraud"))
        ev = parse_event_ts(row["trans_date_trans_time"])
        send_due_labels(ev)  # nhãn đã "đủ trễ" theo thời gian của giao dịch hiện tại
        if not a["no_labels"]:
            heapq.heappush(labels_heap, (ev + delay, row["trans_num"], y))
        fault = None
        payload = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in row.items()}
        if rng.random() < a["fault_rate"]:
            if rng.random() < 0.15 and sent_rows:
                payload, fault = rng.choice(sent_rows), "duplicate"
            else:
                payload, fault = inject(payload, rng)
        elif len(sent_rows) < 1000:
            sent_rows.append(payload)
        cc = str(payload.get("cc_num") or i)
        backlog.acquire()
        shards[zlib.crc32(cc.encode()) % len(shards)].submit(send, payload, fault)
        n_sent = i + 1
        now = time.perf_counter()
        if progress and now - last_progress >= progress_every_s:
            last_progress = now
            progress(snapshot(n_sent))
        target = t_start + (i + 1) * interval
        sleep = target - time.perf_counter()
        if sleep > 0:
            time.sleep(sleep)
    if not stopped and total < int(a["limit"]):
        send_due_labels(float("inf"))  # đã phát hết file: không còn lần chạy sau để gửi phần nhãn chưa tới hạn
    for sh in shards:
        sh.shutdown(wait=True)
    # Chờ gửi xong cả khi bấm Dừng: nhãn đã tới hạn thuộc về lần chạy này, lần sau coi như chúng đã được gửi.
    # Nhãn gửi lỗi ("failed") không tự gửi lại – sửa nguyên nhân rồi chạy một lần với --label-backfill.
    label_pool.shutdown(wait=True)
    client.close()
    dur = time.perf_counter() - t_start
    if progress:
        progress(snapshot(n_sent))
    rep = build_report(stats, n_sent, dur, a | {"key": "***"})
    rep["stopped_early"] = stopped
    # "pending": nhãn chưa tới hạn – lần chạy tiếp theo từ next_offset sẽ gửi
    rep["labels"] = {"sent": stats.labels_sent, "failed": stats.labels_failed, "carried_in": labels_carried,
                     "pending": 0 if a["no_labels"] else labels_carried + n_sent - stats.labels_sent - stats.labels_failed}
    rep["next_offset"] = int(a["offset"]) + n_sent
    return rep


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
    ap.add_argument("--label-backfill", action="store_true",
                    help="gửi lại nhãn của MỌI dòng trước --offset (một lần, cho dữ liệu phát lại bằng bản cũ)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--report", default="replay_report.json", help="file JSON ghi kết quả (để trống = không ghi)")
    a = ap.parse_args()

    rep = replay({k: v for k, v in vars(a).items() if k != "report"})
    n, dur = rep["sent"], rep["duration_s"]
    print(f"\nĐã gửi {n:,} giao dịch trong {dur:.1f} giây ({n / dur if dur else 0:.1f} giao dịch/giây thực tế)")
    for side in ("client", "server"):
        p = rep[f"latency_{side}_ms"]
        if p["n"]:
            print(f"Độ trễ phía {side} (ms): p50={p['p50']:.1f}  p95={p['p95']:.1f}  p99={p['p99']:.1f}  max={p['max']:.1f}")
    print("Phân luồng:", rep["routes"])
    print("Lỗi / lỗi tiêm vào:", rep["errors"])
    lb = rep["labels"]
    print(f"Nhãn trễ: đã gửi {lb['sent']:,}, lỗi {lb['failed']:,}, còn {lb['pending']:,} chưa tới hạn (lần chạy sau gửi)")
    t = rep["t_fail_01"]
    print(f"T-FAIL-01 (0 payload lỗi thành LOW): {'ĐẠT' if t['passed'] else 'KHÔNG ĐẠT ' + str(t['faults_to_low'])} "
          f"– {t['faults_injected']} payload lỗi đã tiêm")
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2, ensure_ascii=False)
        print(f"Đã ghi {a.report}")
    print(f"Lần sau chạy tiếp với --offset {rep['next_offset']}")
    if not t["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

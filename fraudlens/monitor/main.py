"""Giám sát định kỳ: độ lệch phân bố dữ liệu (PSI, không cần nhãn) và chất lượng model khi nhãn trễ về.

Chạy:  python -m fraudlens.monitor.main            (vòng lặp, mở /metrics ở cổng 9105 cho Prometheus)
       python -m fraudlens.monitor.main --once     (tính một lần rồi thoát)
Cửa sổ tính theo thời gian giao dịch: 1 ngày gần nhất (PSI) và 30 ngày gần nhất (nhãn).
"""
from __future__ import annotations

import argparse
import logging
import time

import numpy as np
from prometheus_client import Gauge, start_http_server

from fraudlens.common.config import settings
from fraudlens.common.db import tx
from fraudlens.common.registry import active_version, load_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fraudlens.monitor")
PSI_G = Gauge("fraudlens_psi", "PSI so với phân bố lúc huấn luyện", ["feature"])
Q_G = Gauge("fraudlens_label_metric", "Chất lượng model trên nhãn trễ", ["metric"])
QUEUE_G = Gauge("fraudlens_open_cases", "Số hồ sơ đang mở", ["route"])


def psi(ref_props: np.ndarray, cur_counts: np.ndarray) -> float:
    cur = cur_counts / max(1, cur_counts.sum())
    ref = np.clip(ref_props, 1e-4, None)
    cur = np.clip(cur, 1e-4, None)
    return float(np.sum((cur - ref) * np.log(cur / ref)))


def level(v: float) -> str:
    return "ALERT" if v >= settings.psi_alert else ("WARN" if v >= settings.psi_warn else "OK")


def compute_once() -> None:
    ref = load_json(active_version(), "reference.json")
    with tx() as conn:
        t_end = conn.execute("SELECT max(event_ts) AS t FROM transactions WHERE status = 'VALID'").fetchone()["t"]
        if t_end is None:
            log.info("chưa có giao dịch")
            return
        t0 = int(t_end) - 86400
        rows = conn.execute("SELECT s.features FROM transactions t JOIN scores s ON s.txn_id = t.id "
                            "WHERE t.status = 'VALID' AND t.event_ts > %s AND s.features IS NOT NULL", (t0,)).fetchall()
        out = []
        if len(rows) >= 50:  # quá ít giao dịch thì PSI không có ý nghĩa
            feats = [r["features"] for r in rows]
            for name, spec in ref["numeric"].items():
                v = np.array([float(f[name]) for f in feats if f.get(name) is not None])
                counts = np.bincount(np.searchsorted(np.array(spec["edges"]), v, side="right"), minlength=len(spec["props"]))
                out.append(("psi", name, psi(np.array(spec["props"]), counts)))
            ref_cat = ref["categorical"]["category"]
            cats = sorted(ref_cat)
            cur = np.array([sum(1 for f in feats if f.get("category") == c) for c in cats], dtype=float)
            out.append(("psi", "category", psi(np.array([ref_cat[c] for c in cats]), cur)))
        # chất lượng trên nhãn trễ (30 ngày gần nhất)
        lab = conn.execute(
            "SELECT l.is_fraud, d.route FROM labels_eval l JOIN transactions t ON t.trans_num = l.trans_num "
            "JOIN routing_decisions d ON d.txn_id = t.id WHERE t.event_ts > %s", (int(t_end) - 30 * 86400,)).fetchall()
        if lab:
            y = np.array([r["is_fraud"] for r in lab])
            flagged = np.array([r["route"] in ("MEDIUM", "HIGH") for r in lab])
            if y.sum():
                out.append(("label_recall_medium_high", None, float((y & flagged).sum() / y.sum())))
            if flagged.sum():
                out.append(("label_precision_medium_high", None, float((y & flagged).sum() / flagged.sum())))
            out.append(("labels_count", None, float(len(lab))))
        for r in conn.execute("SELECT route, count(*) AS n FROM cases WHERE status IN ('OPEN','INVESTIGATING','ESCALATED') GROUP BY route"):
            QUEUE_G.labels(r["route"]).set(r["n"])
        for metric, feature, value in out:
            lv = level(value) if metric == "psi" else None
            conn.execute("INSERT INTO monitor_metrics (window_start, window_end, metric, feature, value, level) "
                         "VALUES (%s,%s,%s,%s,%s,%s)", (t0, int(t_end), metric, feature, value, lv))
            if metric == "psi":
                PSI_G.labels(feature).set(value)
                if lv != "OK":
                    log.warning("PSI %s = %.3f (%s)", feature, value, lv)
            else:
                Q_G.labels(metric).set(value)
    log.info("đã tính %d số đo", len(out))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    if a.once:
        compute_once()
        return
    start_http_server(9105)
    while True:
        try:
            compute_once()
        except Exception:  # noqa: BLE001
            log.exception("giám sát lỗi")
        time.sleep(settings.monitor_interval_s)


if __name__ == "__main__":
    main()

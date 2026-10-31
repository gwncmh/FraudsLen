"""Làn nhanh: xử lý một giao dịch trong một request (bước 2–7 trên Hình 2)."""
from __future__ import annotations

import logging
import time

import psycopg
from fastapi import HTTPException
from prometheus_client import Counter, Histogram

from fraudlens.api.history import HistoryStore
from fraudlens.api.scorer_client import ScorerClient
from fraudlens.common import pii
from fraudlens.common.config import settings
from fraudlens.common.db import J, audit, tx
from fraudlens.common.features import FEATURE_SET_VERSION, compute, record_of
from fraudlens.common.routing import CASE_ROUTES, route
from fraudlens.common.validation import validate

log = logging.getLogger("fraudlens.api")
LAT = Histogram("api_transaction_latency_seconds", "Độ trễ làn nhanh (nhận -> ghi xong)",
                buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5))
ROUTES = Counter("api_routes_total", "Số giao dịch theo luồng", ["route"])
STATUS = Counter("api_score_status_total", "Trạng thái chấm điểm", ["status"])
HIST_ERR = Counter("api_history_errors_total", "Lỗi đọc/ghi Redis")


class FastLane:
    def __init__(self, routing_config: dict | None):
        self.rc = routing_config
        self.hist = HistoryStore()
        self.scorer = ScorerClient()
        self.key = settings.require_hmac()

    # ---- tra cứu kết quả cũ (gửi trùng mã giao dịch -> trả lại kết quả đã lưu, không tạo hồ sơ mới)
    @staticmethod
    def _existing(conn, trans_num: str) -> dict | None:
        r = conn.execute(
            "SELECT t.trans_num, t.status AS txn_status, s.status, s.score, s.model_version, d.route, d.config_version, c.id AS case_id "
            "FROM transactions t LEFT JOIN scores s ON s.txn_id = t.id LEFT JOIN routing_decisions d ON d.txn_id = t.id "
            "LEFT JOIN cases c ON c.txn_id = t.id WHERE t.trans_num = %s", (trans_num,)).fetchone()
        if r:
            r = dict(r)
            r["duplicate"] = True
        return r

    def process(self, raw: dict) -> dict:
        t0 = time.perf_counter()
        tn = raw.get("trans_num") if isinstance(raw, dict) else None
        tn = tn.strip() if isinstance(tn, str) and 0 < len(tn.strip()) <= 64 else None
        if tn:
            with tx() as conn:
                old = self._existing(conn, tn)
            if old:
                return old

        errors = validate(raw)
        m, feats, sc = None, None, {"status": "INVALID", "error": ",".join(errors)}
        if not errors:
            m = pii.minimize(raw, self.key)
            try:
                history = self.hist.get(m["card_hash"], m["event_ts"])
                feats = compute(m, history)
            except Exception as e:  # noqa: BLE001 – Redis lỗi: không đoán, đưa cho người xem
                HIST_ERR.inc()
                sc = {"status": "HISTORY_UNAVAILABLE", "error": type(e).__name__}
            if feats is not None:
                if self.rc is None:
                    sc = {"status": "SCORING_FAILED", "error": "NO_APPROVED_ROUTING_CONFIG"}
                else:
                    sc = self.scorer.score(feats, self.rc["model_version"])
        t_med = self.rc["t_medium"] if self.rc else 1.0
        t_high = self.rc["t_high"] if self.rc else 1.0
        rt = route(sc["status"], sc.get("score"), t_med, t_high)

        try:
            with tx() as conn:
                txn_id = self._write(conn, raw, tn, errors, m, feats, sc, rt, t_med, t_high)
                case_id = None
                if rt in CASE_ROUTES:
                    case_id = conn.execute(
                        "INSERT INTO cases (txn_id, route) VALUES (%s,%s) RETURNING id", (txn_id, rt)).fetchone()["id"]
                    conn.execute("INSERT INTO brief_jobs (case_id) VALUES (%s)", (case_id,))
                audit(conn, "transaction", str(tn or txn_id), "ROUTED", "system:api", {
                    "txn_id": txn_id, "score_status": sc["status"], "score": sc.get("score"), "route": rt,
                    "model_version": sc.get("model_version"), "config_version": self.rc and self.rc["config_version"],
                    "case_id": case_id, "errors": errors})
        except psycopg.errors.UniqueViolation:
            with tx() as conn:  # hai request trùng mã đến cùng lúc
                old = self._existing(conn, tn)
            if old:
                return old
            raise HTTPException(503, "xung đột ghi, hãy gửi lại")
        except Exception as e:  # noqa: BLE001 – chưa ghi được thì không trả kết quả
            log.exception("ghi PostgreSQL thất bại")
            raise HTTPException(503, f"chưa ghi được kết quả ({type(e).__name__}), hãy gửi lại") from e

        # bước 7: chỉ cập nhật bản sao lịch sử SAU khi đã ghi xong nguồn sự thật
        if m is not None:
            try:
                self.hist.add(record_of(m), m["card_hash"])
            except Exception:  # noqa: BLE001 – Redis dựng lại được từ PostgreSQL
                HIST_ERR.inc()
        ROUTES.labels(rt).inc()
        STATUS.labels(sc["status"]).inc()
        LAT.observe(time.perf_counter() - t0)
        return {"trans_num": tn, "status": sc["status"], "route": rt, "score": sc.get("score"),
                "model_version": sc.get("model_version"), "config_version": self.rc and self.rc["config_version"],
                "case_id": case_id, "duplicate": False, "latency_ms": round((time.perf_counter() - t0) * 1000, 2)}

    def _write(self, conn, raw, tn, errors, m, feats, sc, rt, t_med, t_high) -> int:
        if m is None:  # payload lỗi: không lưu dữ liệu thô (có thể chứa thông tin cá nhân), chỉ lưu mã lỗi
            txn_id = conn.execute(
                "INSERT INTO transactions (trans_num, status, validation_errors) VALUES (%s,'INVALID',%s) RETURNING id",
                (tn, J({"errors": errors, "fields": sorted(map(str, raw.keys())) if isinstance(raw, dict) else []}))).fetchone()["id"]
        else:
            txn_id = conn.execute(
                "INSERT INTO transactions (trans_num, event_ts, card_hash, card_last4, merchant, category, amt, city_pop, "
                "state, age, distance_km, status) VALUES (%(trans_num)s,%(event_ts)s,%(card_hash)s,%(card_last4)s,"
                "%(merchant)s,%(category)s,%(amt)s,%(city_pop)s,%(state)s,%(age)s,%(distance_km)s,'VALID') RETURNING id",
                m).fetchone()["id"]
        conn.execute(
            "INSERT INTO scores (txn_id, status, score_raw, score, model_version, feature_set_version, features, error) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (txn_id, sc["status"], sc.get("score_raw"), sc.get("score"), sc.get("model_version"),
             FEATURE_SET_VERSION if feats else None, J(feats) if feats else None, sc.get("error")))
        conn.execute(
            "INSERT INTO routing_decisions (txn_id, route, config_version, t_medium, t_high) VALUES (%s,%s,%s,%s,%s)",
            (txn_id, rt, self.rc["config_version"] if self.rc else None, t_med, t_high))
        return txn_id


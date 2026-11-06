"""Endpoint chỉ đọc cho giao diện web: dashboard tổng, danh sách toàn bộ giao dịch, chi tiết giao dịch, audit trail.

Nhãn is_fraud (labels_eval) chỉ dùng ở dạng tổng hợp (recall/precision); không trả về theo từng giao dịch để không
ảnh hưởng nhận định của chuyên viên.
"""
from __future__ import annotations

import math
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from fraudlens.api.auth import User, current_user, require_role
from fraudlens.common.db import tx

router = APIRouter(prefix="/v1")

# validation_errors lưu dạng {"errors": [...], "fields": [...]} (service._write); bản cũ / SEED có thể là mảng rỗng.
ERRS_SQL = ("CASE jsonb_typeof({c}) WHEN 'array' THEN {c} "
            "WHEN 'object' THEN COALESCE({c}->'errors', '[]'::jsonb) ELSE '[]'::jsonb END")


def errors_of(v) -> list:
    """Mảng mã lỗi kiểm tra dữ liệu, bất kể validation_errors lưu dạng object hay mảng."""
    if isinstance(v, dict):
        v = v.get("errors")
    return [str(x) for x in v] if isinstance(v, list) else []


def as_list(v) -> list:
    """JSON lẽ ra là mảng (factors, risk_signals) nhưng có thể là null / kiểu khác trong DB."""
    return v if isinstance(v, list) else []


SCORE_BINS = [0, 1e-4, 3e-4, 1e-3, 3e-3, 1e-2, 3e-2, 0.1, 0.3, 1.0000001]


# ------------------------------------------------------------------ dashboard tổng
@router.get("/dashboard")
def dashboard(window: Literal["live", "stream"] = "live", user: User = Depends(current_user)):
    """live: theo giờ máy (received_at), 60 phút gần nhất, mỗi phút một cột.
    stream: theo thời gian giao dịch (event_ts), 7 ngày cuối của luồng, mỗi giờ một cột."""
    with tx() as conn:
        if window == "live":
            series = conn.execute(
                "SELECT extract(epoch FROM date_trunc('minute', t.received_at))::bigint AS ts, d.route, count(*) AS n "
                "FROM transactions t JOIN routing_decisions d ON d.txn_id = t.id "
                "WHERE t.received_at > now() - interval '60 minutes' GROUP BY 1, 2 ORDER BY 1").fetchall()
        else:
            series = conn.execute(
                "WITH last AS (SELECT max(event_ts) AS m FROM transactions WHERE status = 'VALID') "
                "SELECT (t.event_ts / 3600) * 3600 AS ts, d.route, count(*) AS n FROM transactions t "
                "JOIN routing_decisions d ON d.txn_id = t.id, last "
                "WHERE t.event_ts > last.m - 7 * 86400 GROUP BY 1, 2 ORDER BY 1").fetchall()
        kpi = conn.execute(
            "SELECT count(*) AS total, count(*) FILTER (WHERE route IN ('HIGH','MEDIUM')) AS flagged, "
            "count(*) FILTER (WHERE route = 'HIGH') AS high, count(*) FILTER (WHERE route = 'UNSCORED') AS unscored "
            "FROM routing_decisions").fetchone()
        last_hour = conn.execute(
            "SELECT count(*) AS n, count(*) FILTER (WHERE d.route IN ('HIGH','MEDIUM')) AS flagged, "
            "avg(s.latency_ms) AS avg_latency_ms, percentile_cont(0.95) WITHIN GROUP (ORDER BY s.latency_ms) AS p95_latency_ms "
            "FROM transactions t JOIN routing_decisions d ON d.txn_id = t.id JOIN scores s ON s.txn_id = t.id "
            "WHERE t.received_at > now() - interval '60 minutes'").fetchone()
        open_cases = conn.execute(
            "SELECT route, count(*) AS n FROM cases WHERE status IN ('OPEN','INVESTIGATING','ESCALATED') GROUP BY route").fetchall()
        escalated = conn.execute("SELECT count(*) AS n FROM cases WHERE status = 'ESCALATED'").fetchone()["n"]
        closed = conn.execute(
            "SELECT status, count(*) AS n FROM cases WHERE status LIKE 'CLOSED%%' GROUP BY status").fetchall()
        scores = conn.execute(
            "SELECT score FROM scores WHERE status = 'SCORED' ORDER BY txn_id DESC LIMIT 5000").fetchall()
        labels = conn.execute(
            "SELECT d.route, l.is_fraud, count(*) AS n FROM labels_eval l JOIN transactions t ON t.trans_num = l.trans_num "
            "JOIN routing_decisions d ON d.txn_id = t.id GROUP BY d.route, l.is_fraud").fetchall()
        # "rule trigger": lỗi kiểm tra dữ liệu, lỗi chấm điểm, yếu tố đẩy rủi ro lên trong các brief gần đây
        validation = conn.execute(
            f"SELECT e AS code, count(*) AS n FROM (SELECT {ERRS_SQL.format(c='validation_errors')} AS errs FROM transactions "
            "WHERE status = 'INVALID' ORDER BY id DESC LIMIT 5000) t, jsonb_array_elements_text(t.errs) e "
            "GROUP BY e ORDER BY n DESC LIMIT 10"
        ).fetchall()
        score_err = conn.execute(
            "SELECT split_part(error, ':', 1) AS code, status, count(*) AS n FROM (SELECT error, status FROM scores "
            "WHERE status IN ('SCORING_FAILED','HISTORY_UNAVAILABLE') ORDER BY txn_id DESC LIMIT 5000) s "
            "GROUP BY 1, 2 ORDER BY n DESC LIMIT 10").fetchall()
        factors = conn.execute(
            # factors có thể là JSON null (worker không gọi được /explain): chỉ lấy brief có mảng yếu tố
            "SELECT f->>'feature' AS feature, count(*) AS n FROM (SELECT factors FROM briefs "
            "WHERE jsonb_typeof(factors) = 'array' ORDER BY id DESC LIMIT 500) b, jsonb_array_elements(b.factors) f "
            "WHERE jsonb_typeof(f) = 'object' AND f->>'direction' LIKE 'tăng%' GROUP BY 1 ORDER BY n DESC LIMIT 10").fetchall()
        categories = conn.execute(
            "SELECT t.category, count(*) AS n, sum(t.amt) AS amount FROM routing_decisions d JOIN transactions t ON t.id = d.txn_id "
            "WHERE d.route IN ('HIGH','MEDIUM') GROUP BY 1 ORDER BY n DESC LIMIT 8").fetchall()
        flagged = conn.execute(
            "SELECT c.id AS case_id, c.status AS case_status, c.route, t.id AS txn_id, t.trans_num, t.event_ts, t.received_at, "
            "t.amt, t.category, t.merchant, t.card_last4, t.state, t.validation_errors, s.score, s.status AS score_status, "
            "s.error AS score_error, s.features, d.t_medium, d.t_high, b.factors, b.brief->'risk_signals' AS risk_signals, "
            "b.brief_source FROM cases c JOIN transactions t ON t.id = c.txn_id JOIN scores s ON s.txn_id = t.id "
            "JOIN routing_decisions d ON d.txn_id = t.id "
            "LEFT JOIN LATERAL (SELECT factors, brief, brief_source FROM briefs WHERE case_id = c.id ORDER BY id DESC LIMIT 1) b ON true "
            "ORDER BY c.id DESC LIMIT 15").fetchall()
        monitor = conn.execute(
            "SELECT DISTINCT ON (metric, feature) metric, feature, value, level, owner, action, computed_at FROM monitor_metrics "
            "ORDER BY metric, feature, computed_at DESC").fetchall()
        audit = conn.execute("SELECT seq, ts, entity, entity_id, action, actor FROM audit_log ORDER BY seq DESC LIMIT 12").fetchall()
        briefs = conn.execute("SELECT brief_source, count(*) AS n FROM briefs GROUP BY 1").fetchall()

    hist = [0] * (len(SCORE_BINS) - 1)
    for r in scores:
        v = r["score"]
        if v is None or not math.isfinite(v):
            continue
        for i in range(len(hist)):
            if SCORE_BINS[i] <= v < SCORE_BINS[i + 1]:
                hist[i] += 1
                break
    for f in flagged:
        f["validation_errors"] = errors_of(f["validation_errors"])
        f["factors"], f["risk_signals"] = as_list(f["factors"]), as_list(f["risk_signals"])
    frauds = sum(r["n"] for r in labels if r["is_fraud"])
    caught = sum(r["n"] for r in labels if r["is_fraud"] and r["route"] in ("HIGH", "MEDIUM"))
    flagged_lab = sum(r["n"] for r in labels if r["route"] in ("HIGH", "MEDIUM"))
    return {
        "window": window, "series": series,
        "kpi": dict(kpi) | {"last_hour": last_hour, "escalated": escalated,
                            "open_cases": {r["route"]: r["n"] for r in open_cases},
                            "closed": {r["status"]: r["n"] for r in closed},
                            "labels": {"frauds": frauds, "caught": caught, "flagged_labeled": flagged_lab},
                            "briefs": {r["brief_source"]: r["n"] for r in briefs}},
        "score_hist": [{"lo": SCORE_BINS[i], "hi": min(1.0, SCORE_BINS[i + 1]), "n": n} for i, n in enumerate(hist)],
        "triggers": {"validation": validation, "scoring": score_err, "factors": factors},
        "categories": categories, "flagged": flagged, "monitor": monitor, "audit": audit,
    }


# ------------------------------------------------------------------ toàn bộ giao dịch
SORTS = {"newest": "t.id DESC", "score": "s.score DESC NULLS LAST, t.id DESC", "amount": "t.amt DESC NULLS LAST, t.id DESC",
         "event": "t.event_ts DESC NULLS LAST, t.id DESC"}


@router.get("/transactions")
def list_transactions(route: str | None = None, status: str | None = None, q: str | None = None,
                      category: str | None = None, min_score: float | None = None, min_amt: float | None = None,
                      sort: Literal["newest", "score", "amount", "event"] = "newest", include_seed: bool = False,
                      limit: int = 50, offset: int = 0, user: User = Depends(current_user)):
    where, args = [], []
    if not include_seed:
        where.append("t.status <> 'SEED'")
    if route:
        where.append("d.route = %s"); args.append(route)
    if status:
        where.append("t.status = %s"); args.append(status)
    if category:
        where.append("t.category = %s"); args.append(category)
    if min_score is not None:
        where.append("s.score >= %s"); args.append(min_score)
    if min_amt is not None:
        where.append("t.amt >= %s"); args.append(min_amt)
    if q and q.strip():
        qq = q.strip()
        where.append("(t.trans_num ILIKE %s OR t.card_last4 = %s OR t.merchant ILIKE %s)")
        args += [qq + "%", qq, "%" + qq + "%"]
    w = ("WHERE " + " AND ".join(where)) if where else ""
    base = ("FROM transactions t LEFT JOIN scores s ON s.txn_id = t.id LEFT JOIN routing_decisions d ON d.txn_id = t.id "
            "LEFT JOIN cases c ON c.txn_id = t.id ")
    with tx() as conn:
        total = conn.execute(f"SELECT count(*) AS n {base} {w}", args).fetchone()["n"]
        rows = conn.execute(
            "SELECT t.id, t.trans_num, t.event_ts, t.received_at, t.card_last4, t.merchant, t.category, t.amt, t.state, "
            "t.status, t.validation_errors, s.score, s.status AS score_status, s.error AS score_error, s.latency_ms, "
            f"d.route, c.id AS case_id, c.status AS case_status {base} {w} ORDER BY {SORTS[sort]} LIMIT %s OFFSET %s",
            (*args, min(max(limit, 1), 200), max(offset, 0))).fetchall()
        for r in rows:
            r["validation_errors"] = errors_of(r["validation_errors"])
        cats = conn.execute("SELECT DISTINCT category FROM transactions WHERE category IS NOT NULL ORDER BY 1").fetchall()
    return {"items": rows, "total": total, "categories": [r["category"] for r in cats]}


@router.get("/transactions/{txn_id}")
def get_transaction(txn_id: int, user: User = Depends(current_user)):
    with tx() as conn:
        t = conn.execute(
            "SELECT t.*, s.status AS score_status, s.score, s.score_raw, s.model_version, s.feature_set_version, s.features, "
            "s.error AS score_error, s.latency_ms, d.route, d.config_version, d.t_medium, d.t_high, "
            "c.id AS case_id, c.status AS case_status, c.assigned_role FROM transactions t "
            "LEFT JOIN scores s ON s.txn_id = t.id LEFT JOIN routing_decisions d ON d.txn_id = t.id "
            "LEFT JOIN cases c ON c.txn_id = t.id WHERE t.id = %s", (txn_id,)).fetchone()
        if not t:
            raise HTTPException(404, "không có giao dịch")
        t.pop("card_hash", None)
        t["validation_errors"] = errors_of(t["validation_errors"])
        brief = None
        if t["case_id"]:
            brief = conn.execute("SELECT factors, brief->'risk_signals' AS risk_signals, brief_source FROM briefs "
                                 "WHERE case_id = %s ORDER BY id DESC LIMIT 1", (t["case_id"],)).fetchone()
            if brief:
                brief["factors"], brief["risk_signals"] = as_list(brief["factors"]), as_list(brief["risk_signals"])
    return {"transaction": t, "brief": brief}


# ------------------------------------------------------------------ audit trail
@router.get("/audit")
def audit_trail(entity: str | None = None, q: str | None = None, limit: int = 50, before: int | None = None,
                user: User = Depends(current_user)):
    require_role(user, "risk_lead", "admin")
    where, args = [], []
    if entity:
        where.append("entity = %s"); args.append(entity)
    if q and q.strip():
        where.append("(entity_id ILIKE %s OR actor ILIKE %s OR action ILIKE %s)"); args += [f"%{q.strip()}%"] * 3
    if before:
        where.append("seq < %s"); args.append(before)
    w = ("WHERE " + " AND ".join(where)) if where else ""
    with tx() as conn:
        rows = conn.execute(f"SELECT seq, ts, entity, entity_id, action, actor, payload, hash FROM audit_log {w} "
                            "ORDER BY seq DESC LIMIT %s", (*args, min(max(limit, 1), 200))).fetchall()
        ents = conn.execute("SELECT DISTINCT entity FROM audit_log ORDER BY 1").fetchall()
    return {"items": rows, "entities": [r["entity"] for r in ents]}

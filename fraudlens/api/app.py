"""API – cửa vào duy nhất của hệ thống.

Chạy:  uvicorn fraudlens.api.app:app --port 8000 --workers 2
- Nguồn giao dịch:  POST /v1/transactions, POST /v1/labels   (X-API-Key)
- Dashboard:        /v1/cases..., /v1/stats, /v1/config         (Bearer token)
"""
from __future__ import annotations

import logging
import os
from typing import Literal

from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from fraudlens.api.auth import User, current_user, require_role, require_source
from fraudlens.api.service import FastLane
from fraudlens.common.db import audit, tx, verify_audit_chain
from fraudlens.common.registry import load_active_routing
from fraudlens.tools.product import is_holdout, product_metrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fraudlens.api")
app = FastAPI(title="FraudLens API")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(","),
                   allow_methods=["*"], allow_headers=["*"])
lane: FastLane | None = None


@app.on_event("startup")
def startup() -> None:
    global lane
    try:
        rc = load_active_routing()
        log.info("routing config %s cho model %s", rc["config_version"], rc["model_version"])
    except Exception as e:  # noqa: BLE001 – không có cấu hình được duyệt: mọi giao dịch thành UNSCORED
        log.error("không nạp được routing config (%s) – mọi giao dịch sẽ thành UNSCORED", e)
        rc = None
    lane = FastLane(rc)
    try:
        with tx() as conn:
            lane.stream_ts = conn.execute("SELECT max(event_ts) AS t FROM transactions WHERE status IN ('VALID','SEED')").fetchone()["t"]
    except Exception as e:  # noqa: BLE001 – chưa có thời điểm luồng: bỏ qua kiểm tra độ tươi cho tới giao dịch đầu tiên
        log.error("không đọc được thời điểm luồng: %s", e)
    try:
        if not lane.hist.is_warm():
            with tx() as conn:
                n = lane.hist.rebuild_from_db(conn)
            log.info("warmup Redis từ PostgreSQL: %d bản ghi", n)
    except Exception as e:  # noqa: BLE001
        log.error("warmup Redis thất bại: %s", e)


# ------------------------------------------------------------------ nguồn dữ liệu
@app.post("/v1/transactions", dependencies=[Depends(require_source)])
def ingest(raw: dict = Body(...)):
    return lane.process(raw)


class Label(BaseModel):
    trans_num: str
    is_fraud: bool
    label_ts: int | None = None


@app.post("/v1/labels", dependencies=[Depends(require_source)])
def ingest_label(lb: Label):
    with tx() as conn:
        conn.execute("INSERT INTO labels_eval (trans_num, is_fraud, label_ts) VALUES (%s,%s,%s) "
                     "ON CONFLICT (trans_num) DO UPDATE SET is_fraud = EXCLUDED.is_fraud, label_ts = EXCLUDED.label_ts",
                     (lb.trans_num, lb.is_fraud, lb.label_ts))
    return {"ok": True}


# ------------------------------------------------------------------ dashboard
ROUTE_ORDER = "CASE c.route WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END"


@app.get("/v1/cases")
def list_cases(status: Literal["open", "closed", "all"] = "open", route: str | None = None,
               assigned_role: str | None = None, limit: int = 50, offset: int = 0,
               user: User = Depends(current_user)):
    where, args = [], []
    if status == "open":
        where.append("c.status IN ('OPEN','INVESTIGATING','ESCALATED')")
    elif status == "closed":
        where.append("c.status IN ('CLOSED_FRAUD','CLOSED_LEGIT')")
    if route:
        where.append("c.route = %s"); args.append(route)
    if assigned_role:
        where.append("c.assigned_role = %s"); args.append(assigned_role)
    sql = (
        "SELECT c.id, c.route, c.status, c.assigned_role, c.created_at, t.trans_num, t.event_ts, t.category, "
        "t.merchant, t.amt, t.card_last4, s.score, s.status AS score_status, "
        "(SELECT b.brief_source FROM briefs b WHERE b.case_id = c.id ORDER BY b.created_at DESC LIMIT 1) AS brief_source "
        "FROM cases c JOIN transactions t ON t.id = c.txn_id JOIN scores s ON s.txn_id = t.id "
        + ("WHERE " + " AND ".join(where) if where else "")
        + f" ORDER BY {ROUTE_ORDER}, s.score DESC NULLS LAST, t.amt DESC NULLS LAST LIMIT %s OFFSET %s")
    with tx() as conn:
        rows = conn.execute(sql, (*args, min(limit, 200), offset)).fetchall()
    return {"items": rows}


@app.get("/v1/cases/{case_id}")
def get_case(case_id: int, user: User = Depends(current_user)):
    with tx() as conn:
        c = conn.execute(
            "SELECT c.*, t.trans_num, t.event_ts, t.card_last4, t.merchant, t.category, t.amt, t.city_pop, t.state, "
            "t.age, t.distance_km, t.status AS txn_status, t.validation_errors, s.status AS score_status, s.score, "
            "s.score_raw, s.model_version, s.feature_set_version, s.features, s.error AS score_error, "
            "d.config_version, d.t_medium, d.t_high FROM cases c JOIN transactions t ON t.id = c.txn_id "
            "JOIN scores s ON s.txn_id = t.id JOIN routing_decisions d ON d.txn_id = t.id WHERE c.id = %s",
            (case_id,)).fetchone()
        if not c:
            raise HTTPException(404, "không có hồ sơ")
        brief = conn.execute("SELECT * FROM briefs WHERE case_id = %s ORDER BY created_at DESC LIMIT 1", (case_id,)).fetchone()
        job = conn.execute("SELECT status, attempts, last_error FROM brief_jobs WHERE case_id = %s ORDER BY id DESC LIMIT 1",
                           (case_id,)).fetchone()
        actions = conn.execute("SELECT actor, role, action, note, created_at FROM case_actions WHERE case_id = %s ORDER BY id",
                               (case_id,)).fetchall()
    holdout = is_holdout(case_id)
    if holdout:  # nhóm đối chứng: chuyên viên xử lý không có brief (brief vẫn được soạn và lưu để đánh giá sau)
        brief, job = None, None
    return {"case": c, "brief": brief, "brief_job": job, "brief_holdout": holdout, "actions": actions}


class Action(BaseModel):
    action: Literal["NOTE", "INVESTIGATE", "ESCALATE", "CLOSE_FRAUD", "CLOSE_LEGIT", "REGENERATE_BRIEF",
                    "VIEW", "BRIEF_USEFUL", "BRIEF_NOT_USEFUL"]
    note: str | None = None


NEW_STATUS = {"INVESTIGATE": "INVESTIGATING", "ESCALATE": "ESCALATED", "CLOSE_FRAUD": "CLOSED_FRAUD", "CLOSE_LEGIT": "CLOSED_LEGIT"}
# Thao tác chỉ ghi nhận (đo thời gian xử lý, đánh giá brief), không đổi trạng thái – được phép cả khi hồ sơ đã đóng
PASSIVE = {"VIEW", "BRIEF_USEFUL", "BRIEF_NOT_USEFUL"}


@app.post("/v1/cases/{case_id}/actions")
def act(case_id: int, a: Action, user: User = Depends(current_user)):
    require_role(user, "analyst", "risk_lead")
    note = (a.note or "").strip()
    if a.action.startswith("CLOSE") and not note:
        raise HTTPException(400, "đóng hồ sơ bắt buộc ghi nhận định")
    with tx() as conn:
        c = conn.execute("SELECT status FROM cases WHERE id = %s FOR UPDATE", (case_id,)).fetchone()
        if not c:
            raise HTTPException(404, "không có hồ sơ")
        if c["status"].startswith("CLOSED") and a.action not in PASSIVE:
            raise HTTPException(409, "hồ sơ đã đóng")
        if c["status"] == "ESCALATED" and a.action.startswith("CLOSE"):
            require_role(user, "risk_lead")
        if a.action in NEW_STATUS:
            conn.execute("UPDATE cases SET status = %s, updated_at = now()"
                         + (", assigned_role = 'risk_lead'" if a.action == "ESCALATE" else "") + " WHERE id = %s",
                         (NEW_STATUS[a.action], case_id))
        if a.action == "REGENERATE_BRIEF":
            conn.execute("INSERT INTO brief_jobs (case_id) VALUES (%s)", (case_id,))
        conn.execute("INSERT INTO case_actions (case_id, actor, role, action, note) VALUES (%s,%s,%s,%s,%s)",
                     (case_id, user.name, user.role, a.action, note or None))
        audit(conn, "case", str(case_id), a.action, f"user:{user.name}", {"role": user.role, "note": note})
    return {"ok": True}


@app.get("/v1/stats")
def stats(user: User = Depends(current_user)):
    with tx() as conn:
        open_by_route = conn.execute(
            "SELECT route, count(*) AS n FROM cases WHERE status IN ('OPEN','INVESTIGATING','ESCALATED') GROUP BY route").fetchall()
        routes = conn.execute("SELECT route, count(*) AS n FROM routing_decisions GROUP BY route").fetchall()
        jobs = conn.execute("SELECT status, count(*) AS n FROM brief_jobs GROUP BY status").fetchall()
        drift = conn.execute(
            "SELECT DISTINCT ON (metric, feature) metric, feature, value, level, owner, action, computed_at FROM monitor_metrics "
            "ORDER BY metric, feature, computed_at DESC").fetchall()
    return {"open_cases": open_by_route, "routes_total": routes, "brief_jobs": jobs, "monitor": drift}


@app.get("/v1/stats/product")
def stats_product(user: User = Depends(current_user)):
    require_role(user, "risk_lead", "admin")
    with tx() as conn:
        return product_metrics(conn)


@app.get("/v1/config")
def config(user: User = Depends(current_user)):
    require_role(user, "risk_lead", "admin")
    return lane.rc or {"error": "chưa có routing config được duyệt"}


@app.get("/v1/audit/verify")
def audit_verify(user: User = Depends(current_user)):
    require_role(user, "admin", "risk_lead")
    with tx() as conn:
        ok, bad = verify_audit_chain(conn)
    return {"chain_ok": ok, "first_bad_seq": bad}


@app.get("/health")
def health():
    return {"status": "ok", "routing_config": lane.rc and lane.rc["config_version"],
            "scorer_breaker_open": lane.scorer.breaker.is_open}


@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

"""Vận hành từ giao diện web, thay cho các lệnh terminal: simulator, nạp lịch sử, dựng lại Redis, kiểm tra audit,
truy vết, kiểm tra LLM, duyệt model.

Việc chạy lâu (simulator, nạp lịch sử, check-parity...) là "job nền": một dòng trong bảng ops_jobs và một luồng trong
tiến trình API. Trạng thái, tiến độ, kết quả nằm trong PostgreSQL nên mọi tiến trình API (uvicorn --workers N) đều đọc
được; nút "Dừng" đặt status = STOPPING và luồng đang chạy tự dừng. Luồng cập nhật heartbeat_at mỗi vài giây; job
RUNNING mà heartbeat cũ quá STALE_S giây (API bị khởi động lại giữa chừng) được coi là thất bại.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
import traceback
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from fraudlens.api.auth import User, current_user, require_role
from fraudlens.common.config import settings
from fraudlens.common.db import J, audit, tx

log = logging.getLogger("fraudlens.ops")
router = APIRouter(prefix="/v1")
STALE_S = 30
HEAVY = {"simulator", "seed_history"}  # chỉ một job nặng chạy tại một thời điểm

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ops_jobs (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,
    params        JSONB NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'RUNNING'
                  CHECK (status IN ('RUNNING','STOPPING','DONE','FAILED','STOPPED')),
    progress      JSONB,
    result        JSONB,
    error         TEXT,
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_ops_jobs_kind ON ops_jobs (kind, id DESC);
"""


def _clean(v):
    """JSONB của PostgreSQL không nhận NaN/Infinity: đổi thành null."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def _json(obj) -> J:
    return J(_clean(json.loads(json.dumps(obj, ensure_ascii=False, default=str))))


# ------------------------------------------------------------------ job nền
class JobContext:
    """Truyền cho hàm của job: report(progress) và should_stop()."""

    def __init__(self, job_id: int):
        self.id = job_id
        self._progress: dict | None = None
        self._dirty = False
        self._stop = False
        self._done = threading.Event()
        self._lock = threading.Lock()

    def report(self, progress: dict) -> None:
        with self._lock:
            self._progress, self._dirty = progress, True

    def should_stop(self) -> bool:
        return self._stop

    def _beat(self) -> None:
        """Luồng phụ: ghi tiến độ + heartbeat, đọc cờ dừng."""
        while not self._done.wait(1.0):
            try:
                with self._lock:
                    prog, dirty, self._dirty = self._progress, self._dirty, False
                with tx() as conn:
                    if dirty:
                        row = conn.execute("UPDATE ops_jobs SET progress = %s, heartbeat_at = now() WHERE id = %s "
                                           "RETURNING status", (_json(prog), self.id)).fetchone()
                    else:
                        row = conn.execute("UPDATE ops_jobs SET heartbeat_at = now() WHERE id = %s RETURNING status",
                                           (self.id,)).fetchone()
                self._stop = bool(row and row["status"] == "STOPPING")
            except Exception as e:  # noqa: BLE001 – DB chập chờn: thử lại ở nhịp sau
                log.warning("heartbeat job %s lỗi: %s", self.id, e)


def _mark_stale(conn) -> None:
    conn.execute("UPDATE ops_jobs SET status = 'FAILED', error = 'mất heartbeat (API khởi động lại giữa chừng?)', "
                 "finished_at = now() WHERE status IN ('RUNNING','STOPPING') "
                 f"AND heartbeat_at < now() - interval '{STALE_S} seconds'")


def start_job(kind: str, params: dict, user: User, fn) -> dict:
    """Tạo job và chạy fn(ctx, params) -> dict trong luồng nền. Từ chối nếu đang có job nặng khác."""
    with tx() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(4444)")
        _mark_stale(conn)
        group = tuple(HEAVY) if kind in HEAVY else (kind,)
        busy = conn.execute("SELECT id, kind FROM ops_jobs WHERE status IN ('RUNNING','STOPPING') AND kind = ANY(%s) "
                            "ORDER BY id DESC LIMIT 1", (list(group),)).fetchone()
        if busy:
            raise HTTPException(409, f"đang có job {busy['kind']} #{busy['id']} chạy, đợi xong hoặc bấm Dừng")
        job = conn.execute("INSERT INTO ops_jobs (kind, params, created_by) VALUES (%s,%s,%s) RETURNING *",
                           (kind, _json(params), user.name)).fetchone()
        audit(conn, "ops_job", str(job["id"]), f"START:{kind}", f"user:{user.name}", {"params": params})
    ctx = JobContext(job["id"])

    def run():
        beat = threading.Thread(target=ctx._beat, daemon=True)
        beat.start()
        status, result, error = "DONE", None, None
        try:
            result = fn(ctx, params)
            if ctx.should_stop():
                status = "STOPPED"
        except Exception as e:  # noqa: BLE001
            status, error = "FAILED", f"{type(e).__name__}: {e}"
            log.error("job %s (%s) lỗi:\n%s", ctx.id, kind, traceback.format_exc())
        finally:
            ctx._done.set()
            beat.join(timeout=3)
        try:
            with tx() as conn:
                conn.execute("UPDATE ops_jobs SET status = %s, result = %s, error = %s, progress = COALESCE(%s, progress), "
                             "finished_at = now(), heartbeat_at = now() WHERE id = %s",
                             (status, _json(result) if result is not None else None, error,
                              _json(ctx._progress) if ctx._progress is not None else None, ctx.id))
                audit(conn, "ops_job", str(ctx.id), f"{status}:{kind}", "system:ops", {"error": error})
        except Exception as e:  # noqa: BLE001
            log.error("không ghi được kết quả job %s: %s", ctx.id, e)

    threading.Thread(target=run, name=f"job-{kind}-{job['id']}", daemon=True).start()
    return job


def _list_jobs(kind: str | None, limit: int) -> list[dict]:
    with tx() as conn:
        _mark_stale(conn)
        if kind:
            return conn.execute("SELECT * FROM ops_jobs WHERE kind = %s ORDER BY id DESC LIMIT %s", (kind, limit)).fetchall()
        return conn.execute("SELECT * FROM ops_jobs ORDER BY id DESC LIMIT %s", (limit,)).fetchall()


@router.get("/jobs")
def list_jobs(kind: str | None = None, limit: int = 20, user: User = Depends(current_user)):
    return {"items": _list_jobs(kind, min(limit, 100))}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, user: User = Depends(current_user)):
    with tx() as conn:
        _mark_stale(conn)
        j = conn.execute("SELECT * FROM ops_jobs WHERE id = %s", (job_id,)).fetchone()
    if not j:
        raise HTTPException(404, "không có job")
    return j


@router.post("/jobs/{job_id}/stop")
def stop_job(job_id: int, user: User = Depends(current_user)):
    require_role(user, "risk_lead", "admin")
    with tx() as conn:
        n = conn.execute("UPDATE ops_jobs SET status = 'STOPPING' WHERE id = %s AND status = 'RUNNING'", (job_id,)).rowcount
        if n:
            audit(conn, "ops_job", str(job_id), "STOP", f"user:{user.name}", {})
    if not n:
        raise HTTPException(409, "job không còn chạy")
    return {"ok": True}


# ------------------------------------------------------------------ simulator
class SimParams(BaseModel):
    dataset: str = Field("test", pattern="^(test|train)$")
    limit: int = Field(2000, ge=1, le=600_000)
    offset: int | None = Field(None, ge=0)  # None = chạy tiếp từ lần trước
    tps: float = Field(20, ge=0, le=1000)
    threads: int = Field(4, ge=1, le=32)
    fault_rate: float = Field(0.02, ge=0, le=1)
    label_delay_days: float = Field(7, ge=0, le=60)
    send_labels: bool = True
    seed: int = 7


def _next_offset(dataset: str) -> int:
    with tx() as conn:
        r = conn.execute("SELECT result->>'next_offset' AS o FROM ops_jobs WHERE kind = 'simulator' "
                         "AND params->>'dataset' = %s AND result ? 'next_offset' ORDER BY id DESC LIMIT 1",
                         (dataset,)).fetchone()
    return int(r["o"]) if r and r["o"] else 0


def _csv(dataset: str):
    p = settings.data_dir / ("fraudTest.csv" if dataset == "test" else "fraudTrain.csv")
    if not p.exists():
        raise HTTPException(400, f"không thấy {p} – đặt file dữ liệu Kaggle vào thư mục data/ (xem trang Hệ thống)")
    return p


@router.get("/simulator")
def simulator_status(user: User = Depends(current_user)):
    jobs = _list_jobs("simulator", 15)
    running = next((j for j in jobs if j["status"] in ("RUNNING", "STOPPING")), None)
    return {"running": running, "history": jobs, "next_offset": {d: _next_offset(d) for d in ("test", "train")},
            "data_files": {d: (settings.data_dir / f).exists() for d, f in (("test", "fraudTest.csv"), ("train", "fraudTrain.csv"))}}


@router.post("/simulator/start")
def simulator_start(p: SimParams, user: User = Depends(current_user)):
    require_role(user, "risk_lead", "admin")
    csv = _csv(p.dataset)
    params = p.model_dump()
    if params["offset"] is None:
        params["offset"] = _next_offset(p.dataset)

    def run(ctx: JobContext, prm: dict) -> dict:
        from fraudlens.tools.simulator import replay
        opts = {"csv": str(csv), "api": settings.self_api_url, "limit": prm["limit"], "offset": prm["offset"],
                "tps": prm["tps"], "threads": prm["threads"], "fault_rate": prm["fault_rate"],
                "label_delay_days": prm["label_delay_days"], "no_labels": not prm["send_labels"], "seed": prm["seed"]}
        return replay(opts, progress=ctx.report, should_stop=ctx.should_stop)

    return start_job("simulator", params, user, run)


# ------------------------------------------------------------------ thao tác quản trị (job nền)
class SeedParams(BaseModel):
    days: int = Field(30, ge=1, le=90)


@router.post("/ops/seed-history")
def op_seed(p: SeedParams, user: User = Depends(current_user)):
    require_role(user, "admin")
    csv = _csv("train")

    def run(ctx: JobContext, prm: dict) -> dict:
        from fraudlens.tools.seed_history import seed
        return seed(str(csv), prm["days"], progress=ctx.report)

    return start_job("seed_history", p.model_dump(), user, run)


@router.post("/ops/rebuild-redis")
def op_rebuild_redis(user: User = Depends(current_user)):
    require_role(user, "admin")

    def run(ctx: JobContext, prm: dict) -> dict:
        from fraudlens.api.history import WARM_FLAG, HistoryStore
        h = HistoryStore()
        h.r.delete(WARM_FLAG)
        ctx.report({"message": "đang dựng lại Redis từ PostgreSQL"})
        with tx() as conn:
            return {"redis_records": h.rebuild_from_db(conn)}

    return start_job("rebuild_redis", {}, user, run)


class NParams(BaseModel):
    n: int = Field(20000, ge=1, le=500_000)


@router.post("/ops/check-parity")
def op_parity(p: NParams, user: User = Depends(current_user)):
    require_role(user, "admin", "risk_lead")

    def run(ctx: JobContext, prm: dict) -> dict:
        from fraudlens.tools.parity import compare, load_from_db
        ctx.report({"message": f"đang so feature online/offline trên {prm['n']:,} giao dịch"})
        with tx() as conn:
            df, online = load_from_db(conn, prm["n"])
        if not online:
            raise RuntimeError("chưa có giao dịch đã chấm")
        return compare(df, online)

    return start_job("check_parity", p.model_dump(), user, run)


@router.post("/ops/trace-all")
def op_trace_all(user: User = Depends(current_user)):
    require_role(user, "admin", "risk_lead")

    def run(ctx: JobContext, prm: dict) -> dict:
        from fraudlens.tools.integrity import trace_all
        ctx.report({"message": "đang kiểm tra truy vết cho mọi giao dịch/hồ sơ"})
        with tx() as conn:
            return trace_all(conn)

    return start_job("trace_all", {}, user, run)


@router.post("/ops/eval-briefs")
def op_eval_briefs(p: NParams, user: User = Depends(current_user)):
    require_role(user, "admin", "risk_lead")

    def run(ctx: JobContext, prm: dict) -> dict:
        from fraudlens.worker.evaluate import evaluate_db
        ctx.report({"message": f"đang chấm {prm['n']} brief gần nhất"})
        with tx() as conn:
            rep = evaluate_db(conn, prm["n"], None)
        return {k: v for k, v in rep.items() if k != "claims"}

    return start_job("eval_briefs", {"n": min(p.n, 2000)}, user, run)


# ------------------------------------------------------------------ thao tác nhanh (trả kết quả ngay)
@router.post("/ops/regen-briefs")
def op_regen_briefs(user: User = Depends(current_user)):
    require_role(user, "admin", "risk_lead")
    with tx() as conn:
        n = conn.execute("INSERT INTO brief_jobs (case_id) SELECT id FROM cases WHERE route IN ('HIGH','MEDIUM') "
                         "AND status NOT LIKE 'CLOSED%'").rowcount
        audit(conn, "ops", "regen-briefs", "REGEN_BRIEFS", f"user:{user.name}", {"n": n})
    return {"queued": n}


@router.post("/ops/test-llm")
def op_test_llm(user: User = Depends(current_user)):
    require_role(user, "admin", "risk_lead")
    from fraudlens.worker.llm import LLMClient, LLMUnavailable
    c = LLMClient()
    info = {"base_url": c.base, "model": c.model, "configured": bool(c.enabled)}
    if not c.enabled:
        return info | {"ok": False, "message": "chưa đặt LLM_API_KEY – worker dùng brief dạng mẫu"}
    t0 = time.perf_counter()
    try:
        text, tk = c.chat_json("Trả về JSON.", 'Trả về đúng JSON {"ok": true}')
        return info | {"ok": True, "tokens": tk, "latency_ms": (time.perf_counter() - t0) * 1000, "response": text[:300]}
    except LLMUnavailable as e:
        return info | {"ok": False, "message": str(e)}


@router.get("/ops/trace/{trans_num}")
def op_trace(trans_num: str, user: User = Depends(current_user)):
    with tx() as conn:
        t = conn.execute("SELECT id, trans_num, event_ts, card_last4, merchant, category, amt, state, status, "
                         "validation_errors, received_at FROM transactions WHERE trans_num = %s", (trans_num,)).fetchone()
        if not t:
            raise HTTPException(404, "không có giao dịch")
        out = {"transaction": t,
               "score": conn.execute("SELECT status, score, model_version, feature_set_version, error, latency_ms "
                                     "FROM scores WHERE txn_id=%s", (t["id"],)).fetchone(),
               "routing": conn.execute("SELECT * FROM routing_decisions WHERE txn_id=%s", (t["id"],)).fetchone(),
               "label": conn.execute("SELECT is_fraud, label_ts FROM labels_eval WHERE trans_num=%s", (trans_num,)).fetchone()}
        c = conn.execute("SELECT * FROM cases WHERE txn_id=%s", (t["id"],)).fetchone()
        if c:
            out["case"] = c
            out["briefs"] = conn.execute("SELECT id, brief_source, llm_model, prompt_version, kb_version, chunk_ids, created_at "
                                         "FROM briefs WHERE case_id=%s ORDER BY id", (c["id"],)).fetchall()
            out["actions"] = conn.execute("SELECT actor, role, action, note, created_at FROM case_actions WHERE case_id=%s "
                                          "ORDER BY id", (c["id"],)).fetchall()
        out["audit"] = conn.execute("SELECT seq, ts, entity, action, actor FROM audit_log WHERE entity_id IN (%s,%s) ORDER BY seq",
                                    (trans_num, str(c["id"]) if c else "-")).fetchall()
    return out


# ------------------------------------------------------------------ trạng thái hệ thống
def _probe(url: str, timeout: float = 1.0) -> dict:
    try:
        r = httpx.get(url, timeout=timeout)
        body = None
        if "json" in r.headers.get("content-type", ""):
            body = r.json()
        return {"ok": r.status_code == 200, "status_code": r.status_code, "body": body}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}


def system_status(lane) -> dict:
    from fraudlens.common.registry import root
    out: dict = {"checked_at": datetime.now(timezone.utc).isoformat()}
    # dịch vụ
    svc = {"api": {"ok": True, "routing_config": lane.rc and lane.rc["config_version"],
                   "scorer_breaker_open": lane.scorer.breaker.is_open}}
    svc["scorer"] = _probe(settings.scorer_url.rstrip("/") + "/health")
    svc["worker"] = _probe(settings.worker_metrics_url)
    svc["monitor"] = _probe(settings.monitor_metrics_url)
    svc["worker"].pop("body", None); svc["monitor"].pop("body", None)
    try:
        lane.hist.r.ping()
        svc["redis"] = {"ok": True, "warm": bool(lane.hist.is_warm())}
    except Exception as e:  # noqa: BLE001
        svc["redis"] = {"ok": False, "error": type(e).__name__}
    counts: dict = {}
    try:
        with tx() as conn:
            for r in conn.execute("SELECT status, count(*) AS n FROM transactions GROUP BY status").fetchall():
                counts[f"txn_{r['status'].lower()}"] = r["n"]
            counts["cases"] = conn.execute("SELECT count(*) AS n FROM cases").fetchone()["n"]
            counts["labels"] = conn.execute("SELECT count(*) AS n FROM labels_eval").fetchone()["n"]
            counts["users"] = conn.execute("SELECT count(*) AS n FROM app_users").fetchone()["n"]
            last = conn.execute("SELECT max(computed_at) AS t FROM monitor_metrics").fetchone()["t"]
            counts["monitor_last_run"] = last
            counts["briefs_llm"] = conn.execute("SELECT count(*) AS n FROM briefs WHERE brief_source='llm'").fetchone()["n"]
        svc["postgres"] = {"ok": True}
    except Exception as e:  # noqa: BLE001
        svc["postgres"] = {"ok": False, "error": type(e).__name__}
    out["services"], out["counts"] = svc, counts
    # dữ liệu, model, LLM
    files = {}
    for f in ("fraudTrain.csv", "fraudTest.csv"):
        p = settings.data_dir / f
        files[f] = {"exists": p.exists(), "size_mb": round(p.stat().st_size / 2**20, 1) if p.exists() else None}
    out["data"] = {"dir": str(settings.data_dir), "files": files}
    reg = root()
    active = (reg / "ACTIVE").read_text().strip() if (reg / "ACTIVE").exists() else None
    versions = sorted(p.name for p in reg.iterdir() if p.is_dir() and not p.name.startswith("_")) if reg.exists() else []
    out["model"] = {"active": active, "versions": len(versions), "approved": bool(lane.rc),
                    "registry_writable": _writable(reg)}
    out["llm"] = {"configured": bool(settings.llm_api_key.strip()), "model": settings.llm_model, "base_url": settings.llm_base_url}
    # các bước cài đặt (thứ tự như README)
    seeded = counts.get("txn_seed", 0) > 0
    replayed = counts.get("txn_valid", 0) + counts.get("txn_invalid", 0) > 0
    out["steps"] = [
        {"key": "data", "title": "Đặt dữ liệu Kaggle vào thư mục data/", "done": all(v["exists"] for v in files.values())},
        {"key": "model", "title": "Huấn luyện model (start.ps1 hoặc python -m fraudlens.ml.train)", "done": bool(versions)},
        {"key": "approve", "title": "Risk Lead duyệt model (trang Model)", "done": bool(lane.rc)},
        {"key": "services", "title": "Các dịch vụ đang chạy",
         "done": all(svc[k].get("ok") for k in ("postgres", "redis", "scorer", "worker"))},
        {"key": "seed", "title": "Nạp lịch sử 30 ngày (trang Hệ thống)", "done": seeded},
        {"key": "replay", "title": "Phát lại giao dịch (trang Simulator)", "done": replayed},
        {"key": "llm", "title": "(Tùy chọn) Cấu hình LLM soạn Case Brief trong .env", "done": out["llm"]["configured"], "optional": True},
    ]
    return out


def _writable(p) -> bool:
    try:
        t = p / ".write_test"
        t.write_text("x")
        t.unlink()
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ model registry
def list_models() -> list[dict]:
    from fraudlens.common.registry import load_json, root
    from fraudlens.ml.gates import release_gate
    reg = root()
    if not reg.exists():
        return []
    active = (reg / "ACTIVE").read_text().strip() if (reg / "ACTIVE").exists() else None
    out = []
    for d in sorted((p for p in reg.iterdir() if p.is_dir() and not p.name.startswith("_")), reverse=True):
        try:
            rc = load_json(d.name, "routing_config.json")
            manifest = load_json(d.name, "manifest.json")
        except Exception as e:  # noqa: BLE001
            out.append({"version": d.name, "error": f"không đọc được: {e}"})
            continue
        m = manifest.get("metrics", {})
        try:
            gate = release_gate(manifest, rc)
        except Exception as e:  # noqa: BLE001
            gate = [f"không chạy được gate: {e}"]
        base = {k: (v.get("ranking") or {}).get("recall_at_0.01") for k, v in (m.get("baselines_val_b_report") or {}).items()}
        out.append({
            "version": d.name, "active": d.name == active, "created_at": manifest.get("created_at"),
            "feature_set_version": manifest.get("feature_set_version"), "data": manifest.get("data"),
            "val_b": m.get("val_b"), "val_b_check": rc.get("val_b_check"), "baselines_recall_at_1pct": base,
            "t_medium": rc.get("t_medium"), "t_high": rc.get("t_high"), "K": rc.get("K"), "policy": rc.get("policy"),
            "config_version": rc.get("config_version"), "approved_by": rc.get("approved_by"),
            "approved_at": rc.get("approved_at"), "gate_violations": gate,
            "gate_override": (rc.get("release_gate") or {}).get("override"),
            "has_test_results": (d / "test_results.json").exists(),
        })
    return out


class ApproveReq(BaseModel):
    override: str | None = None


def approve_model(version: str, req: ApproveReq, user: User) -> dict:
    from fraudlens.common.registry import load_json, root, save_json
    from fraudlens.ml.gates import GateConfig, release_gate
    reg = root()
    if not (reg / version / "routing_config.json").exists():
        raise HTTPException(404, "không có phiên bản này")
    if not _writable(reg):
        raise HTTPException(500, "thư mục registry đang chỉ đọc trong container API (xem docker-compose.yml)")
    rc = load_json(version, "routing_config.json")
    manifest = load_json(version, "manifest.json")
    cfg = GateConfig()
    reasons = release_gate(manifest, rc, cfg)
    override = (req.override or "").strip()
    if reasons and not override:
        raise HTTPException(422, {"message": "release gate chặn", "violations": reasons})
    prev = (reg / "ACTIVE").read_text().strip() if (reg / "ACTIVE").exists() else None
    now = datetime.now(timezone.utc).isoformat()
    rc["release_gate"] = {"config": cfg.__dict__, "violations": reasons, "override": override if reasons else None,
                          "checked_at": now}
    rc["approved_by"] = user.display_name or user.name
    rc["approved_at"] = now
    save_json(version, "routing_config.json", rc)
    (reg / "ACTIVE").write_text(version)
    with tx() as conn:
        audit(conn, "model", version, "APPROVE", f"user:{user.name}",
              {"previous": prev, "violations": reasons, "override": override or None})
    return {"active": version, "previous": prev, "violations": reasons}

"""LLM worker – làn chậm. Lấy việc từ outbox trong PostgreSQL, soạn brief, ghi kết quả + audit.

Chạy:  python -m fraudlens.worker.main          (chạy nhiều bản song song được: SKIP LOCKED không trùng việc)
"""
from __future__ import annotations

import logging
import time

import httpx
from prometheus_client import Counter, Histogram, start_http_server

from fraudlens.common.config import settings
from fraudlens.common.db import J, audit, tx
from fraudlens.worker.brief import BriefWriter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fraudlens.worker")
DONE = Counter("worker_briefs_total", "Số brief đã soạn", ["source"])
FAIL = Counter("worker_job_failures_total", "Số lần xử lý job lỗi")
LAT = Histogram("worker_brief_seconds", "Thời gian soạn một brief", buckets=(0.1, 0.5, 1, 2, 5, 10, 20, 40, 60))
MAX_ATTEMPTS = 3

CLAIM = """
UPDATE brief_jobs SET status = 'RUNNING', locked_at = now(), attempts = attempts + 1
WHERE id = (SELECT id FROM brief_jobs WHERE status = 'PENDING' AND next_attempt_at <= now()
            ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, case_id, attempts
"""
CASE_SQL = """
SELECT c.id, c.route, t.card_last4, t.merchant, t.category, t.amt, t.validation_errors,
       s.status AS score_status, s.score, s.features, s.error AS score_error
FROM cases c JOIN transactions t ON t.id = c.txn_id JOIN scores s ON s.txn_id = t.id WHERE c.id = %s
"""


def explain(http: httpx.Client, features: dict | None) -> list | None:
    """Giải thích chỉ tính ở làn chậm, cho hồ sơ cần xem -> không làm chậm làn nhanh."""
    if not features:
        return None
    try:
        r = http.post(f"{settings.scorer_url.rstrip('/')}/explain", json={"features": features})
        r.raise_for_status()
        return r.json()["factors"]
    except Exception as e:  # noqa: BLE001
        log.warning("không lấy được giải thích: %s", e)
        return None


def reclaim_stale() -> None:
    """Job RUNNING quá lâu (worker chết giữa chừng) được trả về hàng đợi."""
    with tx() as conn:
        conn.execute("UPDATE brief_jobs SET status = 'PENDING', locked_at = NULL "
                     "WHERE status = 'RUNNING' AND locked_at < now() - make_interval(secs => %s)", (settings.job_stale_s,))


def run_once(writer: BriefWriter, http: httpx.Client) -> bool:
    with tx() as conn:
        job = conn.execute(CLAIM).fetchone()
    if not job:
        return False
    try:
        with tx() as conn:
            case = conn.execute(CASE_SQL, (job["case_id"],)).fetchone()
        t0 = time.perf_counter()
        res = writer.write(dict(case), explain(http, case["features"]))
        LAT.observe(time.perf_counter() - t0)
        with tx() as conn:
            bid = conn.execute(
                "INSERT INTO briefs (case_id, brief, brief_source, llm_model, prompt_version, kb_version, chunk_ids, factors, "
                "guardrail, tokens, latency_ms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (job["case_id"], J(res["brief"]), res["brief_source"], res["llm_model"], res["prompt_version"],
                 settings.kb_version, res["chunk_ids"], J(res["factors"]), J(res["guardrail"]), res["tokens"],
                 res["latency_ms"])).fetchone()["id"]
            conn.execute("UPDATE brief_jobs SET status = 'DONE', locked_at = NULL, last_error = NULL WHERE id = %s", (job["id"],))
            audit(conn, "case", str(job["case_id"]), "BRIEF_CREATED", "system:llm_worker",
                  {"brief_id": bid, "brief_source": res["brief_source"], "chunk_ids": res["chunk_ids"],
                   "prompt_version": res["prompt_version"], "llm_model": res["llm_model"]})
        DONE.labels(res["brief_source"]).inc()
        log.info("case %s: brief %s (%s ms)", job["case_id"], res["brief_source"], res["latency_ms"])
    except Exception as e:  # noqa: BLE001
        FAIL.inc()
        log.exception("job %s lỗi", job["id"])
        status = "FAILED" if job["attempts"] >= MAX_ATTEMPTS else "PENDING"
        with tx() as conn:
            conn.execute("UPDATE brief_jobs SET status = %s, locked_at = NULL, last_error = %s, "
                         "next_attempt_at = now() + make_interval(secs => %s) WHERE id = %s",
                         (status, f"{type(e).__name__}: {e}"[:500], 30 * job["attempts"], job["id"]))
    return True


def main() -> None:
    start_http_server(settings.worker_metrics_port)
    writer = BriefWriter()
    http = httpx.Client(timeout=2.0)
    log.info("LLM worker chạy; LLM %s", "bật (" + writer.llm.model + ")" if writer.llm.enabled else "TẮT – dùng brief mẫu")
    last_reclaim = 0.0
    while True:
        if time.monotonic() - last_reclaim > 60:
            reclaim_stale()
            last_reclaim = time.monotonic()
        try:
            busy = run_once(writer, http)
        except Exception:  # noqa: BLE001 – DB tạm mất: chờ rồi thử lại
            log.exception("vòng lặp worker lỗi")
            busy = False
        if not busy:
            time.sleep(settings.worker_poll_s)


if __name__ == "__main__":
    main()

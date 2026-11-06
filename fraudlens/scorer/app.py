"""Scorer – tiến trình riêng, chỉ làm một việc: chấm điểm bằng model ACTIVE.

Chạy:  uvicorn fraudlens.scorer.app:app --port 8001
Đổi model: duyệt ở trang Model trên web (hoặc lệnh approve). Scorer tự kiểm tra file ACTIVE mỗi 2 giây và nạp model
mới, không cần khởi động lại. Chưa có model ACTIVE thì vẫn chạy nhưng trả 503 (API ghi giao dịch thành UNSCORED).
"""
from __future__ import annotations

import logging
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

from fraudlens.common.registry import active_version, load_json, root
from fraudlens.ml.model import FraudModel

app = FastAPI(title="FraudLens Scorer")
LAT = Histogram("scorer_latency_seconds", "Thời gian chấm điểm", ["endpoint"],
                buckets=(0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25))
ERR = Counter("scorer_errors_total", "Số lỗi chấm điểm", ["endpoint"])
_model: FraudModel | None = None
_state = {"key": None, "checked": 0.0, "error": None}
_lock = threading.Lock()
log = logging.getLogger("fraudlens.scorer")


class Req(BaseModel):
    features: dict


def _key() -> tuple:
    try:
        a = root() / "ACTIVE"
        return (a.read_text().strip(), a.stat().st_mtime_ns)
    except OSError:
        return (None,)


def refresh(force: bool = False) -> None:
    """Nạp (lại) model khi file ACTIVE đổi. Lỗi nạp giữ nguyên model cũ nếu cùng phiên bản, ngược lại bỏ model."""
    global _model
    if not force and time.monotonic() - _state["checked"] < 2.0:
        return
    with _lock:
        _state["checked"] = time.monotonic()
        k = _key()
        if k == _state["key"]:
            return
        _state["key"] = k
        try:
            v = active_version()
            if _model is None or _model.version != v:
                _model = FraudModel(root() / v, load_json(v, "manifest.json"))
                log.warning("đã nạp model %s", v)
            _state["error"] = None
        except Exception as e:  # noqa: BLE001
            _model, _state["error"] = None, str(e)
            log.error("không nạp được model ACTIVE: %s", e)


@app.on_event("startup")
def load_model() -> None:
    refresh(force=True)


def _require_model() -> FraudModel:
    refresh()
    if _model is None:
        raise HTTPException(503, f"model chưa nạp: {_state['error']}")
    return _model


@app.get("/health")
def health():
    refresh()
    if _model is None:
        raise HTTPException(503, f"model chưa nạp: {_state['error']}")
    return {"status": "ok", "model_version": _model.version, "feature_set_version": _model.manifest["feature_set_version"]}


@app.post("/score")
def score(req: Req):
    m = _require_model()
    t0 = time.perf_counter()
    try:
        X = m.frame([req.features])
        raw, cal = m.predict(X)
        return {"score_raw": float(raw[0]), "score": float(cal[0]), "model_version": m.version}
    except Exception as e:  # noqa: BLE001
        ERR.labels("score").inc()
        raise HTTPException(500, f"lỗi chấm điểm: {e}") from e
    finally:
        LAT.labels("score").observe(time.perf_counter() - t0)


@app.post("/explain")
def explain(req: Req):
    m = _require_model()
    t0 = time.perf_counter()
    try:
        X = m.frame([req.features])
        return {"factors": m.explain(X)[0], "model_version": m.version}
    except Exception as e:  # noqa: BLE001
        ERR.labels("explain").inc()
        raise HTTPException(500, f"lỗi giải thích: {e}") from e
    finally:
        LAT.labels("explain").observe(time.perf_counter() - t0)


@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

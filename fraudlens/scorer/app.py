"""Scorer – tiến trình riêng, chỉ làm một việc: chấm điểm bằng model ACTIVE.

Chạy:  uvicorn fraudlens.scorer.app:app --port 8001
Đổi model: chạy lệnh approve rồi khởi động lại tiến trình này.
"""
from __future__ import annotations

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


class Req(BaseModel):
    features: dict


@app.on_event("startup")
def load_model() -> None:
    global _model
    v = active_version()
    _model = FraudModel(root() / v, load_json(v, "manifest.json"))


@app.get("/health")
def health():
    if _model is None:
        raise HTTPException(503, "model chưa nạp")
    return {"status": "ok", "model_version": _model.version, "feature_set_version": _model.manifest["feature_set_version"]}


@app.post("/score")
def score(req: Req):
    t0 = time.perf_counter()
    try:
        X = _model.frame([req.features])
        raw, cal = _model.predict(X)
        return {"score_raw": float(raw[0]), "score": float(cal[0]), "model_version": _model.version}
    except Exception as e:  # noqa: BLE001
        ERR.labels("score").inc()
        raise HTTPException(500, f"lỗi chấm điểm: {e}") from e
    finally:
        LAT.labels("score").observe(time.perf_counter() - t0)


@app.post("/explain")
def explain(req: Req):
    t0 = time.perf_counter()
    try:
        X = _model.frame([req.features])
        return {"factors": _model.explain(X)[0], "model_version": _model.version}
    except Exception as e:  # noqa: BLE001
        ERR.labels("explain").inc()
        raise HTTPException(500, f"lỗi giải thích: {e}") from e
    finally:
        LAT.labels("explain").observe(time.perf_counter() - t0)


@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

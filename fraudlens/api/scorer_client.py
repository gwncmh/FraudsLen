"""Gọi Scorer với timeout và ngắt mạch. Mọi lỗi trả về trạng thái SCORING_FAILED (không bao giờ đoán điểm)."""
from __future__ import annotations

import httpx

from fraudlens.common.breaker import CircuitBreaker
from fraudlens.common.config import settings


class ScorerClient:
    def __init__(self, base_url: str | None = None):
        self.base = (base_url or settings.scorer_url).rstrip("/")
        self.http = httpx.Client(timeout=httpx.Timeout(settings.scorer_timeout_s, connect=settings.scorer_timeout_s))
        self.breaker = CircuitBreaker(settings.breaker_fail_threshold, settings.breaker_open_s)

    def score(self, features: dict, expected_model: str | None) -> dict:
        if not self.breaker.allow():
            return {"status": "SCORING_FAILED", "error": "CIRCUIT_OPEN"}
        try:
            r = self.http.post(f"{self.base}/score", json={"features": features})
            r.raise_for_status()
            d = r.json()
            if expected_model and d.get("model_version") != expected_model:
                # model đang chạy không khớp ngưỡng đã duyệt -> không dùng điểm này
                self.breaker.failure()
                return {"status": "SCORING_FAILED", "error": f"MODEL_MISMATCH:{d.get('model_version')}"}
            self.breaker.success()
            return {"status": "SCORED", "score": d["score"], "score_raw": d["score_raw"], "model_version": d["model_version"]}
        except Exception as e:  # noqa: BLE001 – timeout, kết nối, HTTP 5xx...
            self.breaker.failure()
            return {"status": "SCORING_FAILED", "error": type(e).__name__}

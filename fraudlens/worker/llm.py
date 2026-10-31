"""Gọi LLM qua API tương thích OpenAI (mặc định Gemini Flash-Lite). Đổi nhà cung cấp chỉ cần đổi .env."""
from __future__ import annotations

import threading
import time

import httpx

from fraudlens.common.config import settings


class LLMUnavailable(Exception):
    pass


class LLMClient:
    def __init__(self):
        self.enabled = bool(settings.llm_api_key)
        self.base = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self.http = httpx.Client(timeout=settings.llm_timeout_s)
        self._last = 0.0
        self._lock = threading.Lock()

    def _throttle(self):
        # gói miễn phí giới hạn số lần gọi mỗi phút -> giãn cách tối thiểu giữa hai lần gọi
        with self._lock:
            wait = settings.llm_min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def chat_json(self, system: str, user: str) -> tuple[str, int]:
        if not self.enabled:
            raise LLMUnavailable("chưa cấu hình LLM_API_KEY")
        self._throttle()
        try:
            r = self.http.post(f"{self.base}/chat/completions",
                               headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                               json={"model": self.model, "temperature": 0,
                                     "response_format": {"type": "json_object"},
                                     "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            r.raise_for_status()
            d = r.json()
            return d["choices"][0]["message"]["content"], int((d.get("usage") or {}).get("total_tokens") or 0)
        except Exception as e:  # noqa: BLE001
            raise LLMUnavailable(f"{type(e).__name__}: {e}") from e

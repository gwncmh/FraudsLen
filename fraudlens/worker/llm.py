"""Gọi LLM qua API tương thích OpenAI (mặc định Qwen 2.5 7B chạy trên máy bằng Ollama – miễn phí). Đổi nhà cung cấp
chỉ cần đổi .env.

Qwen3 là model "lai" có chế độ suy nghĩ (thinking): DashScope bắt buộc tắt khi gọi không streaming, nên request tới
DashScope gửi kèm enable_thinking=false (các nhà cung cấp khác không nhận tham số này nên không gửi). Bản Qwen3 chạy
qua Ollama/vLLM có thể vẫn trả khối <think>…</think> trước JSON: khối đó được bỏ đi trước khi đưa cho guardrail.

Lỗi TẠM THỜI của nhà cung cấp (429 hết hạn mức, 5xx quá tải, quá thời gian chờ, mất kết nối) được thử lại
LLM_TRANSIENT_RETRIES lần, chờ LLM_RETRY_BACKOFF_S giây rồi gấp đôi (theo Retry-After nếu máy chủ gửi, tối đa
LLM_RETRY_MAX_WAIT_S). Lỗi CỐ ĐỊNH (400/401/403/404: sai key, sai tên model...) báo ngay, không thử lại.
Tất cả chạy ở làn chậm (worker), không ảnh hưởng chấm điểm và phân luồng.
"""
from __future__ import annotations

import os
import re
import threading
import time

import httpx

from fraudlens.common.config import settings

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
THINK_RE = re.compile(r"^\s*<think>.*?</think>\s*", re.S)


def local_base(url: str, in_container: bool | None = None) -> str:
    """.env trỏ Ollama bằng host.docker.internal để container gọi ra máy; lệnh chạy thẳng trên máy (admin test-llm...)
    không phân giải được tên đó -> đổi sang localhost, khỏi phải sửa .env qua lại."""
    if in_container is None:
        in_container = os.path.exists("/.dockerenv")
    return url if in_container else url.replace("://host.docker.internal", "://localhost")


def strip_think(text: str) -> str:
    """Bỏ khối suy nghĩ <think>…</think> ở đầu câu trả lời (Qwen3 / model suy luận chạy local)."""
    return THINK_RE.sub("", text or "", count=1)


class LLMUnavailable(Exception):
    pass


def _is_transient(e: Exception) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code in TRANSIENT_STATUS
    return isinstance(e, (httpx.TimeoutException, httpx.TransportError))


def _retry_after(e: Exception) -> float | None:
    """Số giây máy chủ yêu cầu chờ (header Retry-After), nếu có."""
    if isinstance(e, httpx.HTTPStatusError):
        try:
            return float(e.response.headers.get("retry-after", ""))
        except ValueError:
            return None
    return None


class LLMClient:
    def __init__(self, sleep=time.sleep):
        self.enabled = bool(settings.llm_api_key)
        self.base = local_base(settings.llm_base_url.rstrip("/"))
        self.model = settings.llm_model
        self.dashscope = "dashscope" in self.base  # Qwen trên Alibaba Cloud (mọi vùng: -intl, Bắc Kinh...)
        self.http = httpx.Client(timeout=settings.llm_timeout_s)
        self._last = 0.0
        self._lock = threading.Lock()
        self._sleep = sleep  # thay được trong test

    def _throttle(self):
        # gói miễn phí giới hạn số lần gọi mỗi phút -> giãn cách tối thiểu giữa hai lần gọi
        with self._lock:
            wait = settings.llm_min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()

    def _call(self, system: str, user: str) -> tuple[str, int]:
        self._throttle()
        body = {"model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.dashscope:
            body["enable_thinking"] = False
        r = self.http.post(f"{self.base}/chat/completions", headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                           json=body)
        r.raise_for_status()
        d = r.json()
        return strip_think(d["choices"][0]["message"]["content"]), int((d.get("usage") or {}).get("total_tokens") or 0)

    def chat_json(self, system: str, user: str) -> tuple[str, int]:
        if not self.enabled:
            raise LLMUnavailable("chưa cấu hình LLM_API_KEY")
        tries = 1 + max(0, settings.llm_transient_retries)
        wait = settings.llm_retry_backoff_s
        for i in range(tries):
            try:
                return self._call(system, user)
            except Exception as e:  # noqa: BLE001
                transient = _is_transient(e)
                if not transient or i == tries - 1:
                    note = f" (đã thử {i + 1} lần)" if transient and i else ""
                    raise LLMUnavailable(f"{type(e).__name__}: {e}{note}") from e
                ra = _retry_after(e)
                self._sleep(min(settings.llm_retry_max_wait_s, ra if ra is not None else wait))
                wait *= 2
        raise LLMUnavailable("không gọi được LLM")  # không tới được: vòng lặp luôn return hoặc raise

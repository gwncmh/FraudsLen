"""Cấu hình đọc từ biến môi trường (file .env khi chạy local, environment khi chạy Docker).

Mọi tham số hiện thực (timeout, ngưỡng ngắt mạch, model LLM...) đều nằm ở đây để đổi không cần sửa code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # .env chỉ dùng khi chạy local; trong Docker biến môi trường do compose truyền vào
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


@dataclass(frozen=True)
class Settings:
    # Kết nối
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", "postgresql://fraudlens:fraudlens@localhost:5432/fraudlens"))
    redis_url: str = field(default_factory=lambda: _env("REDIS_URL", "redis://localhost:6379/0"))
    scorer_url: str = field(default_factory=lambda: _env("SCORER_URL", "http://localhost:8001"))
    registry_dir: Path = field(default_factory=lambda: Path(_env("REGISTRY_DIR", "registry")))
    playbook_path: Path = field(default_factory=lambda: Path(_env("PLAYBOOK_PATH", "kb/playbook.md")))

    # Bảo vệ dữ liệu
    hmac_key: str = field(default_factory=lambda: _env("HMAC_KEY", ""))
    source_api_key: str = field(default_factory=lambda: _env("SOURCE_API_KEY", "dev-source-key"))
    # Tài khoản web do Admin cấp (không tự đăng ký, không token tĩnh); thời hạn phiên đăng nhập
    session_ttl_h: int = field(default_factory=lambda: _int("SESSION_TTL_H", 168))
    # Thư mục chứa fraudTrain.csv / fraudTest.csv (simulator và nạp lịch sử chạy từ giao diện web)
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "data")))
    # Địa chỉ để trang "Hệ thống" kiểm tra worker / monitor còn chạy (endpoint metrics Prometheus)
    worker_metrics_url: str = field(default_factory=lambda: _env("WORKER_METRICS_URL", "http://localhost:9104/metrics"))
    monitor_metrics_url: str = field(default_factory=lambda: _env("MONITOR_METRICS_URL", "http://localhost:9105/metrics"))
    # API gọi chính nó khi chạy simulator từ web
    self_api_url: str = field(default_factory=lambda: _env("SELF_API_URL", "http://localhost:8000"))

    # Làn nhanh
    scorer_timeout_s: float = field(default_factory=lambda: _float("SCORER_TIMEOUT_S", 0.15))
    breaker_fail_threshold: int = field(default_factory=lambda: _int("BREAKER_FAIL_THRESHOLD", 5))
    breaker_open_s: float = field(default_factory=lambda: _float("BREAKER_OPEN_S", 10.0))
    history_window_days: int = field(default_factory=lambda: _int("HISTORY_WINDOW_DAYS", 30))
    card_lock_ttl_s: float = field(default_factory=lambda: _float("CARD_LOCK_TTL_S", 2.0))
    card_lock_wait_s: float = field(default_factory=lambda: _float("CARD_LOCK_WAIT_S", 0.5))
    # Kiểm tra độ tươi theo thời gian của luồng (giao dịch mới nhất đã thấy), không theo đồng hồ máy.
    # MAX_LATE_S không nên vượt 1 ngày: Redis chỉ giữ lịch sử 30 ngày + 1 ngày đệm tính từ giao dịch mới nhất của thẻ.
    max_future_s: int = field(default_factory=lambda: _int("MAX_FUTURE_S", 86400))
    max_late_s: int = field(default_factory=lambda: _int("MAX_LATE_S", 86400))

    # Làn chậm
    # Mặc định: Qwen 2.5 7B chạy trên máy bằng Ollama (miễn phí) qua API tương thích OpenAI. LLM_API_KEY vẫn phải
    # khác rỗng (Ollama bỏ qua giá trị) – để trống nghĩa là tắt LLM, worker dùng brief mẫu.
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", "http://host.docker.internal:11434/v1"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", ""))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "qwen2.5:7b"))
    llm_timeout_s: float = field(default_factory=lambda: _float("LLM_TIMEOUT_S", 180.0))  # model chạy CPU: chậm
    llm_retries: int = field(default_factory=lambda: _int("LLM_RETRIES", 1))  # sinh lại khi brief không qua guardrail
    # Lỗi tạm thời của nhà cung cấp LLM (429, 5xx, timeout): thử lại N lần, chờ backoff giây rồi gấp đôi
    llm_transient_retries: int = field(default_factory=lambda: _int("LLM_TRANSIENT_RETRIES", 2))
    llm_retry_backoff_s: float = field(default_factory=lambda: _float("LLM_RETRY_BACKOFF_S", 5.0))
    llm_retry_max_wait_s: float = field(default_factory=lambda: _float("LLM_RETRY_MAX_WAIT_S", 30.0))
    llm_min_interval_s: float = field(default_factory=lambda: _float("LLM_MIN_INTERVAL_S", 0.0))
    kb_version: str = field(default_factory=lambda: _env("KB_VERSION", "draft-0"))
    worker_metrics_port: int = field(default_factory=lambda: _int("WORKER_METRICS_PORT", 9104))
    retrieval_k: int = field(default_factory=lambda: _int("RETRIEVAL_K", 5))
    worker_poll_s: float = field(default_factory=lambda: _float("WORKER_POLL_S", 1.0))
    job_stale_s: int = field(default_factory=lambda: _int("JOB_STALE_S", 300))
    # Tỉ lệ hồ sơ thuộc nhóm đối chứng không hiển thị brief (đo giá trị của brief); 0 = luôn hiển thị
    brief_holdout_frac: float = field(default_factory=lambda: _float("BRIEF_HOLDOUT_FRAC", 0.0))

    # Giám sát
    monitor_interval_s: int = field(default_factory=lambda: _int("MONITOR_INTERVAL_S", 300))
    psi_warn: float = field(default_factory=lambda: _float("PSI_WARN", 0.1))
    psi_alert: float = field(default_factory=lambda: _float("PSI_ALERT", 0.2))
    monitor_rate_window: int = field(default_factory=lambda: _int("MONITOR_RATE_WINDOW", 5000))
    unscored_warn: float = field(default_factory=lambda: _float("UNSCORED_WARN", 0.03))
    unscored_alert: float = field(default_factory=lambda: _float("UNSCORED_ALERT", 0.10))
    min_label_frauds: int = field(default_factory=lambda: _int("MIN_LABEL_FRAUDS", 30))

    def require_hmac(self) -> bytes:
        if len(self.hmac_key) < 16:
            raise RuntimeError("HMAC_KEY chưa đặt hoặc quá ngắn (>= 16 ký tự). Xem .env.example.")
        return self.hmac_key.encode()


settings = Settings()

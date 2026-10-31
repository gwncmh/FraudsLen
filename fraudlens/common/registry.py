"""Model registry dạng thư mục: mỗi phiên bản model một thư mục, không ghi đè.

registry/
  ACTIVE                         <- tên phiên bản đang dùng (chỉ đổi qua lệnh approve)
  <version>/model.json           <- XGBoost
  <version>/manifest.json        <- feature, category, sha256, hiệu chỉnh Platt, số đo kiểm định
  <version>/routing_config.json  <- t_medium, t_high, chính sách, người duyệt
  <version>/reference.json       <- phân bố tham chiếu cho giám sát PSI
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fraudlens.common.config import settings


def root() -> Path:
    return settings.registry_dir


def active_version() -> str:
    p = root() / "ACTIVE"
    if not p.exists():
        raise RuntimeError(f"Chưa có model ACTIVE trong {root()}. Chạy huấn luyện rồi lệnh approve (xem README).")
    return p.read_text().strip()


def load_json(version: str, name: str) -> dict:
    return json.loads((root() / version / name).read_text(encoding="utf-8"))


def save_json(version: str, name: str, obj: dict) -> None:
    d = root() / version
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_active_routing() -> dict:
    """Cấu hình ngưỡng của model ACTIVE. Từ chối dùng nếu chưa được duyệt hoặc không khớp model."""
    v = active_version()
    rc = load_json(v, "routing_config.json")
    if rc.get("model_version") != v:
        raise RuntimeError("routing_config không khớp model ACTIVE")
    if not rc.get("approved_by"):
        raise RuntimeError("routing_config chưa được Risk Lead duyệt")
    return rc

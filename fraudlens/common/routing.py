"""Phân luồng an toàn khi lỗi (fail-safe).

Chỉ có MỘT nhánh dẫn tới LOW: đã chấm được (SCORED) và điểm < t_medium.
Mọi trạng thái khác (dữ liệu lỗi, model lỗi, timeout, lỗi không lường trước) đều thành UNSCORED,
tức là tạo hồ sơ cho người xem.
"""
from __future__ import annotations

import math

LOW, MEDIUM, HIGH, UNSCORED = "LOW", "MEDIUM", "HIGH", "UNSCORED"
CASE_ROUTES = {MEDIUM, HIGH, UNSCORED}


def route(status: str, score: float | None, t_medium: float, t_high: float) -> str:
    try:
        if status != "SCORED" or score is None:
            return UNSCORED
        s = float(score)
        if not math.isfinite(s) or not (0.0 <= s <= 1.0):
            return UNSCORED
        if s >= t_high:
            return HIGH
        if s >= t_medium:
            return MEDIUM
        return LOW
    except Exception:  # noqa: BLE001 – bất kỳ lỗi nào cũng về phía an toàn
        return UNSCORED

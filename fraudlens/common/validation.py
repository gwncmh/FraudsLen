"""Kiểm tra payload giao dịch thô trước khi làm bất cứ điều gì khác (FR-01).

Viết tay để kiểm soát chặt kiểu dữ liệu: không tự ép kiểu "dễ dãi" (ví dụ true -> 1).
Trả về danh sách mã lỗi; rỗng nghĩa là hợp lệ.
"""
from __future__ import annotations

from datetime import datetime

REQUIRED = ["trans_num", "trans_date_trans_time", "cc_num", "merchant", "category", "amt",
            "lat", "long", "merch_lat", "merch_long", "city_pop", "dob"]
MAX_AMT = 1_000_000.0


def _is_number(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return v == v  # loại NaN
    if isinstance(v, str):
        try:
            float(v)
            return v.strip().lower() not in ("nan", "inf", "-inf", "")
        except ValueError:
            return False
    return False


def validate(raw: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return ["PAYLOAD_NOT_OBJECT"]
    for k in REQUIRED:
        if k not in raw or raw[k] is None or (isinstance(raw[k], str) and not raw[k].strip()):
            errors.append(f"MISSING:{k}")
    if errors:
        return errors

    cc = str(raw["cc_num"]).strip()
    if not cc.isdigit() or not (8 <= len(cc) <= 19):
        errors.append("BAD:cc_num")
    if not isinstance(raw["trans_num"], str) or len(raw["trans_num"]) > 64:
        errors.append("BAD:trans_num")
    for k in ("merchant", "category"):
        if not isinstance(raw[k], str) or len(raw[k]) > 128:
            errors.append(f"BAD:{k}")
    try:
        datetime.strptime(str(raw["trans_date_trans_time"]).strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        errors.append("BAD:trans_date_trans_time")
    try:
        datetime.strptime(str(raw["dob"]).strip()[:10], "%Y-%m-%d")
    except ValueError:
        errors.append("BAD:dob")

    if not _is_number(raw["amt"]):
        errors.append("BAD_TYPE:amt")
    elif not (0 < float(raw["amt"]) <= MAX_AMT):
        errors.append("OUT_OF_RANGE:amt")
    for k, lo, hi in (("lat", -90, 90), ("merch_lat", -90, 90), ("long", -180, 180), ("merch_long", -180, 180)):
        if not _is_number(raw[k]):
            errors.append(f"BAD_TYPE:{k}")
        elif not (lo <= float(raw[k]) <= hi):
            errors.append(f"OUT_OF_RANGE:{k}")
    if not _is_number(raw["city_pop"]):
        errors.append("BAD_TYPE:city_pop")
    elif float(raw["city_pop"]) < 0:
        errors.append("OUT_OF_RANGE:city_pop")
    return errors

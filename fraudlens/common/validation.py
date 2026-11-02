"""Kiểm tra payload giao dịch thô trước khi làm bất cứ điều gì khác (FR-01).

Viết tay để kiểm soát chặt kiểu dữ liệu: không tự ép kiểu "dễ dãi" (ví dụ true -> 1).
Ngoài schema và khoảng giá trị còn kiểm tra NGỮ NGHĨA (Lec 4):
- tuổi chủ thẻ tại thời điểm giao dịch phải trong [0, 120] (dob sau ngày giao dịch là lỗi);
- category phải thuộc danh sách lúc huấn luyện ("cùng schema, khác nghĩa": category lạ không được lặng lẽ thành NaN);
- độ tươi: event_ts không quá xa thời điểm hiện tại của luồng (giao dịch mới nhất đã thấy) – quá trễ thì lịch sử
  30 ngày trong Redis đã bị dọn một phần, feature không còn đúng; ở "tương lai" thì nghi sai đồng hồ nguồn.
Trả về danh sách mã lỗi; rỗng nghĩa là hợp lệ.
"""
from __future__ import annotations

from datetime import datetime, timezone

REQUIRED = ["trans_num", "trans_date_trans_time", "cc_num", "merchant", "category", "amt",
            "lat", "long", "merch_lat", "merch_long", "city_pop", "dob"]
MAX_AMT = 1_000_000.0
MAX_AGE = 120


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


def validate(raw: dict, known_categories: set[str] | None = None, stream_ts: int | None = None,
             max_future_s: int = 86400, max_late_s: int = 86400) -> list[str]:
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
    ts = dob = None
    try:
        ts = datetime.strptime(str(raw["trans_date_trans_time"]).strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        errors.append("BAD:trans_date_trans_time")
    try:
        dob = datetime.strptime(str(raw["dob"]).strip()[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        errors.append("BAD:dob")
    if ts is not None and dob is not None:
        age = (ts - dob).days / 365.25
        if not (0 <= age <= MAX_AGE):
            errors.append("OUT_OF_RANGE:dob")
    if ts is not None and stream_ts is not None:
        t = int(ts.timestamp())
        if t > stream_ts + max_future_s:
            errors.append("FUTURE:event_ts")
        elif t < stream_ts - max_late_s:
            errors.append("STALE:event_ts")
    if known_categories is not None and isinstance(raw["category"], str) and raw["category"].strip() not in known_categories:
        errors.append("UNKNOWN:category")

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

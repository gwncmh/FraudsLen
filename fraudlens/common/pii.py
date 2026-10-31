"""Ranh giới PII: chạy NGAY khi giao dịch vào hệ thống (API) và khi xử lý dữ liệu offline.

Sau bước này không thành phần nào thấy số thẻ thật, tên, địa chỉ, ngày sinh hay tọa độ.
Cùng một hàm dùng cho offline (huấn luyện) và online (API) nên kết quả giống hệt nhau.
"""
from __future__ import annotations

import hashlib
import hmac
import math
from datetime import datetime, timezone

TIME_FMT = "%Y-%m-%d %H:%M:%S"


def card_hash(cc_num: str, key: bytes) -> str:
    """Băm có khóa (HMAC-SHA256). Không có khóa thì không dò ngược được số thẻ."""
    return hmac.new(key, str(cc_num).strip().encode(), hashlib.sha256).hexdigest()


def last4(cc_num: str) -> str:
    s = str(cc_num).strip()
    return s[-4:] if len(s) >= 4 else s


def parse_event_ts(text: str) -> int:
    """Thời gian sự kiện của Sparkov ('2019-01-01 00:00:18'), coi là UTC -> epoch giây."""
    return int(datetime.strptime(str(text).strip(), TIME_FMT).replace(tzinfo=timezone.utc).timestamp())


def age_years(dob: str, event_ts: int) -> float:
    """Tuổi tại thời điểm giao dịch (thay cho ngày sinh)."""
    d = datetime.strptime(str(dob).strip()[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    t = datetime.fromtimestamp(event_ts, tz=timezone.utc)
    return round((t - d).days / 365.25, 2)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Khoảng cách chủ thẻ – merchant (thay cho tọa độ)."""
    r = 6371.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = p2 - p1
    dl = math.radians(float(lon2) - float(lon1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(min(1.0, math.sqrt(a))), 3)


def minimize(raw: dict, key: bytes) -> dict:
    """Chuyển payload thô (đã qua validator) thành bản ghi đã tối thiểu hóa dùng trong hệ thống."""
    ts = parse_event_ts(raw["trans_date_trans_time"])
    return {
        "trans_num": str(raw["trans_num"]).strip(),
        "event_ts": ts,
        "card_hash": card_hash(raw["cc_num"], key),
        "card_last4": last4(raw["cc_num"]),
        "merchant": str(raw["merchant"]).strip(),
        "category": str(raw["category"]).strip(),
        "amt": float(raw["amt"]),
        "city_pop": int(float(raw["city_pop"])),
        "state": str(raw.get("state", "") or "")[:2],
        "age": age_years(raw["dob"], ts),
        "distance_km": haversine_km(raw["lat"], raw["long"], raw["merch_lat"], raw["merch_long"]),
    }

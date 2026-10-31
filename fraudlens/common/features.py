"""Tính feature – MỘT hàm dùng chung cho huấn luyện (offline) và chạy thật (API).

compute(txn, history) là hàm thuần: không đọc đồng hồ máy, không I/O. Thời gian luôn là thời gian
của giao dịch (event_ts), nên khi dữ liệu đến trễ hoặc phát lại thì feature vẫn đúng.

Quy ước cửa sổ: [t - w, t), chỉ dùng giao dịch TRƯỚC giao dịch hiện tại của CÙNG thẻ.
Giao dịch trùng thời điểm được sắp theo (event_ts, trans_num).
"""
from __future__ import annotations

import math
from collections import deque
from typing import Iterable

import numpy as np
import pandas as pd

H, D = 3600, 86400
WINDOW_S = 30 * D
NO_PREV_SECS = float(WINDOW_S)
FEATURE_SET_VERSION = "fs-v1"

FEATURES = [
    # giao dịch hiện tại
    "amt", "log_amt", "category", "hour", "dow", "is_night", "age", "distance_km", "log_city_pop",
    # tần suất
    "n_txn_1h", "n_txn_24h", "n_txn_7d", "sum_amt_24h",
    # bất thường so với thói quen 30 ngày
    "n_txn_30d", "amt_mean_30d", "amt_ratio_30d", "amt_zscore_30d", "secs_since_last",
    # mới lạ
    "is_new_category_30d", "is_new_merchant_30d", "n_distinct_cat_24h",
]
CATEGORICAL = ["category"]
FEATURE_TYPES = ["c" if f in CATEGORICAL else "q" for f in FEATURES]

# Bản ghi lịch sử lưu trong Redis/offline: (event_ts, trans_num, amt, category, merchant)
HistRecord = tuple


def record_of(txn: dict) -> HistRecord:
    return (int(txn["event_ts"]), str(txn["trans_num"]), float(txn["amt"]), str(txn["category"]), str(txn["merchant"]))


def compute(txn: dict, history: Iterable[HistRecord]) -> dict:
    t = int(txn["event_ts"])
    tn = str(txn["trans_num"])
    amt = float(txn["amt"])
    cat = str(txn["category"])
    lo = t - WINDOW_S
    n1h = n24 = n7d = n30 = 0
    s24 = s30 = ss30 = 0.0
    last_ts = None
    cats30, merch30, cats24 = set(), set(), set()
    for (ht, htn, hamt, hcat, hmer) in history:
        ht = int(ht)
        # lọc phòng thủ: bỏ mọi bản ghi không nằm hẳn trước giao dịch hiện tại
        if ht < lo or ht > t or (ht == t and str(htn) >= tn):
            continue
        hamt = float(hamt)
        dt = t - ht
        n30 += 1
        s30 += hamt
        ss30 += hamt * hamt
        cats30.add(hcat)
        merch30.add(hmer)
        if dt <= 7 * D:
            n7d += 1
            if dt <= D:
                n24 += 1
                s24 += hamt
                cats24.add(hcat)
                if dt <= H:
                    n1h += 1
        if last_ts is None or ht > last_ts:
            last_ts = ht
    hour, dow = int((t // H) % 24), int((t // D + 3) % 7)  # 1970-01-01 là thứ Năm -> Thứ Hai = 0
    mean30 = s30 / n30 if n30 else 0.0
    if n30 >= 2:
        sd = math.sqrt(max(0.0, (ss30 - n30 * mean30 * mean30) / (n30 - 1)))
        z = (amt - mean30) / sd if sd > 1e-9 else 0.0
    else:
        z = 0.0
    return {
        "amt": amt,
        "log_amt": math.log1p(max(amt, 0.0)),
        "category": cat,
        "hour": hour,
        "dow": dow,
        "is_night": int(hour >= 22 or hour < 6),
        "age": float(txn["age"]),
        "distance_km": float(txn["distance_km"]),
        "log_city_pop": math.log1p(max(float(txn["city_pop"]), 0.0)),
        "n_txn_1h": n1h, "n_txn_24h": n24, "n_txn_7d": n7d, "sum_amt_24h": round(s24, 6),
        "n_txn_30d": n30,
        "amt_mean_30d": round(mean30, 6),
        "amt_ratio_30d": round(amt / mean30, 6) if mean30 > 0 else 1.0,
        "amt_zscore_30d": round(max(-50.0, min(50.0, z)), 6),
        "secs_since_last": float(t - last_ts) if last_ts is not None else NO_PREV_SECS,
        "is_new_category_30d": int(n30 > 0 and cat not in cats30),
        "is_new_merchant_30d": int(n30 > 0 and str(txn["merchant"]) not in merch30),
        "n_distinct_cat_24h": len(cats24),
    }


def build_offline(df: pd.DataFrame) -> pd.DataFrame:
    """Tính feature cho cả bảng bằng ĐÚNG hàm compute(), duyệt từng thẻ theo thời gian.
    df cần các cột: card_hash, event_ts, trans_num, amt, category, merchant, age, distance_km, city_pop.
    Trả về DataFrame cùng index với df."""
    order = df.sort_values(["event_ts", "trans_num"], kind="mergesort")
    cols = ["event_ts", "trans_num", "amt", "category", "merchant", "age", "distance_km", "city_pop"]
    idx_out, rows_out = [], []
    for _, g in order.groupby("card_hash", sort=False):
        q: deque = deque()
        for idx, row in zip(g.index, g[cols].itertuples(index=False, name=None)):
            txn = dict(zip(cols, row))
            t = int(txn["event_ts"])
            while q and q[0][0] < t - WINDOW_S:
                q.popleft()
            f = compute(txn, q)
            rows_out.append([f[c] for c in FEATURES])
            idx_out.append(idx)
            q.append(record_of(txn))
    out = pd.DataFrame(rows_out, index=idx_out, columns=FEATURES).loc[df.index]
    return out


def to_model_frame(rows: list[dict] | pd.DataFrame, categories: list[str]) -> np.ndarray:
    """Đưa feature về ma trận float32 đúng thứ tự cột model cần.

    Cột category được mã hóa thành số thứ tự trong danh sách `categories` lúc huấn luyện (category lạ -> NaN,
    XGBoost coi là thiếu) và khai báo với XGBoost là cột phân loại qua FEATURE_TYPES. Không dùng kiểu
    pandas Categorical để không phụ thuộc phiên bản pandas/XGBoost.
    """
    X = pd.DataFrame(rows) if not isinstance(rows, pd.DataFrame) else rows
    index = {c: i for i, c in enumerate(categories)}
    out = np.empty((len(X), len(FEATURES)), dtype=np.float32)
    for j, c in enumerate(FEATURES):
        if c in CATEGORICAL:
            col = X[c].astype(object).map(lambda v: index.get(str(v), np.nan))
        else:
            col = pd.to_numeric(X[c], errors="coerce")
        out[:, j] = np.asarray(col, dtype=np.float64)
    return out


def category_name(code: float, categories: list[str]) -> str | None:
    return None if code is None or math.isnan(code) else categories[int(code)]

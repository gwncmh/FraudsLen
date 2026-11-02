"""Behavioral testing cho model (Lec 5): kiểm tra HÀNH VI chứ không chỉ số đo tổng hợp.

- Invariance: đổi mã giao dịch, đổi tên merchant (giữ nguyên trạng thái mới/cũ của merchant với thẻ) -> điểm không đổi.
- Directional: số tiền tăng mạnh so với thói quen, hoặc thêm một đợt giao dịch dồn dập -> điểm không được giảm.
- MFT (minimum functionality): vài giao dịch "rõ ràng" phải vào MEDIUM hoặc HIGH.

predict(rows) nhận danh sách dict feature, trả về điểm đã hiệu chỉnh – dùng được cho cả model đang huấn luyện
lẫn FraudModel trong registry.
"""
from __future__ import annotations

import math
import random
from typing import Callable

import numpy as np
import pandas as pd

from fraudlens.common.features import compute, record_of

Predict = Callable[[list[dict]], np.ndarray]
D = 86400


def _txn(ts, tn, amt, cat, mer):
    return {"event_ts": ts, "trans_num": tn, "amt": amt, "category": cat, "merchant": mer, "age": 41.0,
            "distance_km": 60.0, "city_pop": 5000}


def invariance(predict: Predict, categories: list[str], n_cards: int = 50, seed: int = 0) -> dict:
    """Dựng lịch sử thẻ tổng hợp, tính feature bằng compute() thật, rồi đổi trans_num + tên merchant nhất quán."""
    rng = random.Random(seed)
    base, renamed = [], []
    for c in range(n_cards):
        t0 = 1_590_000_000 + c * 7 * D
        merchants = [f"m{c}_{i}" for i in range(4)]
        hist = [_txn(t0 + i * 3 * 3600, f"c{c}t{i:03d}", round(rng.uniform(5, 200), 2), rng.choice(categories),
                     rng.choice(merchants)) for i in range(rng.randint(0, 25))]
        cur = _txn(t0 + 5 * D, f"c{c}cur", round(rng.uniform(5, 1200), 2), rng.choice(categories),
                   rng.choice(merchants + ["m_new"]))
        rename = {m: f"Ten khac {m}" for m in merchants + ["m_new"]}
        hist2 = [{**h, "merchant": rename[h["merchant"]], "trans_num": "X" + h["trans_num"]} for h in hist]
        cur2 = {**cur, "merchant": rename[cur["merchant"]], "trans_num": "X" + cur["trans_num"]}
        base.append(compute(cur, [record_of(h) for h in hist]))
        renamed.append(compute(cur2, [record_of(h) for h in hist2]))
    a, b = predict(base), predict(renamed)
    diff = float(np.max(np.abs(a - b)))
    return {"n": n_cards, "max_abs_diff": diff, "passed": diff == 0.0}


def _amount_spike(f: dict, factor: float) -> dict:
    g = dict(f)
    amt = f["amt"] * factor
    g["amt"], g["log_amt"] = amt, math.log1p(amt)
    mean = f["amt_mean_30d"]
    if mean > 0:
        g["amt_ratio_30d"] = amt / mean
    z = f["amt_zscore_30d"]
    if z and abs(f["amt"] - mean) > 1e-9:
        sd = (f["amt"] - mean) / z
        g["amt_zscore_30d"] = max(-50.0, min(50.0, (amt - mean) / sd))
    return g


def _burst(f: dict, extra: int) -> dict:
    """Thêm `extra` giao dịch trong 1 giờ trước đó, số tiền bằng mức trung bình (không đổi thói quen số tiền)."""
    g = dict(f)
    mean = f["amt_mean_30d"] if f["n_txn_30d"] else f["amt"]
    for k in ("n_txn_1h", "n_txn_24h", "n_txn_7d", "n_txn_30d"):
        g[k] = f[k] + extra
    g["sum_amt_24h"] = f["sum_amt_24h"] + extra * mean
    g["secs_since_last"] = min(f["secs_since_last"], 600.0)
    if not f["n_txn_30d"]:
        g["amt_mean_30d"], g["amt_ratio_30d"] = mean, 1.0
    return g


def directional(predict: Predict, rows: list[dict], t_medium: float, rel_tol: float = 0.05) -> dict:
    """violation_rate: điểm sau nhiễu < điểm gốc × (1 - rel_tol) (báo cáo).
    downgrade_rate: trong các giao dịch đang bị gắn cờ (≥ t_medium), tỉ lệ bị nhiễu đẩy xuống LOW –
    vi phạm có ảnh hưởng tới phân luồng, dùng cho release gate. Muốn bảo đảm cứng: huấn luyện với --monotone."""
    base = predict(rows)
    out = {}
    for name, fn, keep in (
        ("amount_x3_vs_habit", lambda f: _amount_spike(f, 3.0), lambda f: f["n_txn_30d"] >= 3),
        ("burst_+5_in_1h", lambda f: _burst(f, 5), lambda f: True),
    ):
        idx = [i for i, f in enumerate(rows) if keep(f)]
        if not idx:
            out[name] = {"n": 0, "violation_rate": float("nan"), "n_flagged": 0, "downgrade_rate": 0.0}
            continue
        new = predict([fn(rows[i]) for i in idx])
        old = base[idx]
        bad = new < old * (1 - rel_tol) - 1e-9
        was_flagged = old >= t_medium
        down = was_flagged & (new < t_medium)
        out[name] = {"n": len(idx), "violation_rate": float(bad.mean()),
                     "n_flagged": int(was_flagged.sum()),
                     "downgrade_rate": float(down.sum() / was_flagged.sum()) if was_flagged.any() else 0.0,
                     "median_ratio_new_old": float(np.median(new / np.maximum(old, 1e-12)))}
    return out


def _obvious(cat: str, amt: float, hour: int, mean: float) -> dict:
    return {"amt": amt, "log_amt": math.log1p(amt), "category": cat, "hour": hour, "dow": 5,
            "is_night": int(hour >= 22 or hour < 6), "age": 45.0, "distance_km": 80.0, "log_city_pop": math.log1p(5000),
            "n_txn_1h": 3, "n_txn_24h": 8, "n_txn_7d": 14, "sum_amt_24h": round(3 * amt, 2),
            "n_txn_30d": 40, "amt_mean_30d": mean, "amt_ratio_30d": round(amt / mean, 4), "amt_zscore_30d": 20.0,
            "secs_since_last": 900.0, "is_new_category_30d": 1, "is_new_merchant_30d": 1, "n_distinct_cat_24h": 4}


MFT_CASES = {
    "night_shopping_net_large_burst": _obvious("shopping_net", 980.0, 23, 55.0),
    "night_misc_net_large_burst": _obvious("misc_net", 850.0, 1, 50.0),
    "night_grocery_pos_spike_burst": _obvious("grocery_pos", 320.0, 22, 40.0),
}


def mft(predict: Predict, t_medium: float) -> dict:
    names = list(MFT_CASES)
    s = predict([MFT_CASES[n] for n in names])
    cases = {n: {"score": float(v), "flagged": bool(v >= t_medium)} for n, v in zip(names, s)}
    return {"cases": cases, "passed": all(c["flagged"] for c in cases.values())}


def run_all(predict: Predict, categories: list[str], F_sample: pd.DataFrame, t_medium: float,
            max_downgrade_rate: float = 0.05) -> dict:
    rows = F_sample.to_dict("records")
    inv = invariance(predict, categories)
    dirn = directional(predict, rows, t_medium)
    m = mft(predict, t_medium)
    dir_ok = directional_ok(dirn, max_downgrade_rate)
    return {"invariance": inv, "directional": dirn, "directional_max_downgrade_rate": max_downgrade_rate,
            "directional_passed": dir_ok, "mft": m, "passed": bool(inv["passed"] and dir_ok and m["passed"])}


def directional_ok(dirn: dict, max_downgrade_rate: float) -> bool:
    return all(v["downgrade_rate"] <= max_downgrade_rate for v in dirn.values())

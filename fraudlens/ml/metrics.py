"""Số đo đánh giá: Recall@Top1%, PR-AUC, chọn ngưỡng theo năng lực review, số đo phân luồng,
confusion matrix/F1 tại ngưỡng, hiệu chỉnh xác suất (Brier, ECE), đánh giá theo slice kèm khoảng tin cậy bootstrap."""
from __future__ import annotations

import math

import numpy as np
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

FRACS = (0.01, 0.003, 0.001)


def top_k_metrics(y: np.ndarray, s: np.ndarray, frac: float) -> dict:
    k = max(1, int(math.ceil(len(s) * frac)))
    order = np.argsort(-s, kind="mergesort")[:k]
    tp = int(y[order].sum())
    pos = int(y.sum())
    return {"k": k, "recall": tp / pos if pos else float("nan"), "precision": tp / k, "tp": tp, "fp": k - tp,
            "missed": pos - tp}


def ranking_metrics(y: np.ndarray, s: np.ndarray) -> dict:
    out = {"n": int(len(y)), "frauds": int(y.sum()),
           "pr_auc": float(average_precision_score(y, s)) if y.sum() else float("nan"),
           "roc_auc": float(roc_auc_score(y, s)) if 0 < y.sum() < len(y) else float("nan")}
    for frac in FRACS:
        m = top_k_metrics(y, s, frac)
        out[f"recall_at_{frac:g}"] = m["recall"]
        out[f"precision_at_{frac:g}"] = m["precision"]
        out[f"fp_at_{frac:g}"] = m["fp"]
        out[f"missed_at_{frac:g}"] = m["missed"]
    return out


def review_k(n: int, review_frac: float, capacity_per_day: int | None = None, days: float | None = None) -> int:
    """K = số hồ sơ review được: capacity_per_day × số ngày nếu có, ngược lại review_frac × số giao dịch.
    Dùng chung khi chọn ngưỡng (val_A), kiểm tra (val_B) và chạy test để K nhất quán."""
    if capacity_per_day and days:
        return max(1, min(n, int(capacity_per_day * days)))
    return max(1, int(math.ceil(n * review_frac)))


def span_days(ts: np.ndarray) -> float:
    return float((ts.max() - ts.min()) / 86400 + 1)


def choose_thresholds(y: np.ndarray, s: np.ndarray, review_frac: float, high_precision: float,
                      capacity_per_day: int | None = None, days: float | None = None) -> dict:
    """Ngưỡng theo năng lực review.
    - t_medium: điểm của hồ sơ thứ K (K = review_frac × số giao dịch, hoặc capacity_per_day × số ngày).
    - t_high: ngưỡng thấp nhất trong top K mà precision của nhóm HIGH vẫn ≥ high_precision."""
    n = len(s)
    k = review_k(n, review_frac, capacity_per_day, days)
    order = np.argsort(-s, kind="mergesort")
    s_sorted, y_sorted = s[order], y[order]
    t_medium = float(s_sorted[k - 1])
    cumprec = np.cumsum(y_sorted[:k]) / np.arange(1, k + 1)
    ok = np.where(cumprec >= high_precision)[0]
    t_high = float(s_sorted[ok.max()]) if len(ok) else 1.0
    t_high = max(t_high, t_medium)
    return {"t_medium": t_medium, "t_high": t_high, "K": k, "policy": "capacity",
            "review_frac": review_frac, "high_precision_target": high_precision}


def routing_metrics(y: np.ndarray, s: np.ndarray, t_medium: float, t_high: float, k_capacity: int,
                    n_unscored: int = 0) -> dict:
    """n_unscored: số giao dịch thành UNSCORED (dữ liệu lỗi, category lạ...). Chúng cũng tạo hồ sơ cho người xem
    nên được tính vào workload, dù không nằm trong y/s."""
    high = s >= t_high
    med = (s >= t_medium) & ~high
    flagged = high | med
    pos = y.sum()
    n_all = len(s) + n_unscored
    return {
        "frac_high": float(high.sum() / n_all), "frac_medium": float(med.sum() / n_all),
        "frac_unscored": float(n_unscored / n_all), "frac_low": float((~flagged).sum() / n_all),
        "n_cases": int(flagged.sum() + n_unscored), "n_unscored": int(n_unscored), "k_capacity": int(k_capacity),
        "workload_ratio": float((flagged.sum() + n_unscored) / max(1, k_capacity)),
        "recall_medium_high": float(y[flagged].sum() / pos) if pos else float("nan"),
        "precision_medium_high": float(y[flagged].mean()) if flagged.any() else float("nan"),
        "precision_high": float(y[high].mean()) if high.any() else float("nan"),
        "recall_high": float(y[high].sum() / pos) if pos else float("nan"),
    }


def threshold_metrics(y: np.ndarray, s: np.ndarray, t: float) -> dict:
    """Confusion matrix, precision, recall, F1 khi coi score ≥ t là cảnh báo."""
    pred = s >= t
    tp = int((pred & (y == 1)).sum())
    fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    tn = int((~pred & (y == 0)).sum())
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    f1 = 2 * p * r / (p + r) if tp else 0.0
    return {"threshold": float(t), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": p, "recall": r, "f1": f1}


def calibration_metrics(y: np.ndarray, p: np.ndarray, bins: int = 15) -> dict:
    """Brier, log loss, ECE và bảng reliability.

    Gian lận rất hiếm nên bin chia theo phân vị (mỗi bin cùng số giao dịch) + 1 bin riêng cho top 1%,
    thay vì bin đều trên [0, 1] (gần như mọi giao dịch rơi vào bin đầu)."""
    p = np.clip(p.astype(float), 1e-12, 1 - 1e-12)
    y = y.astype(float)
    edges = np.unique(np.concatenate([np.quantile(p, np.linspace(0, 0.99, bins)), [np.quantile(p, 0.99)]]))
    idx = np.searchsorted(edges, p, side="right") - 1
    table, ece = [], 0.0
    for b in np.unique(idx):
        m = idx == b
        conf, acc = float(p[m].mean()), float(y[m].mean())
        ece += m.mean() * abs(conf - acc)
        table.append({"p_min": float(p[m].min()), "p_max": float(p[m].max()), "n": int(m.sum()),
                      "mean_pred": conf, "fraud_rate": acc})
    return {"brier": float(np.mean((p - y) ** 2)),
            "log_loss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
            "ece": float(ece), "mean_pred": float(p.mean()), "fraud_rate": float(y.mean()), "reliability": table}


def bootstrap_recall_at(y: np.ndarray, s: np.ndarray, frac: float, n_boot: int = 200, seed: int = 0) -> dict:
    """Khoảng tin cậy 95% của recall@frac: lấy mẫu lại giao dịch, mỗi lần tính lại K và ngưỡng top-frac."""
    rng = np.random.default_rng(seed)
    n = len(s)
    vals = []
    for _ in range(n_boot):
        i = rng.integers(0, n, n)
        yy, ss = y[i], s[i]
        pos = yy.sum()
        if not pos:
            continue
        k = max(1, int(math.ceil(n * frac)))
        cut = np.partition(ss, n - k)[n - k]
        # chia đều số chỗ còn lại cho các giao dịch bằng đúng ngưỡng cắt
        above = ss > cut
        tie = ss == cut
        tp = yy[above].sum() + yy[tie].sum() * (k - above.sum()) / max(1, tie.sum())
        vals.append(tp / pos)
    point = top_k_metrics(y, s, frac)["recall"]
    lo, hi = (np.percentile(vals, [2.5, 97.5]) if vals else (float("nan"), float("nan")))
    return {"recall": point, "ci95": [float(lo), float(hi)], "n_boot": len(vals)}


def _ci(y: np.ndarray, flagged: np.ndarray, rng, n_boot: int) -> list[float]:
    """CI 95% của recall trong slice, ngưỡng cố định (ngưỡng vận hành)."""
    pos_idx = np.where(y == 1)[0]
    if len(pos_idx) == 0:
        return [float("nan"), float("nan")]
    hits = flagged[pos_idx].astype(float)
    # recall chỉ phụ thuộc các ca gian lận: lấy mẫu lại các ca gian lận trong slice
    boots = hits[rng.integers(0, len(hits), (n_boot, len(hits)))].mean(axis=1)
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return [float(lo), float(hi)]


def slice_metrics(y: np.ndarray, s: np.ndarray, threshold: float, masks: dict[str, np.ndarray],
                  n_boot: int = 500, seed: int = 0, min_frauds: int = 20) -> dict:
    """Recall/precision theo slice tại một ngưỡng chung (ngưỡng top-K toàn tập = cách hệ thống vận hành).
    Slice có < min_frauds ca gian lận vẫn được báo cáo nhưng gắn cờ small_sample."""
    rng = np.random.default_rng(seed)
    out = {}
    for name, m in masks.items():
        m = np.asarray(m, dtype=bool)
        yy, ss = y[m], s[m]
        flagged = ss >= threshold
        pos = int(yy.sum())
        tp = int((flagged & (yy == 1)).sum())
        out[name] = {
            "n": int(m.sum()), "frauds": pos, "fraud_rate": float(yy.mean()) if m.any() else float("nan"),
            "flagged": int(flagged.sum()), "tp": tp, "missed": pos - tp,
            "recall": tp / pos if pos else float("nan"),
            "precision": tp / flagged.sum() if flagged.any() else float("nan"),
            "recall_ci95": _ci(yy, flagged, rng, n_boot),
            "small_sample": pos < min_frauds,
        }
    return out


ONLINE_CATS = {"shopping_net", "misc_net", "grocery_net"}
AMT_BANDS = [(0, 50), (50, 200), (200, 500), (500, 1000), (1000, float("inf"))]


def slice_masks(F, df, states: bool = True, min_state_n: int = 1000) -> dict[str, np.ndarray]:
    """Các slice rủi ro cao: thẻ ít lịch sử (cold-start), online/offline, giao dịch đêm, khoảng số tiền, category, bang.
    F: bảng feature, df: bảng giao dịch đã tối thiểu hóa (cùng thứ tự dòng)."""
    cat = F["category"].astype(str).to_numpy()
    amt = F["amt"].to_numpy(float)
    online = np.isin(cat, list(ONLINE_CATS))
    m = {
        "cold_start(n_txn_30d<3)": F["n_txn_30d"].to_numpy() < 3,
        "warm(n_txn_30d>=3)": F["n_txn_30d"].to_numpy() >= 3,
        "online": online, "offline": ~online,
        "night": F["is_night"].to_numpy() == 1, "day": F["is_night"].to_numpy() == 0,
    }
    for lo, hi in AMT_BANDS:
        m[f"amt[{lo:g},{hi:g})"] = (amt >= lo) & (amt < hi)
    for v in np.unique(cat):
        m[f"category={v}"] = cat == v
    if states and "state" in df.columns:
        st = df["state"].astype(str).to_numpy()
        vals, counts = np.unique(st, return_counts=True)
        for v, c in zip(vals, counts):
            if c >= min_state_n:
                m[f"state={v}"] = st == v
    return m


CRITICAL_SLICES = ("cold_start(n_txn_30d<3)", "online", "night")


def pr_curve(y: np.ndarray, s: np.ndarray, points: int = 200) -> dict:
    """Đường precision–recall rút gọn để vẽ hình trong báo cáo: lấy mẫu đều theo recall,
    precision nội suy (precision lớn nhất tại recall ≥ r)."""
    if not y.sum():
        return {"precision": [], "recall": []}
    p, r, _ = precision_recall_curve(y, s)  # r giảm dần
    p_interp = np.maximum.accumulate(p)
    grid = np.linspace(0, 1, points)
    idx = np.searchsorted(-r, -grid, side="right") - 1
    return {"precision": p_interp[idx].round(5).tolist(), "recall": grid.round(5).tolist()}


def evaluation_report(y: np.ndarray, s: np.ndarray, F, df, t_medium: float, t_high: float, k_capacity: int,
                      n_unscored: int = 0, n_boot: int = 200, is_probability: bool = True) -> dict:
    """Bộ số đo đầy đủ cho một tập (val_B hoặc test) – cùng protocol cho mọi model và mốc so sánh.
    is_probability=False (điểm luật) thì bỏ số đo hiệu chỉnh."""
    k1 = top_k_metrics(y, s, 0.01)
    cut_1pct = float(np.sort(s)[::-1][k1["k"] - 1])
    return {
        "ranking": ranking_metrics(y, s),
        "pr_curve": pr_curve(y, s),
        "recall_at_0.01_ci": bootstrap_recall_at(y, s, 0.01, n_boot),
        "at_t_medium": threshold_metrics(y, s, t_medium),
        "at_t_high": threshold_metrics(y, s, t_high),
        "routing": routing_metrics(y, s, t_medium, t_high, k_capacity, n_unscored),
        "calibration": calibration_metrics(y, s) if is_probability else None,
        "slices_at_top1pct": {"threshold": cut_1pct, "slices": slice_metrics(y, s, cut_1pct, slice_masks(F, df))},
    }

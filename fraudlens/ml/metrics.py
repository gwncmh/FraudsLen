"""Số đo đánh giá: Recall@Top1%, PR-AUC, chọn ngưỡng theo năng lực review, số đo phân luồng."""
from __future__ import annotations

import math

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score


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
    for frac in (0.01, 0.003, 0.001):
        m = top_k_metrics(y, s, frac)
        out[f"recall_at_{frac:g}"] = m["recall"]
        out[f"precision_at_{frac:g}"] = m["precision"]
    return out


def choose_thresholds(y: np.ndarray, s: np.ndarray, review_frac: float, high_precision: float,
                      capacity_per_day: int | None = None, days: float | None = None) -> dict:
    """Ngưỡng theo năng lực review.
    - t_medium: điểm của hồ sơ thứ K (K = review_frac × số giao dịch, hoặc capacity_per_day × số ngày).
    - t_high: ngưỡng thấp nhất trong top K mà precision của nhóm HIGH vẫn ≥ high_precision."""
    n = len(s)
    if capacity_per_day and days:
        k = max(1, min(n, int(capacity_per_day * days)))
    else:
        k = max(1, int(math.ceil(n * review_frac)))
    order = np.argsort(-s, kind="mergesort")
    s_sorted, y_sorted = s[order], y[order]
    t_medium = float(s_sorted[k - 1])
    cumprec = np.cumsum(y_sorted[:k]) / np.arange(1, k + 1)
    ok = np.where(cumprec >= high_precision)[0]
    t_high = float(s_sorted[ok.max()]) if len(ok) else 1.0
    t_high = max(t_high, t_medium)
    return {"t_medium": t_medium, "t_high": t_high, "K": k, "policy": "capacity",
            "review_frac": review_frac, "high_precision_target": high_precision}


def routing_metrics(y: np.ndarray, s: np.ndarray, t_medium: float, t_high: float, k_capacity: int) -> dict:
    high = s >= t_high
    med = (s >= t_medium) & ~high
    flagged = high | med
    pos = y.sum()
    return {
        "frac_high": float(high.mean()), "frac_medium": float(med.mean()), "frac_low": float((~flagged).mean()),
        "n_cases": int(flagged.sum()), "workload_ratio": float(flagged.sum() / max(1, k_capacity)),
        "recall_medium_high": float(y[flagged].sum() / pos) if pos else float("nan"),
        "precision_medium_high": float(y[flagged].mean()) if flagged.any() else float("nan"),
        "precision_high": float(y[high].mean()) if high.any() else float("nan"),
        "recall_high": float(y[high].sum() / pos) if pos else float("nan"),
    }

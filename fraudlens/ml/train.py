"""Huấn luyện model chốt và đăng ký vào registry (chưa ACTIVE cho tới khi Risk Lead duyệt).

  python -m fraudlens.ml.train                       # toàn bộ Sparkov
  python -m fraudlens.ml.train --sample-cards 0.3    # máy yếu / chạy thử nhanh
  python -m fraudlens.ml.train --no-baselines        # bỏ mốc so sánh luật/LR (approve sẽ chặn phiên bản này)
  python -m fraudlens.ml.train --monotone            # ràng buộc đơn điệu: điểm không giảm khi số tiền/tần suất tăng

Các bước: nạp + tối thiểu hóa -> tính feature (hàm dùng chung) -> chia theo thời gian
-> XGBoost (trọng số lớp căn bậc hai, dừng sớm trên 10% cuối train) -> hiệu chỉnh Platt trên val_A
-> chọn ngưỡng theo năng lực review trên val_A -> kiểm tra trên val_B (số đo đầy đủ, slice, mốc so sánh)
-> behavioral test -> ghi registry.
Test KHÔNG được chạm ở đây (xem fraudlens.ml.final_test).
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.linear_model import LogisticRegression

from fraudlens.common.config import settings
from fraudlens.common.features import FEATURE_SET_VERSION, FEATURE_TYPES, FEATURES, to_model_frame
from fraudlens.common.registry import root, save_json, sha256_file
from fraudlens.ml import behavior
from fraudlens.ml.baselines import lr_baseline, rules_score
from fraudlens.ml.data import load_prepared
from fraudlens.ml.metrics import (choose_thresholds, evaluation_report, ranking_metrics, review_k, routing_metrics,
                                  span_days)
from fraudlens.ml.model import logit, platt_apply

MONITORED_NUMERIC = ["amt", "hour", "distance_km", "n_txn_24h", "age"]
MONOTONE_UP = ["amt_ratio_30d", "amt_zscore_30d", "n_txn_1h", "n_txn_24h"]


def score_edges(p: np.ndarray, bins: int = 10) -> dict:
    """Phân bố tham chiếu của điểm (giám sát drift của chính điểm): biên theo phân vị, thêm biên top 1%."""
    edges = np.unique(np.concatenate([np.quantile(p, np.linspace(0, 1, bins + 1)[1:-1]), [np.quantile(p, 0.99)]]))
    counts = np.bincount(np.searchsorted(edges, p, side="right"), minlength=len(edges) + 1)
    return {"edges": edges.tolist(), "props": (counts / counts.sum()).tolist()}


def reference_stats(F: pd.DataFrame, cal_scores: np.ndarray | None = None, expected_routes: dict | None = None) -> dict:
    """Phân bố tham chiếu cho giám sát: PSI feature (biên 10 bin theo phân vị, trên train),
    phân bố điểm và tỉ lệ phân luồng kỳ vọng (trên val_B)."""
    ref = {"numeric": {}, "categorical": {}}
    for c in MONITORED_NUMERIC:
        v = F[c].astype(float).to_numpy()
        edges = np.unique(np.quantile(v, np.linspace(0, 1, 11)[1:-1]))
        counts = np.bincount(np.searchsorted(edges, v, side="right"), minlength=len(edges) + 1)
        ref["numeric"][c] = {"edges": edges.tolist(), "props": (counts / counts.sum()).tolist()}
    vc = F["category"].astype(str).value_counts(normalize=True)
    ref["categorical"]["category"] = vc.to_dict()
    if cal_scores is not None:
        ref["score"] = score_edges(cal_scores)
    if expected_routes is not None:
        ref["expected_routes"] = expected_routes
    return ref


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--sample-cards", type=float, default=1.0, help="tỉ lệ số thẻ dùng (0.3 cho máy yếu)")
    ap.add_argument("--review-frac", type=float, default=0.01, help="ngân sách review (đề bài: 1%%)")
    ap.add_argument("--capacity-per-day", type=int, default=None, help="năng lực review tuyệt đối (hồ sơ/ngày), thay cho --review-frac")
    ap.add_argument("--high-precision", type=float, default=0.8, help="precision tối thiểu của nhóm HIGH")
    ap.add_argument("--n-estimators", type=int, default=600)
    ap.add_argument("--learning-rate", type=float, default=0.05)
    ap.add_argument("--max-depth", type=int, default=6)
    ap.add_argument("--n-jobs", type=int, default=0, help="số luồng CPU (0 = tự chọn)")
    ap.add_argument("--baselines", action="store_true", help="(giữ cho tương thích – baseline nay chạy mặc định)")
    ap.add_argument("--no-baselines", action="store_true", help="bỏ qua mốc so sánh (approve sẽ chặn)")
    ap.add_argument("--monotone", action="store_true", help="ràng buộc đơn điệu tăng cho " + ", ".join(MONOTONE_UP))
    a = ap.parse_args()

    t0 = time.time()
    key = settings.require_hmac()
    print("[1/6] Chuẩn bị dữ liệu và feature")
    df, F = load_prepared(Path(a.data_dir), key, a.sample_cards, Path(a.data_dir) / "cache")
    y = df["is_fraud"].to_numpy()
    sp = df["split"].to_numpy()
    for s in ("train", "val_a", "val_b", "test"):
        m = sp == s
        print(f"      {s:6s}: {m.sum():>9,} giao dịch, gian lận {y[m].mean() * 100:.3f}%")

    categories = sorted(F.loc[sp == "train", "category"].astype(str).unique().tolist())
    X = to_model_frame(F, categories)
    tr, va, vb = sp == "train", sp == "val_a", sp == "val_b"

    print("[2/6] Huấn luyện XGBoost")
    Xtr, ytr = X[tr], y[tr]
    cut = int(len(Xtr) * 0.9)  # dừng sớm trên 10% CUỐI của train (theo thời gian), không đụng val_A
    pos = max(1, int(ytr[:cut].sum()))
    spw = float(np.sqrt((cut - pos) / pos))
    clf = xgb.XGBClassifier(
        n_estimators=a.n_estimators, learning_rate=a.learning_rate, max_depth=a.max_depth,
        subsample=0.8, colsample_bytree=0.8, tree_method="hist", enable_categorical=True,
        feature_types=FEATURE_TYPES,
        scale_pos_weight=spw, eval_metric="aucpr", early_stopping_rounds=50,
        monotone_constraints=("(" + ",".join("1" if f in MONOTONE_UP else "0" for f in FEATURES) + ")") if a.monotone else None,
        n_jobs=(a.n_jobs or None), random_state=42)
    clf.fit(Xtr[:cut], ytr[:cut], eval_set=[(Xtr[cut:], ytr[cut:])], verbose=False)
    print(f"      số cây dùng: {clf.best_iteration + 1}, scale_pos_weight = {spw:.2f}")

    print("[3/6] Hiệu chỉnh Platt trên val_A")
    raw_a = clf.predict_proba(X[va])[:, 1]
    raw_b = clf.predict_proba(X[vb])[:, 1]
    pl = LogisticRegression(C=1e6, max_iter=1000).fit(logit(raw_a).reshape(-1, 1), y[va])
    a_, b_ = float(pl.coef_[0, 0]), float(pl.intercept_[0])
    cal_a, cal_b = platt_apply(raw_a, a_, b_), platt_apply(raw_b, a_, b_)

    print("[4/6] Chọn ngưỡng theo năng lực review trên val_A")
    days_a = (df.loc[va, "event_ts"].max() - df.loc[va, "event_ts"].min()) / 86400 + 1
    th = choose_thresholds(y[va], cal_a, a.review_frac, a.high_precision, a.capacity_per_day, days_a)
    print(f"      t_medium = {th['t_medium']:.5f}, t_high = {th['t_high']:.5f}, K(val_A) = {th['K']}")

    print("[5/6] Kiểm tra trên val_B (số đo đầy đủ, slice, mốc so sánh) + behavioral test")
    k_b = review_k(int(vb.sum()), a.review_frac, a.capacity_per_day, span_days(df.loc[vb, "event_ts"].to_numpy()))
    unknown_b = ~F.loc[vb, "category"].astype(str).isin(categories).to_numpy()  # online sẽ thành UNSCORED
    n_unscored_b = int((unknown_b & (cal_b < th["t_medium"])).sum())
    metrics = {"val_a": ranking_metrics(y[va], cal_a), "val_b": ranking_metrics(y[vb], cal_b),
               "val_b_routing": routing_metrics(y[vb], cal_b, th["t_medium"], th["t_high"], k_b, n_unscored_b),
               "val_b_report": evaluation_report(y[vb], cal_b, F[vb], df[vb], th["t_medium"], th["t_high"], k_b,
                                                 n_unscored_b)}
    if not a.no_baselines:
        # mốc so sánh đi đúng protocol của model: ngưỡng chọn trên val_A, đánh giá trên val_B, cùng K
        lr_a, lr_b = lr_baseline(F[tr], y[tr], F[va], F[vb])
        metrics["baselines_val_b_report"] = {}
        for name, sa, sb in (("rules", rules_score(F[va]), rules_score(F[vb])), ("lr", lr_a, lr_b)):
            bth = choose_thresholds(y[va], sa, a.review_frac, a.high_precision, a.capacity_per_day, days_a)
            metrics[f"baseline_{name}_val_b"] = ranking_metrics(y[vb], sb)
            metrics["baselines_val_b_report"][name] = evaluation_report(
                y[vb], sb, F[vb], df[vb], bth["t_medium"], bth["t_high"], k_b, is_probability=(name != "rules"))
    for k, v in metrics.items():
        keys = [x for x in ("recall_at_0.01", "fp_at_0.01", "recall_at_0.003", "pr_auc", "recall_medium_high",
                            "precision_high", "workload_ratio") if x in v]
        if keys:
            print(f"      {k:22s} " + "  ".join(f"{x}={v[x]:.4f}" for x in keys))
    rep_b = metrics["val_b_report"]
    print(f"      F1@t_medium={rep_b['at_t_medium']['f1']:.4f}  Brier={rep_b['calibration']['brier']:.6f}  "
          f"ECE={rep_b['calibration']['ece']:.6f}  recall@1% CI95={rep_b['recall_at_0.01_ci']['ci95']}")
    for name, sl in rep_b["slices_at_top1pct"]["slices"].items():
        if not name.startswith("state="):
            print(f"      slice {name:26s} n={sl['n']:>7,} frauds={sl['frauds']:>4} recall={sl['recall']:.3f} "
                  f"CI95=[{sl['recall_ci95'][0]:.3f}, {sl['recall_ci95'][1]:.3f}]")

    def predict(rows: list[dict]) -> np.ndarray:
        return platt_apply(clf.predict_proba(to_model_frame(rows, categories))[:, 1], a_, b_)

    rs = np.random.default_rng(0)
    vb_idx = np.where(vb)[0]
    pick = np.union1d(rs.choice(vb_idx, size=min(2000, len(vb_idx)), replace=False), vb_idx[y[vb_idx] == 1][:500])
    beh = behavior.run_all(predict, categories, F.iloc[pick][FEATURES], th["t_medium"])
    print(f"      behavioral: invariance={beh['invariance']['passed']}  mft={beh['mft']['passed']}  "
          + "  ".join(f"{k}: điểm giảm {v['violation_rate']:.3f}, bị hạ xuống LOW {v['downgrade_rate']:.3f}"
                      for k, v in beh["directional"].items()))

    print("[6/6] Ghi registry")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    tmp = root() / f"_tmp_{stamp}"
    tmp.mkdir(parents=True, exist_ok=True)
    clf.get_booster().feature_names = FEATURES  # ghi tên cột vào model để Scorer kiểm tra khớp
    clf.save_model(str(tmp / "model.json"))
    digest = sha256_file(tmp / "model.json")
    version = f"xgb-{FEATURE_SET_VERSION}-{stamp}-{digest[:8]}"
    tmp.rename(root() / version)

    span = lambda m: [int(df.loc[m, "event_ts"].min()), int(df.loc[m, "event_ts"].max())]  # noqa: E731
    manifest = {
        "model_version": version, "model": "xgboost", "xgboost_version": xgb.__version__, "sha256": digest,
        "feature_set_version": FEATURE_SET_VERSION, "features": FEATURES, "categories": categories,
        "calibration": {"method": "platt", "a": a_, "b": b_, "fit_on": "val_a"},
        "params": {"n_estimators_used": int(clf.best_iteration + 1), "learning_rate": a.learning_rate,
                   "max_depth": a.max_depth, "scale_pos_weight": spw,
                   "monotone": MONOTONE_UP if a.monotone else None},
        "data": {"sample_cards": a.sample_cards, "train_span": span(tr), "val_a_span": span(va), "val_b_span": span(vb)},
        "metrics": metrics, "behavior": beh, "created_at": stamp,
    }
    save_json(version, "manifest.json", manifest)
    save_json(version, "routing_config.json", {
        "config_version": f"routing-{stamp}", "model_version": version, **th,
        "capacity_per_day": a.capacity_per_day, "selected_on": "val_a",
        "val_b_check": metrics["val_b_routing"], "approved_by": None, "approved_at": None})
    rb = metrics["val_b_routing"]
    save_json(version, "reference.json", reference_stats(
        F[tr], cal_b, {"HIGH": rb["frac_high"], "MEDIUM": rb["frac_medium"], "UNSCORED": rb["frac_unscored"]}))
    print(f"\nXong trong {time.time() - t0:.0f} giây. Phiên bản: {version}")
    print("Bước tiếp theo: Risk Lead xem registry/<version>/routing_config.json rồi chạy")
    print(f'  python -m fraudlens.ml.approve --version {version} --by "Tên Risk Lead"')
    (root() / "LATEST_TRAINED").write_text(version)
    print(json.dumps({"version": version, "t_medium": th["t_medium"], "t_high": th["t_high"]}, indent=2))


if __name__ == "__main__":
    main()

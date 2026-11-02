"""Chạy test set MỘT lần cho model đã duyệt (đúng protocol: ngưỡng không được chỉnh theo test).

  python -m fraudlens.ml.final_test --version <version>

- Test LUÔN là toàn bộ fraudTest.csv, kể cả khi model huấn luyện với --sample-cards < 1 (cùng protocol với các nhóm).
- Mốc so sánh (luật, LR) chạy trên CÙNG test set; ngưỡng của mốc chọn trên val_A với cùng chính sách năng lực review.
- K (năng lực review) tính đúng như lúc chọn ngưỡng: capacity_per_day × số ngày nếu có, ngược lại review_frac × số GD.
Tạo registry/<version>/TEST_RUN.lock; chạy lại sẽ bị từ chối.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from fraudlens.common.config import settings
from fraudlens.common.registry import load_json, root, save_json
from fraudlens.ml.baselines import lr_baseline, rules_score
from fraudlens.ml.data import load_prepared
from fraudlens.ml.metrics import choose_thresholds, evaluation_report, review_k, span_days
from fraudlens.ml.model import FraudModel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--n-boot", type=int, default=200)
    a = ap.parse_args()

    lock = root() / a.version / "TEST_RUN.lock"
    if lock.exists():
        raise SystemExit(f"Test đã chạy cho {a.version} lúc {lock.read_text()}. Không chạy lại.")
    rc = load_json(a.version, "routing_config.json")
    if not rc.get("approved_by"):
        raise SystemExit("Ngưỡng chưa được Risk Lead duyệt – duyệt trước rồi mới chạy test.")
    manifest = load_json(a.version, "manifest.json")
    # sample_cards = 1.0: test luôn là toàn bộ fraudTest.csv, không phụ thuộc mẫu lúc huấn luyện
    df, F = load_prepared(Path(a.data_dir), settings.require_hmac(), 1.0, Path(a.data_dir) / "cache")
    sp = df["split"].to_numpy()
    te, tr, va = sp == "test", sp == "train", sp == "val_a"
    y_all = df["is_fraud"].to_numpy()
    y = y_all[te]

    model = FraudModel(root() / a.version, manifest)
    model.nthread = 0  # đánh giá cả tập: dùng mọi nhân CPU
    model.booster.set_param({"nthread": 0})
    _, s = model.predict(model.frame(F[te]))

    days_te = span_days(df.loc[te, "event_ts"].to_numpy())
    k = review_k(int(te.sum()), rc["review_frac"], rc.get("capacity_per_day"), days_te)
    unknown = ~F.loc[te, "category"].astype(str).isin(manifest["categories"]).to_numpy()  # online -> UNSCORED
    n_unscored = int((unknown & (s < rc["t_medium"])).sum())
    res = {
        "protocol": {"test_rows": int(te.sum()), "test_frauds": int(y.sum()), "test_file": "fraudTest.csv (toàn bộ)",
                     "model_trained_on_sample_cards": manifest["data"]["sample_cards"], "k_capacity": k,
                     "k_policy": "capacity_per_day × days" if rc.get("capacity_per_day") else "review_frac × n",
                     "days": days_te, "thresholds_from": rc["config_version"]},
        "model": evaluation_report(y, s, F[te], df[te], rc["t_medium"], rc["t_high"], k, n_unscored, a.n_boot),
    }

    print("Chạy mốc so sánh trên cùng test set (LR huấn luyện trên split train của toàn bộ thẻ)...")
    days_a = span_days(df.loc[va, "event_ts"].to_numpy())
    lr_a, lr_te = lr_baseline(F[tr], y_all[tr], F[va], F[te])
    res["baselines"] = {}
    for name, sa, st in (("rules", rules_score(F[va]), rules_score(F[te])), ("lr", lr_a, lr_te)):
        bth = choose_thresholds(y_all[va], sa, rc["review_frac"], rc["high_precision_target"],
                                rc.get("capacity_per_day"), days_a)
        res["baselines"][name] = evaluation_report(y, st, F[te], df[te], bth["t_medium"], bth["t_high"], k,
                                                   n_boot=a.n_boot, is_probability=(name != "rules"))
    # bảng so sánh gọn cho báo cáo
    rows = {"model": res["model"], **res["baselines"]}
    res["comparison"] = {n: {"recall@1%": r["ranking"]["recall_at_0.01"], "precision@1%": r["ranking"]["precision_at_0.01"],
                             "fp@1%": r["ranking"]["fp_at_0.01"], "missed@1%": r["ranking"]["missed_at_0.01"],
                             "recall@1%_ci95": r["recall_at_0.01_ci"]["ci95"], "pr_auc": r["ranking"]["pr_auc"],
                             "f1@t_medium": r["at_t_medium"]["f1"], "workload_ratio": r["routing"]["workload_ratio"]}
                         for n, r in rows.items()}

    save_json(a.version, "test_results.json", res)
    lock.write_text(datetime.now(timezone.utc).isoformat())
    print(json.dumps(res["comparison"], indent=2))
    cal = res["model"]["calibration"]
    print(f"Hiệu chỉnh: Brier={cal['brier']:.6f}  ECE={cal['ece']:.6f}  điểm TB={cal['mean_pred']:.5f}  "
          f"tỉ lệ gian lận thực={cal['fraud_rate']:.5f}")
    print(f"Chi tiết (confusion matrix, slice, reliability): registry/{a.version}/test_results.json")


if __name__ == "__main__":
    main()

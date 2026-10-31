"""Chạy test set MỘT lần cho model đã duyệt (đúng protocol: ngưỡng không được chỉnh theo test).

  python -m fraudlens.ml.final_test --version <version>

Tạo registry/<version>/TEST_RUN.lock; chạy lại sẽ bị từ chối.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from fraudlens.common.config import settings
from fraudlens.common.registry import load_json, root, save_json
from fraudlens.ml.data import load_prepared
from fraudlens.ml.metrics import ranking_metrics, routing_metrics
from fraudlens.ml.model import FraudModel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--data-dir", default="data")
    a = ap.parse_args()

    lock = root() / a.version / "TEST_RUN.lock"
    if lock.exists():
        raise SystemExit(f"Test đã chạy cho {a.version} lúc {lock.read_text()}. Không chạy lại.")
    rc = load_json(a.version, "routing_config.json")
    if not rc.get("approved_by"):
        raise SystemExit("Ngưỡng chưa được Risk Lead duyệt – duyệt trước rồi mới chạy test.")
    manifest = load_json(a.version, "manifest.json")
    df, F = load_prepared(Path(a.data_dir), settings.require_hmac(), manifest["data"]["sample_cards"], Path(a.data_dir) / "cache")
    te = (df["split"] == "test").to_numpy()
    y = df["is_fraud"].to_numpy()[te]
    model = FraudModel(root() / a.version, manifest)
    model.nthread = 0  # đánh giá cả tập: dùng mọi nhân CPU
    model.booster.set_param({"nthread": 0})
    _, s = model.predict(model.frame(F[te]))
    k = int(np.ceil(te.sum() * rc["review_frac"]))
    res = {"test": ranking_metrics(y, s), "test_routing": routing_metrics(y, s, rc["t_medium"], rc["t_high"], k)}
    save_json(a.version, "test_results.json", res)
    lock.write_text(datetime.now(timezone.utc).isoformat())
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()

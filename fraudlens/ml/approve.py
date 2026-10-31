"""Risk Lead duyệt cặp model + ngưỡng và chuyển con trỏ ACTIVE (quay lui cũng dùng lệnh này).

  python -m fraudlens.ml.approve --list
  python -m fraudlens.ml.approve --version <version> --by "Tên Risk Lead"

Sau khi đổi ACTIVE: khởi động lại scorer và api để nạp model/ngưỡng mới (docker compose restart scorer api).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from fraudlens.common.registry import load_json, root, save_json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--version")
    ap.add_argument("--by")
    a = ap.parse_args()

    active = (root() / "ACTIVE").read_text().strip() if (root() / "ACTIVE").exists() else None
    if a.list or not a.version:
        for d in sorted(p for p in root().iterdir() if p.is_dir() and not p.name.startswith("_")):
            rc = load_json(d.name, "routing_config.json")
            m = load_json(d.name, "manifest.json")["metrics"]["val_b"]
            flag = "ACTIVE " if d.name == active else "       "
            print(f"{flag}{d.name}  recall@1%(val_B)={m['recall_at_0.01']:.4f}  "
                  f"t_medium={rc['t_medium']:.5f} t_high={rc['t_high']:.5f}  duyệt bởi: {rc.get('approved_by')}")
        return
    if not a.by:
        raise SystemExit("Cần --by: tên người duyệt (Risk Lead)")
    rc = load_json(a.version, "routing_config.json")
    print(json.dumps({k: rc[k] for k in ("t_medium", "t_high", "K", "policy", "val_b_check")}, indent=2, ensure_ascii=False))
    rc["approved_by"] = a.by
    rc["approved_at"] = datetime.now(timezone.utc).isoformat()
    save_json(a.version, "routing_config.json", rc)
    (root() / "ACTIVE").write_text(a.version)
    print(f"ACTIVE = {a.version} (trước đó: {active}). Khởi động lại scorer và api để áp dụng.")


if __name__ == "__main__":
    main()

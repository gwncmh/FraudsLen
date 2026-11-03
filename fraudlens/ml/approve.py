"""Risk Lead duyệt cặp model + ngưỡng và chuyển con trỏ ACTIVE (quay lui cũng dùng lệnh này).

  python -m fraudlens.ml.approve --list
  python -m fraudlens.ml.approve --version <version> --by "Tên Risk Lead"
  python -m fraudlens.ml.approve --version <version> --by "..." --override "lý do"   # duyệt dù gate chặn (ghi lại)

Trước khi duyệt, release gate (fraudlens.ml.gates) tự kiểm tra trên val_B: vượt baseline luật, LR và cây quyết định,
workload_ratio, recall của slice trọng yếu, behavioral test, đã chạy baseline. Gate chặn thì từ chối,
trừ khi Risk Lead ghi rõ lý do bằng --override (lý do và danh sách vi phạm lưu trong routing_config.json).

Sau khi đổi ACTIVE: khởi động lại scorer và api để nạp model/ngưỡng mới (docker compose restart scorer api).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from fraudlens.common.registry import load_json, root, save_json
from fraudlens.ml.gates import GateConfig, release_gate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--version")
    ap.add_argument("--by")
    ap.add_argument("--override", help="lý do duyệt dù release gate chặn")
    ap.add_argument("--max-workload", type=float, default=GateConfig.max_workload_ratio)
    ap.add_argument("--slice-floor", type=float, default=GateConfig.slice_recall_floor)
    a = ap.parse_args()
    cfg = GateConfig(max_workload_ratio=a.max_workload, slice_recall_floor=a.slice_floor)

    active = (root() / "ACTIVE").read_text().strip() if (root() / "ACTIVE").exists() else None
    if a.list or not a.version:
        for d in sorted(p for p in root().iterdir() if p.is_dir() and not p.name.startswith("_")):
            rc = load_json(d.name, "routing_config.json")
            manifest = load_json(d.name, "manifest.json")
            m = manifest["metrics"]["val_b"]
            gate = "gate OK" if not release_gate(manifest, rc, cfg) else (
                "gate CHẶN (đã duyệt có override)" if (rc.get("release_gate") or {}).get("override") else "gate CHẶN")
            flag = "ACTIVE " if d.name == active else "       "
            print(f"{flag}{d.name}  recall@1%(val_B)={m['recall_at_0.01']:.4f}  "
                  f"t_medium={rc['t_medium']:.5f} t_high={rc['t_high']:.5f}  {gate}  duyệt bởi: {rc.get('approved_by')}")
        return
    if not a.by:
        raise SystemExit("Cần --by: tên người duyệt (Risk Lead)")
    rc = load_json(a.version, "routing_config.json")
    manifest = load_json(a.version, "manifest.json")
    print(json.dumps({k: rc[k] for k in ("t_medium", "t_high", "K", "policy", "val_b_check")}, indent=2, ensure_ascii=False))

    reasons = release_gate(manifest, rc, cfg)
    if reasons:
        print("\nRELEASE GATE CHẶN:")
        for r in reasons:
            print("  -", r)
        if not (a.override and a.override.strip()):
            raise SystemExit("Không duyệt. Sửa model/ngưỡng, hoặc dùng --override \"lý do\" nếu Risk Lead chấp nhận rủi ro.")
        print(f"Duyệt có override: {a.override}")
    rc["release_gate"] = {"config": cfg.__dict__, "violations": reasons, "override": a.override if reasons else None,
                          "checked_at": datetime.now(timezone.utc).isoformat()}
    rc["approved_by"] = a.by
    rc["approved_at"] = datetime.now(timezone.utc).isoformat()
    save_json(a.version, "routing_config.json", rc)
    (root() / "ACTIVE").write_text(a.version)
    print(f"ACTIVE = {a.version} (trước đó: {active}). Khởi động lại scorer và api để áp dụng.")


if __name__ == "__main__":
    main()

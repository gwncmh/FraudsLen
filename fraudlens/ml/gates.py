"""Release-blocking gates (Lec 5): điều kiện tối thiểu để một cặp model + ngưỡng được duyệt ACTIVE.

Tất cả kiểm tra trên val_B (không đụng test). Trả về danh sách lý do chặn; rỗng = được duyệt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from fraudlens.ml.metrics import CRITICAL_SLICES


@dataclass(frozen=True)
class GateConfig:
    max_workload_ratio: float = 1.2      # số hồ sơ / năng lực review trên val_B
    slice_recall_floor: float = 0.6      # recall tối thiểu (tại ngưỡng top 1%) của mỗi slice trọng yếu
    baseline_margin: float = 0.0         # recall@1% phải ≥ mốc tốt nhất + margin


def release_gate(manifest: dict, routing_config: dict, cfg: GateConfig = GateConfig()) -> list[str]:
    m = manifest.get("metrics", {})
    reasons: list[str] = []

    rep = m.get("val_b_report")
    if not rep:
        return ["THIẾU val_b_report: model huấn luyện bằng phiên bản cũ, chạy lại fraudlens.ml.train"]
    ours = rep["ranking"]["recall_at_0.01"]

    base = m.get("baselines_val_b_report") or {}
    if not base:
        reasons.append("CHƯA CHẠY BASELINE (luật, LR, cây) trên val_B")
    for name, b in base.items():
        r = b["ranking"]["recall_at_0.01"]
        if not (ours >= r + cfg.baseline_margin):
            reasons.append(f"recall@1% val_B = {ours:.4f} không vượt baseline {name} = {r:.4f}")

    wl = routing_config.get("val_b_check", {}).get("workload_ratio", rep["routing"]["workload_ratio"])
    if not (wl <= cfg.max_workload_ratio):
        reasons.append(f"workload_ratio val_B = {wl:.3f} > {cfg.max_workload_ratio}")

    slices = rep.get("slices_at_top1pct", {}).get("slices", {})
    for name in CRITICAL_SLICES:
        sl = slices.get(name)
        if sl is None:
            reasons.append(f"thiếu slice {name}")
            continue
        if sl["small_sample"] or (isinstance(sl["recall"], float) and math.isnan(sl["recall"])):
            continue  # quá ít ca gian lận để kết luận – báo cáo nhưng không chặn
        if sl["recall"] < cfg.slice_recall_floor:
            reasons.append(f"slice {name}: recall = {sl['recall']:.3f} < sàn {cfg.slice_recall_floor} "
                           f"(CI95 {sl['recall_ci95'][0]:.3f}–{sl['recall_ci95'][1]:.3f})")

    beh = manifest.get("behavior")
    if not beh:
        reasons.append("CHƯA CHẠY behavioral test")
    elif not beh.get("passed"):
        failed = [k for k, ok in (("invariance", beh["invariance"]["passed"]), ("mft", beh["mft"]["passed"]),
                                  ("directional", beh["directional_passed"])) if not ok]
        if "directional" in failed:  # nêu rõ nhiễu nào (phiên bản cũ chặn theo mọi nhiễu)
            lim = beh.get("directional_max_downgrade_rate", 0.05)
            bad = [k for k in (beh.get("directional_blocking") or beh["directional"])
                   if beh["directional"].get(k, {}).get("downgrade_rate", 0) > lim]
            failed[failed.index("directional")] = f"directional ({', '.join(bad)})"
        reasons.append("behavioral test KHÔNG ĐẠT: " + ", ".join(failed))
    return reasons

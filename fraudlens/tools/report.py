"""Gom số đo của một phiên bản model thành report.md + hình để đưa thẳng vào báo cáo.

  python -m fraudlens.tools.admin report --version <v>
  python -m fraudlens.tools.admin report --version <v> --out reports/<v> --replay replay_report.json --db

Đọc registry/<v>/manifest.json (val_B: model + mốc luật/LR/cây, hiệu chỉnh trước/sau Platt, slice, behavioral test),
registry/<v>/test_results.json nếu đã chạy final_test, replay_report.json của simulator nếu có (--replay),
và nếu --db: chất lượng brief (eval-briefs) + giá trị sản phẩm (product-metrics) từ PostgreSQL.
Hình: PR curve, reliability diagram trước/sau Platt, recall theo slice kèm CI 95%.
Phần nào thiếu dữ liệu (model cũ chưa có số đo mới, chưa chạy test...) thì ghi rõ là thiếu, không bỏ qua im lặng.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from fraudlens.common.registry import load_json, root  # noqa: E402
from fraudlens.ml.gates import release_gate  # noqa: E402

NAMES = {"model": "XGBoost (FraudLens)", "rules": "M0 Luật viết tay", "lr": "M1 Logistic Regression",
         "tree": "M2 Decision Tree"}


def _f(v, nd: int = 4) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if isinstance(v, int):
        return f"{v:,}"
    return f"{v:.{nd}f}"


def _table(head: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def comparison_table(reports: dict[str, dict]) -> str:
    rows = []
    for name, r in reports.items():
        rk, ci = r["ranking"], r["recall_at_0.01_ci"]["ci95"]
        rows.append([NAMES.get(name, name), _f(rk["pr_auc"]), _f(rk["roc_auc"]),
                     f"{_f(rk['recall_at_0.01'])} [{_f(ci[0], 3)}, {_f(ci[1], 3)}]", _f(rk["precision_at_0.01"]),
                     _f(rk["fp_at_0.01"]), _f(rk["missed_at_0.01"]), _f(r["at_t_medium"]["f1"]),
                     _f(r["routing"]["workload_ratio"], 3)])
    return _table(["Model", "PR-AUC", "ROC-AUC", "Recall@1% [CI 95%]", "Precision@1%", "FP@1%", "Bỏ sót@1%",
                   "F1@t_medium", "Workload"], rows)


def confusion_table(rep: dict) -> str:
    rows = []
    for key, label in (("at_t_medium", "t_medium (MEDIUM+HIGH)"), ("at_t_high", "t_high (HIGH)")):
        t = rep[key]
        rows.append([label, _f(t["threshold"], 5), _f(t["tp"]), _f(t["fp"]), _f(t["fn"]), _f(t["tn"]),
                     _f(t["precision"]), _f(t["recall"]), _f(t["f1"])])
    return _table(["Ngưỡng", "Giá trị", "TP", "FP", "FN", "TN", "Precision", "Recall", "F1"], rows)


def calibration_table(cal: dict) -> str:
    rows = [[when.replace("_", " "), _f(c["brier"], 6), _f(c["ece"], 6), _f(c["log_loss"], 5), _f(c["mean_pred"], 5),
             _f(c["fraud_rate"], 5)] for when, c in cal.items()]
    return _table(["", "Brier", "ECE", "Log loss", "Điểm TB", "Tỉ lệ gian lận thực"], rows)


def slice_table(slices: dict) -> str:
    rows = [[name, _f(s["n"]), _f(s["frauds"]), _f(s["missed"]), _f(s["recall"], 3),
             f"[{_f(s['recall_ci95'][0], 3)}, {_f(s['recall_ci95'][1], 3)}]", "⚠ ít mẫu" if s["small_sample"] else ""]
            for name, s in slices.items()]
    return _table(["Slice", "Số GD", "Gian lận", "Bỏ sót", "Recall", "CI 95%", ""], rows)


# ---------------------------------------------------------------- hình
def plot_pr(reports: dict[str, dict], title: str, path: Path) -> bool:
    curves = {n: r.get("pr_curve") for n, r in reports.items() if r.get("pr_curve", {}).get("recall")}
    if not curves:
        return False
    fig, ax = plt.subplots(figsize=(6, 4.5))
    for n, c in curves.items():
        ax.plot(c["recall"], c["precision"], label=f"{NAMES.get(n, n)} (PR-AUC {reports[n]['ranking']['pr_auc']:.3f})",
                lw=2 if n == "model" else 1.2)
    ax.set(xlabel="Recall", ylabel="Precision", title=title, xlim=(0, 1), ylim=(0, 1.02))
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return True


def plot_reliability(cal: dict, title: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(5.5, 5))
    hi = 0.0
    for when, c in cal.items():
        x = [b["mean_pred"] for b in c["reliability"]]
        y = [b["fraud_rate"] for b in c["reliability"]]
        hi = max(hi, *x, *y)
        ax.plot(x, y, marker="o", ms=4, label=f"{when.replace('_', ' ')} (ECE {c['ece']:.5f})")
    ax.plot([0, hi], [0, hi], "k--", lw=1, label="hiệu chỉnh hoàn hảo")
    ax.set(xlabel="Xác suất dự đoán trung bình (bin)", ylabel="Tỉ lệ gian lận thực (bin)", title=title)
    ax.set_xscale("symlog", linthresh=1e-3)
    ax.set_yscale("symlog", linthresh=1e-3)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_slices(slices: dict, title: str, path: Path) -> None:
    items = [(n, s) for n, s in slices.items() if s["frauds"] > 0 and not n.startswith("state=")]
    fig, ax = plt.subplots(figsize=(7, 0.32 * len(items) + 1.2))
    names = [n for n, _ in items][::-1]
    rec = [s["recall"] for _, s in items][::-1]
    lo = [s["recall"] - s["recall_ci95"][0] for _, s in items][::-1]
    hi = [s["recall_ci95"][1] - s["recall"] for _, s in items][::-1]
    small = [s["small_sample"] for _, s in items][::-1]
    for i, (r, l, h, sm) in enumerate(zip(rec, lo, hi, small)):
        ax.errorbar(r, i, xerr=[[l], [h]], fmt="o", ms=5, capsize=3, color="#aaaaaa" if sm else "#3b6ea8")
    ax.set_yticks(range(len(names)), names)
    left = math.floor(max(0.0, min(s["recall_ci95"][0] for _, s in items) - 0.05) * 10) / 10
    ax.set(xlim=(left, 1.01), xlabel=f"Recall tại ngưỡng top 1% (CI 95%; xám: < 20 ca gian lận; trục bắt đầu ở {left:g})",
           title=title)
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------- các phần của báo cáo
def section_eval(md: list[str], out: Path, tag: str, label: str, model: dict, baselines: dict, cal: dict | None) -> None:
    reports = {"model": model, **baselines}
    md += [f"## {label}", "", "### So sánh với mốc", "", comparison_table(reports), "",
           "Recall@1%: trong 1% giao dịch có điểm cao nhất (năng lực review). Workload = số hồ sơ / năng lực review.", ""]
    missing = {"rules", "lr", "tree"} - set(baselines)
    if missing:
        md += [f"> Thiếu mốc: {', '.join(sorted(missing))} – phiên bản này huấn luyện trước khi có mốc đó, "
               "chạy lại `fraudlens.ml.train` để có đủ.", ""]
    if plot_pr(reports, f"PR curve – {label}", out / f"pr_curve_{tag}.png"):
        md += [f"![PR curve]({f'pr_curve_{tag}.png'})", ""]
    else:
        md += ["> Chưa có dữ liệu PR curve (phiên bản cũ) – huấn luyện lại để có hình.", ""]
    md += ["### Confusion matrix tại ngưỡng vận hành", "", confusion_table(model), ""]
    md += ["### Hiệu chỉnh xác suất", ""]
    if cal:
        md += [calibration_table(cal), ""]
        plot_reliability(cal, f"Reliability – {label}", out / f"reliability_{tag}.png")
        md += [f"![Reliability diagram](reliability_{tag}.png)", ""]
    elif model.get("calibration"):
        md += [calibration_table({"after_platt": model["calibration"]}), "",
               "> Chỉ có số đo sau Platt – phiên bản này chưa ghi số đo trước Platt.", ""]
        plot_reliability({"after_platt": model["calibration"]}, f"Reliability – {label}", out / f"reliability_{tag}.png")
        md += [f"![Reliability diagram](reliability_{tag}.png)", ""]
    sl = model["slices_at_top1pct"]
    md += ["### Recall theo slice (phân tích bỏ sót)", "",
           f"Ngưỡng chung = ngưỡng top 1% toàn tập ({sl['threshold']:.5f}).", "", slice_table(sl["slices"]), ""]
    plot_slices(sl["slices"], f"Recall theo slice – {label}", out / f"slices_{tag}.png")
    md += [f"![Recall theo slice](slices_{tag}.png)", ""]


def section_behavior_gate(md: list[str], manifest: dict, rc: dict) -> None:
    beh = manifest.get("behavior") or {}
    md += ["## Behavioral test và release gate", ""]
    if beh:
        rows = [["Invariance (đổi mã GD / tên merchant)", "ĐẠT" if beh["invariance"]["passed"] else "KHÔNG ĐẠT",
                 f"chênh lệch tối đa {beh['invariance']['max_abs_diff']:.2e}"]]
        # phiên bản cũ (chưa có directional_blocking) chặn theo mọi nhiễu directional
        blocking = beh.get("directional_blocking") or list(beh["directional"])
        for k, v in beh["directional"].items():
            rows.append([f"Directional: {k}", "chặn" if k in blocking else "chỉ báo cáo",
                         f"điểm giảm {v['violation_rate']:.3f}, hạ xuống LOW {v['downgrade_rate']:.3f}"])
        rows.append(["Directional (phần chặn)", "ĐẠT" if beh.get("directional_passed") else "KHÔNG ĐẠT",
                     f"hạ xuống LOW ≤ {beh.get('directional_max_downgrade_rate')} với {', '.join(blocking)}"])
        rows.append(["MFT (ca rõ ràng phải bị gắn cờ)", "ĐẠT" if beh["mft"]["passed"] else "KHÔNG ĐẠT",
                     f"{sum(c['flagged'] for c in beh['mft']['cases'].values())}/{len(beh['mft']['cases'])} ca"])
        md += [_table(["Kiểm thử", "Kết quả", "Chi tiết"], rows), ""]
    reasons = release_gate(manifest, rc)
    md += [f"**Release gate:** {'ĐẠT' if not reasons else 'CHẶN'}", ""]
    md += [f"- {r}" for r in reasons] + ([""] if reasons else [])
    rg = rc.get("release_gate") or {}
    if rg.get("override"):
        md += [f"**Duyệt có override** bởi {rc.get('approved_by')} lúc {rg.get('checked_at')}, khi gate chặn vì: "
               + "; ".join(rg.get("violations") or []) + ".", "", f"> Lý do override: {rg['override']}", ""]
    md += [f"Routing config `{rc.get('config_version')}`: t_medium = {_f(rc.get('t_medium'), 5)}, "
           f"t_high = {_f(rc.get('t_high'), 5)}, duyệt bởi {rc.get('approved_by') or '(chưa duyệt)'}.", ""]


def section_replay(md: list[str], rep: dict) -> None:
    md += ["## Phát lại giao dịch (simulator)", "",
           f"{rep['sent']:,} giao dịch trong {rep['duration_s']:.1f} giây ({rep['throughput_tps']:.1f} GD/giây), "
           f"chạy lúc {rep['run_at']}.", ""]
    rows = [[side, _f(p.get("p50"), 1), _f(p.get("p95"), 1), _f(p.get("p99"), 1), _f(p.get("max"), 1), _f(p["n"])]
            for side, p in (("phía client (gồm mạng)", rep["latency_client_ms"]),
                            ("phía server (API xử lý)", rep["latency_server_ms"]))]
    md += [_table(["Độ trễ (ms)", "p50", "p95", "p99", "max", "n"], rows), ""]
    md += [_table(["Luồng", "Số GD"], [[k, f"{v:,}"] for k, v in sorted(rep["routes"].items())]), ""]
    if rep["faults_by_route"]:
        routes = sorted({r for v in rep["faults_by_route"].values() for r in v})
        md += ["Lỗi tiêm vào × luồng:", "",
               _table(["Lỗi"] + routes, [[f] + [v.get(r, 0) for r in routes] for f, v in rep["faults_by_route"].items()]), ""]
    t = rep["t_fail_01"]
    md += [f"**T-FAIL-01** (không payload lỗi nào thành LOW): {'ĐẠT' if t['passed'] else 'KHÔNG ĐẠT'} – "
           f"{t['faults_injected']} payload lỗi, {sum(t['faults_to_low'].values())} thành LOW.", ""]


def section_db(md: list[str]) -> None:
    from fraudlens.common.db import tx
    from fraudlens.tools.integrity import trace_all
    from fraudlens.tools.product import product_metrics
    from fraudlens.worker.evaluate import evaluate_db
    with tx() as conn:
        ev = evaluate_db(conn, 200)
        pm = product_metrics(conn)
        tr = trace_all(conn)
    md += ["## Chất lượng brief (LLM)", "",
           f"{ev['briefs']} brief, {ev['n_claims']} nhận định; tỉ lệ có căn cứ = {_f(ev['grounded_rate'])} "
           f"(mục tiêu ≥ {ev['target']}): **{'ĐẠT' if ev['meets_target'] else 'CHƯA ĐẠT'}**.", ""]
    if ev.get("by_source"):
        md += [_table(["Nguồn", "Brief", "Nhận định", "Có căn cứ"],
                      [[k, v["briefs"], v["claims"], _f(v["grounded_rate"])] for k, v in ev["by_source"].items()]), ""]
    md += ["## Giá trị sản phẩm", ""]
    rows = [[k, *[_f(v.get(x)) if isinstance(v, dict) else "–" for x in ("n", "median_s", "mean_s")]]
            for k, v in pm["handling_time_by_arm"].items()]
    md += ["Thời gian xử lý hồ sơ (giây), có brief vs nhóm đối chứng không có brief "
           f"(BRIEF_HOLDOUT_FRAC = {pm['holdout_frac']}):", "", _table(["Nhóm", "n", "Trung vị", "Trung bình"], rows), ""]
    bu = pm["brief_useful"]
    md += [f"Tỉ lệ brief được đánh giá hữu ích: {_f(bu['useful_rate'])} trên {bu['n']} đánh giá.", ""]
    md += ["## Truy vết (T-INT-01)", "",
           f"{tr['scope']['transactions']:,} giao dịch, {tr['scope']['cases']:,} hồ sơ; chuỗi băm audit "
           f"{'hợp lệ' if tr['audit_chain']['ok'] else 'SAI'}: **{'ĐẠT' if tr['passed'] else 'KHÔNG ĐẠT'}**.", "",
           _table(["Kiểm tra", "Vi phạm"], [[c["description"], c["violations"]] for c in tr["checks"].values()]), ""]


def build(version: str, out: Path, replay: Path | None = None, db: bool = False) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    manifest = load_json(version, "manifest.json")
    rc = load_json(version, "routing_config.json")
    m = manifest["metrics"]
    d = manifest["data"]
    md = [f"# Báo cáo đánh giá – `{version}`", "",
          f"Feature set `{manifest['feature_set_version']}`, {len(manifest['features'])} feature; "
          f"huấn luyện trên {d['sample_cards']:.0%} số thẻ; hiệu chỉnh {manifest['calibration']['method']} "
          f"fit trên {manifest['calibration']['fit_on']}; {manifest['params']['n_estimators_used']} cây.", ""]
    section_eval(md, out, "val_b", "Validation (val_B)", m["val_b_report"], m.get("baselines_val_b_report") or {},
                 m.get("val_b_calibration"))
    test_path = root() / version / "test_results.json"
    if test_path.exists():
        t = json.loads(test_path.read_text(encoding="utf-8"))
        section_eval(md, out, "test", f"Test set ({t['protocol']['test_rows']:,} GD, chạy một lần)", t["model"],
                     t["baselines"], t.get("calibration"))
    else:
        md += ["## Test set", "", f"> Chưa chạy: `python -m fraudlens.ml.final_test --version {version}` "
               "(chỉ chạy một lần, sau khi Risk Lead duyệt ngưỡng).", ""]
    section_behavior_gate(md, manifest, rc)
    if replay:
        section_replay(md, json.loads(replay.read_text(encoding="utf-8")))
    if db:
        section_db(md)
    path = out / "report.md"
    path.write_text("\n".join(md), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="admin report")
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", default=None, help="thư mục ghi (mặc định reports/<version>)")
    ap.add_argument("--replay", default=None, help="replay_report.json của simulator")
    ap.add_argument("--db", action="store_true", help="thêm chất lượng brief, giá trị sản phẩm, truy vết từ PostgreSQL")
    a = ap.parse_args(argv)
    path = build(a.version, Path(a.out or f"reports/{a.version}"), Path(a.replay) if a.replay else None, a.db)
    print(f"Đã ghi {path} (kèm hình .png cùng thư mục)")


if __name__ == "__main__":
    main()

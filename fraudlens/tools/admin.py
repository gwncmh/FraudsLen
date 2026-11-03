"""Lệnh quản trị nhỏ.

  python -m fraudlens.tools.admin rebuild-redis     # dựng lại lịch sử thẻ trong Redis từ PostgreSQL
  python -m fraudlens.tools.admin verify-audit      # kiểm tra chuỗi băm của nhật ký audit
  python -m fraudlens.tools.admin trace <trans_num> # truy vết một giao dịch: điểm, ngưỡng, hồ sơ, brief, thao tác, audit
  python -m fraudlens.tools.admin trace-all         # T-INT-01: kiểm tra truy vết đủ cho MỌI giao dịch/hồ sơ (thoát mã 1 nếu thiếu)
  python -m fraudlens.tools.admin test-llm          # gọi thử LLM theo cấu hình .env, in rõ lỗi nếu có
  python -m fraudlens.tools.admin list-models       # liệt kê tên model mà key này được dùng
  python -m fraudlens.tools.admin test-brief        # soạn thử brief cho hồ sơ MEDIUM/HIGH mới nhất (không ghi gì)
  python -m fraudlens.tools.admin regen-briefs      # soạn lại brief cho mọi hồ sơ MEDIUM/HIGH đang mở
  python -m fraudlens.tools.admin check-parity [N]  # so feature online với offline trên N giao dịch gần nhất (mặc định 20000)
  python -m fraudlens.tools.admin product-metrics   # thời gian xử lý hồ sơ có/không có brief LLM, tỉ lệ brief hữu ích
  python -m fraudlens.tools.admin eval-briefs [N]   # chấm N brief gần nhất theo rubric "nhận định có căn cứ" (mục tiêu ≥ 90%)
  python -m fraudlens.tools.admin report --version <v> [--out reports/<v>] [--replay replay_report.json] [--db]
                                                    # gom số đo thành report.md + hình cho báo cáo (xem tools/report.py)
"""
from __future__ import annotations

import json
import sys

from fraudlens.api.history import WARM_FLAG, HistoryStore
from fraudlens.common.db import close_pool, tx, verify_audit_chain


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "rebuild-redis":
        h = HistoryStore()
        h.r.delete(WARM_FLAG)
        with tx() as conn:
            print("bản ghi:", h.rebuild_from_db(conn))
    elif cmd == "verify-audit":
        with tx() as conn:
            ok, bad = verify_audit_chain(conn)
        print("chuỗi băm hợp lệ" if ok else f"chuỗi băm SAI từ seq {bad}")
    elif cmd == "trace" and len(sys.argv) > 2:
        tn = sys.argv[2]
        with tx() as conn:
            t = conn.execute("SELECT * FROM transactions WHERE trans_num = %s", (tn,)).fetchone()
            if not t:
                raise SystemExit("không có giao dịch")
            out = {"transaction": t,
                   "score": conn.execute("SELECT status, score, model_version, feature_set_version, error FROM scores WHERE txn_id=%s", (t["id"],)).fetchone(),
                   "routing": conn.execute("SELECT * FROM routing_decisions WHERE txn_id=%s", (t["id"],)).fetchone()}
            c = conn.execute("SELECT * FROM cases WHERE txn_id=%s", (t["id"],)).fetchone()
            if c:
                out["case"] = c
                out["briefs"] = conn.execute("SELECT id, brief_source, llm_model, prompt_version, kb_version, chunk_ids, created_at FROM briefs WHERE case_id=%s", (c["id"],)).fetchall()
                out["actions"] = conn.execute("SELECT * FROM case_actions WHERE case_id=%s ORDER BY id", (c["id"],)).fetchall()
            out["audit"] = conn.execute("SELECT seq, ts, entity, action, actor FROM audit_log WHERE entity_id IN (%s,%s) ORDER BY seq",
                                        (tn, str(c["id"]) if c else "-")).fetchall()
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    elif cmd == "trace-all":
        from fraudlens.tools.integrity import trace_all
        with tx() as conn:
            rep = trace_all(conn)
        print(json.dumps(rep, indent=2, ensure_ascii=False, default=str))
        print("T-INT-01 ĐẠT" if rep["passed"] else "T-INT-01 KHÔNG ĐẠT – xem các kiểm tra có violations > 0")
        if not rep["passed"]:
            raise SystemExit(1)
    elif cmd == "report":
        from fraudlens.tools.report import main as report_main
        report_main(sys.argv[2:])
    elif cmd == "test-llm":
        from fraudlens.common.config import settings
        from fraudlens.worker.llm import LLMClient, LLMUnavailable
        c = LLMClient()
        print(f"base_url = {c.base}\nmodel    = {c.model}\nkey      = {'(trống)' if not c.enabled else settings.llm_api_key.strip()[:6] + '…'}")
        try:
            text, tk = c.chat_json("Trả về JSON.", 'Trả về đúng JSON {"ok": true}')
            print(f"OK – {tk} token, phản hồi: {text[:200]}")
        except LLMUnavailable as e:
            print(f"LỖI: {e}")
    elif cmd == "list-models":
        import httpx
        from fraudlens.common.config import settings
        r = httpx.get(settings.llm_base_url.rstrip("/") + "/models",
                      headers={"Authorization": f"Bearer {settings.llm_api_key.strip()}"}, timeout=20)
        if r.status_code != 200:
            raise SystemExit(f"LỖI HTTP {r.status_code}: {r.text[:400]}")
        ids = sorted(m["id"].removeprefix("models/") for m in r.json().get("data", []))
        lite = [i for i in ids if "lite" in i]
        print("Model Flash-Lite dùng được:", *(lite or ["(không có)"]), sep="\n  ")
        print(f"\n(tổng cộng {len(ids)} model; chạy với --all để xem hết)")
        if "--all" in sys.argv:
            print(*ids, sep="\n")
    elif cmd == "test-brief":
        import httpx
        from fraudlens.common.config import settings
        from fraudlens.worker.brief import BriefWriter
        from fraudlens.worker.main import CASE_SQL, explain
        with tx() as conn:
            row = conn.execute("SELECT id FROM cases WHERE route IN ('HIGH','MEDIUM') ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                raise SystemExit("chưa có hồ sơ MEDIUM/HIGH")
            case = conn.execute(CASE_SQL, (row["id"],)).fetchone()
        res = BriefWriter().write(dict(case), explain(httpx.Client(timeout=5), case["features"]))
        print(json.dumps({"case_id": row["id"], "brief_source": res["brief_source"], "guardrail": res["guardrail"],
                          "brief": res["brief"]}, indent=2, ensure_ascii=False, default=str))
    elif cmd == "regen-briefs":
        with tx() as conn:
            n = conn.execute("INSERT INTO brief_jobs (case_id) SELECT id FROM cases WHERE route IN ('HIGH','MEDIUM') "
                             "AND status NOT LIKE 'CLOSED%'").rowcount
        print(f"đã xếp {n} hồ sơ vào hàng đợi soạn lại brief")
    elif cmd == "check-parity":
        from fraudlens.tools.parity import compare, load_from_db
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
        with tx() as conn:
            df, online = load_from_db(conn, limit)
        if not online:
            raise SystemExit("chưa có giao dịch đã chấm")
        rep = compare(df, online)
        print(json.dumps(rep, indent=2, ensure_ascii=False, default=str))
        print("KHỚP" if not rep["mismatched_rows"] else
              f"LỆCH {rep['mismatched_rows']}/{rep['compared']} giao dịch – xem per_feature; kiểm tra khóa theo thẻ và thứ tự gửi")
    elif cmd == "product-metrics":
        from fraudlens.tools.product import product_metrics
        with tx() as conn:
            print(json.dumps(product_metrics(conn), indent=2, ensure_ascii=False, default=str))
    elif cmd == "eval-briefs":
        from fraudlens.worker.evaluate import evaluate_db
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 200
        export = sys.argv[sys.argv.index("--export") + 1] if "--export" in sys.argv else None
        with tx() as conn:
            rep = evaluate_db(conn, limit, export)
        print(json.dumps({k: v for k, v in rep.items() if k != "claims"}, indent=2, ensure_ascii=False))
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main()
    finally:
        close_pool()

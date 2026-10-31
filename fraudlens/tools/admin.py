"""Lệnh quản trị nhỏ.

  python -m fraudlens.tools.admin rebuild-redis     # dựng lại lịch sử thẻ trong Redis từ PostgreSQL
  python -m fraudlens.tools.admin verify-audit      # kiểm tra chuỗi băm của nhật ký audit
  python -m fraudlens.tools.admin trace <trans_num> # truy vết một giao dịch: điểm, ngưỡng, hồ sơ, brief, thao tác, audit
  python -m fraudlens.tools.admin test-llm          # gọi thử LLM theo cấu hình .env, in rõ lỗi nếu có
  python -m fraudlens.tools.admin list-models       # liệt kê tên model mà key này được dùng
  python -m fraudlens.tools.admin test-brief        # soạn thử brief cho hồ sơ MEDIUM/HIGH mới nhất (không ghi gì)
  python -m fraudlens.tools.admin regen-briefs      # soạn lại brief cho mọi hồ sơ MEDIUM/HIGH đang mở
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
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main()
    finally:
        close_pool()

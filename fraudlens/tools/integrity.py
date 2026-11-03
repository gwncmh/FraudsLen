"""T-INT-01: truy vết được 100% giao dịch và hồ sơ (chạy trên toàn DB, không phải từng mã như admin trace).

  python -m fraudlens.tools.admin trace-all

Mỗi kiểm tra là một câu SQL đếm bản ghi VI PHẠM (kèm vài id mẫu); đạt khi mọi kiểm tra = 0 và chuỗi băm audit hợp lệ.
Giao dịch SEED (chỉ làm lịch sử, không chấm điểm) không thuộc phạm vi.
"""
from __future__ import annotations

from fraudlens.common.db import verify_audit_chain

# tên -> (mô tả, SQL trả về các cột id của bản ghi vi phạm)
CHECKS: dict[str, tuple[str, str]] = {
    "txn_without_score": (
        "giao dịch không có dòng scores",
        "SELECT t.id FROM transactions t LEFT JOIN scores s ON s.txn_id = t.id "
        "WHERE t.status <> 'SEED' AND s.txn_id IS NULL"),
    "txn_without_routing": (
        "giao dịch không có quyết định phân luồng",
        "SELECT t.id FROM transactions t LEFT JOIN routing_decisions d ON d.txn_id = t.id "
        "WHERE t.status <> 'SEED' AND d.txn_id IS NULL"),
    "flagged_without_case": (
        "MEDIUM/HIGH/UNSCORED nhưng không có hồ sơ",
        "SELECT d.txn_id AS id FROM routing_decisions d LEFT JOIN cases c ON c.txn_id = d.txn_id "
        "WHERE d.route IN ('MEDIUM','HIGH','UNSCORED') AND c.id IS NULL"),
    "case_route_mismatch": (
        "luồng của hồ sơ khác quyết định phân luồng",
        "SELECT c.id FROM cases c JOIN routing_decisions d ON d.txn_id = c.txn_id WHERE c.route <> d.route"),
    "txn_without_audit": (
        "giao dịch không có dòng audit ROUTED",
        "SELECT t.id FROM transactions t WHERE t.status <> 'SEED' AND NOT EXISTS ("
        "SELECT 1 FROM audit_log a WHERE a.entity = 'transaction' AND a.action = 'ROUTED' "
        "AND a.entity_id = COALESCE(t.trans_num, t.id::text))"),
    "action_without_audit": (
        "thao tác trên hồ sơ nhiều hơn số dòng audit tương ứng",
        "SELECT x.case_id AS id FROM (SELECT case_id, action, count(*) AS n FROM case_actions GROUP BY 1, 2) x "
        "LEFT JOIN (SELECT entity_id, action, count(*) AS n FROM audit_log WHERE entity = 'case' GROUP BY 1, 2) a "
        "ON a.entity_id = x.case_id::text AND a.action = x.action WHERE COALESCE(a.n, 0) < x.n"),
    "brief_without_audit": (
        "brief nhiều hơn số dòng audit BRIEF_CREATED",
        "SELECT b.case_id AS id FROM (SELECT case_id, count(*) AS n FROM briefs GROUP BY 1) b "
        "LEFT JOIN (SELECT entity_id, count(*) AS n FROM audit_log WHERE entity = 'case' AND action = 'BRIEF_CREATED' "
        "GROUP BY 1) a ON a.entity_id = b.case_id::text WHERE COALESCE(a.n, 0) < b.n"),
    "closed_without_human": (
        "hồ sơ đã đóng mà không có thao tác CLOSE của người dùng",
        "SELECT c.id FROM cases c WHERE c.status LIKE 'CLOSED%' AND NOT EXISTS ("
        "SELECT 1 FROM case_actions x WHERE x.case_id = c.id AND x.action LIKE 'CLOSE%' AND x.role IN ('analyst','risk_lead'))"),
}


def trace_all(conn, sample: int = 5) -> dict:
    out = {"scope": {
        "transactions": conn.execute("SELECT count(*) AS n FROM transactions WHERE status <> 'SEED'").fetchone()["n"],
        "cases": conn.execute("SELECT count(*) AS n FROM cases").fetchone()["n"]}, "checks": {}}
    for name, (desc, sql) in CHECKS.items():
        n = conn.execute(f"SELECT count(*) AS n FROM ({sql}) q").fetchone()["n"]
        ids = [r["id"] for r in conn.execute(f"SELECT id FROM ({sql}) q ORDER BY id LIMIT {int(sample)}").fetchall()]
        out["checks"][name] = {"description": desc, "violations": n, "sample_ids": ids}
    ok, bad = verify_audit_chain(conn)
    out["audit_chain"] = {"ok": ok, "first_bad_seq": bad}
    out["passed"] = ok and all(c["violations"] == 0 for c in out["checks"].values())
    return out

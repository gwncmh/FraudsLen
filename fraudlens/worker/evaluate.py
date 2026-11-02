"""Đánh giá chất lượng LLM theo rubric (mục 4.2: ≥ 90% nhận định có căn cứ) và đánh giá truy xuất playbook.

Rubric tự động cho một nhận định (mỗi phần tử summary / risk_signals của brief):
  có căn cứ  <=>  có trường căn cứ hợp lệ  AND  mọi con số có trong dữ liệu  AND  không trái dữ liệu
               AND  không có cụm từ kết luận/ra lệnh  (cùng tiêu chí với guardrail.grade_claim)
Chấm trên brief ĐÃ LƯU (cả brief LLM lẫn brief mẫu), dữ liệu dựng lại đúng như lúc soạn (build_data).
--export ghi CSV từng nhận định kèm cột human_grounded để người chấm đối chiếu (đo độ khớp rubric tự động vs người).

  python -m fraudlens.tools.admin eval-briefs 200 [--export briefs_eval.csv]
"""
from __future__ import annotations

import csv
from collections import Counter

from fraudlens.worker.brief import build_data, signals_of
from fraudlens.worker.guardrail import _data_numbers, grade_claim
from fraudlens.worker.retrieval import Retriever, build_query

TARGET = 0.90


def claims_of(brief: dict) -> list[tuple[str, str, list]]:
    out = []
    for s in brief.get("summary", []):
        if isinstance(s, dict):
            out.append(("summary", str(s.get("text", "")), s.get("evidence")))
    for s in brief.get("risk_signals", []):
        if isinstance(s, dict):
            out.append(("risk_signal", str(s.get("signal", "")), s.get("evidence")))
    return out


def evaluate_briefs(items: list[dict]) -> dict:
    """items: [{"brief_id", "case_id", "source", "brief", "data"}]. Trả về tỉ lệ nhận định có căn cứ, theo nguồn."""
    claims, by_src, errs = [], {}, Counter()
    for it in items:
        pool = _data_numbers(it["data"])
        for kind, text, ev in claims_of(it["brief"]):
            e = grade_claim(text, ev, it["data"], pool)
            ok = not e
            claims.append({"brief_id": it["brief_id"], "case_id": it["case_id"], "source": it["source"], "kind": kind,
                           "text": text, "evidence": ev, "auto_grounded": ok, "auto_errors": e})
            st = by_src.setdefault(it["source"], {"claims": 0, "grounded": 0, "briefs": set()})
            st["claims"] += 1
            st["grounded"] += ok
            st["briefs"].add(it["brief_id"])
            errs.update(x.split(":")[0] for x in e)
    n = len(claims)
    g = sum(c["auto_grounded"] for c in claims)
    return {
        "briefs": len(items), "claims": claims, "n_claims": n,
        "grounded_rate": g / n if n else float("nan"), "target": TARGET, "meets_target": bool(n and g / n >= TARGET),
        "by_source": {k: {"briefs": len(v["briefs"]), "claims": v["claims"], "grounded_rate": v["grounded"] / v["claims"]}
                      for k, v in by_src.items()},
        "error_counts": dict(errs),
    }


EVAL_SQL = """
SELECT b.id AS brief_id, b.brief, b.brief_source, b.factors, c.id, c.route, t.card_last4, t.merchant, t.category, t.amt,
       t.validation_errors, s.status AS score_status, s.score, s.features, s.error AS score_error
FROM briefs b JOIN cases c ON c.id = b.case_id JOIN transactions t ON t.id = c.txn_id JOIN scores s ON s.txn_id = t.id
ORDER BY b.id DESC LIMIT %s
"""


def evaluate_db(conn, limit: int = 200, export: str | None = None) -> dict:
    items = []
    for r in conn.execute(EVAL_SQL, (limit,)).fetchall():
        r = dict(r)
        items.append({"brief_id": r["brief_id"], "case_id": r["id"], "source": r["brief_source"], "brief": r["brief"],
                      "data": build_data(r, r["factors"])})
    rep = evaluate_briefs(items)
    if export:
        with open(export, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["brief_id", "case_id", "source", "kind", "text", "evidence", "auto_grounded", "auto_errors",
                        "human_grounded"])
            for c in rep["claims"]:
                w.writerow([c["brief_id"], c["case_id"], c["source"], c["kind"], c["text"], ";".join(map(str, c["evidence"] or [])),
                            int(c["auto_grounded"]), ";".join(c["auto_errors"]), ""])
        rep["exported"] = export
    return rep


# ------------------------------------------------------------------ truy xuất: mỗi tín hiệu phải kéo đúng mục playbook
RETRIEVAL_CASES = [
    ("amount_spike", {"route": "HIGH", "category": "grocery_pos", "amt_ratio_30d": 8.0, "amt": 300}, "PB-02"),
    ("velocity", {"route": "HIGH", "category": "gas_transport", "n_txn_1h": 5, "n_txn_24h": 12}, "PB-03"),
    ("night", {"route": "MEDIUM", "category": "entertainment", "is_night": 1, "hour": 23}, "PB-04"),
    ("new_category", {"route": "MEDIUM", "category": "travel", "is_new_category_30d": 1}, "PB-05"),
    ("new_merchant", {"route": "MEDIUM", "category": "food_dining", "is_new_merchant_30d": 1}, "PB-05"),
    ("far", {"route": "MEDIUM", "category": "travel", "distance_km": 140.0}, "PB-06"),
    ("online", {"route": "HIGH", "category": "shopping_net"}, "PB-07"),
    ("low_history", {"route": "MEDIUM", "category": "kids_pets", "n_txn_30d": 1}, "PB-13"),
    ("unscored", {"route": "UNSCORED", "category": ""}, "PB-11"),
]


def retrieval_eval(retriever: Retriever, k: int) -> dict:
    """hit@k: mục playbook tương ứng tín hiệu có nằm trong top-k đoạn truy xuất không (truy vấn dựng như worker)."""
    rows = {}
    for name, fields, expected in RETRIEVAL_CASES:
        sigs = [t for t, _ in signals_of(fields)]
        got = [c.id for c in retriever.search(build_query(fields, sigs), k)]
        rows[name] = {"expected": expected, "retrieved": got, "hit": expected in got,
                      "rank": got.index(expected) + 1 if expected in got else None}
    hits = sum(r["hit"] for r in rows.values())
    return {"k": k, "hit_rate": hits / len(rows), "cases": rows}

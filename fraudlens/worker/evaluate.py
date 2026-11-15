"""Đánh giá chất lượng LLM theo rubric (mục 4.2: ≥ 90% nhận định có căn cứ) và đánh giá truy xuất playbook.

Rubric tự động cho một nhận định (mỗi phần tử summary / risk_signals của brief):
  có căn cứ  <=>  có trường căn cứ hợp lệ  AND  mọi con số có trong dữ liệu  AND  không trái dữ liệu
               AND  không có cụm từ kết luận/ra lệnh  (cùng tiêu chí với guardrail.grade_claim)
Chấm trên brief ĐÃ LƯU (cả brief LLM lẫn brief mẫu), dữ liệu dựng lại đúng như lúc soạn (build_data).

Lưu ý khi đọc số: brief LLM được lưu đã ĐI QUA guardrail, mà guardrail dùng đúng rubric này, nên grounded_rate của
nguồn "llm" gần như luôn ~100% – con số đó chỉ nói guardrail chạy đúng, không nói LLM viết tốt. Hai số đo độc lập hơn:
- llm_raw (guardrail_stats): tỉ lệ đầu ra THÔ của LLM qua kiểm tra ngay lần đầu / sau khi sinh lại / phải dùng brief mẫu,
  lấy từ briefs.guardrail (số lần gọi và lý do bị loại) – đo chất lượng LLM trước khi lọc.
- human (human_agreement): người chấm điền cột human_grounded (1/0) trong file --export, rồi --import để tính tỉ lệ có
  căn cứ theo NGƯỜI và độ khớp rubric tự động với người (accuracy, Cohen's kappa, số ca rubric bỏ lọt).

  python -m fraudlens.tools.admin eval-briefs 200 [--export briefs_eval.csv]
  python -m fraudlens.tools.admin eval-briefs --import briefs_eval.csv      # sau khi người chấm điền human_grounded
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


def guardrail_stats(rows: list[dict]) -> dict:
    """Chất lượng đầu ra THÔ của LLM, trước khi guardrail lọc. rows: [{"source", "guardrail": {"attempts", "rejections"}}].
    Chỉ tính brief có gọi LLM (attempts > 0) và LLM có trả lời (không tính lần LLM không khả dụng)."""
    called = first_ok = retry_ok = rejected = unavailable = 0
    reasons: Counter = Counter()
    for r in rows:
        g = r.get("guardrail") or {}
        rej = [x for x in (g.get("rejections") or []) if isinstance(x, list)]
        if not g.get("attempts"):
            continue
        if any(str(e).startswith("LLM_UNAVAILABLE") for x in rej for e in x):
            unavailable += 1
            continue
        called += 1
        reasons.update(str(e).split(":")[0] for x in rej for e in x)
        if r["source"] == "llm":
            first_ok += not rej
            retry_ok += bool(rej)
        else:
            rejected += 1
    return {"briefs_llm_answered": called, "llm_unavailable": unavailable,
            "accepted_first_try": first_ok, "accepted_after_retry": retry_ok, "rejected_to_fallback": rejected,
            "first_try_pass_rate": first_ok / called if called else None,
            "final_pass_rate": (first_ok + retry_ok) / called if called else None,
            "rejection_reasons": dict(reasons)}


def human_agreement(path: str) -> dict:
    """Đọc file --export đã có cột human_grounded (1/0, bỏ trống = chưa chấm). So rubric tự động với người chấm."""
    truthy, falsy = {"1", "true", "yes", "y", "co", "có", "x"}, {"0", "false", "no", "n", "khong", "không"}
    pairs, by_src, skipped = [], {}, 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        # Excel với định dạng vùng Việt Nam lưu CSV bằng dấu ';' (hoặc tab khi lưu "Unicode Text"): đoán từ dòng tiêu đề
        header = f.readline()
        delim = max((",", ";", "\t"), key=header.count)
        f.seek(0)
        for row in csv.DictReader(f, delimiter=delim):
            h = (row.get("human_grounded") or "").strip().lower()
            if h not in truthy | falsy:
                skipped += 1
                continue
            a, hv = (row.get("auto_grounded") or "").strip().lower() in ("1", "true"), h in truthy
            pairs.append((a, hv))
            st = by_src.setdefault(row.get("source") or "?", [0, 0])
            st[0] += 1
            st[1] += hv
    n = len(pairs)
    if not n:
        return {"graded": 0, "ungraded": skipped, "message": "chưa có dòng nào được chấm: điền 1/0 vào cột human_grounded"}
    tp = sum(a and h for a, h in pairs)
    tn = sum((not a) and (not h) for a, h in pairs)
    fp = sum(a and (not h) for a, h in pairs)   # rubric cho qua nhưng người chấm là KHÔNG có căn cứ (nguy hiểm nhất)
    fn = sum((not a) and h for a, h in pairs)
    po = (tp + tn) / n
    pe = ((tp + fp) / n) * ((tp + fn) / n) + ((tn + fn) / n) * ((tn + fp) / n)
    human_rate = (tp + fn) / n
    return {"graded": n, "ungraded": skipped,
            "human_grounded_rate": human_rate, "target": TARGET, "meets_target": human_rate >= TARGET,
            "auto_grounded_rate": (tp + fp) / n,
            "agreement": po, "cohen_kappa": (po - pe) / (1 - pe) if pe < 1 else 1.0,
            "confusion": {"auto_ok_human_ok": tp, "auto_ok_human_bad": fp, "auto_bad_human_ok": fn, "auto_bad_human_bad": tn},
            "by_source": {k: {"graded": v[0], "human_grounded_rate": v[1] / v[0]} for k, v in by_src.items()}}


EVAL_SQL = """
SELECT b.id AS brief_id, b.brief, b.brief_source, b.factors, b.guardrail, c.id, c.route, t.card_last4, t.merchant, t.category, t.amt,
       t.validation_errors, s.status AS score_status, s.score, s.features, s.error AS score_error
FROM briefs b JOIN cases c ON c.id = b.case_id JOIN transactions t ON t.id = c.txn_id JOIN scores s ON s.txn_id = t.id
ORDER BY b.id DESC LIMIT %s
"""


def evaluate_db(conn, limit: int = 200, export: str | None = None) -> dict:
    items, raw = [], []
    for r in conn.execute(EVAL_SQL, (limit,)).fetchall():
        r = dict(r)
        items.append({"brief_id": r["brief_id"], "case_id": r["id"], "source": r["brief_source"], "brief": r["brief"],
                      "data": build_data(r, r["factors"])})
        raw.append({"source": r["brief_source"], "guardrail": r["guardrail"]})
    rep = evaluate_briefs(items)
    rep["llm_raw"] = guardrail_stats(raw)
    rep["note"] = ("grounded_rate chấm brief ĐÃ qua guardrail (cùng rubric) nên với nguồn llm luôn gần 100%; "
                   "xem llm_raw.first_try_pass_rate và kết quả người chấm (--export rồi --import) để đánh giá LLM.")
    if export:
        export_csv(export, rep["claims"], {it["brief_id"]: it["data"] for it in items})
        rep["exported"] = export
    return rep


def data_text(data: dict) -> str:
    """Dữ liệu brief được cung cấp, dạng 'trường = giá trị' mỗi dòng – để người chấm đối chiếu ngay trong file."""
    rows = [f"route = {data.get('route')}", f"score = {data.get('score')}", f"score_status = {data.get('score_status')}"]
    if data.get("errors"):
        rows.append(f"errors = {data['errors']}")
    rows += [f"{k} = {v}" for k, v in (data.get("fields") or {}).items()]
    if data.get("top_factors"):
        rows.append("top_factors = " + ", ".join(f"{x['feature']} ({x['direction']})" for x in data["top_factors"]))
    return "\n".join(rows)


# Cột cho người chấm đứng trước; cột chấm tự động để cuối (người chấm nên ẩn đi để không bị ảnh hưởng).
# human_agreement đọc theo TÊN cột nên thứ tự cột không quan trọng khi nhập lại.
EXPORT_COLUMNS = ["brief_id", "case_id", "source", "kind", "text", "evidence", "data", "human_grounded", "note",
                  "auto_grounded", "auto_errors"]


def export_csv(path: str, claims: list[dict], data_by_brief: dict) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(EXPORT_COLUMNS)
        for c in claims:
            w.writerow([c["brief_id"], c["case_id"], c["source"], c["kind"], c["text"],
                        ";".join(map(str, c["evidence"] or [])), data_text(data_by_brief[c["brief_id"]]), "", "",
                        int(c["auto_grounded"]), ";".join(c["auto_errors"])])


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

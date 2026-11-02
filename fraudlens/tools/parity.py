"""So sánh feature online (scores.features lưu lúc chấm) với feature tính lại offline bằng build_offline()
trên cùng các giao dịch trong PostgreSQL. Lệch nghĩa là online đã thấy lịch sử khác offline: xử lý song song
cùng thẻ, giao dịch đến sai thứ tự, Redis mất dữ liệu...

  python -m fraudlens.tools.admin check-parity [--limit 20000]
"""
from __future__ import annotations

import math

import pandas as pd

from fraudlens.common.features import FEATURES, WINDOW_S, build_offline


def _equal(a, b) -> bool:
    if isinstance(a, str) or isinstance(b, str):
        return str(a) == str(b)
    a, b = float(a), float(b)
    return a == b or math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)


def compare(txns: pd.DataFrame, online: dict[int, dict], examples: int = 10) -> dict:
    """txns: mọi giao dịch VALID/SEED cần để dựng lịch sử (cột id, card_hash, event_ts, trans_num, amt, category,
    merchant, age, distance_km, city_pop). online: {id: feature dict lưu lúc chấm} cho các giao dịch cần so."""
    off = build_offline(txns.set_index("id", drop=False))
    per_feature = {f: 0 for f in FEATURES}
    bad_rows, ex = 0, []
    for tid, f_on in online.items():
        if tid not in off.index:
            continue
        row = off.loc[tid]
        diff = [c for c in FEATURES if c in f_on and not _equal(row[c], f_on[c])]
        for c in diff:
            per_feature[c] += 1
        if diff:
            bad_rows += 1
            if len(ex) < examples:
                ex.append({"txn_id": int(tid), "diff": {c: {"online": f_on[c], "offline": _jsonable(row[c])} for c in diff}})
    n = len(online)
    return {"compared": n, "mismatched_rows": bad_rows, "mismatch_rate": bad_rows / n if n else 0.0,
            "per_feature": {k: v for k, v in per_feature.items() if v}, "examples": ex}


def _jsonable(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return str(v)


def load_from_db(conn, limit: int) -> tuple[pd.DataFrame, dict[int, dict]]:
    """Lấy `limit` giao dịch đã chấm gần nhất + toàn bộ lịch sử 30 ngày của các thẻ đó."""
    rows = conn.execute(
        "SELECT t.id, t.card_hash, t.event_ts, s.features FROM transactions t JOIN scores s ON s.txn_id = t.id "
        "WHERE t.status = 'VALID' AND s.features IS NOT NULL ORDER BY t.event_ts DESC LIMIT %s", (limit,)).fetchall()
    if not rows:
        return pd.DataFrame(), {}
    online = {r["id"]: r["features"] for r in rows}
    cards = list({r["card_hash"] for r in rows})
    t_min = min(r["event_ts"] for r in rows) - WINDOW_S
    t_max = max(r["event_ts"] for r in rows)
    hist = conn.execute(
        "SELECT id, card_hash, event_ts, trans_num, amt, category, merchant, age, distance_km, city_pop FROM transactions "
        "WHERE status IN ('VALID','SEED') AND card_hash = ANY(%s) AND event_ts BETWEEN %s AND %s",
        (cards, t_min, t_max)).fetchall()
    df = pd.DataFrame(hist)
    df["amt"] = df["amt"].astype(float)
    return df, online

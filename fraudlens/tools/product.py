"""Số đo giá trị sản phẩm (mục 4.2): thời gian xử lý hồ sơ có và không có brief, tỉ lệ brief được đánh giá hữu ích.

- Thời gian xử lý = từ lần đầu chuyên viên mở hồ sơ (VIEW) tới lúc đóng (CLOSE_FRAUD / CLOSE_LEGIT), lấy từ case_actions.
- Nhóm so sánh theo brief chuyên viên THẤY lúc mở hồ sơ: llm / fallback / none (brief chưa soạn xong),
  và theo nhóm đối chứng ngẫu nhiên (BRIEF_HOLDOUT_FRAC: hồ sơ bị ẩn brief) – so sánh nhân quả sạch hơn quan sát.
- Hữu ích: nút BRIEF_USEFUL / BRIEF_NOT_USEFUL, mỗi (hồ sơ, chuyên viên) lấy lần bấm cuối.
"""
from __future__ import annotations

import numpy as np

from fraudlens.common.config import settings

HANDLING_SQL = """
WITH v AS (SELECT case_id, min(created_at) AS first_view FROM case_actions WHERE action = 'VIEW' GROUP BY case_id),
     cl AS (SELECT case_id, min(created_at) AS closed_at FROM case_actions
            WHERE action IN ('CLOSE_FRAUD','CLOSE_LEGIT') GROUP BY case_id)
SELECT c.id, c.route, EXTRACT(EPOCH FROM cl.closed_at - v.first_view) AS handling_s,
       (SELECT b.brief_source FROM briefs b WHERE b.case_id = c.id AND b.created_at <= v.first_view
        ORDER BY b.created_at DESC LIMIT 1) AS brief_at_view
FROM cases c JOIN v ON v.case_id = c.id JOIN cl ON cl.case_id = c.id
"""
FEEDBACK_SQL = """
SELECT f.case_id, f.action,
       (SELECT b.brief_source FROM briefs b WHERE b.case_id = f.case_id AND b.created_at <= f.created_at
        ORDER BY b.created_at DESC LIMIT 1) AS brief_source
FROM (SELECT DISTINCT ON (case_id, actor) case_id, actor, action, created_at FROM case_actions
      WHERE action IN ('BRIEF_USEFUL','BRIEF_NOT_USEFUL') ORDER BY case_id, actor, id DESC) f
"""


def is_holdout(case_id: int, frac: float | None = None) -> bool:
    """Gán nhóm đối chứng cố định theo case_id (băm nhân Knuth), không cần lưu thêm cột."""
    frac = settings.brief_holdout_frac if frac is None else frac
    return frac > 0 and (int(case_id) * 2654435761 % 2**32) / 2**32 < frac


def _summary(v: list[float]) -> dict:
    if not v:
        return {"n": 0}
    a = np.array(v, dtype=float)
    return {"n": len(a), "median_s": float(np.median(a)), "p75_s": float(np.percentile(a, 75)), "mean_s": float(a.mean())}


def product_metrics(conn) -> dict:
    rows = conn.execute(HANDLING_SQL).fetchall()
    by_brief: dict[str, list[float]] = {}
    by_arm: dict[str, list[float]] = {"brief_shown": [], "holdout_no_brief": []}
    for r in rows:
        h = float(r["handling_s"])
        holdout = is_holdout(r["id"])
        by_brief.setdefault("holdout" if holdout else (r["brief_at_view"] or "none"), []).append(h)
        by_arm["holdout_no_brief" if holdout else "brief_shown"].append(h)
    fb = conn.execute(FEEDBACK_SQL).fetchall()
    useful: dict[str, list[int]] = {}
    for r in fb:
        useful.setdefault(r["brief_source"] or "none", []).append(int(r["action"] == "BRIEF_USEFUL"))
    allv = [x for v in useful.values() for x in v]
    return {
        "handling_time_by_brief_at_view": {k: _summary(v) for k, v in by_brief.items()},
        "handling_time_by_arm": {k: _summary(v) for k, v in by_arm.items()},
        "holdout_frac": settings.brief_holdout_frac,
        "brief_useful": {"n": len(allv), "useful_rate": (sum(allv) / len(allv)) if allv else None,
                         "by_source": {k: {"n": len(v), "useful_rate": sum(v) / len(v)} for k, v in useful.items()}},
    }

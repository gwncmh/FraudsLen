"""Kiểm tra đầu ra LLM bằng code trước khi hiển thị cho chuyên viên.

Brief bị từ chối nếu: sai schema, có con số không có trong dữ liệu, trích nguồn không thuộc các đoạn
đã truy xuất, dùng trường dữ liệu không tồn tại làm căn cứ, có cụm từ kết luận/ra lệnh, hoặc thiếu disclaimer.
"""
from __future__ import annotations

import json
import re

FORBIDDEN = [
    "chắc chắn là gian lận", "chắc chắn gian lận", "đã xác nhận gian lận", "khách hàng là kẻ gian", "kẻ lừa đảo",
    "khóa thẻ ngay", "chặn giao dịch ngay", "hoàn tiền ngay", "fraud confirmed", "definitely fraud",
    "bỏ qua hướng dẫn", "ignore previous",
]
ALLOWED_CONSTANTS = {0, 1, 3, 6, 7, 22, 24, 30, 100}  # cửa sổ thời gian, giờ khuya... dùng trong câu chữ
NUM_RE = re.compile(r"(?<![\w-])-?\d[\d.,]*")


def _to_float(tok: str) -> float | None:
    t = tok.strip(".,")
    if not t:
        return None
    if "," in t and "." in t:  # 1.234,56 hoặc 1,234.56
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        parts = t.split(",")
        t = t.replace(",", "") if all(len(p) == 3 for p in parts[1:]) else t.replace(",", ".")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def _data_numbers(data) -> list[float]:
    out = []

    def walk(v):
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            out.extend([float(v), round(float(v), 2), round(float(v), 1), round(float(v)), round(float(v) * 100, 2),
                        round(float(v) * 100, 1), round(float(v) * 100)])
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            for tok in NUM_RE.findall(v):
                f = _to_float(tok)
                if f is not None:
                    out.append(f)
    walk(data)
    return out


def _num_ok(x: float, pool: list[float]) -> bool:
    if x in ALLOWED_CONSTANTS:
        return True
    return any(abs(x - p) <= max(0.011, abs(p) * 0.01) for p in pool)


def check(text: str, data: dict, allowed_sources: set[str]) -> tuple[dict | None, list[str]]:
    errs: list[str] = []
    try:
        b = json.loads(text)
    except Exception:  # noqa: BLE001
        return None, ["JSON_INVALID"]
    if not isinstance(b, dict):
        return None, ["JSON_NOT_OBJECT"]
    for k, typ in (("summary", list), ("risk_signals", list), ("checklist", list), ("limitations", list), ("disclaimer", str)):
        if not isinstance(b.get(k), typ):
            errs.append(f"SCHEMA:{k}")
    if errs:
        return None, errs

    fields = set(data.get("fields", {}).keys())
    texts = []
    for i, s in enumerate(b["summary"]):
        if not isinstance(s, dict) or not isinstance(s.get("text"), str):
            errs.append(f"SCHEMA:summary[{i}]"); continue
        texts.append(s["text"])
        bad = [e for e in s.get("evidence", []) if e not in fields]
        if bad:
            errs.append(f"EVIDENCE_UNKNOWN:{bad}")
    for i, s in enumerate(b["risk_signals"]):
        if not isinstance(s, dict) or not isinstance(s.get("signal"), str):
            errs.append(f"SCHEMA:risk_signals[{i}]"); continue
        texts.append(s["signal"])
        bad = [e for e in s.get("evidence", []) if e not in fields]
        if bad:
            errs.append(f"EVIDENCE_UNKNOWN:{bad}")
    for i, c in enumerate(b["checklist"]):
        if not isinstance(c, dict) or not isinstance(c.get("step"), str):
            errs.append(f"SCHEMA:checklist[{i}]"); continue
        if c.get("source") not in allowed_sources:
            errs.append(f"SOURCE_NOT_RETRIEVED:{c.get('source')}")
        texts.append(c["step"])
    texts += [x for x in b["limitations"] if isinstance(x, str)]
    if len(b["disclaimer"].strip()) < 10:
        errs.append("DISCLAIMER_MISSING")

    pool = _data_numbers(data)
    for t in texts:
        for tok in NUM_RE.findall(t):
            f = _to_float(tok)
            if f is not None and not _num_ok(f, pool):
                errs.append(f"NUMBER_NOT_IN_DATA:{tok}")
    low = " ".join(texts + [b["disclaimer"]]).lower()
    errs += [f"FORBIDDEN:{p}" for p in FORBIDDEN if p in low]
    return (b if not errs else None), errs

"""Kiểm tra đầu ra LLM bằng code trước khi hiển thị cho chuyên viên (test oracle cho LLM – Lec 5).

Brief bị từ chối nếu:
- sai schema, hoặc thiếu disclaimer;
- một nhận định (summary / risk_signals) không ghi trường căn cứ, hoặc ghi trường không tồn tại trong DATA;
- có con số không có trong dữ liệu;
- nhận định trái với dữ liệu (ví dụ nói "merchant mới" trong khi is_new_merchant_30d = 0);
- trích nguồn không thuộc các đoạn playbook đã truy xuất;
- có cụm từ kết luận gian lận / ra lệnh / chèn chỉ dẫn. So khớp trên văn bản đã bỏ dấu, gộp khoảng trắng và theo
  mẫu (regex), nên viết khác đi một chút ("gian lận đã được xác nhận") vẫn bị bắt; có xét phủ định ("chưa xác nhận").
"""
from __future__ import annotations

import json
import re
import unicodedata

ALLOWED_CONSTANTS = {0, 1, 3, 6, 7, 22, 24, 30, 100}  # cửa sổ thời gian, giờ khuya... dùng trong câu chữ
NUM_RE = re.compile(r"(?<![\w-])-?\d[\d.,]*")
META_EVIDENCE = {"score", "route", "score_status", "errors"}  # trường cấp hồ sơ, ngoài DATA.fields
ONLINE_CATS = {"shopping_net", "misc_net", "grocery_net"}
NEGATIONS = ("khong", "chua", "not ")

# Mẫu cấm trên văn bản đã chuẩn hóa (chữ thường, bỏ dấu, đ -> d)
FORBIDDEN = {
    "ket_luan_gian_lan": r"chac chan\W+(la\W+)?(mot\W+)?(vu\W+|giao dich\W+)?gian lan",
    "xac_nhan_gian_lan": r"(da|duoc)\W+(duoc\W+)?xac nhan\W+(la\W+)?(co\W+)?gian lan",
    "gian_lan_da_xac_nhan": r"gian lan\W+(da\W+)?(duoc\W+)?xac nhan",
    "ke_gian": r"\bke\W+(gian|lua dao)\b",
    "lenh_khoa_the": r"\b(khoa|dong bang|huy)\W+the\b",
    "lenh_chan_giao_dich": r"\bchan\W+(ngay\W+)?giao dich\b",
    "lenh_hoan_tien": r"\bhoan\W+tien\b",
    "fraud_confirmed": r"\b(fraud\W+confirmed|confirmed\W+fraud|definitely\W+fraud)\b",
    "chen_chi_dan": r"(ignore\W+(all\W+)?previous|bo qua\W+(moi\W+|cac\W+|tat ca\W+)?(huong dan|chi dan|quy tac))",
}
FORBIDDEN_RE = {k: re.compile(v) for k, v in FORBIDDEN.items()}


def _f(fields: dict, k: str, default=None):
    v = fields.get(k, default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


# (mã, mẫu nhận định trên văn bản chuẩn hóa, trường cần có, điều kiện dữ liệu để nhận định đúng)
CLAIMS = [
    ("NEW_CATEGORY", r"loai\W+(merchant|cua hang)\W+moi|danh muc\W+moi", ["is_new_category_30d"],
     lambda f: _f(f, "is_new_category_30d") == 1),
    ("NEW_MERCHANT", r"(?<!loai )(merchant|cua hang)\W+moi", ["is_new_merchant_30d"],
     lambda f: _f(f, "is_new_merchant_30d") == 1),
    ("NIGHT", r"gio\W+khuya|ban\W+dem|nua\W+dem|dem\W+khuya", ["is_night"], lambda f: _f(f, "is_night") == 1),
    ("ONLINE", r"truc\W+tuyen|\bonline\b", ["category"], lambda f: f.get("category") in ONLINE_CATS),
    ("LOW_HISTORY", r"it\W+lich\W+su", ["n_txn_30d"], lambda f: _f(f, "n_txn_30d") < 3),
    ("FAR", r"khoang\W+cach(\W+dia\W+ly)?\W+(rat\W+|kha\W+)?xa", ["distance_km"], lambda f: _f(f, "distance_km") >= 50),
    ("AMOUNT_SPIKE", r"so\W+tien\W+(rat\W+)?bat\W+thuong|gap\W+[\d.,]+\W+lan", ["amt_ratio_30d"],
     lambda f: _f(f, "amt_ratio_30d") >= 2),
    ("VELOCITY", r"nhieu\W+giao\W+dich\W+trong\W+(thoi\W+gian|khoang\W+thoi\W+gian)\W+ngan|don\W+dap", ["n_txn_1h"],
     lambda f: _f(f, "n_txn_1h", 0) >= 2 or _f(f, "n_txn_24h", 0) >= 5),
]
CLAIMS_RE = [(code, re.compile(p), need, ok) for code, p, need, ok in CLAIMS]


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", t).strip()


def _negated(norm: str, start: int) -> bool:
    before = norm[max(0, start - 14):start]
    return any(n in before for n in NEGATIONS)


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


def number_errors(text: str, pool: list[float]) -> list[str]:
    errs = []
    for tok in NUM_RE.findall(text):
        f = _to_float(tok)
        if f is not None and not _num_ok(f, pool):
            errs.append(f"NUMBER_NOT_IN_DATA:{tok}")
    return errs


def evidence_errors(evidence, data: dict) -> list[str]:
    if not isinstance(evidence, list) or not evidence:
        return ["EVIDENCE_EMPTY"]
    allowed = set(data.get("fields", {}).keys()) | META_EVIDENCE
    bad = [e for e in evidence if e not in allowed]
    return [f"EVIDENCE_UNKNOWN:{bad}"] if bad else []


def claim_errors(text: str, fields: dict) -> list[str]:
    """Nhận định có trái với dữ liệu không. Trường cần để kiểm chứng không có trong DATA -> cũng là lỗi."""
    norm = normalize(text)
    errs = []
    for code, rx, need, ok in CLAIMS_RE:
        for m in rx.finditer(norm):
            if _negated(norm, m.start()):
                continue
            if any(k not in fields for k in need):
                errs.append(f"CLAIM_UNVERIFIABLE:{code}")
            else:
                try:
                    good = bool(ok(fields))
                except Exception:  # noqa: BLE001 – dữ liệu không so sánh được -> coi như không kiểm chứng được
                    good = False
                if not good:
                    errs.append(f"CLAIM_CONTRADICTS_DATA:{code}")
            break
    return errs


def forbidden_errors(text: str) -> list[str]:
    norm = normalize(text)
    errs = []
    for name, rx in FORBIDDEN_RE.items():
        if any(not _negated(norm, m.start()) for m in rx.finditer(norm)):
            errs.append(f"FORBIDDEN:{name}")
    return errs


def grade_claim(text: str, evidence, data: dict, pool: list[float] | None = None) -> list[str]:
    """Một nhận định 'có căn cứ' khi: có trường căn cứ hợp lệ, mọi con số có trong dữ liệu, không trái dữ liệu,
    không có cụm từ cấm. Dùng cho cả guardrail lẫn bộ đánh giá theo rubric."""
    pool = _data_numbers(data) if pool is None else pool
    return (evidence_errors(evidence, data) + number_errors(text, pool)
            + claim_errors(text, data.get("fields", {})) + forbidden_errors(text))


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

    pool = _data_numbers(data)
    others = []
    for key, tkey in (("summary", "text"), ("risk_signals", "signal")):
        for i, s in enumerate(b[key]):
            if not isinstance(s, dict) or not isinstance(s.get(tkey), str):
                errs.append(f"SCHEMA:{key}[{i}]")
                continue
            errs += grade_claim(s[tkey], s.get("evidence"), data, pool)
    for i, c in enumerate(b["checklist"]):
        if not isinstance(c, dict) or not isinstance(c.get("step"), str):
            errs.append(f"SCHEMA:checklist[{i}]")
            continue
        if c.get("source") not in allowed_sources:
            errs.append(f"SOURCE_NOT_RETRIEVED:{c.get('source')}")
        others.append(c["step"])
    others += [x for x in b["limitations"] if isinstance(x, str)]
    if len(b["disclaimer"].strip()) < 10:
        errs.append("DISCLAIMER_MISSING")
    for t in others:
        errs += number_errors(t, pool)
    errs += forbidden_errors(" ".join(others + [b["disclaimer"]]))
    errs = list(dict.fromkeys(errs))  # bỏ trùng, giữ thứ tự
    return (b if not errs else None), errs

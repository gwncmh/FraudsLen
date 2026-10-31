"""Kiểm thử không cần PostgreSQL/Redis: chống rò rỉ dữ liệu tương lai, phân luồng an toàn, kiểm tra dữ liệu,
kiểm tra brief, ranh giới PII, ngắt mạch.   Chạy: pytest -q
"""
import json
import math

import pandas as pd
import pytest

from fraudlens.common import pii
from fraudlens.common.breaker import CircuitBreaker
from fraudlens.common.features import FEATURES, build_offline, compute, record_of
from fraudlens.common.routing import route
from fraudlens.common.validation import validate
from fraudlens.worker import guardrail

KEY = b"test-key-0123456789abcdef"
RAW = {"trans_num": "t1", "trans_date_trans_time": "2020-06-21 12:14:25", "cc_num": "2291163933867244",
       "merchant": "fraud_Kirlin and Sons", "category": "personal_care", "amt": 2.86, "first": "Jeff", "last": "Elliott",
       "street": "351 Darlene Green", "state": "SC", "lat": 33.9659, "long": -80.9355, "city_pop": 333497,
       "dob": "1968-03-19", "merch_lat": 33.986391, "merch_long": -81.200714}


def txn(ts, tn, amt=10.0, cat="grocery_pos", mer="m1"):
    return {"event_ts": ts, "trans_num": tn, "amt": amt, "category": cat, "merchant": mer, "age": 40.0,
            "distance_km": 5.0, "city_pop": 1000}


# ---------------------------------------------------------------- feature: không dùng dữ liệu tương lai
def test_future_and_current_records_are_ignored():
    t = 1_600_000_000
    cur = txn(t, "b")
    hist = [record_of(txn(t - 100, "a")), record_of(cur), record_of(txn(t + 50, "z")), record_of(txn(t, "c"))]
    f = compute(cur, hist)
    assert f["n_txn_1h"] == 1  # chỉ "a"; bỏ chính nó, giao dịch tương lai và giao dịch cùng giây xếp sau
    assert f["secs_since_last"] == 100


def test_window_excludes_older_than_30_days():
    t = 1_600_000_000
    f = compute(txn(t, "x"), [record_of(txn(t - 31 * 86400, "old"))])
    assert f["n_txn_30d"] == 0 and f["is_new_category_30d"] == 0


def test_offline_equals_online_replay():
    rows = [txn(1_600_000_000 + i * 1800, f"t{i:03d}", amt=5 + i, cat="cat" + str(i % 3)) for i in range(60)]
    df = pd.DataFrame(rows)
    df["card_hash"] = ["A" if i % 2 else "B" for i in range(60)]
    off = build_offline(df)
    hist = {"A": [], "B": []}
    for i, r in df.iterrows():
        on = compute(r.to_dict(), hist[r["card_hash"]])
        hist[r["card_hash"]].append(record_of(r.to_dict()))
        for c in FEATURES:
            a, b = off.loc[i, c], on[c]
            assert (a == b) or (isinstance(a, float) and math.isclose(a, b, rel_tol=1e-9)), (i, c, a, b)


# ---------------------------------------------------------------- phân luồng an toàn khi lỗi
@pytest.mark.parametrize("status,score,expected", [
    ("SCORED", 0.001, "LOW"), ("SCORED", 0.5, "MEDIUM"), ("SCORED", 0.95, "HIGH"),
    ("SCORING_FAILED", None, "UNSCORED"), ("INVALID", None, "UNSCORED"), ("HISTORY_UNAVAILABLE", None, "UNSCORED"),
    ("SCORED", None, "UNSCORED"), ("SCORED", float("nan"), "UNSCORED"), ("SCORED", 1.5, "UNSCORED"),
    ("SCORED", "abc", "UNSCORED"),
])
def test_route_failsafe(status, score, expected):
    assert route(status, score, 0.1, 0.9) == expected


# ---------------------------------------------------------------- kiểm tra dữ liệu đầu vào
@pytest.mark.parametrize("mutate,code", [
    (lambda r: r.pop("amt"), "MISSING:amt"), (lambda r: r.update(amt=True), "BAD_TYPE:amt"),
    (lambda r: r.update(amt=-1), "OUT_OF_RANGE:amt"), (lambda r: r.update(amt="abc"), "BAD_TYPE:amt"),
    (lambda r: r.update(lat=123), "OUT_OF_RANGE:lat"), (lambda r: r.update(cc_num="12ab"), "BAD:cc_num"),
    (lambda r: r.update(trans_date_trans_time="31/02/2020"), "BAD:trans_date_trans_time"),
])
def test_validator_catches_faults(mutate, code):
    r = dict(RAW)
    mutate(r)
    assert code in validate(r)


def test_validator_accepts_clean():
    assert validate(dict(RAW)) == []


# ---------------------------------------------------------------- ranh giới PII
def test_minimize_removes_pii():
    m = pii.minimize(RAW, KEY)
    s = json.dumps(m)
    for v in ("Jeff", "Elliott", "Darlene", "1968-03-19", "2291163933867244", "33.9659"):
        assert v not in s
    assert m["card_last4"] == "7244" and m["card_hash"] == pii.card_hash(RAW["cc_num"], KEY)
    assert pii.card_hash(RAW["cc_num"], b"another-key-0123456789") != m["card_hash"]


# ---------------------------------------------------------------- kiểm tra brief của LLM
DATA = {"case_id": 1, "route": "HIGH", "score": 0.93, "fields": {"amt": 950.5, "n_txn_24h": 7, "category": "shopping_net"}}
GOOD = {"summary": [{"text": "Giao dịch 950.5 ở shopping_net.", "evidence": ["amt", "category"]}],
        "risk_signals": [{"signal": "Có 7 giao dịch trong 24 giờ.", "evidence": ["n_txn_24h"]}],
        "checklist": [{"step": "So sánh với trung bình 30 ngày.", "source": "PB-02"}],
        "limitations": ["Chưa có thông tin xác minh."], "disclaimer": "Brief không phải kết luận gian lận."}


def test_guardrail_accepts_grounded_brief():
    b, errs = guardrail.check(json.dumps(GOOD), DATA, {"PB-02", "PB-03"})
    assert b is not None, errs


@pytest.mark.parametrize("patch,err", [
    (lambda g: g["summary"].append({"text": "Số tiền 12345 rất lớn.", "evidence": ["amt"]}), "NUMBER_NOT_IN_DATA"),
    (lambda g: g["checklist"].append({"step": "Xem lại.", "source": "PB-99"}), "SOURCE_NOT_RETRIEVED"),
    (lambda g: g["summary"].append({"text": "Đây chắc chắn là gian lận.", "evidence": []}), "FORBIDDEN"),
    (lambda g: g.update(disclaimer=""), "DISCLAIMER_MISSING"),
    (lambda g: g["risk_signals"].append({"signal": "IP lạ.", "evidence": ["ip_address"]}), "EVIDENCE_UNKNOWN"),
])
def test_guardrail_rejects(patch, err):
    g = json.loads(json.dumps(GOOD))
    patch(g)
    b, errs = guardrail.check(json.dumps(g), DATA, {"PB-02", "PB-03"})
    assert b is None and any(e.startswith(err) for e in errs), errs


def test_guardrail_rejects_bad_json():
    assert guardrail.check("không phải json", DATA, set())[1] == ["JSON_INVALID"]


# ---------------------------------------------------------------- ngắt mạch
def test_breaker_opens_after_threshold():
    br = CircuitBreaker(3, 60)
    for _ in range(3):
        assert br.allow()
        br.failure()
    assert br.is_open and not br.allow()
    br.success()
    assert br.allow()

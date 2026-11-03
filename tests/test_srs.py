"""Kiểm thử các yêu cầu trong SRS chưa có test tự động (không cần PostgreSQL/Redis):
T-HUM-01 (không đóng hồ sơ nếu không qua người), T-SEC-01 (truy cập sai quyền), T-SEC-02 (không có PII trong prompt LLM),
T-SEC-03 (không có key trong repo), T-MON-01 (tiêm drift -> có cảnh báo), T-FAIL-01 (kiểm tự động của simulator),
và lệnh admin report.   T-INT-01 cần DB: chạy `python -m fraudlens.tools.admin trace-all`.   Chạy: pytest -q
"""
import json
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import fraudlens.api.app as api
from fraudlens.api import auth
from fraudlens.api.auth import User
from fraudlens.common import pii, registry
from fraudlens.common.features import FEATURES, compute
from fraudlens.ml.metrics import evaluation_report
from fraudlens.ml.train import MONITORED_NUMERIC, reference_stats
from fraudlens.monitor.main import binned, level, psi
from fraudlens.tools import simulator
from fraudlens.tools.report import build
from fraudlens.worker.brief import BriefWriter
from fraudlens.worker.llm import LLMUnavailable

REPO = Path(__file__).resolve().parents[1]
TOKENS = {"tok-analyst": User("an", "analyst"), "tok-lead": User("lead", "risk_lead"), "tok-admin": User("ad", "admin")}


def bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


class FakeConn:
    """Thay PostgreSQL: trả trạng thái hồ sơ cho câu SELECT, ghi lại mọi câu lệnh."""

    def __init__(self, status: str | None):
        self.status, self.sql = status, []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        row = {"status": self.status} if self.status and sql.startswith("SELECT status FROM cases") else None
        return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: [])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "_tokens", lambda: TOKENS)
    audits = []
    monkeypatch.setattr(api, "audit", lambda conn, *a: audits.append(a))
    state = SimpleNamespace(conn=FakeConn("OPEN"), audits=audits)

    @contextmanager
    def fake_tx():
        yield state.conn

    monkeypatch.setattr(api, "tx", fake_tx)
    c = TestClient(api.app)  # không dùng "with": bỏ qua startup (cần Redis/DB)
    c.state = state
    return c


def act(client, tok: str, action: str, note: str | None = "đã gọi xác minh chủ thẻ", case_id: int = 7):
    return client.post(f"/v1/cases/{case_id}/actions", json={"action": action, "note": note}, headers=bearer(tok))


# ---------------------------------------------------------------- T-HUM-01
def test_hum01_analyst_closes_case_and_it_is_recorded(client):
    r = act(client, "tok-analyst", "CLOSE_FRAUD")
    assert r.status_code == 200
    sql = [s for s, _ in client.state.conn.sql]
    assert any(s.startswith("UPDATE cases SET status") for s in sql)
    ins = [p for s, p in client.state.conn.sql if s.startswith("INSERT INTO case_actions")]
    assert ins == [(7, "an", "analyst", "CLOSE_FRAUD", "đã gọi xác minh chủ thẻ")]
    assert client.state.audits == [("case", "7", "CLOSE_FRAUD", "user:an", {"role": "analyst", "note": "đã gọi xác minh chủ thẻ"})]


@pytest.mark.parametrize("tok, note, status, code", [
    ("tok-analyst", None, "OPEN", 400),          # đóng phải ghi nhận định
    ("tok-analyst", "   ", "OPEN", 400),
    ("tok-admin", "ok", "OPEN", 403),            # admin không phải người xử lý hồ sơ
    ("tok-analyst", "ok", "ESCALATED", 403),     # hồ sơ đã chuyển cấp chỉ Risk Lead đóng
    ("tok-lead", "ok", "CLOSED_LEGIT", 409),     # hồ sơ đã đóng không đổi được
])
def test_hum01_close_blocked(client, tok, note, status, code):
    client.state.conn = FakeConn(status)
    assert act(client, tok, "CLOSE_LEGIT", note).status_code == code
    assert not any(s.startswith(("UPDATE", "INSERT")) for s, _ in client.state.conn.sql)
    assert client.state.audits == []


def test_hum01_risk_lead_closes_escalated(client):
    client.state.conn = FakeConn("ESCALATED")
    assert act(client, "tok-lead", "CLOSE_LEGIT").status_code == 200


def test_hum01_no_automated_path_closes_cases():
    """Chỉ endpoint thao tác của người dùng được đổi trạng thái hồ sơ; API nạp giao dịch, worker, monitor thì không."""
    writers = [p.relative_to(REPO).as_posix() for p in (REPO / "fraudlens").rglob("*.py")
               if re.search(r"UPDATE\s+cases\s+SET\s+status", p.read_text(encoding="utf-8"), re.I)]
    assert writers == ["fraudlens/api/app.py"]


# ---------------------------------------------------------------- T-SEC-01
@pytest.mark.parametrize("path", ["/v1/cases", "/v1/cases/1", "/v1/stats", "/v1/config", "/v1/audit/verify"])
@pytest.mark.parametrize("headers", [{}, bearer(""), bearer("sai-token"), bearer("tok-analystX")])
def test_sec01_dashboard_requires_valid_token(client, path, headers):
    assert client.get(path, headers=headers).status_code == 401


@pytest.mark.parametrize("path", ["/v1/config", "/v1/audit/verify", "/v1/stats/product"])
def test_sec01_analyst_cannot_reach_lead_endpoints(client, path):
    assert client.get(path, headers=bearer("tok-analyst")).status_code == 403


@pytest.mark.parametrize("action", ["CLOSE_FRAUD", "INVESTIGATE", "NOTE"])
def test_sec01_admin_cannot_act_on_cases(client, action):
    assert act(client, "tok-admin", action).status_code == 403


@pytest.mark.parametrize("path, body", [("/v1/transactions", {"trans_num": "x"}),
                                        ("/v1/labels", {"trans_num": "x", "is_fraud": True})])
@pytest.mark.parametrize("key", [None, "sai-key"])
def test_sec01_source_endpoints_require_api_key(client, path, body, key):
    headers = {"X-API-Key": key} if key else {}
    assert client.post(path, json=body, headers=headers).status_code == 401


def test_sec01_dashboard_token_is_not_a_source_key(client):
    assert client.post("/v1/labels", json={"trans_num": "x", "is_fraud": True},
                       headers={"X-API-Key": "tok-lead"}).status_code == 401


# ---------------------------------------------------------------- T-SEC-02
PII_RAW = {"trans_num": "t-pii", "trans_date_trans_time": "2020-06-21 03:14:25", "cc_num": "2291163933867244",
           "merchant": "fraud_Kirlin and Sons", "category": "shopping_net", "amt": 950.5, "first": "Jeff",
           "last": "Elliott", "gender": "M", "street": "351 Darlene Green", "city": "Columbia", "zip": "29209",
           "state": "SC", "lat": 33.9659, "long": -80.9355, "city_pop": 333497, "job": "Mechanical engineer",
           "dob": "1968-03-19", "merch_lat": 33.986391, "merch_long": -81.200714}
PII_VALUES = ["2291163933867244", "Jeff", "Elliott", "351 Darlene Green", "Columbia", "29209", "Mechanical engineer",
              "1968-03-19", "33.9659", "-80.9355", "33.986391", "-81.200714"]


def test_sec02_llm_prompt_has_no_pii(monkeypatch):
    m = pii.minimize(PII_RAW, b"test-key-0123456789abcdef")
    feats = compute(m, [])
    case = {"id": 1, "route": "HIGH", "score_status": "SCORED", "score": 0.97, "features": feats, "score_error": None,
            "validation_errors": [], **{k: m[k] for k in ("card_last4", "merchant", "category", "amt")}}
    sent = []

    def capture(system, msg):
        sent.append(system + "\n" + msg)
        raise LLMUnavailable("test")

    w = BriefWriter()
    w.llm = SimpleNamespace(enabled=True, model="test", chat_json=capture)
    res = w.write(case, [{"feature": "amt_ratio_30d", "direction": "tăng rủi ro", "contribution": 1.2, "value": 9}])
    assert sent, "prompt phải được dựng và gửi"
    stored = json.dumps({k: res[k] for k in ("brief", "factors", "guardrail")}, ensure_ascii=False)
    for text in (*sent, stored):
        for v in PII_VALUES:
            assert v not in text, f"PII {v!r} lọt vào prompt/brief"
    assert "7244" in sent[0]  # 4 số cuối được phép (để chuyên viên đối chiếu)


# ---------------------------------------------------------------- T-SEC-03
SECRET_PATTERNS = {
    "google_api_key": r"AIza[0-9A-Za-z_\-]{35}",
    "openai_style_key": r"\bsk-[A-Za-z0-9_\-]{20,}",
    "private_key": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "aws_access_key": r"\bAKIA[0-9A-Z]{16}\b",
    "assigned_secret": r"(?i)\b\w*(?:api_?key|secret|password|token)\w*\s*[=:]\s*['\"]?(?!dev-|change-?me|your|xxx|<)"
                       r"[A-Za-z0-9_\-/+]{24,}",
}


def _tracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("không có git")
    return [REPO / p for p in out.splitlines()]


def test_sec03_env_file_not_tracked():
    names = {p.name for p in _tracked_files()}
    assert ".env" not in names
    assert ".env" in (REPO / ".gitignore").read_text(encoding="utf-8").split()


def test_sec03_no_secret_in_tracked_files():
    hits = []
    for p in _tracked_files():
        if not p.is_file() or p.suffix in {".png", ".jpg", ".ico", ".json"} and "lock" in p.name or p.stat().st_size > 2_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pat in SECRET_PATTERNS.items():
            for mt in re.finditer(pat, text):
                hits.append(f"{p.relative_to(REPO)}: {name}: {mt.group(0)[:12]}…")
    assert not hits, "nghi lộ key:\n" + "\n".join(hits)


def test_sec03_scanner_detects_planted_key():
    planted = 'LLM_API_KEY = "AIza' + "x" * 35 + '"\nOPENAI = "sk-' + "a" * 30 + '"\ndb_password: Zq8vT2mN4rL7pX1wK9sD3fG6hJ0bC5\n'
    found = {n for n, pat in SECRET_PATTERNS.items() if re.search(pat, planted)}
    assert {"google_api_key", "openai_style_key", "assigned_secret"} <= found


# ---------------------------------------------------------------- T-MON-01
def _monitor_frame(n: int, rng, amt_scale: float = 1.0) -> pd.DataFrame:
    return pd.DataFrame({"amt": rng.lognormal(3.5, 1.0, n) * amt_scale, "hour": rng.integers(0, 24, n),
                         "distance_km": rng.normal(75, 25, n), "n_txn_24h": rng.poisson(3, n),
                         "age": rng.normal(45, 15, n), "category": rng.choice(["grocery_pos", "gas_transport"], n)})


def _psi_levels(ref: dict, F: pd.DataFrame) -> dict[str, tuple[float, str]]:
    out = {}
    for c in MONITORED_NUMERIC:
        spec = ref["numeric"][c]
        p = psi(np.array(spec["props"]), binned(spec["edges"], len(spec["props"]), F[c].to_numpy(float)))
        out[c] = (p, level(p))
    return out


def test_mon01_no_alert_without_drift():
    rng = np.random.default_rng(0)
    ref = reference_stats(_monitor_frame(50_000, rng))
    assert all(lv == "OK" for _, lv in _psi_levels(ref, _monitor_frame(5_000, rng)).values())


def test_mon01_injected_drift_raises_alert():
    rng = np.random.default_rng(1)
    ref = reference_stats(_monitor_frame(50_000, rng))
    levels = _psi_levels(ref, _monitor_frame(5_000, rng, amt_scale=3.0))  # số tiền tăng gấp 3
    assert levels["amt"][1] == "ALERT"
    assert all(lv == "OK" for c, (_, lv) in levels.items() if c != "amt")


# ---------------------------------------------------------------- T-FAIL-01 (simulator)
def test_fail01_report_passes_when_faults_never_low():
    st = simulator.Stats()
    st.add(12.0, "UNSCORED", "fault:bad_amt", "bad_amt", 3.0)
    st.add(15.0, "LOW", "fault:duplicate", "duplicate")  # gửi lại GD hợp lệ: trả đúng luồng cũ, không tính
    st.add(9.0, "LOW", None, None, 2.5)
    st.add(30.0, None, "http_503", "bad_time")
    rep = simulator.build_report(st, 4, 2.0, {})
    assert rep["t_fail_01"] == {"passed": True, "faults_injected": 2, "faults_to_low": {}}
    assert rep["faults_by_route"]["bad_time"] == {"NO_RESPONSE": 1}
    assert rep["latency_server_ms"]["n"] == 2 and rep["latency_client_ms"]["n"] == 4


def test_fail01_report_fails_when_fault_routed_low():
    st = simulator.Stats()
    st.add(10.0, "LOW", "fault:negative_amt", "negative_amt")
    rep = simulator.build_report(st, 1, 1.0, {})
    assert rep["t_fail_01"]["passed"] is False and rep["t_fail_01"]["faults_to_low"] == {"negative_amt": 1}


# ---------------------------------------------------------------- admin report
def test_report_builds_markdown_and_figures(tmp_path, monkeypatch):
    rng = np.random.default_rng(0)
    n = 20_000
    y = (rng.random(n) < 0.01).astype(int)
    s = np.clip(rng.beta(1, 80, n) + y * rng.beta(4, 3, n), 0, 1)
    F = pd.DataFrame({f: rng.random(n) for f in FEATURES})
    F["category"] = rng.choice(["grocery_pos", "shopping_net"], n)
    F["is_night"] = rng.integers(0, 2, n)
    df = pd.DataFrame({"state": rng.choice(["CA", "TX"], n)})
    rep = evaluation_report(y, s, F, df, 0.3, 0.6, 200, n_boot=20)
    base = {"rules": evaluation_report(y, rng.random(n), F, df, 0.5, 0.9, 200, n_boot=20, is_probability=False)}
    v = "xgb-test"
    (tmp_path / v).mkdir()
    manifest = {"model_version": v, "feature_set_version": "fs-v1", "features": FEATURES,
                "calibration": {"method": "platt", "fit_on": "val_a"}, "params": {"n_estimators_used": 10},
                "data": {"sample_cards": 0.1}, "behavior": {},
                "metrics": {"val_b_report": rep, "baselines_val_b_report": base,
                            "val_b_calibration": {"before_platt": rep["calibration"], "after_platt": rep["calibration"]}}}
    (tmp_path / v / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / v / "routing_config.json").write_text(json.dumps({"config_version": "r1", "t_medium": 0.3, "t_high": 0.6}),
                                                      encoding="utf-8")
    monkeypatch.setattr(registry, "settings", SimpleNamespace(registry_dir=tmp_path))
    st = simulator.Stats()
    st.add(10.0, "UNSCORED", "fault:bad_amt", "bad_amt", 2.0)
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps(simulator.build_report(st, 1, 1.0, {})), encoding="utf-8")

    path = build(v, tmp_path / "out", replay)
    md = path.read_text(encoding="utf-8")
    for needle in ("M0 Luật viết tay", "before platt", "category=shopping_net", "Thiếu mốc: lr, tree",
                   "Test set", "T-FAIL-01", "Release gate"):
        assert needle in md, needle
    for png in ("pr_curve_val_b.png", "reliability_val_b.png", "slices_val_b.png"):
        assert (tmp_path / "out" / png).stat().st_size > 1000

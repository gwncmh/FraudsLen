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
from fraudlens.api import auth, ops, views
from fraudlens.api.auth import SESSION_PREFIX, User
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
# phiên đăng nhập giả (thay bảng app_sessions); test gọi bằng tên ngắn "tok-...", bearer() đổi sang token phiên
TOKENS = {"tok-analyst": User("an", "analyst", user_id=1), "tok-lead": User("lead", "risk_lead", user_id=2),
          "tok-admin": User("ad", "admin", user_id=3)}
SESSION_TOKENS = {f"{SESSION_PREFIX}{k}-{'x' * 20}": v for k, v in TOKENS.items()}


def bearer(tok: str) -> dict:
    return {"Authorization": f"Bearer {SESSION_PREFIX}{tok}-{'x' * 20}" if tok in TOKENS else f"Bearer {tok}"}


class FakeConn:
    """Thay PostgreSQL: trả trạng thái hồ sơ cho câu SELECT, ghi lại mọi câu lệnh."""

    def __init__(self, status: str | None, assigned_role: str | None = None, txn_status: str = "VALID"):
        self.status, self.sql = status, []
        self.case = {"status": status, "assigned_role": assigned_role or ("risk_lead" if status == "ESCALATED" else "analyst"),
                     "txn_status": txn_status}

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        row = dict(self.case) if self.status and sql.startswith("SELECT c.status, c.assigned_role") else None
        return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: [])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "_session_user", SESSION_TOKENS.get)
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


@pytest.mark.parametrize("action", ["CLOSE_FRAUD", "CLOSE_LEGIT", "INVESTIGATE", "ESCALATE"])
def test_hum01_analyst_cannot_take_back_escalated_case(client, action):
    """Hồ sơ đã chuyển cấp thuộc về Risk Lead kể cả khi trạng thái không còn là ESCALATED (Risk Lead bấm "Điều tra thêm"):
    chuyên viên không đổi trạng thái hay đóng được – trước đây chỉ xét status == ESCALATED nên lách được."""
    for status in ("ESCALATED", "INVESTIGATING"):
        client.state.conn = FakeConn(status, assigned_role="risk_lead")
        assert act(client, "tok-analyst", action).status_code == 403
        assert not any(s.startswith(("UPDATE", "INSERT")) for s, _ in client.state.conn.sql)
    assert client.state.audits == []


def test_hum01_analyst_can_still_note_on_escalated_case(client):
    client.state.conn = FakeConn("ESCALATED")
    assert act(client, "tok-analyst", "NOTE", "chủ thẻ gọi lại xác nhận").status_code == 200
    assert not any(s.startswith("UPDATE cases") for s, _ in client.state.conn.sql)


def test_hum01_risk_lead_keeps_escalated_case_while_investigating(client):
    client.state.conn = FakeConn("ESCALATED")
    assert act(client, "tok-lead", "INVESTIGATE").status_code == 200
    upd = [s for s, _ in client.state.conn.sql if s.startswith("UPDATE cases")]
    assert upd and "assigned_role" not in upd[0]  # vẫn do Risk Lead phụ trách


@pytest.mark.parametrize("tok, action, status, role", [
    ("tok-analyst", "INVESTIGATE", "INVESTIGATING", "analyst"),   # bấm lại thao tác không đổi gì
    ("tok-lead", "ESCALATE", "ESCALATED", "risk_lead"),
    ("tok-lead", "ESCALATE", "INVESTIGATING", "risk_lead"),       # đã do Risk Lead phụ trách: không còn cấp nào để chuyển
])
def test_hum01_noop_transition_rejected(client, tok, action, status, role):
    client.state.conn = FakeConn(status, assigned_role=role)
    assert act(client, tok, action).status_code == 409
    assert client.state.audits == []


@pytest.mark.parametrize("note", [None, "", "   "])
def test_hum01_empty_note_rejected(client, note):
    assert act(client, "tok-analyst", "NOTE", note).status_code == 400
    assert client.state.audits == []


def test_hum01_close_invalid_only_for_invalid_payload(client):
    """Payload lỗi không có dữ liệu để kết luận: đóng 'lỗi dữ liệu' thay vì ép chọn gian lận/hợp lệ. Giao dịch có dữ liệu thì không."""
    assert act(client, "tok-analyst", "CLOSE_INVALID").status_code == 400
    assert act(client, "tok-analyst", "CLOSE_INVALID", None).status_code == 400  # vẫn bắt buộc ghi nhận định
    client.state.conn = FakeConn("OPEN", txn_status="INVALID")
    assert act(client, "tok-analyst", "CLOSE_INVALID").status_code == 200
    assert ("CLOSED_INVALID", 7) in [p for s, p in client.state.conn.sql if s.startswith("UPDATE cases SET status")]
    assert client.state.audits[-1][2] == "CLOSE_INVALID"


def test_schema_file_covers_every_table_and_new_status():
    """db/schema.sql là nguồn duy nhất của lược đồ (API chạy lại lúc khởi động): phải chạy lại được và có đủ bảng."""
    sql = api.SCHEMA_FILE.read_text(encoding="utf-8")
    for t in ("transactions", "scores", "routing_decisions", "cases", "brief_jobs", "briefs", "case_actions", "labels_eval",
              "monitor_metrics", "audit_log", "app_users", "app_sessions", "ops_jobs"):
        assert f"CREATE TABLE IF NOT EXISTS {t} " in sql, t
    assert not re.search(r"CREATE (TABLE|INDEX) (?!IF NOT EXISTS)", sql)       # mọi lệnh tạo đều chạy lại được
    assert sql.count("CLOSED_INVALID") >= 3 and "DROP CONSTRAINT cases_status_check" in sql  # DB cũ được nâng cấp
    for status in api.NEW_STATUS.values():
        assert f"'{status}'" in sql


def test_decision_vs_label_summary():
    from fraudlens.tools.product import decision_vs_label
    rows = [{"status": "CLOSED_FRAUD", "is_fraud": True, "n": 8}, {"status": "CLOSED_FRAUD", "is_fraud": False, "n": 2},
            {"status": "CLOSED_LEGIT", "is_fraud": True, "n": 1}, {"status": "CLOSED_LEGIT", "is_fraud": False, "n": 9}]
    d = decision_vs_label(rows)
    assert d["n"] == 20 and d["agreement_rate"] == 0.85
    assert d["closed_legit_label_fraud"] == 1 and d["closed_fraud_label_legit"] == 2
    assert decision_vs_label([]) == {"n": 0, "agreement_rate": None, "closed_fraud_label_fraud": 0,
                                     "closed_fraud_label_legit": 0, "closed_legit_label_fraud": 0, "closed_legit_label_legit": 0}


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
    planted = 'LLM_API_KEY = "AIza' + "x" * 35 + '"\nOPENAI = "sk-' + "a" * 30 + '"\ndb_password: ' + "Zq8vT2mN4rL7" + 'pX1wK9sD3fG6hJ0bC5\n'  # tách chuỗi để chính file này không bị quét trúng
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


# ---------------------------------------------------------------- T-SEC-01 (bổ sung): truy vết và nhãn thật
class TraceConn:
    """PostgreSQL giả cho /v1/ops/trace: có giao dịch, có nhãn (is_fraud = True), không có hồ sơ."""

    def execute(self, sql, params=None):
        if "FROM transactions" in sql:
            row = {"id": 5, "trans_num": "t-1", "event_ts": 1, "card_last4": "7244", "merchant": "m", "category": "c",
                   "amt": 9.5, "state": "SC", "status": "VALID", "validation_errors": [], "received_at": None}
        elif "FROM labels_eval" in sql:
            assert "is_fraud" not in sql, "truy vết không được đọc giá trị nhãn"
            row = {"?column?": 1}
        else:
            row = None
        return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: [])


def test_sec01_trace_needs_lead_or_admin_and_never_returns_label(client, monkeypatch):
    @contextmanager
    def fake_tx():
        yield TraceConn()

    monkeypatch.setattr(ops, "tx", fake_tx)
    assert client.get("/v1/ops/trace/t-1").status_code == 401
    assert client.get("/v1/ops/trace/t-1", headers=bearer("tok-analyst")).status_code == 403
    for tok in ("tok-lead", "tok-admin"):
        r = client.get("/v1/ops/trace/t-1", headers=bearer(tok))
        assert r.status_code == 200
        body = r.json()
        assert body["label_received"] is True and body["transaction"]["trans_num"] == "t-1"
        assert "label" not in body and "is_fraud" not in r.text


def test_sec01_no_endpoint_returns_label_per_transaction():
    """Nhãn thật chỉ được đọc ở dạng tổng hợp (GROUP BY) trong API; không endpoint nào SELECT is_fraud theo một giao dịch."""
    found = 0
    for mod in (api, ops, views):
        lines = Path(mod.__file__).read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            m = re.search(r"SELECT\b(.*?)\bFROM labels_eval", line)
            if m and "is_fraud" in m.group(1):  # câu SELECT lấy cột is_fraud: phải là câu tổng hợp
                found += 1
                assert "GROUP BY" in " ".join(lines[i:i + 2]), f"{mod.__name__}:{i + 1} đọc is_fraud theo từng giao dịch"
    assert found >= 1  # views.LABELS_BY_ROUTE_SQL, dùng chung cho /v1/stats và /v1/dashboard
    assert "t.status = 'VALID'" in views.LABELS_BY_ROUTE_SQL  # nhãn của payload lỗi không tính vào recall của model


def test_sec01_no_static_tokens(monkeypatch):
    """Không còn token tĩnh / tự đăng ký: chỉ phiên "fls_..." hợp lệ trong DB mới qua (bản cũ có admin-token...)."""
    monkeypatch.setenv("DASHBOARD_TOKENS", "admin:admin-token:admin")
    looked_up = []
    monkeypatch.setattr(auth, "_session_user", lambda t: looked_up.append(t))
    c = TestClient(api.app)
    for tok in ("admin-token", "lead-token", "analyst-token", "", SESSION_PREFIX + "ngan"):
        assert c.get("/v1/stats", headers=bearer(tok)).status_code == 401
    assert looked_up == []  # token không đúng dạng phiên thì không cả hỏi DB
    assert not hasattr(auth, "_tokens")
    assert c.post("/v1/auth/register", json={"username": "moi.nguoi", "password": "matkhau123"}).status_code in (404, 405)


def test_sec01_security_warnings_flag_demo_config(monkeypatch):
    from fraudlens.common.config import Settings
    monkeypatch.setattr(auth, "settings", Settings(source_api_key="dev-source-key"))
    assert len(auth.security_warnings()) == 1
    monkeypatch.setattr(auth, "settings", Settings(source_api_key="k" * 32))
    assert auth.security_warnings() == []


def test_sec01_system_status_warnings_only_for_admin(client, monkeypatch):
    monkeypatch.setattr(api.ops, "system_status", lambda lane: {"steps": []})
    monkeypatch.setattr(api, "refresh_routing", lambda force=False: None)
    monkeypatch.setattr(api, "security_warnings", lambda: ["yếu"])
    assert client.get("/v1/system/status", headers=bearer("tok-admin")).json()["security_warnings"] == ["yếu"]
    for tok in ("tok-analyst", "tok-lead"):
        assert "security_warnings" not in client.get("/v1/system/status", headers=bearer(tok)).json()


# ---------------------------------------------------------------- độ trễ làn nhanh được lưu (FR-10, mục tiêu p95 ≤ 500 ms)
def test_fast_lane_stores_latency_with_score():
    from fraudlens.api.service import FastLane
    conn = FakeConn(None)
    conn.execute = lambda sql, params=None: (conn.sql.append((sql, params)),
                                             SimpleNamespace(fetchone=lambda: {"id": 11}, fetchall=lambda: []))[1]
    lane = SimpleNamespace(rc={"config_version": "r1"})
    txn_id = FastLane._write(lane, conn, {"amt": "abc"}, "t-9", ["BAD_TYPE:amt"], None, None,
                             {"status": "INVALID", "error": "BAD_TYPE:amt"}, "UNSCORED", 0.1, 0.5, 12.34)
    assert txn_id == 11
    sql, params = next((s, p) for s, p in conn.sql if s.startswith("INSERT INTO scores"))
    assert "latency_ms" in sql and params[-1] == 12.34


# ---------------------------------------------------------------- nhóm đối chứng: brief ẩn, giải thích ML vẫn có
def test_holdout_case_hides_brief_but_keeps_ml_factors(client, monkeypatch):
    factors = [{"feature": "amt_ratio_30d", "value": 9, "contribution": 1.2, "direction": "tăng rủi ro"}]

    class Conn:
        def execute(self, sql, params=None):
            if sql.startswith("SELECT c.*"):
                row = {"id": 7, "validation_errors": [], "status": "OPEN"}
            elif "FROM briefs" in sql:
                row = {"id": 3, "brief": {"summary": []}, "brief_source": "llm", "factors": list(factors)}
            elif "FROM brief_jobs" in sql:
                row = {"status": "DONE", "attempts": 1, "last_error": None}
            else:
                row = None
            return SimpleNamespace(fetchone=lambda: row, fetchall=lambda: [])

    client.state.conn = Conn()
    monkeypatch.setattr(api, "is_holdout", lambda case_id: True)
    d = client.get("/v1/cases/7", headers=bearer("tok-analyst")).json()
    assert d["brief_holdout"] is True and d["brief"] is None and d["brief_job"] is None
    assert d["factors"] == factors
    monkeypatch.setattr(api, "is_holdout", lambda case_id: False)
    d = client.get("/v1/cases/7", headers=bearer("tok-analyst")).json()
    assert d["brief"]["brief_source"] == "llm" and d["factors"] == factors


# ---------------------------------------------------------------- T-FAIL-01 (bổ sung): gửi lại mã giao dịch đã có
def _lane_with_original(monkeypatch, original_raw, txn_status="VALID", route="LOW"):
    """FastLane không cần Redis/Scorer; DB giả có sẵn bản gốc của giao dịch."""
    from fraudlens.api import service
    key = b"test-key-0123456789abcdef"
    m = pii.minimize(original_raw, key)
    row = {"trans_num": m["trans_num"], "txn_status": txn_status, "status": "SCORED", "score": 0.001,
           "model_version": "v1", "route": route, "config_version": "r1", "case_id": None,
           **({k: m[k] for k in service.DUP_FIELDS} if txn_status == "VALID" else dict.fromkeys(service.DUP_FIELDS))}
    audits = []

    @contextmanager
    def fake_tx():
        yield SimpleNamespace(execute=lambda sql, params=None: SimpleNamespace(fetchone=lambda: dict(row)))

    monkeypatch.setattr(service, "tx", fake_tx)
    monkeypatch.setattr(service, "audit", lambda conn, *a: audits.append(a))
    lane = object.__new__(service.FastLane)
    lane.key, lane.categories, lane.stream_ts, lane.rc = key, None, None, None
    return lane, audits


def test_fail01_exact_resend_returns_stored_result(monkeypatch):
    lane, audits = _lane_with_original(monkeypatch, PII_RAW)
    r = lane.process(dict(PII_RAW))
    assert r["duplicate"] is True and r["route"] == "LOW" and r["status"] == "SCORED"
    assert "card_hash" not in r and "event_ts" not in r and audits == []


@pytest.mark.parametrize("change, code", [
    ({"amt": -5}, "OUT_OF_RANGE:amt"),
    ({"amt": "abc"}, "BAD_TYPE:amt"),
    ({"amt": True}, "BAD_TYPE:amt"),
    ({"lat": 123.4}, "OUT_OF_RANGE:lat"),
    ({"trans_date_trans_time": "31/02/2020 25:61"}, "BAD:trans_date_trans_time"),
    ({"dob": None}, "MISSING:dob"),
    ({"amt": 951.5}, "CONFLICT:amt"),                      # hợp lệ nhưng khác bản gốc
    ({"cc_num": "4000123412341234"}, "CONFLICT:card_hash"),
    ({"trans_date_trans_time": "2020-06-22 03:14:25"}, "CONFLICT:event_ts"),
])
def test_fail01_corrupted_resend_is_never_low(monkeypatch, change, code):
    lane, audits = _lane_with_original(monkeypatch, PII_RAW)
    r = lane.process({**PII_RAW, **change})
    assert r["route"] == "UNSCORED" and r["status"] == "DUPLICATE_CONFLICT" and code in r["errors"], r
    assert r["original"]["route"] == "LOW" and r["case_id"] is None
    assert pii.card_hash(PII_RAW["cc_num"], lane.key) not in str(r) and PII_RAW["cc_num"] not in str(r)  # không lộ thẻ
    assert audits and audits[0][2] == "DUPLICATE_CONFLICT" and audits[0][1] == PII_RAW["trans_num"]


def test_fail01_resend_of_invalid_original_returns_unscored(monkeypatch):
    lane, audits = _lane_with_original(monkeypatch, PII_RAW, txn_status="INVALID", route="UNSCORED")
    r = lane.process({**PII_RAW, "amt": -1})
    assert r["route"] == "UNSCORED" and r["duplicate"] is True and audits == []


# ---------------------------------------------------------------- simulator web: "chạy tiếp" tính cả dữ liệu đã có
def test_next_offset_accounts_for_rows_already_in_db(monkeypatch, tmp_path):
    times = [f"2020-06-21 12:{i:02d}:00" for i in range(50)]
    pd.DataFrame({"trans_date_trans_time": times, "trans_num": [f"t{i}" for i in range(50)]}).to_csv(tmp_path / "fraudTest.csv", index=False)
    state = {"job": None, "last": None}

    def execute(sql, params=None):
        row = {"o": state["job"]} if "ops_jobs" in sql else {"t": state["last"]}
        return SimpleNamespace(fetchone=lambda: row)

    @contextmanager
    def fake_tx():
        yield SimpleNamespace(execute=execute)

    monkeypatch.setattr(ops, "tx", fake_tx)
    monkeypatch.setattr(ops, "settings", SimpleNamespace(data_dir=tmp_path))
    assert ops._next_offset("test") == 0                                   # DB trống, chưa chạy lần nào
    state["last"] = int(pd.Timestamp("2020-06-21 12:29:00", tz="UTC").timestamp())
    assert ops._next_offset("test") == 30                                  # đã chạy bằng dòng lệnh tới dòng 30
    state["job"] = "10"
    assert ops._next_offset("test") == 30                                  # job web cũ hơn dữ liệu trong DB
    state["job"] = "40"
    assert ops._next_offset("test") == 40
    assert ops._next_offset("train") == 40                                 # không có fraudTrain.csv: theo job

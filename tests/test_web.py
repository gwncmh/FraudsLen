"""Kiểm thử phần giao diện web không cần DB: băm mật khẩu, phiên đăng nhập, phân quyền endpoint vận hành,
simulator dùng chung (replay) có tiến độ và dừng sớm."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import fraudlens.api.app as api
from fraudlens.api import accounts, auth
from fraudlens.api.auth import SESSION_PREFIX, User
from fraudlens.tools import simulator

ANALYST = SESSION_PREFIX + "analyst" + "z" * 20


def test_password_hash_roundtrip_and_salt():
    h1, h2 = accounts.hash_password("matkhau123"), accounts.hash_password("matkhau123")
    assert h1 != h2 and h1.startswith("pbkdf2_sha256$")
    assert accounts.verify_password("matkhau123", h1)
    assert not accounts.verify_password("matkhau124", h1)
    assert not accounts.verify_password("x", "rác")


@pytest.mark.parametrize("pw", ["ngan1", "chikhongcoso", "12345678", "a1" * 65])
def test_password_rule(pw):
    with pytest.raises(Exception):
        accounts._check_password_rule(pw)


@pytest.fixture
def client(monkeypatch):
    sessions = {SESSION_PREFIX + "x" * 30: User("lead", "risk_lead", "Trưởng nhóm", 1),
                ANALYST: User("an", "analyst", None, 2)}
    monkeypatch.setattr(auth, "_session_user", lambda t: sessions.get(t))
    return TestClient(api.app)


def test_session_token_is_accepted(client):
    r = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {SESSION_PREFIX}{'x' * 30}"})
    assert r.status_code == 200
    assert r.json() == {"username": "lead", "display_name": "Trưởng nhóm", "role": "risk_lead"}


def test_unknown_session_rejected(client):
    assert client.get("/v1/auth/me", headers={"Authorization": f"Bearer {SESSION_PREFIX}{'y' * 30}"}).status_code == 401


@pytest.mark.parametrize("method,path,body", [
    ("post", "/v1/simulator/start", {"limit": 10}),
    ("post", "/v1/ops/seed-history", {"days": 30}),
    ("post", "/v1/ops/rebuild-redis", None),
    ("post", "/v1/models/v1/approve", {}),
    ("get", "/v1/users", None),
    ("get", "/v1/models", None),
    ("get", "/v1/audit", None),
])
def test_analyst_cannot_run_ops(client, method, path, body):
    r = getattr(client, method)(path, headers={"Authorization": f"Bearer {ANALYST}"}, **({"json": body} if body is not None else {}))
    assert r.status_code == 403


# ---------------------------------------------------------------- simulator dùng chung cho CLI và web
class _FakeApi(BaseHTTPRequestHandler):
    labels: list = []       # trans_num của các nhãn đã nhận (đặt lại trong fixture)
    label_status = 200

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        body = b'{"route": "LOW", "latency_ms": 1.0}' if self.path == "/v1/transactions" else b'{"ok": true}'
        status = 200
        if self.path == "/v1/labels":
            status = type(self).label_status
            if status == 200:
                type(self).labels.append(json.loads(raw)["trans_num"])
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_api():
    _FakeApi.labels, _FakeApi.label_status = [], 200
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeApi)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _csv(tmp_path, n=60):
    rows = [{"trans_date_trans_time": f"2020-06-21 12:{i // 60:02d}:{i % 60:02d}", "cc_num": str(4000 + i % 7), "merchant": "m",
             "category": "grocery_pos", "amt": 10.0 + i, "first": "a", "last": "b", "gender": "F", "street": "s", "city": "c",
             "state": "NY", "zip": "10001", "lat": 40.0, "long": -73.0, "city_pop": 1000, "job": "j", "dob": "1980-01-01",
             "trans_num": f"t{i}", "merch_lat": 40.1, "merch_long": -73.1, "is_fraud": 0} for i in range(n)]
    p = tmp_path / "sim.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def test_replay_reports_progress_and_next_offset(tmp_path, fake_api):
    seen = []
    rep = simulator.replay({"csv": str(_csv(tmp_path)), "api": fake_api, "limit": 50, "offset": 5, "tps": 0, "fault_rate": 0},
                           progress=seen.append, progress_every_s=0)
    assert rep["sent"] == 50 and rep["routes"] == {"LOW": 50}
    assert rep["next_offset"] == 55 and not rep["stopped_early"]
    assert seen and seen[-1]["done"] == 50 and seen[-1]["total"] == 50


def test_replay_stops_early(tmp_path, fake_api):
    t0 = time.perf_counter()
    rep = simulator.replay({"csv": str(_csv(tmp_path)), "api": fake_api, "limit": 60, "tps": 20, "fault_rate": 0},
                           should_stop=lambda: time.perf_counter() - t0 > 0.5)
    assert rep["stopped_early"] and rep["sent"] < 60
    assert rep["next_offset"] == rep["sent"]


# ---------------------------------------------------------------- nhãn trễ qua nhiều lần chạy
def _hourly_csv(tmp_path, n=240):
    """Mỗi giờ một giao dịch trong 10 ngày: một lần chạy 100 dòng (~4 ngày) ngắn hơn nhiều nhãn trễ."""
    t0 = pd.Timestamp("2020-06-21 00:00:00")
    rows = [{"trans_date_trans_time": (t0 + pd.Timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"), "cc_num": str(4000 + i % 7),
             "merchant": "m", "category": "grocery_pos", "amt": 10.0, "lat": 40.0, "long": -73.0, "city_pop": 1000,
             "dob": "1980-01-01", "trans_num": f"t{i}", "merch_lat": 40.1, "merch_long": -73.1, "is_fraud": int(i % 50 == 0)}
            for i in range(n)]
    p = tmp_path / "hourly.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


def _run(csv, api, offset, **kw):
    return simulator.replay({"csv": str(csv), "api": api, "limit": 100, "offset": offset, "tps": 0, "fault_rate": 0,
                             "label_delay_days": 2, **kw})


def test_labels_not_yet_due_are_sent_by_the_next_run(tmp_path, fake_api):
    """Bản cũ bỏ mọi nhãn chưa tới hạn khi lần chạy kết thúc -> chạy từng đoạn ngắn thì không nhãn nào tới được API.
    Giờ lần chạy sau tính lại phần còn nợ từ CSV: mỗi nhãn được gửi đúng một lần, đúng hạn, và hết file thì gửi nốt."""
    csv = _hourly_csv(tmp_path)
    r1 = _run(csv, fake_api, 0)       # dòng 0..99: nhãn tới hạn là của dòng 0..51 (trễ 48 giờ)
    assert r1["labels"] == {"sent": 52, "failed": 0, "carried_in": 0, "pending": 48}
    assert sorted(_FakeApi.labels, key=lambda t: int(t[1:])) == [f"t{i}" for i in range(52)]
    r2 = _run(csv, fake_api, 100)     # nhận 48 nhãn còn nợ của lần 1, gửi tới dòng 151
    assert r2["labels"] == {"sent": 100, "failed": 0, "carried_in": 48, "pending": 48}
    r3 = _run(csv, fake_api, 200)     # chỉ còn 40 dòng: hết file -> gửi nốt
    assert r3["sent"] == 40 and r3["labels"] == {"sent": 88, "failed": 0, "carried_in": 48, "pending": 0}
    assert sorted(_FakeApi.labels, key=lambda t: int(t[1:])) == [f"t{i}" for i in range(240)]  # đủ, không trùng


def test_label_backfill_resends_everything_before_offset(tmp_path, fake_api):
    r = _run(_hourly_csv(tmp_path), fake_api, 100, label_backfill=True)
    assert r["labels"]["carried_in"] == 100 and r["labels"]["sent"] == 152
    assert set(_FakeApi.labels) == {f"t{i}" for i in range(152)}


def test_no_labels_and_failed_labels_are_reported(tmp_path, fake_api):
    csv = _hourly_csv(tmp_path)
    assert _run(csv, fake_api, 100, no_labels=True)["labels"] == {"sent": 0, "failed": 0, "carried_in": 0, "pending": 0}
    _FakeApi.label_status = 401       # sai API key: trước đây vẫn được đếm là "đã gửi"
    r = _run(csv, fake_api, 0)
    assert r["labels"]["sent"] == 0 and r["labels"]["failed"] == 52 and _FakeApi.labels == []


def test_stopped_run_leaves_labels_for_next_run(tmp_path, fake_api):
    csv = _hourly_csv(tmp_path)
    seen = {"n": 0}

    def stop():
        seen["n"] += 1
        return seen["n"] > 3          # should_stop được hỏi mỗi 20 dòng -> dừng ở dòng 60

    r = simulator.replay({"csv": str(csv), "api": fake_api, "limit": 100, "tps": 0, "fault_rate": 0, "label_delay_days": 2},
                         should_stop=stop)
    assert r["stopped_early"] and r["sent"] == 60 and r["next_offset"] == 60
    lb = r["labels"]
    assert lb == {"sent": 12, "failed": 0, "carried_in": 0, "pending": 48}  # nhãn đã tới hạn vẫn được gửi hết khi dừng
    _run(csv, fake_api, 60)           # chạy tiếp từ chỗ dừng
    assert len(set(_FakeApi.labels)) == 112  # 160 dòng đã phát, nhãn tới hạn: dòng 0..111


@pytest.mark.parametrize("path", ["/v1/dashboard", "/v1/transactions", "/v1/transactions/1", "/v1/audit"])
def test_new_views_require_login(client, path):
    assert client.get(path).status_code == 401


def test_validation_errors_normalized_to_list():
    from fraudlens.api.views import ERRS_SQL, errors_of
    assert errors_of({"errors": ["BAD_TYPE:amt", "MISSING:dob"], "fields": ["amt"]}) == ["BAD_TYPE:amt", "MISSING:dob"]
    assert errors_of(["UNKNOWN:category"]) == ["UNKNOWN:category"]
    assert errors_of({}) == [] and errors_of(None) == [] and errors_of([]) == []
    assert "->'errors'" in ERRS_SQL.format(c="validation_errors")


def test_json_lists_tolerate_null():
    from fraudlens.api.views import as_list
    assert as_list(None) == [] and as_list({"a": 1}) == [] and as_list("x") == []
    assert as_list([{"feature": "amt"}]) == [{"feature": "amt"}]


def test_auth_config_reports_users(client, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    @contextmanager
    def fake_tx():
        yield SimpleNamespace(execute=lambda sql, params=None: SimpleNamespace(fetchone=lambda: {"n": 2}))

    monkeypatch.setattr(accounts, "tx", fake_tx)
    assert client.get("/v1/auth/config").json() == {"has_users": True, "db_ready": True}


def test_oversized_credentials_rejected_before_hashing(client):
    r = client.post("/v1/auth/login", json={"username": "a" * 65, "password": "x"})
    assert r.status_code == 422
    r = client.post("/v1/auth/login", json={"username": "ok.name", "password": "a1" * 600})
    assert r.status_code == 422


def test_provision_user_validates_and_audits(monkeypatch):
    """Cấp tài khoản (Admin trên web và lệnh create-user dùng chung): kiểm tên, vai trò, mật khẩu, trùng tên; ghi audit."""
    from types import SimpleNamespace
    from fastapi import HTTPException

    class Conn:
        def __init__(self, exists=False):
            self.exists = exists

        def execute(self, sql, params=None):
            if sql.startswith("SELECT 1"):
                row = {"n": 1} if self.exists else None
            else:
                row = {"id": 9, "username": params[0], "display_name": params[1], "password_hash": params[2], "role": params[3]}
            return SimpleNamespace(fetchone=lambda: row)

    audits = []
    monkeypatch.setattr(accounts, "audit", lambda conn, *a: audits.append(a))
    u = accounts.provision_user(Conn(), "  Hung.Nguyen ", "matkhau123", "risk_lead", None, "cli:admin")
    assert u["username"] == "hung.nguyen" and u["display_name"] == "hung.nguyen" and u["role"] == "risk_lead"
    assert accounts.verify_password("matkhau123", u["password_hash"])
    assert audits == [("user", "hung.nguyen", "CREATE", "cli:admin", {"role": "risk_lead"})]
    for args in [("x", "matkhau123", "admin"), ("ok.name", "matkhau123", "boss"), ("ok.name", "ngan", "admin")]:
        with pytest.raises(HTTPException) as e:
            accounts.provision_user(Conn(), *args, None, "t")
        assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        accounts.provision_user(Conn(exists=True), "ok.name", "matkhau123", "admin", None, "t")
    assert e.value.status_code == 409


def test_inject_always_corrupts_payload():
    """Mọi payload do inject() trả về phải thực sự hỏng (validator bắt được) – không có 'lỗi' nào để nguyên giao dịch."""
    import random
    from fraudlens.common.validation import validate
    row = {"trans_num": "t1", "trans_date_trans_time": "2020-06-21 12:00:00", "cc_num": "4000123412341234", "merchant": "m",
           "category": "grocery_pos", "amt": 10.0, "lat": 40.0, "long": -73.0, "merch_lat": 40.1, "merch_long": -73.1,
           "city_pop": 1000, "dob": "1980-01-01"}
    assert validate(row) == []
    rng, kinds = random.Random(3), set()
    for _ in range(300):
        bad, kind = simulator.inject(row, rng)
        kinds.add(kind)
        assert kind in simulator.FAIL_SAFE_FAULTS and validate(bad), kind
    assert kinds == simulator.FAIL_SAFE_FAULTS


def test_replay_applies_backpressure_at_max_speed(tmp_path, fake_api):
    """--tps 0: số giao dịch đang chờ gửi bị giới hạn (4 × số luồng), nếu không các luồng gửi lệch nhau nhiều ngày thời gian
    giao dịch (API đánh STALE oan) và should_stop không còn dừng được gì."""
    seen = []
    rep = simulator.replay({"csv": str(_hourly_csv(tmp_path)), "api": fake_api, "limit": 240, "tps": 0, "threads": 2,
                            "fault_rate": 0, "no_labels": True}, progress=seen.append, progress_every_s=0)
    assert rep["sent"] == 240 and rep["routes"] == {"LOW": 240}
    assert max(s["queued"] - s["done"] for s in seen) <= 2 * 4

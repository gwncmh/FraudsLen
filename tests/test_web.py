"""Kiểm thử phần giao diện web không cần DB: băm mật khẩu, phiên đăng nhập, phân quyền endpoint vận hành,
simulator dùng chung (replay) có tiến độ và dừng sớm."""
from __future__ import annotations

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


def test_password_hash_roundtrip_and_salt():
    h1, h2 = accounts.hash_password("matkhau123"), accounts.hash_password("matkhau123")
    assert h1 != h2 and h1.startswith("pbkdf2_sha256$")
    assert accounts.verify_password("matkhau123", h1)
    assert not accounts.verify_password("matkhau124", h1)
    assert not accounts.verify_password("x", "rác")


@pytest.mark.parametrize("pw", ["ngan1", "chikhongcoso", "12345678"])
def test_password_rule(pw):
    with pytest.raises(Exception):
        accounts._check_password_rule(pw)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "_tokens", lambda: {"tok-analyst": User("an", "analyst")})
    sessions = {SESSION_PREFIX + "x" * 30: User("lead", "risk_lead", "Trưởng nhóm", 1)}
    monkeypatch.setattr(auth, "_session_user", lambda t: sessions.get(t))
    return TestClient(api.app)


def test_session_token_is_accepted(client):
    r = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {SESSION_PREFIX}{'x' * 30}"})
    assert r.status_code == 200
    assert r.json() == {"username": "lead", "display_name": "Trưởng nhóm", "role": "risk_lead", "account": True}


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
    r = getattr(client, method)(path, headers={"Authorization": "Bearer tok-analyst"}, **({"json": body} if body is not None else {}))
    assert r.status_code == 403


# ---------------------------------------------------------------- simulator dùng chung cho CLI và web
class _FakeApi(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        body = b'{"route": "LOW", "latency_ms": 1.0}' if self.path == "/v1/transactions" else b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_api():
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

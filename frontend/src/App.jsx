import React, { useEffect, useState } from "react";
import { getToken, setToken, api } from "./api.js";
import Queue from "./Queue.jsx";
import CaseDetail from "./CaseDetail.jsx";

// Điều hướng đơn giản bằng hash: #/ là hàng đợi, #/cases/<id> là chi tiết hồ sơ.
function useRoute() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const m = hash.match(/^#\/cases\/(\d+)/);
  return m ? { page: "case", id: Number(m[1]) } : { page: "queue" };
}

function Login({ onDone }) {
  const [token, setTok] = useState("");
  const [err, setErr] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    setToken(token.trim());
    try { await api.stats(); onDone(); } catch { setErr("Token không hợp lệ"); setToken(""); }
  };
  return (
    <form className="login" onSubmit={submit}>
      <h1>FraudLens</h1>
      <p className="muted">Đăng nhập bằng token được cấp (bản đồ án).</p>
      <input value={token} onChange={(e) => setTok(e.target.value)} placeholder="Token" autoFocus />
      <button type="submit">Đăng nhập</button>
      {err && <p className="error">{err}</p>}
    </form>
  );
}

function StatsBar() {
  const [s, setS] = useState(null);
  useEffect(() => {
    const load = () => api.stats().then(setS).catch(() => {});
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);
  if (!s) return null;
  const open = Object.fromEntries(s.open_cases.map((r) => [r.route, r.n]));
  const jobs = Object.fromEntries(s.brief_jobs.map((r) => [r.status, r.n]));
  const alerts = s.monitor.filter((m) => m.level && m.level !== "OK");
  return (
    <div className="stats">
      <span className="pill HIGH">HIGH {open.HIGH || 0}</span>
      <span className="pill MEDIUM">MEDIUM {open.MEDIUM || 0}</span>
      <span className="pill UNSCORED">UNSCORED {open.UNSCORED || 0}</span>
      <span className="muted">Brief đang chờ: {(jobs.PENDING || 0) + (jobs.RUNNING || 0)}</span>
      {alerts.length > 0 && <span className="pill warn">Cảnh báo dữ liệu: {alerts.map((a) => a.feature).join(", ")}</span>}
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(Boolean(getToken()));
  const route = useRoute();
  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  const logout = () => { setToken(""); setAuthed(false); };
  const onAuthError = (e) => { if (e.message === "UNAUTHORIZED") logout(); };
  return (
    <div className="app">
      <header>
        <a href="#/" className="brand">FraudLens</a>
        <StatsBar />
        <button className="link" onClick={logout}>Đăng xuất</button>
      </header>
      <main>
        {route.page === "case"
          ? <CaseDetail id={route.id} onAuthError={onAuthError} />
          : <Queue onAuthError={onAuthError} />}
      </main>
    </div>
  );
}

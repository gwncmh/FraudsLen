import React, { useEffect, useState } from "react";
import { api } from "./api.js";

const fmtTime = (ts) => (ts ? new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 16) : "–");

export default function Queue({ onAuthError }) {
  const [filters, setFilters] = useState({ status: "open", route: "", assigned_role: "" });
  const [items, setItems] = useState([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    const params = Object.fromEntries(Object.entries({ ...filters, limit: 100 }).filter(([, v]) => v !== ""));
    api.cases(params).then((d) => { setItems(d.items); setErr(""); }).catch((e) => { onAuthError(e); setErr(e.message); });
  }, [filters]);

  const set = (k) => (e) => setFilters({ ...filters, [k]: e.target.value });
  return (
    <section>
      <div className="toolbar">
        <h2>Hàng đợi hồ sơ</h2>
        <select value={filters.status} onChange={set("status")}>
          <option value="open">Đang mở</option>
          <option value="closed">Đã đóng</option>
          <option value="all">Tất cả</option>
        </select>
        <select value={filters.route} onChange={set("route")}>
          <option value="">Mọi mức</option>
          <option value="HIGH">HIGH</option>
          <option value="MEDIUM">MEDIUM</option>
          <option value="UNSCORED">UNSCORED</option>
        </select>
        <select value={filters.assigned_role} onChange={set("assigned_role")}>
          <option value="">Mọi người phụ trách</option>
          <option value="analyst">Analyst</option>
          <option value="risk_lead">Risk Lead (chuyển cấp)</option>
        </select>
      </div>
      {err && <p className="error">{err}</p>}
      <table>
        <thead>
          <tr><th>Hồ sơ</th><th>Mức</th><th>Điểm</th><th>Số tiền</th><th>Loại merchant</th><th>Thẻ</th><th>Thời gian GD</th><th>Trạng thái</th><th>Brief</th></tr>
        </thead>
        <tbody>
          {items.map((c) => (
            <tr key={c.id} onClick={() => { window.location.hash = `#/cases/${c.id}`; }}>
              <td>#{c.id}</td>
              <td><span className={`pill ${c.route}`}>{c.route}</span></td>
              <td>{c.score != null ? Number(c.score).toFixed(3) : "–"}</td>
              <td>{c.amt != null ? Number(c.amt).toLocaleString("en-US", { maximumFractionDigits: 2 }) : "–"}</td>
              <td>{c.category || "–"}</td>
              <td>{c.card_last4 ? `•••• ${c.card_last4}` : "–"}</td>
              <td>{fmtTime(c.event_ts)}</td>
              <td>{c.status}</td>
              <td>{c.brief_source || "đang soạn"}</td>
            </tr>
          ))}
          {items.length === 0 && <tr><td colSpan="9" className="muted">Không có hồ sơ.</td></tr>}
        </tbody>
      </table>
    </section>
  );
}

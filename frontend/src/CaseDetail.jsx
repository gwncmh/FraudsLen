import React, { useEffect, useState } from "react";
import { api } from "./api.js";

const fmt = (v, d = 2) => (v == null ? "–" : typeof v === "number" ? Number(v.toFixed(d)) : v);
const FEATURE_LABELS = {
  amt: "Số tiền", hour: "Giờ", distance_km: "Khoảng cách (km)", age: "Tuổi chủ thẻ", n_txn_1h: "Số GD 1 giờ",
  n_txn_24h: "Số GD 24 giờ", n_txn_7d: "Số GD 7 ngày", n_txn_30d: "Số GD 30 ngày", amt_mean_30d: "TB số tiền 30 ngày",
  amt_ratio_30d: "Số tiền / TB 30 ngày", is_new_category_30d: "Loại merchant mới", is_new_merchant_30d: "Merchant mới",
  secs_since_last: "Giây từ GD trước", is_night: "Giờ khuya",
};

function Brief({ brief, job, holdout, onFeedback }) {
  if (holdout) return <p className="muted">Hồ sơ thuộc nhóm đối chứng: brief không hiển thị (dùng để đo giá trị của brief).</p>;
  if (!brief) return <p className="muted">Brief đang được soạn{job?.last_error ? ` (lỗi gần nhất: ${job.last_error})` : ""}…</p>;
  const b = brief.brief;
  return (
    <div className="brief">
      <p className="muted">
        Nguồn: {brief.brief_source === "llm" ? `LLM (${brief.llm_model})` : "brief dạng mẫu"} · playbook {brief.kb_version} ·
        đoạn đã dùng: {(brief.chunk_ids || []).join(", ")}
      </p>
      <h4>Tóm tắt</h4>
      <ul>{b.summary.map((s, i) => <li key={i}>{s.text}</li>)}</ul>
      <h4>Tín hiệu rủi ro</h4>
      <ul>{b.risk_signals.length ? b.risk_signals.map((s, i) => <li key={i}>{s.signal}</li>) : <li className="muted">Không có</li>}</ul>
      <h4>Các bước kiểm tra</h4>
      <ol>{b.checklist.map((c, i) => <li key={i}>{c.step} <span className="src">[{c.source}]</span></li>)}</ol>
      {b.limitations?.length > 0 && (<><h4>Giới hạn</h4><ul>{b.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></>)}
      <p className="disclaimer">{b.disclaimer}</p>
      <div className="actions">
        <span className="muted">Brief này có hữu ích không?</span>
        <button onClick={() => onFeedback("BRIEF_USEFUL", brief.id)}>Hữu ích</button>
        <button onClick={() => onFeedback("BRIEF_NOT_USEFUL", brief.id)}>Không hữu ích</button>
      </div>
    </div>
  );
}

export default function CaseDetail({ id, onAuthError }) {
  const [d, setD] = useState(null);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState("");

  const load = () => api.caseDetail(id).then(setD).catch((e) => { onAuthError(e); setMsg(e.message); });
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, [id]);
  // ghi lần mở hồ sơ để đo thời gian xử lý (mở -> đóng); lỗi ở đây không ảnh hưởng thao tác
  useEffect(() => { api.act(id, "VIEW", null).catch(() => {}); }, [id]);

  const act = async (action) => {
    try { await api.act(id, action, note); setNote(""); setMsg("Đã ghi nhận."); load(); }
    catch (e) { onAuthError(e); setMsg(e.message); }
  };
  const feedback = async (action, briefId) => {
    try { await api.act(id, action, `brief_id=${briefId}`); setMsg("Đã ghi nhận đánh giá brief."); load(); }
    catch (e) { onAuthError(e); setMsg(e.message); }
  };
  if (!d) return <p className="muted">{msg || "Đang tải…"}</p>;
  const c = d.case;
  const f = c.features || {};
  const closed = c.status.startsWith("CLOSED");
  const factors = d.brief?.factors || [];
  return (
    <section className="detail">
      <a href="#/">← Hàng đợi</a>
      <h2>Hồ sơ #{c.id} <span className={`pill ${c.route}`}>{c.route}</span> <span className="muted">{c.status}</span></h2>
      <div className="grid">
        <div className="card">
          <h3>Giao dịch</h3>
          <dl>
            <dt>Mã GD</dt><dd>{c.trans_num || "–"}</dd>
            <dt>Thẻ</dt><dd>{c.card_last4 ? `•••• ${c.card_last4}` : "–"}</dd>
            <dt>Số tiền</dt><dd>{fmt(c.amt != null ? Number(c.amt) : null)}</dd>
            <dt>Merchant</dt><dd>{c.merchant || "–"} ({c.category || "–"})</dd>
            <dt>Thời gian</dt><dd>{c.event_ts ? new Date(c.event_ts * 1000).toISOString().replace("T", " ").slice(0, 19) : "–"}</dd>
          </dl>
          <h3>Điểm và phân luồng</h3>
          <dl>
            <dt>Trạng thái chấm</dt><dd>{c.score_status}{c.score_error ? ` (${c.score_error})` : ""}</dd>
            <dt>Điểm</dt><dd>{fmt(c.score, 4)}</dd>
            <dt>Ngưỡng</dt><dd>MEDIUM ≥ {fmt(c.t_medium, 4)} · HIGH ≥ {fmt(c.t_high, 4)}</dd>
            <dt>Phiên bản</dt><dd>{c.model_version || "–"} · {c.config_version || "–"}</dd>
          </dl>
          {factors.length > 0 && (<>
            <h3>Yếu tố ảnh hưởng nhiều nhất</h3>
            <ul>{factors.map((x, i) => <li key={i}>{FEATURE_LABELS[x.feature] || x.feature} = {String(x.value)} → {x.direction}</li>)}</ul>
          </>)}
          <h3>Lịch sử thẻ (feature)</h3>
          <dl>{Object.keys(FEATURE_LABELS).filter((k) => f[k] != null).map((k) => (
            <React.Fragment key={k}><dt>{FEATURE_LABELS[k]}</dt><dd>{fmt(f[k])}</dd></React.Fragment>))}</dl>
        </div>
        <div className="card">
          <h3>Case Brief</h3>
          <Brief brief={d.brief} job={d.brief_job} holdout={d.brief_holdout} onFeedback={feedback} />
          <h3>Xử lý</h3>
          {closed ? <p className="muted">Hồ sơ đã đóng.</p> : (<>
            <textarea value={note} onChange={(e) => setNote(e.target.value)} placeholder="Nhận định của chuyên viên (bắt buộc khi đóng hồ sơ)" />
            <div className="actions">
              <button onClick={() => act("NOTE")} disabled={!note.trim()}>Ghi nhận định</button>
              <button onClick={() => act("INVESTIGATE")}>Điều tra thêm</button>
              <button onClick={() => act("ESCALATE")}>Chuyển cấp</button>
              <button className="danger" onClick={() => act("CLOSE_FRAUD")} disabled={!note.trim()}>Đóng: gian lận</button>
              <button onClick={() => act("CLOSE_LEGIT")} disabled={!note.trim()}>Đóng: hợp lệ</button>
              <button className="link" onClick={() => act("REGENERATE_BRIEF")}>Soạn lại brief</button>
            </div>
          </>)}
          {msg && <p className="muted">{msg}</p>}
          <h3>Lịch sử thao tác</h3>
          <ul>{d.actions.filter((a) => a.action !== "VIEW").map((a, i) => <li key={i}>{a.created_at?.slice(0, 19)} · {a.actor} ({a.role}): {a.action}{a.note ? ` – ${a.note}` : ""}</li>)}</ul>
        </div>
      </div>
    </section>
  );
}

import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs) {
  return twMerge(clsx(inputs));
}

export const ROLE_LABELS = { analyst: "Fraud Analyst", risk_lead: "Risk Lead", admin: "Admin" };
export const CASE_STATUS = {
  OPEN: "Mới", INVESTIGATING: "Đang điều tra", ESCALATED: "Đã chuyển cấp", CLOSED_FRAUD: "Đóng – gian lận", CLOSED_LEGIT: "Đóng – hợp lệ",
};
export const JOB_STATUS = { RUNNING: "Đang chạy", STOPPING: "Đang dừng", DONE: "Xong", FAILED: "Lỗi", STOPPED: "Đã dừng" };
export const JOB_KINDS = {
  simulator: "Simulator", seed_history: "Nạp lịch sử", rebuild_redis: "Dựng lại Redis", check_parity: "Offline = online",
  trace_all: "Truy vết toàn bộ", eval_briefs: "Chấm brief",
};
export const FEATURE_LABELS = {
  amt: "Số tiền", hour: "Giờ", distance_km: "Khoảng cách (km)", age: "Tuổi chủ thẻ", n_txn_1h: "Số GD 1 giờ",
  n_txn_24h: "Số GD 24 giờ", n_txn_7d: "Số GD 7 ngày", n_txn_30d: "Số GD 30 ngày", amt_mean_30d: "TB số tiền 30 ngày",
  amt_ratio_30d: "Số tiền / TB 30 ngày", amt_zscore_30d: "Z-score số tiền 30 ngày", is_new_category_30d: "Loại merchant mới",
  is_new_merchant_30d: "Merchant mới", secs_since_last: "Giây từ GD trước", is_night: "Giờ khuya", category: "Loại merchant",
  city_pop: "Dân số thành phố",
};

export const num = (v, d = 0) =>
  v == null || Number.isNaN(Number(v)) ? "–" : Number(v).toLocaleString("vi-VN", { maximumFractionDigits: d });
export const pct = (v, d = 1) =>
  v == null || Number.isNaN(Number(v)) ? "–" : `${(Number(v) * 100).toLocaleString("vi-VN", { maximumFractionDigits: d })}%`;
export const money = (v) =>
  v == null ? "–" : `$${Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
export const fmtTs = (ts) => (ts ? new Date(ts * 1000).toISOString().replace("T", " ").slice(0, 16) : "–");
export const fmtDate = (s) => (s ? new Date(s).toLocaleString("vi-VN", { hour12: false }) : "–");
export const ago = (s) => {
  if (!s) return "–";
  const sec = (Date.now() - new Date(s).getTime()) / 1000;
  if (sec < 60) return `${Math.max(0, Math.round(sec))} giây trước`;
  if (sec < 3600) return `${Math.round(sec / 60)} phút trước`;
  if (sec < 86400) return `${Math.round(sec / 3600)} giờ trước`;
  return `${Math.round(sec / 86400)} ngày trước`;
};
export const can = (user, ...roles) => Boolean(user && roles.includes(user.role));

/** Mức nghiêm trọng hiển thị: HIGH có xác suất gian lận ≥ 0,5 thành CRITICAL. */
export function severityOf(route, score) {
  if (route === "HIGH") return score != null && score >= 0.5 ? "CRITICAL" : "HIGH";
  return route || "LOW";
}

export function downloadCsv(filename, rows) {
  if (!rows.length) return;
  const cols = Object.keys(rows[0]);
  const esc = (v) => {
    const s = v == null ? "" : typeof v === "object" ? JSON.stringify(v) : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }));
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

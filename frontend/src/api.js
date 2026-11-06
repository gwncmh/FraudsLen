// Mọi thao tác của giao diện đều đi qua API; giao diện không truy cập cơ sở dữ liệu.
const TOKEN_KEY = "fraudlens_token";

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
export function setToken(t) {
  try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch { /* bỏ qua */ }
}

function detailText(d) {
  if (!d) return "";
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => x.msg || JSON.stringify(x)).join("; ");
  if (d.message) return d.message + (d.violations ? ": " + d.violations.join("; ") : "");
  return JSON.stringify(d);
}

export class ApiError extends Error {
  constructor(status, message, detail) { super(message); this.status = status; this.detail = detail; }
}

async function call(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}`, ...(options.headers || {}) },
    });
  } catch {
    throw new ApiError(0, "Không kết nối được API – kiểm tra container api đang chạy");
  }
  const body = await res.json().catch(() => ({}));
  if (res.status === 401 && !path.startsWith("/v1/auth/login")) {
    window.dispatchEvent(new Event("fraudlens:unauthorized"));
    throw new ApiError(401, "UNAUTHORIZED");
  }
  if (!res.ok) throw new ApiError(res.status, detailText(body.detail) || `Lỗi ${res.status}`, body.detail);
  return body;
}

const post = (path, data) => call(path, { method: "POST", body: JSON.stringify(data ?? {}) });

export const api = {
  // tài khoản
  authConfig: () => call("/v1/auth/config"),
  login: (username, password) => post("/v1/auth/login", { username, password }),
  register: (data) => post("/v1/auth/register", data),
  logout: () => post("/v1/auth/logout"),
  me: () => call("/v1/auth/me"),
  changePassword: (old_password, new_password) => post("/v1/auth/password", { old_password, new_password }),
  users: () => call("/v1/users"),
  createUser: (data) => post("/v1/users", data),
  updateUser: (id, data) => call(`/v1/users/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  // hồ sơ
  cases: (params) => call(`/v1/cases?${new URLSearchParams(params)}`),
  caseDetail: (id) => call(`/v1/cases/${id}`),
  act: (id, action, note) => post(`/v1/cases/${id}/actions`, { action, note }),
  // thống kê
  stats: () => call("/v1/stats"),
  productStats: () => call("/v1/stats/product"),
  config: () => call("/v1/config"),
  // dashboard, giao dịch, audit
  dashboard: (win = "live") => call(`/v1/dashboard?window=${win}`),
  transactions: (params) => call(`/v1/transactions?${new URLSearchParams(params)}`),
  transaction: (id) => call(`/v1/transactions/${id}`),
  audit: (params) => call(`/v1/audit?${new URLSearchParams(params)}`),
  // vận hành
  system: () => call("/v1/system/status"),
  simulator: () => call("/v1/simulator"),
  startSimulator: (params) => post("/v1/simulator/start", params),
  jobs: (kind) => call(`/v1/jobs${kind ? `?kind=${kind}` : ""}`),
  job: (id) => call(`/v1/jobs/${id}`),
  stopJob: (id) => post(`/v1/jobs/${id}/stop`),
  seedHistory: (days) => post("/v1/ops/seed-history", { days }),
  rebuildRedis: () => post("/v1/ops/rebuild-redis"),
  checkParity: (n) => post("/v1/ops/check-parity", { n }),
  traceAll: () => post("/v1/ops/trace-all"),
  evalBriefs: (n) => post("/v1/ops/eval-briefs", { n }),
  regenBriefs: () => post("/v1/ops/regen-briefs"),
  testLlm: () => post("/v1/ops/test-llm"),
  trace: (tn) => call(`/v1/ops/trace/${encodeURIComponent(tn)}`),
  verifyAudit: () => call("/v1/audit/verify"),
  // model
  models: () => call("/v1/models"),
  approve: (version, override) => post(`/v1/models/${encodeURIComponent(version)}/approve`, { override }),
};

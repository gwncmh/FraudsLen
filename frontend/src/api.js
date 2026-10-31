// Mọi thao tác của giao diện đều đi qua API; giao diện không truy cập cơ sở dữ liệu.
const TOKEN_KEY = "fraudlens_token";

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; }
}
export function setToken(t) {
  try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch { /* bỏ qua */ }
}

async function call(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}`, ...(options.headers || {}) },
  });
  if (res.status === 401) throw new Error("UNAUTHORIZED");
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `Lỗi ${res.status}`);
  return body;
}

export const api = {
  cases: (params) => call(`/v1/cases?${new URLSearchParams(params)}`),
  caseDetail: (id) => call(`/v1/cases/${id}`),
  act: (id, action, note) => call(`/v1/cases/${id}/actions`, { method: "POST", body: JSON.stringify({ action, note }) }),
  stats: () => call("/v1/stats"),
  config: () => call("/v1/config"),
};

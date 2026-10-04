const API_BASE = window.COURSE_API_BASE || "http://127.0.0.1:8000/api/v1/admin";
const STORAGE_KEY = "course-compass-admin-auth";

export class AdminApiError extends Error {
  constructor(status, payload) {
    super(payload?.error?.message || "管理请求失败，请稍后重试。");
    this.name = "AdminApiError";
    this.status = status;
    this.code = payload?.error?.code || "UNKNOWN_ERROR";
    this.details = payload?.error?.details || {};
  }
}

export function getAdminAuth() {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "null");
  } catch {
    return null;
  }
}

export function setAdminAuth(auth) {
  if (auth) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(auth));
  else sessionStorage.removeItem(STORAGE_KEY);
}

async function parseResponse(response) {
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new AdminApiError(response.status, payload);
  return payload;
}

async function refresh() {
  const auth = getAdminAuth();
  if (!auth?.refresh_token) return false;
  const response = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: auth.refresh_token }),
  });
  if (!response.ok) {
    setAdminAuth(null);
    return false;
  }
  setAdminAuth(await response.json());
  return true;
}

export async function request(path, options = {}, retry = true) {
  const auth = getAdminAuth();
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (auth?.access_token) headers.set("Authorization", `Bearer ${auth.access_token}`);
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (response.status === 401 && retry && path !== "/auth/login" && (await refresh())) {
    return request(path, options, false);
  }
  return parseResponse(response);
}

const json = (method, body) => ({ method, body: JSON.stringify(body) });

export const adminApi = {
  login: (username, password) => request("/auth/login", json("POST", { username, password })),
  logout: (refreshToken) => request("/auth/logout", json("POST", { refresh_token: refreshToken })),
  me: () => request("/me"),
  dashboard: () => request("/dashboard"),
  courses: (query = "") => request(`/courses?limit=100&query=${encodeURIComponent(query)}`),
  course: (id) => request(`/courses/${encodeURIComponent(id)}`),
  createCourse: (payload) => request("/courses", json("POST", payload)),
  updateCourse: (id, payload) => request(`/courses/${encodeURIComponent(id)}`, json("PATCH", payload)),
  setCourseStatus: (id, action, rowVersion, reason = null) => request(`/courses/${encodeURIComponent(id)}:${action}`, json("POST", { row_version: rowVersion, reason })),
  addAlias: (id, aliasText) => request(`/courses/${encodeURIComponent(id)}/aliases`, json("POST", { alias_text: aliasText })),
  validateImport: (payload) => request("/course-imports:validate", json("POST", payload)),
  imports: () => request("/course-imports"),
  commitImport: (id, rowVersion, reason = null) => request(`/course-imports/${encodeURIComponent(id)}:commit`, json("POST", { row_version: rowVersion, reason })),
  cancelImport: (id, rowVersion, reason = null) => request(`/course-imports/${encodeURIComponent(id)}:cancel`, json("POST", { row_version: rowVersion, reason })),
  users: () => request("/users?limit=100"),
  user: (id) => request(`/users/${encodeURIComponent(id)}`),
  setUserStatus: (id, action, rowVersion, reason = null) => request(`/users/${encodeURIComponent(id)}:${action}`, json("POST", { row_version: rowVersion, reason })),
  resolutions: () => request("/course-resolutions?limit=100"),
  resolution: (id) => request(`/course-resolutions/${encodeURIComponent(id)}`),
  reviewResolution: (id, payload) => request(`/course-resolutions/${encodeURIComponent(id)}/review`, json("POST", payload)),
  recommendations: () => request("/recommendations?limit=100"),
  recommendation: (id) => request(`/recommendations/${encodeURIComponent(id)}`),
  simulateRecommendation: (payload) => request("/recommendation-simulations", json("POST", payload)),
  hyperparameterSchema: () => request("/hyperparameter-schema"),
  experiments: () => request("/hyperparameter-experiments?limit=100"),
  experiment: (id) => request(`/hyperparameter-experiments/${encodeURIComponent(id)}`),
  experimentResults: (id) => request(`/hyperparameter-experiments/${encodeURIComponent(id)}/results?limit=100`),
  createExperiment: (payload) => request("/hyperparameter-experiments", json("POST", payload)),
  exportExperiment: (id, format = "CSV") => request(`/hyperparameter-experiments/${encodeURIComponent(id)}:export`, json("POST", { format })),
  cloneExperiment: (id) => request(`/hyperparameter-experiments/${encodeURIComponent(id)}:clone`, json("POST", {})),
  cancelExperiment: (id) => request(`/hyperparameter-experiments/${encodeURIComponent(id)}:cancel`, json("POST", {})),
  compareResults: (resultIds, baselineResultId) => request("/hyperparameter-experiments:compare", json("POST", {
    result_ids: resultIds,
    baseline_result_id: baselineResultId,
  })),
  createPolicyDraft: (resultId, reason) => request(`/hyperparameter-results/${encodeURIComponent(resultId)}:create-policy-draft`, json("POST", { reason })),
  policies: () => request("/fairness-policies"),
  computePolicy: (payload) => request("/fairness-policies:compute", json("POST", payload)),
  activatePolicy: (id, payload) => request(`/fairness-policies/${encodeURIComponent(id)}:activate`, json("POST", payload)),
  jobs: () => request("/jobs?limit=100"),
  cancelJob: (id) => request(`/jobs/${encodeURIComponent(id)}:cancel`, json("POST", {})),
  retryJob: (id) => request(`/jobs/${encodeURIComponent(id)}:retry`, json("POST", {})),
  audits: () => request("/audit-logs?limit=100"),
  health: () => request("/system/health"),
  config: () => request("/system/config"),
  admins: () => request("/admins"),
  createAdmin: (payload) => request("/admins", json("POST", payload)),
  updateAdmin: (id, payload) => request(`/admins/${encodeURIComponent(id)}`, json("PATCH", payload)),
  setAdminStatus: (id, action, rowVersion, reason = null) => request(`/admins/${encodeURIComponent(id)}:${action}`, json("POST", { row_version: rowVersion, reason })),
  resetAdminPassword: (id, password) => request(`/admins/${encodeURIComponent(id)}:reset-password`, json("POST", { new_password: password })),
};

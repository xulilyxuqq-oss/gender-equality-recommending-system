const API_BASE = window.COURSE_API_BASE || "http://127.0.0.1:8000/api/v1";

export class ApiError extends Error {
  constructor(status, payload) {
    super(payload?.error?.message || "请求失败，请稍后重试。");
    this.name = "ApiError";
    this.status = status;
    this.code = payload?.error?.code || "UNKNOWN_ERROR";
    this.details = payload?.error?.details || {};
  }
}

export function getAuth() {
  try {
    return JSON.parse(localStorage.getItem("course-compass-auth") || "null");
  } catch {
    return null;
  }
}

export function setAuth(auth) {
  if (auth) {
    localStorage.setItem("course-compass-auth", JSON.stringify(auth));
  } else {
    localStorage.removeItem("course-compass-auth");
  }
}

async function parseResponse(response) {
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

async function refreshTokens() {
  const auth = getAuth();
  if (!auth?.refresh_token) return false;
  const response = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: auth.refresh_token }),
  });
  if (!response.ok) {
    setAuth(null);
    return false;
  }
  const next = await response.json();
  setAuth(next);
  return true;
}

export async function request(path, options = {}, retry = true) {
  const auth = getAuth();
  const headers = new Headers(options.headers || {});
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  if (auth?.access_token) headers.set("Authorization", `Bearer ${auth.access_token}`);
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (response.status === 401 && retry && (await refreshTokens())) {
    return request(path, options, false);
  }
  return parseResponse(response);
}

function parseSseBlock(block) {
  let event = "message";
  let id = null;
  const data = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("id:")) id = line.slice(3).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  return { event, id, data: data.length ? JSON.parse(data.join("\n")) : {} };
}

export async function streamMessage(sessionId, payload, onEvent, retry = true) {
  const auth = getAuth();
  const response = await fetch(`${API_BASE}/chat/sessions/${sessionId}/messages:stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...(auth?.access_token ? { Authorization: `Bearer ${auth.access_token}` } : {}),
    },
    body: JSON.stringify(payload),
  });
  if (response.status === 401 && retry && (await refreshTokens())) {
    return streamMessage(sessionId, payload, onEvent, false);
  }
  if (!response.ok) throw new ApiError(response.status, await response.json().catch(() => ({})));
  if (!response.body) throw new Error("浏览器不支持流式响应。");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done }).replaceAll("\r\n", "\n");
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      if (block.trim()) onEvent(parseSseBlock(block));
    }
    if (done) break;
  }
  if (buffer.trim()) onEvent(parseSseBlock(buffer));
}

export const api = {
  register: (username, password) => request("/auth/register", { method: "POST", body: JSON.stringify({ username, password }) }),
  login: (username, password) => request("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: (refreshToken) => request("/auth/logout", { method: "POST", body: JSON.stringify({ refresh_token: refreshToken }) }),
  profile: () => request("/me/profile"),
  confirmProfile: (profileVersion, chatSessionId) => request("/me/profile/confirm", {
    method: "POST",
    body: JSON.stringify({ profile_version: profileVersion, chat_session_id: chatSessionId }),
  }),
  createSession: () => request("/chat/sessions", { method: "POST", body: JSON.stringify({ purpose: "RECOMMENDATION" }) }),
  session: (id) => request(`/chat/sessions/${id}`),
  patchDraft: (id, payload) => request(`/chat/sessions/${id}/profile-draft`, { method: "PATCH", body: JSON.stringify(payload) }),
  removeDraftCourse: (id, courseId) => request(`/chat/sessions/${id}/profile-draft/completed-courses/${courseId}`, { method: "DELETE" }),
  decideResolution: (sessionId, resolutionId, payload) => request(
    `/chat/sessions/${sessionId}/course-resolutions/${resolutionId}`,
    { method: "POST", body: JSON.stringify(payload) },
  ),
  createRecommendation: (profileVersion, topN = 8) => request("/recommendations", {
    method: "POST",
    body: JSON.stringify({ profile_version: profileVersion, top_n: topN }),
  }),
  recommendationHistory: () => request("/recommendations?limit=30"),
  recommendation: (id) => request(`/recommendations/${id}`),
  deleteRecommendation: (id) => request(`/recommendations/${id}`, { method: "DELETE" }),
  favorites: () => request("/favorites?limit=50"),
  addFavorite: (courseId) => request(`/favorites/${courseId}`, { method: "PUT" }),
  removeFavorite: (courseId) => request(`/favorites/${courseId}`, { method: "DELETE" }),
  course: (courseId) => request(`/courses/${courseId}`),
};


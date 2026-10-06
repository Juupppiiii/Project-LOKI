/* The only place the front end talks to the server.
 *
 * Holds no business logic. It does not decide what is safe, approvable or
 * runnable -- it forwards calls and preserves whatever the server says, so a
 * tampered client gains nothing.
 *
 * Point this at a remote server by setting window.OVERLAY_API_BASE before the
 * bundle loads. That is the only change a desktop shell needs.
 */

const BASE = typeof window !== "undefined" ? window.OVERLAY_API_BASE || "" : "";

/** Who is acting. Set from the UI; a real deployment replaces this with SSO. */
let actor = "dev@loki.local";
export const setActor = (v) => {
  actor = v?.trim() || "unknown";
};
export const getActor = () => actor;

export class ApiError extends Error {
  constructor(message, status, info) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    // Structured detail, when the server sends it (e.g. a duplicate run names
    // the existing one). Lets callers offer a real choice instead of guessing.
    this.info = info || null;
  }
}

async function request(path, { method = "GET", body, isForm } = {}) {
  const headers = { "X-Actor": actor };
  if (!isForm) headers["Content-Type"] = "application/json";

  const res = await fetch(`${BASE}/api${path}`, {
    method,
    headers,
    // Send the session cookie so the server knows who is signed in.
    credentials: "include",
    body: isForm ? body : body ? JSON.stringify(body) : undefined,
  });

  let payload = null;
  try {
    payload = await res.json();
  } catch {
    /* empty or non-JSON body */
  }

  if (!res.ok) {
    const detail = payload?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || `Request failed (${res.status})`;
    throw new ApiError(
      message,
      res.status,
      detail && typeof detail === "object" ? detail : null
    );
  }
  return payload;
}

export const api = {
  /* -- auth -- */
  authStatus: () => request("/auth/status"),
  me: () => request("/auth/me"),
  devLogin: (email, role, name) =>
    request("/auth/dev-login", { method: "POST", body: { email, role, name } }),
  loginUrl: () => request("/auth/login"),
  logout: () => request("/auth/logout", { method: "POST" }),

  /* -- meta -- */
  health: () => request("/health"),
  summary: () => request("/summary"),
  procedures: () => request("/procedures"),
  riskPolicy: () => request("/risk-policy"),
  sampleTickets: (limit = 60) => request(`/sample-tickets?limit=${limit}`),

  /* -- runs -- */
  match: (ticket) => request("/match", { method: "POST", body: ticket }),
  listRuns: (limit = 500) => request(`/runs?limit=${limit}`),
  getRun: (id) => request(`/runs/${id}`),
  audit: (id) => request(`/runs/${id}/audit`),
  createRun: (ticket, allowDuplicate = false) =>
    request(`/runs${allowDuplicate ? "?allow_duplicate=true" : ""}`, {
      method: "POST",
      body: ticket,
    }),
  setParameters: (id, parameters) =>
    request(`/runs/${id}/parameters`, {
      method: "PATCH",
      body: { parameters },
    }),
  decideChunk: (runId, chunkId, decision, reason = "") =>
    request(`/runs/${runId}/chunks/${chunkId}/decision`, {
      method: "POST",
      body: { decision, reason },
    }),
  executeChunk: (runId, chunkId) =>
    request(`/runs/${runId}/chunks/${chunkId}/execute`, { method: "POST" }),
  completeStep: (runId, stepId, evidence) =>
    request(`/runs/${runId}/steps/${stepId}/complete`, {
      method: "POST",
      body: { evidence },
    }),
  skipStep: (runId, stepId, reason) =>
    request(`/runs/${runId}/steps/${stepId}/skip`, {
      method: "POST",
      body: { reason },
    }),
  cancelRun: (id, reason = "") =>
    request(`/runs/${id}/cancel`, { method: "POST", body: { reason } }),

  /* -- intelligence (advisory, authorises nothing) -- */
  suggest: (ticket, top = 3) =>
    request(`/suggest?top=${top}`, { method: "POST", body: ticket }),
  recommendSteps: (ticket) =>
    request("/recommend-steps", { method: "POST", body: ticket }),
  efficiencyAdvice: (runId) => request(`/runs/${runId}/efficiency-advice`),

  /* -- knowledge -- */
  formats: () => request("/knowledge/formats"),
  sources: () => request("/knowledge/sources"),
  drafts: (status) =>
    request(`/knowledge/drafts${status ? `?status=${status}` : ""}`),
  draft: (id) => request(`/knowledge/drafts/${id}`),
  publishDraft: (id, note = "") =>
    request(`/knowledge/drafts/${id}/publish`, {
      method: "POST",
      body: { note },
    }),
  rejectDraft: (id, note) =>
    request(`/knowledge/drafts/${id}/reject`, {
      method: "POST",
      body: { note },
    }),
  sourceContent: (id) => request(`/knowledge/sources/${id}/content`),
  deleteSource: (id) =>
    request(`/knowledge/sources/${id}`, { method: "DELETE" }),
  published: () => request("/knowledge/published"),
  togglePublished: (id, enabled) =>
    request(`/knowledge/published/${id}`, {
      method: "PATCH",
      body: { enabled },
    }),
  dictionary: () => request("/knowledge/dictionary"),
  toggleDictionary: (id, enabled) =>
    request(`/knowledge/dictionary/${id}`, {
      method: "PATCH",
      body: { enabled },
    }),
  testDictionary: (text, assignmentGroup = "") =>
    request("/knowledge/dictionary/test", {
      method: "POST",
      body: { text, assignment_group: assignmentGroup },
    }),
  upload: (file, kind) => {
    const form = new FormData();
    form.append("file", file);
    form.append("kind", kind);
    // FormData sets its own multipart boundary; do not add Content-Type.
    return request("/knowledge/upload", {
      method: "POST",
      body: form,
      isForm: true,
    });
  },

  /* -- access credentials --
     No response from these ever contains a secret value. A value may be sent
     once, on create, and is written to the OS credential store. */
  connectors: () => request("/credentials/connectors"),
  credentials: () => request("/credentials"),
  addCredential: (body) =>
    request("/credentials", { method: "POST", body }),
  testCredential: (id) =>
    request(`/credentials/${id}/test`, { method: "POST" }),
  toggleCredential: (id, enabled) =>
    request(`/credentials/${id}`, { method: "PATCH", body: { enabled } }),
  deleteCredential: (id) =>
    request(`/credentials/${id}`, { method: "DELETE" }),

  /* -- live feed (read-only intake) -- */
  liveStatus: () => request("/live/status"),
  liveConfig: () => request("/live/config"),
  liveQueryOptions: () => request("/live/query-options"),
  updateLiveConfig: (changes) =>
    request("/live/config", { method: "PATCH", body: changes }),
  liveProbe: () => request("/live/probe", { method: "POST" }),
  livePoll: () => request("/live/poll", { method: "POST" }),
  liveTickets: (limit = 500) => request(`/live/tickets?limit=${limit}`),
  proposeFromLive: (id, allowDuplicate = false) =>
    request(
      `/live/tickets/${id}/propose${allowDuplicate ? "?allow_duplicate=true" : ""}`,
      { method: "POST" }
    ),
  ignoreLiveTicket: (id, reason = "") =>
    request(`/live/tickets/${id}/ignore`, {
      method: "POST",
      body: { reason },
    }),
};

const BASE = import.meta.env.VITE_API_URL || "/api";
const AGENT_KEY = "agentSession";
const USER_KEY = "userSession";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

function readJSON(store, key) {
  try { return JSON.parse(store.getItem(key)); } catch { return null; }
}
function removeKey(store, key) {
  try { store.removeItem(key); } catch { /* storage blocked */ }
}

// ---- agent session (sessionStorage: cleared when the tab closes) ----------
let onAgentLogout = () => {};

export const session = {
  get: () => readJSON(sessionStorage, AGENT_KEY),
  set(value) {
    try { sessionStorage.setItem(AGENT_KEY, JSON.stringify(value)); } catch { /* storage blocked */ }
  },
  clear: () => removeKey(sessionStorage, AGENT_KEY),
  onLogout(fn) { onAgentLogout = fn; },
};

// ---- user account session ----------------------------------------------------
// "Keep me signed in" keeps it in localStorage (survives closing the browser);
// otherwise sessionStorage (gone when the tab closes).
let onUserLogout = () => {};

export const userSession = {
  get: () => readJSON(localStorage, USER_KEY) || readJSON(sessionStorage, USER_KEY),
  set(value, remember) {
    this.clear();
    try {
      (remember ? localStorage : sessionStorage).setItem(USER_KEY, JSON.stringify(value));
    } catch { /* storage blocked: signed in for this page only */ }
  },
  /** Update the stored user (e.g. a new token) wherever it's kept. */
  update(value) {
    for (const store of [localStorage, sessionStorage]) {
      if (readJSON(store, USER_KEY)) {
        try { store.setItem(USER_KEY, JSON.stringify(value)); } catch { /* blocked */ }
      }
    }
  },
  clear() {
    removeKey(localStorage, USER_KEY);
    removeKey(sessionStorage, USER_KEY);
  },
  onLogout(fn) { onUserLogout = fn; },
};

async function request(path, { method = "GET", body, headers = {}, raw = false,
  keepalive = false } = {}) {
  const isForm = body instanceof FormData;
  const res = await fetch(`${BASE}${path}`, {
    method,
    keepalive,   // lets a request finish while the page is closing
    // For FormData the browser sets the multipart Content-Type (with boundary) itself.
    headers: isForm || !body ? headers : { "Content-Type": "application/json", ...headers },
    body: isForm ? body : body ? JSON.stringify(body) : undefined,
  });
  if (raw && res.ok) return res;
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(data.error || `Request failed (${res.status})`, res.status);
  return data;
}

/** Request made as the logged-in agent; a 401 means the session expired. */
async function agentRequest(path, options = {}) {
  const token = session.get()?.token;
  try {
    return await request(path, {
      ...options, headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
  } catch (err) {
    if (err.status === 401) {
      session.clear();
      onAgentLogout();
    }
    throw err;
  }
}

/** Request made as the signed-in user; a 401 means sign in again. */
async function userRequest(path, options = {}) {
  const token = userSession.get()?.token;
  try {
    return await request(path, {
      ...options, headers: { ...(options.headers || {}), ...(token ? { "X-User-Token": token } : {}) },
    });
  } catch (err) {
    if (err.status === 401) {
      userSession.clear();
      onUserLogout();
    }
    throw err;
  }
}

const asCustomer = (token) => ({ "X-Conversation-Token": token });

export const api = {
  health: () => request("/health"),
  config: () => request("/config"),   // help desk contact, email set up, login required

  // user accounts
  register: (name, email, password) =>
    request("/users/register", { method: "POST", body: { name, email, password } }),
  userLogin: (email, password) =>
    request("/users/login", { method: "POST", body: { email, password } }),
  me: () => userRequest("/users/me"),
  forgotPassword: (email) =>
    request("/users/forgot-password", { method: "POST", body: { email } }),
  resetPassword: (email, code, password) =>
    request("/users/reset-password", { method: "POST", body: { email, code, password } }),
  myChats: () => userRequest("/users/me/conversations"),
  claimChats: (tokens) =>
    userRequest("/users/me/claim-chats", { method: "POST", body: { tokens } }),

  // chats (each chat also has its own access token)
  createConversation: (customer_name = "Guest", mode = "service", temporary = false) =>
    userRequest("/conversations", { method: "POST", body: { customer_name, mode, temporary } }),
  getConversation: (id, token) =>
    request(`/conversations/${id}`, { headers: asCustomer(token) }),
  deleteConversation: (id, token, { keepalive = false } = {}) =>
    request(`/conversations/${id}`, { method: "DELETE", headers: asCustomer(token), keepalive }),
  /** Rename ({title}), pin ({pinned}) or move into a project ({project_id}, null = none). */
  updateConversation: (id, token, changes) =>
    request(`/conversations/${id}`, { method: "PATCH", body: changes, headers: asCustomer(token) }),
  /** Create or update the public read-only link. */
  shareConversation: (id, token) =>
    request(`/conversations/${id}/share`, { method: "POST", headers: asCustomer(token) }),
  unshareConversation: (id, token) =>
    request(`/conversations/${id}/share`, { method: "DELETE", headers: asCustomer(token) }),
  getShared: (shareId) => request(`/conversations/shared/${encodeURIComponent(shareId)}`),
  sendMessage: (id, token, content, files = [], mode = "service", webSearch = false) => {
    let body = { content, mode, web_search: webSearch };
    if (files.length) {
      body = new FormData();
      body.append("content", content);
      body.append("mode", mode);
      body.append("web_search", webSearch ? "true" : "false");
      files.forEach((f) => body.append("files", f, f.name));
    }
    return request(`/conversations/${id}/messages`,
      { method: "POST", body, headers: asCustomer(token) });
  },
  /** Replace the latest assistant reply with a new answer. */
  regenerate: (id, token, messageId, mode, webSearch = false) =>
    request(`/conversations/${id}/messages/${messageId}/regenerate`,
      { method: "POST", body: { mode, web_search: webSearch }, headers: asCustomer(token) }),
  /** Change one of your messages; the replies after it are replaced by a new answer. */
  editMessage: (id, token, messageId, content, mode, webSearch = false) =>
    request(`/conversations/${id}/messages/${messageId}`,
      { method: "PATCH", body: { content, mode, web_search: webSearch }, headers: asCustomer(token) }),

  // projects (folders of chats; need an account)
  listProjects: () => userRequest("/projects"),
  createProject: (name) => userRequest("/projects", { method: "POST", body: { name } }),
  renameProject: (id, name) => userRequest(`/projects/${id}`, { method: "PATCH", body: { name } }),
  deleteProject: (id) => userRequest(`/projects/${id}`, { method: "DELETE" }),
  /** Fetch an uploaded file as a Blob (the token can't go on an <img src>). */
  attachmentBlob: async (attachmentId, token) => {
    const res = await request(`/attachments/${attachmentId}`,
      { headers: asCustomer(token), raw: true });
    return res.blob();
  },

  // agent (support staff)
  login: (username, password) =>
    request("/auth/login", { method: "POST", body: { username, password } }),
  listTickets: (status) => agentRequest(`/tickets${status ? `?status=${status}` : ""}`),
  updateTicket: (id, changes) =>
    agentRequest(`/tickets/${id}`, { method: "PATCH", body: changes }),
  analytics: () => agentRequest("/analytics"),
};

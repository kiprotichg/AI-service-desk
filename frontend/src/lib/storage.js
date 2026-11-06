// Per-browser storage helpers. Storage can be blocked (private mode, settings),
// so every access is wrapped and the app keeps working without it.

const CHATS_KEY = "recentChats";
const SIDEBAR_KEY = "sidebarOpen";
const MAX_CHATS = 50;

function read(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function write(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* storage blocked */ }
}

/**
 * Recent chats live in this browser only: chat users have no account, so each
 * chat's access token is what lets them reopen it.
 * Entry shape: { id, token, title, updated_at }
 */
export const recentChats = {
  load() {
    const list = read(CHATS_KEY, []);
    return Array.isArray(list) ? list.filter((c) => c && c.id && c.token) : [];
  },
  save(list) {
    write(CHATS_KEY, list.slice(0, MAX_CHATS));
  },
};

export function sortChats(list) {
  return [...list].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
}

export const sidebarPref = {
  load: () => read(SIDEBAR_KEY, true) !== false,
  save: (open) => write(SIDEBAR_KEY, open),
};

// ---- settings (Settings dialog) -----------------------------------------
const SETTINGS_KEY = "settings";
export const DEFAULT_SETTINGS = { name: "", theme: "system", defaultMode: "service",
                                  webSearch: false, micLang: "en-KE" };
const MIC_LANGS = ["en-KE", "en-US", "en-GB", "sw-KE"];
const THEMES = ["system", "light", "dark"];
const MODES = ["service", "sales", "general"];

export const settingsStore = {
  load() {
    const s = { ...DEFAULT_SETTINGS, ...read(SETTINGS_KEY, {}) };
    if (!THEMES.includes(s.theme)) s.theme = "system";
    if (!MODES.includes(s.defaultMode)) s.defaultMode = "service";
    s.name = typeof s.name === "string" ? s.name.slice(0, 120) : "";
    s.webSearch = s.webSearch === true;
    if (!MIC_LANGS.includes(s.micLang)) s.micLang = "en-KE";
    return s;
  },
  save: (s) => write(SETTINGS_KEY, s),
};

// ---- theme ---------------------------------------------------------------
const darkQuery = () => window.matchMedia("(prefers-color-scheme: dark)");

/** "light" | "dark" actually shown for a preference of system/light/dark. */
export function effectiveTheme(pref) {
  if (pref === "light" || pref === "dark") return pref;
  return darkQuery().matches ? "dark" : "light";
}

/** Put the preference on <html data-theme>; CSS picks the colours from it. */
export function applyTheme(pref) {
  const root = document.documentElement;
  if (pref === "light" || pref === "dark") root.dataset.theme = pref;
  else delete root.dataset.theme;
}

export function onSystemThemeChange(fn) {
  const q = darkQuery();
  q.addEventListener("change", fn);
  return () => q.removeEventListener("change", fn);
}

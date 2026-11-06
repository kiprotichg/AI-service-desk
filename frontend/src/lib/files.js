import { api } from "../api/client.js";

// Mirrors the backend limits so people get instant feedback (the server re-checks).
export const MAX_FILES = 3;
const MAX_BINARY = 5 * 1024 * 1024;
const MAX_TEXT = 200 * 1024;
const BINARY_EXT = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"];
const TEXT_EXT = [".txt", ".md", ".csv", ".json", ".log"];
export const ACCEPT = [...BINARY_EXT, ...TEXT_EXT].join(",");

const ext = (name) => (name.match(/\.[^.]+$/)?.[0] || "").toLowerCase();
export const isImageName = (name) => [".png", ".jpg", ".jpeg", ".gif", ".webp"].includes(ext(name));

/** Returns an error message, or null if the file looks acceptable. */
export function checkFile(file) {
  const e = ext(file.name);
  if (BINARY_EXT.includes(e)) {
    return file.size > MAX_BINARY ? `${file.name} is larger than 5 MB` : null;
  }
  if (TEXT_EXT.includes(e)) {
    return file.size > MAX_TEXT ? `${file.name} is larger than 200 KB` : null;
  }
  return `${file.name}: only images, PDFs and text files (${TEXT_EXT.join(", ")}) can be attached`;
}

export function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// Object URLs for uploaded files, keyed by attachment id. Files the user just
// sent are seeded from the local copy, so they don't get downloaded again.
// Keyed by the attachment's unique `key` (ids can be reused after a delete in older
// databases, which would otherwise show a deleted photo in place of a new one).
const urls = new Map();
const cacheKey = (att) => att.key || `id-${att.id}`;

export function rememberLocalUrl(att, url) {
  urls.set(cacheKey(att), Promise.resolve(url));
}

export function attachmentUrl(att, token) {
  const k = cacheKey(att);
  if (!urls.has(k)) {
    const p = api.attachmentBlob(att.id, token).then((b) => URL.createObjectURL(b));
    p.catch(() => urls.delete(k)); // allow a retry later
    urls.set(k, p);
  }
  return urls.get(k);
}

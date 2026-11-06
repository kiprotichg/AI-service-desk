import { useState } from "react";
import { api } from "../api/client.js";
import Dialog from "./Dialog.jsx";
import { CheckIcon, CopyIcon, LinkIcon } from "./Icons.jsx";

export const shareUrl = (shareId) => `${window.location.origin}/share/${shareId}`;

async function copy(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.cssText = "position:fixed;top:-1000px;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    return ok;
  }
}

/**
 * Share a chat as a public, read-only link. The link shows the chat as it is when
 * shared; "Update link" adds newer messages, "Stop sharing" turns the link off.
 */
export default function ShareDialog({ chat, onClose, onChanged }) {
  const [shareId, setShareId] = useState(chat.share_id || null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [updated, setUpdated] = useState(false);

  async function run(fn) {
    setBusy(true);
    setError("");
    try { await fn(); } catch (err) { setError(err.message); } finally { setBusy(false); }
  }

  const create = () => run(async () => {
    const res = await api.shareConversation(chat.id, chat.token);
    const wasShared = Boolean(shareId);
    setShareId(res.share_id);
    onChanged({ shared: true, share_id: res.share_id });
    if (wasShared) setUpdated(true);
    else if (await copy(shareUrl(res.share_id))) flashCopied();
  });

  const stop = () => run(async () => {
    await api.unshareConversation(chat.id, chat.token);
    setShareId(null);
    setUpdated(false);
    onChanged({ shared: false, share_id: null });
  });

  function flashCopied() {
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  }

  return (
    <Dialog title="Share chat" onClose={onClose} className="small">
      <p className="share-title">{chat.title || "New chat"}</p>
      {shareId ? (
        <>
          <p className="setting-hint">Anyone with this link can read the chat. Files you
            attached aren't included, and messages sent after you share are added only when
            you update the link.</p>
          <div className="share-link">
            <LinkIcon width={16} height={16} />
            <input readOnly value={shareUrl(shareId)} aria-label="Share link"
              onFocus={(e) => e.target.select()} />
            <button className="primary" onClick={async () => { if (await copy(shareUrl(shareId))) flashCopied(); }}
              data-autofocus>
              {copied ? <><CheckIcon width={16} height={16} /> Copied</> : <><CopyIcon width={16} height={16} /> Copy</>}
            </button>
          </div>
          {updated && <p className="share-ok" role="status">Link updated with the latest messages.</p>}
          <div className="dialog-actions spread">
            <button className="danger-btn" onClick={stop} disabled={busy}>Stop sharing</button>
            <button className="ghost" onClick={create} disabled={busy}>Update link</button>
          </div>
        </>
      ) : (
        <>
          <p className="setting-hint">Create a public, read-only link to this chat. Anyone
            with the link can read it, without signing in. Files you attached aren't
            included.</p>
          <div className="dialog-actions">
            <button className="ghost" onClick={onClose}>Cancel</button>
            <button className="primary" onClick={create} disabled={busy} data-autofocus>
              <LinkIcon width={16} height={16} /> {busy ? "Creating…" : "Create public link"}
            </button>
          </div>
        </>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </Dialog>
  );
}

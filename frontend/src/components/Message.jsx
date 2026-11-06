import { useEffect, useRef, useState } from "react";
import Attachment from "./Attachment.jsx";
import { CheckIcon, CopyIcon, EditIcon, RegenerateIcon, SparkIcon } from "./Icons.jsx";

/** Copy text; falls back to a hidden textarea when the Clipboard API is refused. */
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:-1000px;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { ok = false; }
    ta.remove();
    return ok;
  }
}

function CopyButton({ text, label }) {
  const [state, setState] = useState("idle"); // idle | copied | failed
  async function copy() {
    setState((await copyText(text)) ? "copied" : "failed");
    setTimeout(() => setState("idle"), 1500);
  }
  const copied = state === "copied";
  return (
    <button className="icon-btn small" onClick={copy}
      title={copied ? "Copied" : state === "failed" ? "Couldn't copy" : "Copy"}
      aria-label={copied ? "Copied" : state === "failed" ? "Couldn't copy" : label}
      disabled={!text}>
      {copied ? <CheckIcon width={16} height={16} /> : <CopyIcon width={16} height={16} />}
    </button>
  );
}

const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; } };

/** Web pages a web-search answer was based on. */
export function Sources({ sources }) {
  if (!sources?.length) return null;
  return (
    <div className="sources">
      <span className="sources-label">Sources</span>
      {sources.map((s) => (
        <a key={s.url} href={s.url} target="_blank" rel="noopener noreferrer nofollow"
          className="source" title={s.url}>
          {s.title && s.title !== s.url ? s.title : hostOf(s.url)}
        </a>
      ))}
    </div>
  );
}

/** In-place editor for one of your messages (Enter sends, Escape cancels). */
function EditBox({ initial, hasFiles, hasLater, onCancel, onSave }) {
  const [text, setText] = useState(initial);
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    el?.focus();
    el?.setSelectionRange(el.value.length, el.value.length);
  }, []);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [text]);

  const canSave = (text.trim() || hasFiles) && text.trim() !== initial.trim();
  return (
    <div className="edit-box">
      <textarea ref={ref} value={text} maxLength={2000} aria-label="Edit message"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") onCancel();
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (canSave) onSave(text.trim());
          }
        }} />
      <div className="edit-foot">
        {hasLater && <span className="edit-hint">The replies after this message will be replaced.</span>}
        <button className="ghost" onClick={onCancel}>Cancel</button>
        <button className="primary" disabled={!canSave} onClick={() => onSave(text.trim())}>Send</button>
      </div>
    </div>
  );
}

/**
 * One chat message with ChatGPT-style actions:
 *  - your messages: Copy, Edit
 *  - assistant replies: Copy, and Regenerate on the latest reply
 */
export default function Message({ message, token, busy, isLast, hasLater, onRegenerate, onEdit,
  onSuggest, closed }) {
  const { id, role, content, attachments = [], suggestions = [], sources = [] } = message;
  const [editing, setEditing] = useState(false);
  const saved = typeof id === "number"; // optimistic messages can't be edited yet

  if (role === "user") {
    return (
      <div className="msg msg-user">
        {attachments.length > 0 && (
          <div className="msg-attachments">
            {attachments.map((a) => <Attachment key={a.key || a.id} att={a} token={token} />)}
          </div>
        )}
        {editing ? (
          <EditBox initial={content} hasFiles={attachments.length > 0} hasLater={hasLater}
            onCancel={() => setEditing(false)}
            onSave={(text) => { setEditing(false); onEdit(id, text); }} />
        ) : (
          <>
            {content && <div className="user-bubble">{content}</div>}
            {saved && (
              <div className="msg-actions user-actions">
                <CopyButton text={content} label="Copy message" />
                <button className="icon-btn small" onClick={() => setEditing(true)} disabled={busy}
                  title="Edit message" aria-label="Edit message">
                  <EditIcon width={16} height={16} />
                </button>
              </div>
            )}
          </>
        )}
      </div>
    );
  }

  return (
    <div className="msg msg-assistant">
      <div className="avatar" aria-hidden="true"><SparkIcon width={16} height={16} /></div>
      <div className="assistant-body">
        <div className="assistant-text">{content}</div>
        <Sources sources={sources} />
        {message.notice && <p className="reply-notice">{message.notice}</p>}
        <div className="msg-actions">
          <CopyButton text={content} label="Copy reply" />
          {isLast && saved && (
            <button className="icon-btn small" onClick={() => onRegenerate(id)} disabled={busy}
              title="Regenerate response" aria-label="Regenerate response">
              <RegenerateIcon width={16} height={16} />
            </button>
          )}
        </div>
        {/* Follow-up questions, only under the latest reply (older ones go stale). */}
        {isLast && !closed && suggestions.length > 0 && (
          <div className="followups" role="group" aria-label="Suggested follow-up questions">
            {suggestions.map((s) => (
              <button key={s} className="followup" onClick={() => onSuggest(s)} disabled={busy}>
                <span aria-hidden="true" className="followup-arrow">↳</span>{s}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

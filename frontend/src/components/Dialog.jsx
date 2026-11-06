import { useEffect, useRef, useState } from "react";
import { CloseIcon } from "./Icons.jsx";

/**
 * A centred modal: focus goes inside, Escape and the backdrop close it, Tab stays
 * inside, and focus returns where it was afterwards.
 */
export default function Dialog({ title, onClose, children, className = "" }) {
  const ref = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const titleId = useRef(`dlg-${Math.random().toString(36).slice(2)}`).current;

  useEffect(() => {
    const prev = document.activeElement;
    (ref.current?.querySelector("input, [data-autofocus]") || ref.current?.querySelector("button"))?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); closeRef.current(); }
      if (e.key === "Tab" && ref.current) {
        const f = [...ref.current.querySelectorAll("button, input, a[href]")].filter((el) => !el.disabled);
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f.at(-1).focus(); }
        else if (!e.shiftKey && document.activeElement === f.at(-1)) { e.preventDefault(); f[0].focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, []);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${className}`} role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref}>
        <div className="modal-head">
          <h2 id={titleId}>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><CloseIcon /></button>
        </div>
        {children}
      </div>
    </div>
  );
}

/**
 * Ask for one line of text (rename a chat, name a project).
 * onSubmit(value) may return a promise; a thrown error's message is shown.
 */
export function PromptDialog({ title, label, initial = "", confirmText = "Save", maxLength = 80,
  onSubmit, onClose }) {
  const [value, setValue] = useState(initial);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const clean = value.trim();

  async function submit(e) {
    e.preventDefault();
    if (!clean || busy) return;
    setBusy(true);
    setError("");
    try {
      await onSubmit(clean);
      onClose();
    } catch (err) {
      setError(err.message || "Something went wrong");
      setBusy(false);
    }
  }

  return (
    <Dialog title={title} onClose={onClose} className="small">
      <form onSubmit={submit} className="prompt-form">
        <label className="setting-label" htmlFor="prompt-input">{label}</label>
        <input id="prompt-input" value={value} maxLength={maxLength}
          onChange={(e) => setValue(e.target.value)}
          onFocus={(e) => e.target.select()} autoComplete="off" />
        {error && <p className="error" role="alert">{error}</p>}
        <div className="dialog-actions">
          <button type="button" className="ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="primary" disabled={!clean || busy}>
            {busy ? "Saving…" : confirmText}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

import { useEffect, useRef, useState } from "react";
import { CloseIcon } from "./Icons.jsx";

export const OPEN_HELPDESK = "open-helpdesk";
export const openHelpdesk = () => window.dispatchEvent(new Event(OPEN_HELPDESK));

/** Speech bubble with a phone handset: the usual "chat on WhatsApp" glyph. */
const WhatsAppGlyph = ({ size = 28 }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
    <path fill="currentColor" d="M16 3.2C8.9 3.2 3.2 8.9 3.2 16c0 2.3.6 4.5 1.7 6.4L3.2 28.8l6.6-1.7a12.8 12.8 0 0 0 6.2 1.6c7.1 0 12.8-5.7 12.8-12.8S23.1 3.2 16 3.2Zm0 23.3c-2 0-3.9-.5-5.6-1.5l-.4-.2-3.9 1 1-3.8-.3-.4A10.5 10.5 0 1 1 16 26.5Z" />
    <path fill="currentColor" d="M21.8 18.6c-.3-.2-1.9-.9-2.2-1-.3-.1-.5-.2-.7.2l-1 1.2c-.2.2-.4.2-.7.1a8.6 8.6 0 0 1-4.3-3.7c-.3-.6.3-.5.9-1.7.1-.2 0-.4 0-.6l-1-2.4c-.3-.6-.5-.5-.7-.5h-.6c-.2 0-.6.1-.9.4-.3.3-1.2 1.1-1.2 2.8s1.2 3.2 1.4 3.4c.2.2 2.4 3.6 5.7 5.1 2.1.9 2.9 1 4 .8.6-.1 1.9-.8 2.2-1.6.3-.8.3-1.4.2-1.6-.1-.2-.3-.2-.6-.4Z" />
  </svg>
);

/**
 * Floating WhatsApp button (bottom right) to message the person in charge of the
 * ICT help desk. Uses WhatsApp's click-to-chat link (wa.me): it opens WhatsApp with
 * the message filled in, and nothing is sent until the user presses send there.
 */
export default function HelpDeskButton({ helpdesk, userName, onAuthPage = false }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const wrapRef = useRef(null);
  const name = helpdesk?.name || "ICT Help Desk";
  const number = helpdesk?.whatsapp || null;

  // Other parts of the app (the + menu's "Add connector") can open the panel.
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener(OPEN_HELPDESK, show);
    return () => window.removeEventListener(OPEN_HELPDESK, show);
  }, []);

  // Fresh, editable starter message each time the panel opens.
  useEffect(() => {
    if (!open) return;
    const who = (userName || "").trim();
    setText(`Hello ${name}, ${who ? `this is ${who}. ` : ""}I need help with: `);
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e) => { if (!wrapRef.current?.contains(e.target)) setOpen(false); };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open, name, userName]);

  const link = number ? `https://wa.me/${number}?text=${encodeURIComponent(text.trim())}` : null;

  return (
    <div className={`helpdesk ${onAuthPage ? "on-auth" : ""}`} ref={wrapRef}>
      {open && (
        <div className="helpdesk-panel" role="dialog" aria-labelledby="helpdesk-title">
          <div className="helpdesk-head">
            <span className="helpdesk-badge"><WhatsAppGlyph size={22} /></span>
            <div>
              <div id="helpdesk-title" className="helpdesk-title">{name}</div>
              <div className="helpdesk-sub">
                {helpdesk?.hours ? `Available ${helpdesk.hours}` : "Chat with the person in charge on WhatsApp"}
              </div>
            </div>
            <button className="icon-btn small" onClick={() => setOpen(false)} aria-label="Close help desk">
              <CloseIcon width={16} height={16} />
            </button>
          </div>

          {number ? (
            <>
              <label htmlFor="helpdesk-msg" className="sr-only">Message to the help desk</label>
              <textarea id="helpdesk-msg" rows={4} value={text} maxLength={1000}
                onChange={(e) => setText(e.target.value)} autoFocus />
              <a className="helpdesk-send" href={link} target="_blank" rel="noopener noreferrer"
                onClick={() => setOpen(false)}>
                <WhatsAppGlyph size={18} /> Open WhatsApp
              </a>
              <p className="helpdesk-note">
                Opens WhatsApp with this message. Nothing is sent until you press send there.
              </p>
            </>
          ) : (
            <p className="helpdesk-note">
              The help desk's WhatsApp number hasn't been set up yet. An administrator can set
              <code> HELPDESK_WHATSAPP_NUMBER</code> in <code>backend/.env</code> and restart the backend.
            </p>
          )}
        </div>
      )}

      <button className="helpdesk-fab" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        aria-label={`Message the ${name} on WhatsApp`} title={`${name} on WhatsApp`}>
        <WhatsAppGlyph />
        <span className="helpdesk-fab-label">Help desk</span>
      </button>
    </div>
  );
}

import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import { applyTheme, effectiveTheme, settingsStore } from "../lib/storage.js";
import Logo from "./Logo.jsx";
import ThemeToggle from "./ThemeToggle.jsx";
import { FileIcon, SparkIcon } from "./Icons.jsx";
import { Sources } from "./Message.jsx";
import { MODE_INFO } from "./ModeSwitch.jsx";

/** The public, read-only page a share link opens (/share/<id>). No sign-in needed. */
export default function SharedView({ shareId }) {
  const [chat, setChat] = useState(null);
  const [error, setError] = useState("");
  const [theme, setTheme] = useState(() => settingsStore.load().theme);

  useEffect(() => {
    api.getShared(shareId)
      .then((c) => { setChat(c); document.title = `${c.title} · AI in Ideal`; })
      .catch((err) => setError(err.status === 404
        ? "This shared chat doesn't exist or is no longer shared."
        : `Couldn't load the shared chat: ${err.message}`));
  }, [shareId]);

  function toggleTheme() {
    const next = effectiveTheme(theme) === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
    settingsStore.save({ ...settingsStore.load(), theme: next });
  }

  const mode = chat ? MODE_INFO[chat.mode] : null;
  return (
    <div className="shared-page">
      <header className="shared-header">
        <a href="/" aria-label="AI in Ideal home"><Logo className="logo-header" /></a>
        <span className="shared-badge">Shared chat</span>
        <span className="spacer" />
        <ThemeToggle theme={effectiveTheme(theme)} onToggle={toggleTheme} />
        <a className="primary-link" href="/">Start your own chat</a>
      </header>
      <main className="shared-main">
        {error && <p className="error center" role="alert">{error}</p>}
        {!chat && !error && <p className="muted center">Loading…</p>}
        {chat && (
          <>
            <h1 className="shared-title">{chat.title}</h1>
            {mode && <p className="muted shared-mode"><mode.Icon width={15} height={15} /> {mode.label} mode · read-only copy</p>}
            <div className="thread-inner shared-thread">
              {chat.messages.map((m, i) => (m.role === "user" ? (
                <div key={i} className="msg msg-user">
                  {m.attachments.length > 0 && (
                    <div className="msg-attachments">
                      {m.attachments.map((a, j) => (
                        <span key={j} className="shared-file" title="Files aren't included in shared chats">
                          <FileIcon width={15} height={15} /> {a.filename}
                        </span>
                      ))}
                    </div>
                  )}
                  {m.content && <div className="user-bubble">{m.content}</div>}
                </div>
              ) : (
                <div key={i} className="msg msg-assistant">
                  <div className="avatar" aria-hidden="true"><SparkIcon width={16} height={16} /></div>
                  <div className="assistant-body">
                    <div className="assistant-text">{m.content}</div>
                    <Sources sources={m.sources} />
                  </div>
                </div>
              )))}
            </div>
            <p className="fine-print">This is a copy of a chat from AI in Ideal. AI can make mistakes.</p>
          </>
        )}
      </main>
    </div>
  );
}

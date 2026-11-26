import { useEffect, useRef } from "react";
import { CloseIcon, MoonIcon, SunIcon } from "./Icons.jsx";
import { MODE_INFO } from "./ModeSwitch.jsx";
import { speak, ttsSupported, useVoices } from "../lib/speech.js";

const THEMES = [
  { key: "system", label: "System" },
  { key: "light", label: "Light", Icon: SunIcon },
  { key: "dark", label: "Dark", Icon: MoonIcon },
];

const RATES = [{ value: 0.75, label: "Slow" }, { value: 1, label: "Normal" },
               { value: 1.25, label: "Fast" }, { value: 1.5, label: "Faster" }];

/** Read answers aloud: on/off, voice and speed. */
function ReadAloudSettings({ settings, set }) {
  const voices = useVoices();
  if (!ttsSupported) {
    return (
      <section className="setting">
        <span className="setting-label">Read answers aloud</span>
        <p className="setting-hint">This browser can't read text aloud. Use Chrome or Edge.</p>
      </section>
    );
  }
  // English and Kiswahili voices first, then the rest.
  const useful = (v) => /^(en|sw)/i.test(v.lang);
  const sorted = [...voices.filter(useful), ...voices.filter((v) => !useful(v))];
  return (
    <section className="setting">
      <span className="setting-label" id="read-label">Read answers aloud</span>
      <p className="setting-hint">Sound plays through your computer's speaker. To use an external
        speaker, connect it and choose it as the output in Windows (the speaker icon on the
        taskbar). Every answer also has a speaker button to read it on demand.</p>
      <label className="check-row">
        <input type="checkbox" checked={settings.autoRead}
          onChange={(e) => set({ autoRead: e.target.checked })} />
        Read every new answer aloud automatically
      </label>
      <div className="read-row">
        <label htmlFor="settings-voice">Voice</label>
        <select id="settings-voice" value={settings.voiceName}
          onChange={(e) => set({ voiceName: e.target.value })}>
          <option value="">Automatic (matches your voice-typing language)</option>
          {sorted.map((v) => (
            <option key={v.voiceURI || v.name} value={v.name}>{v.name} ({v.lang})</option>
          ))}
        </select>
      </div>
      <div className="segmented" role="radiogroup" aria-label="Reading speed">
        {RATES.map(({ value, label }) => (
          <button key={value} type="button" role="radio" aria-checked={settings.readRate === value}
            className={settings.readRate === value ? "active" : ""}
            onClick={() => set({ readRate: value })}>{label}</button>
        ))}
      </div>
      <button type="button" className="ghost test-voice"
        onClick={() => speak("Hello! This is how AI in Ideal will read your answers.",
          settings.micLang || "en-KE", { voiceName: settings.voiceName, rate: settings.readRate })}>
        Test voice
      </button>
    </section>
  );
}

/** Settings are saved as soon as they change; "Done" just closes the dialog. */
export default function SettingsDialog({ settings, onChange, onClose, chatCount, onDeleteAll,
  account }) {
  const dialogRef = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  // Runs once per opening: focus the first field, Escape closes, Tab stays inside,
  // and focus returns to where it was (the profile menu) on close.
  useEffect(() => {
    const prev = document.activeElement;
    dialogRef.current?.querySelector("input")?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") closeRef.current();
      // Keep Tab inside the dialog.
      if (e.key === "Tab" && dialogRef.current) {
        const f = [...dialogRef.current.querySelectorAll("button, input")].filter((el) => !el.disabled);
        if (!f.length) return;
        if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f.at(-1).focus(); }
        else if (!e.shiftKey && document.activeElement === f.at(-1)) { e.preventDefault(); f[0].focus(); }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, []);

  const set = (patch) => onChange({ ...settings, ...patch });

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="settings-title"
        ref={dialogRef}>
        <div className="modal-head">
          <h2 id="settings-title">Settings</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close settings">
            <CloseIcon />
          </button>
        </div>

        {account ? (
          <section className="setting">
            <span className="setting-label">Account</span>
            <p className="account-line"><strong>{account.name}</strong> · {account.email}</p>
            <p className="setting-hint">Your chats are saved to this account, on any device.</p>
          </section>
        ) : (
          <section className="setting">
            <label htmlFor="settings-name" className="setting-label">Your name</label>
            <p className="setting-hint">Shown on your profile and on new chats you start.</p>
            <input id="settings-name" value={settings.name} maxLength={120} placeholder="Guest"
              onChange={(e) => set({ name: e.target.value })} />
          </section>
        )}

        <section className="setting">
          <span className="setting-label" id="theme-label">Appearance</span>
          <div className="segmented" role="radiogroup" aria-labelledby="theme-label">
            {THEMES.map(({ key, label, Icon }) => (
              <button key={key} type="button" role="radio" aria-checked={settings.theme === key}
                className={settings.theme === key ? "active" : ""}
                onClick={() => set({ theme: key })}>
                {Icon && <Icon width={16} height={16} />} {label}
              </button>
            ))}
          </div>
        </section>

        <section className="setting">
          <span className="setting-label" id="mode-label">Default mode for new chats</span>
          <p className="setting-hint">You can still switch at the top of any chat.</p>
          <div className="segmented" role="radiogroup" aria-labelledby="mode-label">
            {Object.entries(MODE_INFO).map(([key, { label, Icon }]) => (
              <button key={key} type="button" role="radio" aria-checked={settings.defaultMode === key}
                className={settings.defaultMode === key ? "active" : ""}
                onClick={() => set({ defaultMode: key })}>
                <Icon width={16} height={16} /> {label}
              </button>
            ))}
          </div>
        </section>

        <ReadAloudSettings settings={settings} set={set} />

        <section className="setting">
          <span className="setting-label">Chat history</span>
          <p className="setting-hint">
            {chatCount ? `${chatCount} chat${chatCount === 1 ? "" : "s"} saved ${account ? "in your account" : "in this browser"}.`
              : "No saved chats."}
          </p>
          <button className="danger-btn" onClick={onDeleteAll} disabled={!chatCount}>
            Delete all chats
          </button>
        </section>

        <div className="modal-foot">
          <button className="primary" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { ACCEPT, formatSize } from "../lib/files.js";
import { MIC_LANGS, speechSupported, useDictation } from "../lib/speech.js";
import {
  ArrowUpIcon, CameraIcon, ChevronDownIcon, CloseIcon, ConnectorIcon, FileIcon, FolderIcon,
  GlobeIcon, MicIcon, PaperclipIcon, PlusIcon, SkillsIcon, WaveIcon,
} from "./Icons.jsx";
import Menu, { placementFor } from "./Menu.jsx";
import { MODE_INFO } from "./ModeSwitch.jsx";

const canScreenshot = typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getDisplayMedia);
const MAX_SHOT_BYTES = 4.5 * 1024 * 1024;   // uploads are limited to 5 MB

/** Capture one frame of a screen, window or tab the user picks. Returns a File or null. */
async function captureScreen() {
  let stream;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
    const video = document.createElement("video");
    video.muted = true;
    video.srcObject = stream;
    await video.play();
    await new Promise((r) => setTimeout(r, 250));   // let the first frame arrive
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    const toBlob = (type, q) => new Promise((r) => canvas.toBlob(r, type, q));
    let blob = await toBlob("image/png");
    if (blob && blob.size <= MAX_SHOT_BYTES) return new File([blob], `screenshot-${stamp}.png`, { type: "image/png" });
    for (const q of [0.85, 0.7, 0.5]) {      // big screens: fall back to JPEG
      blob = await toBlob("image/jpeg", q);
      if (blob && blob.size <= MAX_SHOT_BYTES) return new File([blob], `screenshot-${stamp}.jpg`, { type: "image/jpeg" });
    }
    throw new Error("The screenshot is too large");
  } finally {
    stream?.getTracks().forEach((t) => t.stop());
  }
}

/**
 * The message box: auto-growing text, the + menu (files, screenshot, project, answer
 * mode, help desk, web search), voice typing, voice mode and the send button.
 * Controlled by ChatView so a failed send can put the draft back.
 * files: [{ key, file, url (object URL for photos, else null) }]
 */
export default function Composer({ text, setText, files, onAddFiles, onRemoveFile, onSubmit,
  disabled, busy, autoFocus, placeholder = "Ask anything", onError,
  webSearch, onToggleWebSearch, chatMode, onChangeMode, projects, projectId, onPickProject,
  onNewProject, onOpenHelpdesk, onVoice, micLang, onMicLang, temporary }) {
  const inputRef = useRef(null);
  const fileRef = useRef(null);
  const plusRef = useRef(null);
  const langRef = useRef(null);
  const [menu, setMenu] = useState(null);         // null | "plus" | "lang"
  const [placement, setPlacement] = useState("");
  const dictation = useDictation({ lang: micLang, onText: setText });

  // Grow with the text up to 200px; re-measure when the width changes too
  // (sidebar toggled, window resized), since wrapping depends on it.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    const fit = () => {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
    };
    fit();
    if (typeof ResizeObserver === "undefined") return;
    let lastWidth = el.clientWidth;
    const ro = new ResizeObserver(() => {
      if (el.clientWidth !== lastWidth) { lastWidth = el.clientWidth; fit(); }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [text]);

  useEffect(() => { if (autoFocus) inputRef.current?.focus(); }, [autoFocus]);

  // Ctrl+U (Cmd+U on a Mac) opens the file picker, as shown in the + menu.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "u" && !disabled) {
        e.preventDefault();
        fileRef.current?.click();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [disabled]);

  useEffect(() => { if (dictation.error) onError?.(dictation.error); }, [dictation.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const canSend = !disabled && !busy && (text.trim() || files.length > 0);

  function submit(e) {
    e?.preventDefault();
    if (!canSend) return;
    if (dictation.listening) dictation.stop();
    onSubmit();
  }

  function onKeyDown(e) {
    // Enter sends, Shift+Enter adds a new line (and don't send mid-IME composition).
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(e);
  }

  function onPaste(e) {
    const pasted = [...(e.clipboardData?.files || [])];
    if (pasted.length) {
      e.preventDefault();
      onAddFiles(pasted);
    }
  }

  function openMenu(name, anchor) {
    setPlacement(placementFor(anchor.current, name === "plus" ? 360 : 200));
    setMenu((m) => (m === name ? null : name));
  }

  async function screenshot() {
    try {
      const file = await captureScreen();
      if (file) onAddFiles([file]);
    } catch (err) {
      if (err?.name !== "NotAllowedError" && err?.name !== "AbortError") {
        onError?.(`Couldn't take a screenshot: ${err.message || err}`);
      }
    }
    inputRef.current?.focus();
  }

  function toggleDictation() {
    if (dictation.listening) dictation.stop();
    else { dictation.clearError(); dictation.start(text); inputRef.current?.focus(); }
  }

  const projectItems = projects ? [
    ...projects.map((p) => ({
      label: p.name, icon: <FolderIcon width={16} height={16} />,
      checked: p.id === projectId, onSelect: () => onPickProject(p.id === projectId ? null : p.id),
    })),
    projects.length ? { divider: true } : null,
    { label: "New project…", icon: <PlusIcon width={16} height={16} />, onSelect: onNewProject },
    projectId ? { label: "Remove from project", onSelect: () => onPickProject(null) } : null,
  ] : null;

  const plusItems = [
    { label: "Upload or add files", icon: <PaperclipIcon width={18} height={18} />, shortcut: "Ctrl+U",
      onSelect: () => fileRef.current?.click() },
    canScreenshot ? { label: "Take a screenshot", icon: <CameraIcon width={18} height={18} />,
      onSelect: screenshot } : null,
    projectItems && !temporary ? { label: "Add to project", icon: <FolderIcon width={18} height={18} />,
      submenu: projectItems } : null,
    { divider: true },
    { label: "Skills", icon: <SkillsIcon width={18} height={18} />,
      submenu: Object.entries(MODE_INFO).map(([key, info]) => ({
        label: `${info.label} ${key === "general" ? "questions" : "module"}`, hint: info.hint,
        icon: <info.Icon width={16} height={16} />, checked: chatMode === key,
        onSelect: () => onChangeMode(key),
      })) },
    { label: "Add connector", icon: <ConnectorIcon width={18} height={18} />,
      submenu: [{ label: "WhatsApp ICT help desk", hint: "Message the help desk team",
                  onSelect: onOpenHelpdesk }] },
    { divider: true },
    { label: "Web search", icon: <GlobeIcon width={18} height={18} />, checked: webSearch,
      keepOpen: true, onSelect: onToggleWebSearch },
  ];

  const langItems = MIC_LANGS.map((l) => ({
    label: l.label, checked: l.code === micLang, onSelect: () => onMicLang(l.code),
  }));

  const showVoice = !text.trim() && files.length === 0 && !dictation.listening;

  return (
    <form className={`composer ${disabled ? "is-disabled" : ""}`} onSubmit={submit}>
      {files.length > 0 && (
        <div className="draft-files">
          {files.map((f) => (
            <div key={f.key} className={f.url ? "draft-image" : "draft-file"}>
              {f.url ? <img src={f.url} alt={f.file.name} /> : (
                <>
                  <span className="att-icon"><FileIcon width={18} height={18} /></span>
                  <span className="att-text">
                    <span className="att-name">{f.file.name}</span>
                    <span className="att-meta">{formatSize(f.file.size)}</span>
                  </span>
                </>
              )}
              <button type="button" className="draft-remove" onClick={() => onRemoveFile(f.key)}
                aria-label={`Remove ${f.file.name}`} title="Remove">
                <CloseIcon width={14} height={14} />
              </button>
            </div>
          ))}
        </div>
      )}

      <textarea
        ref={inputRef} rows={1} value={text} disabled={disabled}
        onChange={(e) => setText(e.target.value)} onKeyDown={onKeyDown} onPaste={onPaste}
        placeholder={disabled ? "This chat is closed" : dictation.listening ? "Listening…" : placeholder}
        maxLength={2000} aria-label="Message"
      />

      <div className="composer-row">
        <div className="composer-left">
          <div className="menu-anchor">
            <button type="button" ref={plusRef} className={`icon-btn plus-btn ${menu === "plus" ? "open" : ""}`}
              onClick={() => openMenu("plus", plusRef)} disabled={disabled}
              aria-haspopup="menu" aria-expanded={menu === "plus"}
              title="Add files, screenshots and more" aria-label="Add files, screenshots and more">
              <PlusIcon />
            </button>
            {menu === "plus" && (
              <Menu items={plusItems} label="Add" anchorRef={plusRef}
                className={`menu-left wide ${placement}`} onClose={() => setMenu(null)} />
            )}
          </div>
          {webSearch && (
            <button type="button" className="tool-chip" onClick={onToggleWebSearch}
              title="Web search is on: click to turn it off" aria-label="Turn off web search">
              <GlobeIcon width={15} height={15} /> Web search
              <CloseIcon width={13} height={13} className="tool-chip-x" />
            </button>
          )}
        </div>
        <input ref={fileRef} type="file" multiple accept={ACCEPT} hidden
          onChange={(e) => { onAddFiles([...e.target.files]); e.target.value = ""; }} />

        <div className="composer-right">
          <div className="mic-group menu-anchor">
            <button type="button" className={`icon-btn mic-btn ${dictation.listening ? "listening" : ""}`}
              onClick={toggleDictation} disabled={disabled || !speechSupported}
              aria-pressed={dictation.listening}
              title={!speechSupported ? "Voice typing needs Chrome or Edge"
                : dictation.listening ? "Stop voice typing" : "Voice typing"}
              aria-label={dictation.listening ? "Stop voice typing" : "Voice typing"}>
              <MicIcon width={19} height={19} />
            </button>
            <button type="button" ref={langRef} className="icon-btn lang-btn"
              onClick={() => openMenu("lang", langRef)} disabled={disabled || !speechSupported}
              aria-haspopup="menu" aria-expanded={menu === "lang"}
              title="Voice language" aria-label="Voice language">
              <ChevronDownIcon width={14} height={14} />
            </button>
            {menu === "lang" && (
              <Menu items={langItems} label="Voice language" anchorRef={langRef}
                className={placement} onClose={() => setMenu(null)} />
            )}
          </div>
          {showVoice ? (
            <button type="button" className="send-btn voice-start" onClick={onVoice}
              disabled={disabled || busy} title={speechSupported ? "Voice mode" : "Voice mode needs Chrome or Edge"}
              aria-label="Start voice mode">
              <WaveIcon width={18} height={18} />
            </button>
          ) : (
            <button type="submit" className="send-btn" disabled={!canSend}
              aria-label="Send message" title="Send">
              <ArrowUpIcon width={18} height={18} />
            </button>
          )}
        </div>
      </div>
    </form>
  );
}

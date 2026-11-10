import { useEffect, useRef, useState } from "react";
import { api } from "../api/client.js";
import { MAX_FILES, checkFile, isImageName, rememberLocalUrl } from "../lib/files.js";
import Composer from "./Composer.jsx";
import Message from "./Message.jsx";
import Welcome from "./Welcome.jsx";
import VoiceMode from "./VoiceMode.jsx";
import { TemporaryIcon } from "./Icons.jsx";

// What the empty screen shows in each answer mode.
const EMPTY = {
  service: {
    heading: "How can I help with the Service module?",
    sub: "Service mode: service requests, technicians, job cards, parts, warranties and contracts.",
    suggestions: ["How do I log a new service request?",
                  "How do I assign a service request to a technician?",
                  "Is this equipment under warranty?", "Which service reports are available?"],
    placeholder: "Ask about service requests, technicians, contracts or warranties",
  },
  sales: {
    heading: "How can I help with the Sales module?",
    sub: "Sales mode: quotations, sales orders, invoices, credit notes, pricing and reports.",
    suggestions: ["How do I create a sales quotation?",
                  "How do I convert a quotation into a sales order?",
                  "How do I issue a credit note?", "Which sales reports are available?"],
    placeholder: "Ask about quotations, sales orders, invoices or pricing",
  },
  general: {
    heading: "What can I help with?",
    sub: "General mode: ask about anything.",
    suggestions: ["Why is the sky blue?", "Give me 3 tips for a job interview",
                  "Explain photosynthesis simply", "Write a short poem about rain"],
    placeholder: "Ask anything",
  },
};

let draftKey = 0;

/**
 * One conversation. chat = {id, token, title, mode} to reopen a saved chat, or null
 * for a new one (created on the first message). chatMode = the answer mode picked
 * at the top ("service" | "sales" | "general"); it's sent with every message.
 */
export default function ChatView({ chat, aiMode, chatMode, customerName, greetName,
  onModeLoaded, onChatChanged, onChatMissing, temporary = false, webSearch = false,
  onToggleWebSearch, onChangeMode, projects, projectId, onPatchChat, onNewProject,
  micLang = "en-KE", onMicLang, onOpenHelpdesk }) {
  const [conv, setConv] = useState(chat ? { ...chat, status: "open" } : null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(Boolean(chat));
  const [sending, setSending] = useState(false);
  const [text, setText] = useState("");
  const [files, setFiles] = useState([]);
  const [error, setError] = useState("");
  const [ticket, setTicket] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [voice, setVoice] = useState(false);
  // "Add to project" picked before the chat exists: applied once it's created.
  const [pendingProject, setPendingProject] = useState(null);
  const endRef = useRef(null);
  const mounted = useRef(true);

  // Set on every mount: React's StrictMode mounts, unmounts and remounts in dev.
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // Load a saved chat once, when the view opens.
  useEffect(() => {
    if (!chat) return;
    api.getConversation(chat.id, chat.token)
      .then((c) => {
        if (!mounted.current) return;
        setConv({ ...c, token: chat.token });
        onModeLoaded?.(c.mode);
        setMessages(c.messages);
      })
      .catch((err) => {
        if (!mounted.current) return;
        if (err.status === 401 || err.status === 404) {
          onChatMissing(chat.id);
          setConv(null);
          setError("That chat is no longer available (it was deleted or its link expired).");
        } else {
          setError(err.message);
        }
      })
      .finally(() => mounted.current && setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const streamingNow = messages.some((m) => m.streaming);
  useEffect(() => {
    // Follow the answer as it's written (instant), smooth otherwise.
    endRef.current?.scrollIntoView({ behavior: streamingNow ? "auto" : "smooth", block: "end" });
  }, [messages, sending, streamingNow]);

  function addFiles(list) {
    setError("");
    const room = MAX_FILES - files.length;
    if (list.length > room) {
      setError(`You can attach up to ${MAX_FILES} files per message.`);
      list = list.slice(0, Math.max(room, 0));
    }
    const ok = [];
    for (const file of list) {
      const problem = checkFile(file);
      if (problem) { setError(problem); continue; }
      ok.push({ key: ++draftKey, file, url: isImageName(file.name) ? URL.createObjectURL(file) : null });
    }
    if (ok.length) setFiles((f) => [...f, ...ok]);
  }

  function removeFile(key) {
    setFiles((list) => {
      const gone = list.find((f) => f.key === key);
      if (gone?.url) URL.revokeObjectURL(gone.url);
      return list.filter((f) => f.key !== key);
    });
  }

  /** Send a message; resolves with the reply text (null if it failed). */
  async function send(override) {
    const content = (override ?? text).trim();
    const draft = override === undefined ? files : [];
    if ((!content && draft.length === 0) || sending) return null;

    setError("");
    setSending(true);
    if (override === undefined) { setText(""); setFiles([]); }
    const tempId = `pending-${Date.now()}`;
    setMessages((m) => [...m, {
      id: tempId, role: "user", content,
      attachments: draft.map((f) => ({
        id: `local-${f.key}`, filename: f.file.name, size: f.file.size,
        kind: f.url ? "image" : "file", localUrl: f.url || URL.createObjectURL(f.file),
      })),
    }]);

    // The answer appears word by word in this placeholder while it's being written.
    const answerId = `answer-${tempId}`;
    let asked = null;   // the saved question, once the server confirms it
    try {
      let current = conv;
      if (!current) {
        const created = await api.createConversation(customerName?.trim() || "Guest", chatMode,
          temporary);
        current = { ...created, token: created.token };
        if (mounted.current) setConv(current);
        onChatChanged(created, created.token, mounted.current);
        if (pendingProject && !temporary) {
          onPatchChat?.({ id: created.id, token: created.token }, { project_id: pendingProject });
        }
      }
      const editAnswer = (fn) =>
        setMessages((m) => m.map((x) => (x.id === answerId ? { ...x, content: fn(x.content) } : x)));
      const res = await api.sendMessageStream(current.id, current.token, content,
        draft.map((f) => f.file), chatMode, webSearch, (ev) => {
          if (ev.type === "start") {
            asked = ev.user_message;
            // Reuse the local copies for display instead of downloading them again.
            asked.attachments.forEach((a, i) => {
              const local = draft[i];
              if (local) rememberLocalUrl(a, local.url || URL.createObjectURL(local.file));
            });
            if (!mounted.current) return;
            setMessages((m) => [...m.filter((x) => x.id !== tempId), asked,
              { id: answerId, role: "assistant", content: "", streaming: true }]);
          } else if (ev.type === "delta" && mounted.current) {
            editAnswer((c) => c + ev.text);
          } else if (ev.type === "reset" && mounted.current) {
            editAnswer(() => "");   // a half-written answer is being replaced
          }
        });
      onChatChanged(res.conversation, current.token);
      if (!mounted.current) return null;
      setMessages((m) => [...m.filter((x) => x.id !== tempId && x.id !== answerId && x.id !== res.user_message.id),
        res.user_message, res.reply]);
      setConv((c) => ({ ...c, ...res.conversation }));
      if (res.ticket) setTicket(res.ticket);
      return res.reply.content;
    } catch (err) {
      if (!mounted.current) return null;
      if (asked) {
        // The question was saved but answering failed: keep it, drop the partial answer.
        setMessages((m) => m.filter((x) => x.id !== answerId));
        setError(`${err.message} You can edit your message to send it again.`);
      } else {
        setMessages((m) => m.filter((x) => x.id !== tempId));
        if (override === undefined) { setText(content); setFiles(draft); } // give the draft back
        setError(err.message);
      }
      return null;
    } finally {
      if (mounted.current) setSending(false);
    }
  }

  // Shared by Regenerate and Edit: show the change at once, ask the server,
  // and put the chat back exactly as it was if that fails.
  async function rework(optimistic, call, merge) {
    if (sending || !conv) return;
    const before = messages;
    setError("");
    setSending(true);
    setMessages(optimistic);
    try {
      const res = await call();
      onChatChanged(res.conversation, conv.token);
      if (!mounted.current) return;
      setMessages((m) => merge(m, res));
      setConv((c) => ({ ...c, ...res.conversation }));
      if (res.ticket) setTicket(res.ticket);
    } catch (err) {
      if (!mounted.current) return;
      setMessages(before);
      setError(err.message);
    } finally {
      if (mounted.current) setSending(false);
    }
  }

  /** Replace the latest reply with a new answer, in the mode selected now. */
  function regenerate(replyId) {
    rework(
      (m) => m.filter((x) => x.id !== replyId),
      () => api.regenerate(conv.id, conv.token, replyId, chatMode, webSearch),
      (m, res) => [...m, res.reply],
    );
  }

  /** Change one of your messages: later messages go, and a new answer comes back. */
  function editMessage(messageId, newText) {
    rework(
      (m) => {
        const i = m.findIndex((x) => x.id === messageId);
        return [...m.slice(0, i), { ...m[i], content: newText }];
      },
      () => api.editMessage(conv.id, conv.token, messageId, newText, chatMode, webSearch),
      (m, res) => [...m.map((x) => (x.id === messageId ? res.user_message : x)), res.reply],
    );
  }

  function onDrop(e) {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer?.files?.length) addFiles([...e.dataTransfer.files]);
  }

  const closed = conv?.status === "closed";
  const escalated = ticket || conv?.status === "escalated";
  const empty = !loading && messages.length === 0;

  function pickProject(id) {
    if (conv?.id) onPatchChat?.(conv, { project_id: id });
    else setPendingProject(id);
  }

  async function newProjectHere() {
    const project = await onNewProject?.();
    if (project) pickProject(project.id);
  }

  const composer = (
    <Composer text={text} setText={setText} files={files} onAddFiles={addFiles}
      onRemoveFile={removeFile} onSubmit={() => send()} disabled={closed} busy={sending}
      autoFocus placeholder={EMPTY[chatMode].placeholder} onError={setError}
      webSearch={webSearch} onToggleWebSearch={onToggleWebSearch}
      chatMode={chatMode} onChangeMode={onChangeMode}
      projects={projects} projectId={conv?.id ? projectId : pendingProject}
      onPickProject={pickProject} onNewProject={newProjectHere}
      onOpenHelpdesk={onOpenHelpdesk} onVoice={() => setVoice(true)}
      micLang={micLang} onMicLang={onMicLang} temporary={temporary} />
  );
  const voiceOverlay = voice && (
    <VoiceMode lang={micLang} onAsk={(said) => send(said)} onClose={() => setVoice(false)} />
  );

  return (
    <div className={`chat-view ${dragging ? "dragging" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={(e) => { if (e.currentTarget === e.target) setDragging(false); }}
      onDrop={onDrop}>
      {dragging && <div className="drop-hint">Drop photos or files to attach</div>}

      {empty ? (
        <div className="empty-state">
          {temporary && (
            <div className="temp-note">
              <TemporaryIcon width={18} height={18} />
              <span><strong>Temporary chat.</strong> It won't appear in Recent and is deleted
                when you leave it.</span>
            </div>
          )}
          <Welcome name={greetName} />
          <p className="mode-prompt">{EMPTY[chatMode].heading}</p>
          <p className="muted center empty-sub">{EMPTY[chatMode].sub}</p>
          {aiMode === "offline" && (
            <p className="muted center">The AI is offline, so answers come straight from the help articles.</p>
          )}
          {error && <p className="error center" role="alert">{error}</p>}
          <div className="composer-wrap">{composer}</div>
          <div className="suggestions">
            {EMPTY[chatMode].suggestions.map((s) => (
              <button key={s} className="chip" onClick={() => send(s)}>{s}</button>
            ))}
          </div>
        </div>
      ) : (
        <>
          <div className="thread" aria-live="polite">
            <div className="thread-inner">
              {loading && <p className="muted center">Loading chat…</p>}
              {messages.map((m, i) => (
                <Message key={m.id} message={m} token={conv?.token}
                  busy={sending || closed} isLast={i === messages.length - 1}
                  hasLater={i < messages.length - 1}
                  onRegenerate={regenerate} onEdit={editMessage}
                  onSuggest={(s) => send(s)} closed={closed} />
              ))}
              {sending && !streamingNow && (
                <div className="msg msg-assistant">
                  <div className="avatar" aria-hidden="true" />
                  <div className="typing" aria-label="Assistant is typing"><span /><span /><span /></div>
                </div>
              )}
              <div ref={endRef} />
            </div>
          </div>
          {temporary && (
            <div className="temp-strip"><TemporaryIcon width={14} height={14} /> Temporary chat:
              not saved in Recent</div>
          )}
          <div className="composer-dock">
            {escalated && (
              <div className="banner">
                {ticket ? `Ticket #${ticket.id} opened (${ticket.priority} priority). ` : ""}
                Our {(ticket?.module || conv?.mode) === "sales" ? "Sales" : "Service"} support
                team has been notified and will follow up.
                {ticket && ticket.module !== "sales" && (ticket.serial_number || ticket.customer_site) && (
                  <span className="banner-details">
                    {ticket.serial_number && <>Equipment serial: <strong>{ticket.serial_number}</strong></>}
                    {ticket.serial_number && ticket.customer_site && " · "}
                    {ticket.customer_site && <>Site: <strong>{ticket.customer_site}</strong></>}
                  </span>
                )}
              </div>
            )}
            {error && <p className="error" role="alert">{error}</p>}
            {composer}
            <p className="fine-print">AI can make mistakes. Check important information.</p>
          </div>
        </>
      )}
      {voiceOverlay}
    </div>
  );
}

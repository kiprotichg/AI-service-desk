import { useCallback, useEffect, useRef, useState } from "react";
import { api, session, userSession } from "./api/client.js";
import {
  applyTheme, effectiveTheme, onSystemThemeChange, recentChats, settingsStore, sidebarPref,
  sortChats,
} from "./lib/storage.js";
import Sidebar from "./components/Sidebar.jsx";
import ChatView from "./components/ChatView.jsx";
import TicketsPanel from "./components/TicketsPanel.jsx";
import AnalyticsPanel from "./components/AnalyticsPanel.jsx";
import LoginForm from "./components/LoginForm.jsx";
import ModeSwitch from "./components/ModeSwitch.jsx";
import ProfileMenu from "./components/ProfileMenu.jsx";
import ThemeToggle from "./components/ThemeToggle.jsx";
import SettingsDialog from "./components/SettingsDialog.jsx";
import {
  ChevronDownIcon, CloseIcon, EditIcon, FolderIcon, NewChatIcon, PinIcon, PlusIcon, ShareIcon,
  SidebarIcon, TemporaryIcon, TrashIcon,
} from "./components/Icons.jsx";
import { greet } from "./lib/time.js";
import Logo from "./components/Logo.jsx";
import HelpDeskButton, { openHelpdesk } from "./components/HelpDeskButton.jsx";
import AuthPage from "./components/AuthPage.jsx";
import Menu from "./components/Menu.jsx";
import ShareDialog from "./components/ShareDialog.jsx";
import { PromptDialog } from "./components/Dialog.jsx";

const isNarrow = () => window.matchMedia("(max-width: 767px)").matches;
const byName = (list) => [...list].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));

/** What the app keeps about each chat (sidebar row, header title menu). */
function toEntry(conv, token) {
  return { id: conv.id, token, title: conv.title, mode: conv.mode, updated_at: conv.updated_at,
           pinned: Boolean(conv.pinned), project_id: conv.project_id ?? null,
           shared: Boolean(conv.shared), share_id: conv.share_id ?? null,
           temporary: Boolean(conv.temporary) };
}

export default function App() {
  const [aiMode, setAiMode] = useState(null);           // claude | gemini | offline | down
  const [agent, setAgent] = useState(() => session.get()?.agent ?? null);
  const [user, setUser] = useState(() => userSession.get()?.user ?? null);   // signed-in account
  const [config, setConfig] = useState(null);           // {helpdesk, email_enabled, require_login}
  const [settings, setSettings] = useState(() => settingsStore.load());
  // With accounts the list comes from the server; without, from this browser.
  const [chats, setChats] = useState(() => (userSession.get() ? [] : sortChats(recentChats.load())));
  const [active, setActive] = useState(null);           // {id, token, title, mode} or null
  const [chatKey, setChatKey] = useState(0);            // remounts ChatView for another chat
  const [chatMode, setChatMode] = useState(() => settingsStore.load().defaultMode);
  const [view, setView] = useState("chat");             // chat | tickets | analytics
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [toast, setToast] = useState(null);             // e.g. the sign-in greeting
  const [projects, setProjects] = useState(null);       // null until signed in (they need an account)
  const [temporary, setTemporary] = useState(false);    // next new chat isn't saved
  const [shareFor, setShareFor] = useState(null);       // chat shown in the Share dialog
  const [prompt, setPrompt] = useState(null);           // rename / new project dialog
  const [titleMenu, setTitleMenu] = useState(false);
  const titleBtnRef = useRef(null);
  const activeRef = useRef(null);
  const toastTimer = useRef(null);
  useEffect(() => () => clearTimeout(toastTimer.current), []);
  const [, setSystemTick] = useState(0);                // re-render when the OS theme flips
  // On phones the sidebar is an overlay and starts closed.
  const [sidebarOpen, setSidebarOpen] = useState(() => !isNarrow() && sidebarPref.load());
  useEffect(() => {
    api.health().then((h) => setAiMode(h.ai_mode)).catch(() => setAiMode("down"));
    // If the backend can't be reached, assume sign-in is required.
    api.config().then(setConfig).catch(() => setConfig({ helpdesk: null, require_login: true }));
    session.onLogout(() => setAgent(null));
    userSession.onLogout(() => resetAfterSignOut());
    return onSystemThemeChange(() => setSystemTick((t) => t + 1));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Signed in: check the session is still valid, refresh the name, load the chats.
  useEffect(() => {
    if (!user) return;
    let live = true;
    api.me().then((fresh) => {
      if (!live) return;
      setUser(fresh);
      userSession.update({ ...userSession.get(), user: fresh });
    }).catch(() => {});   // a 401 signs out through userSession.onLogout
    loadChats();
    api.listProjects().then((list) => live && setProjects(byName(list))).catch(() => {});
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  // Only the no-accounts setup keeps its chat list in the browser.
  useEffect(() => { if (!user) recentChats.save(chats); }, [chats, user]);

  async function loadChats() {
    try {
      const list = await api.myChats();
      setChats(sortChats(list.map((c) => toEntry(c, c.token))));
    } catch { /* signed out, or offline: keep what's shown */ }
  }

  async function handleAuthed({ token, user: account }, remember) {
    userSession.set({ token, user: account }, remember);
    // Chats this browser made before accounts existed move into the account.
    const local = recentChats.load();
    if (local.length) {
      try {
        await api.claimChats(local.map((c) => c.token));
        recentChats.save([]);
      } catch { /* not fatal: they stay in this browser's storage */ }
    }
    setUser(account);
    setActive(null);
    setChatKey((k) => k + 1);
    setView("chat");
    showToast(`${greet(account.name)} You're signed in.`);
  }

  // A temporary chat is deleted when you leave it (new chat, another chat, sign out,
  // closing the tab). Any that slip through are removed by the server after a day.
  activeRef.current = active;
  function discardTemporary() {
    const a = activeRef.current;
    if (a?.temporary) api.deleteConversation(a.id, a.token, { keepalive: true }).catch(() => {});
  }
  useEffect(() => {
    window.addEventListener("pagehide", discardTemporary);
    return () => window.removeEventListener("pagehide", discardTemporary);
  }, []);

  function resetAfterSignOut() {
    discardTemporary();
    setTemporary(false);
    setProjects(null);
    userSession.clear();
    session.clear();
    setUser(null);
    setAgent(null);
    setChats([]);
    setActive(null);
    setChatKey((k) => k + 1);
    setView("chat");
  }
  useEffect(() => {
    settingsStore.save(settings);
    applyTheme(settings.theme);
  }, [settings]);

  const theme = effectiveTheme(settings.theme);
  const toastEl = (
    <div className="toast-area" role="status" aria-live="polite">
      {toast && (
        <div className="toast">
          <span>{toast}</span>
          <button className="icon-btn small" onClick={() => setToast(null)} aria-label="Dismiss">
            <CloseIcon width={14} height={14} />
          </button>
        </div>
      )}
    </div>
  );
  const toggleTheme = () =>
    setSettings((s) => ({ ...s, theme: effectiveTheme(s.theme) === "dark" ? "light" : "dark" }));

  function toggleSidebar() {
    setSidebarOpen((open) => {
      if (!isNarrow()) sidebarPref.save(!open);
      return !open;
    });
  }
  const closeOnPhone = () => { if (isNarrow()) setSidebarOpen(false); };

  function newChat() {
    discardTemporary();
    setTemporary(false);
    setView("chat");
    setActive(null);
    setChatMode(settings.defaultMode);
    setChatKey((k) => k + 1);
    closeOnPhone();
  }

  function selectChat(chat) {
    setView("chat");
    if (chat.id !== active?.id) {
      discardTemporary();
      setTemporary(false);
      setActive(chat);
      setChatMode(chat.mode || "service");
      setChatKey((k) => k + 1);
    }
    closeOnPhone();
  }

  // Called by ChatView when a chat is created (activate=true) or changes (title, mode, activity).
  // The ChatView keeps its key, so a chat that's just been created isn't reloaded mid-send.
  const upsertChat = useCallback((conv, token, activate = false) => {
    if (!conv.temporary) {   // temporary chats stay out of the sidebar
      setChats((list) => {
        const existing = list.find((c) => c.id === conv.id);
        const entry = toEntry(conv, token ?? existing?.token);
        return sortChats([entry, ...list.filter((c) => c.id !== conv.id)]);
      });
    }
    setActive((a) => {
      if (activate) return toEntry(conv, token);
      return a && a.id === conv.id ? toEntry(conv, token ?? a.token) : a;
    });
  }, []);

  /** Apply changes the Share dialog made without a round trip. */
  function mergeChat(id, patch) {
    setChats((list) => list.map((c) => (c.id === id ? { ...c, ...patch } : c)));
    setActive((a) => (a && a.id === id ? { ...a, ...patch } : a));
    setShareFor((c) => (c && c.id === id ? { ...c, ...patch } : c));
  }

  /** Pin, move to a project...: errors show as a toast. */
  async function patchChat(chat, changes) {
    try {
      const conv = await api.updateConversation(chat.id, chat.token, changes);
      upsertChat(conv, chat.token);
    } catch (err) {
      showToast(err.message);
    }
  }

  function renameChat(chat) {
    setPrompt({
      title: "Rename chat", label: "Chat name", initial: chat.title || "", maxLength: 60,
      onSubmit: async (title) => upsertChat(await api.updateConversation(chat.id, chat.token, { title }), chat.token),
    });
  }

  /** Ask for a name and create a project; resolves with it (or null if cancelled). */
  function newProject() {
    return new Promise((resolve) => {
      setPrompt({
        title: "New project", label: "Project name", confirmText: "Create",
        onSubmit: async (name) => {
          const project = await api.createProject(name);
          setProjects((list) => byName([...(list || []), project]));
          resolve(project);
        },
        onCancel: () => resolve(null),
      });
    });
  }

  function renameProject(project) {
    setPrompt({
      title: "Rename project", label: "Project name", initial: project.name,
      onSubmit: async (name) => {
        const saved = await api.renameProject(project.id, name);
        setProjects((list) => byName(list.map((x) => (x.id === saved.id ? saved : x))));
      },
    });
  }

  async function deleteProject(project) {
    if (!window.confirm(`Delete the project "${project.name}"? Its chats move back to Recent.`)) return;
    try {
      await api.deleteProject(project.id);
      setProjects((list) => list.filter((x) => x.id !== project.id));
      setChats((list) => list.map((c) => (c.project_id === project.id ? { ...c, project_id: null } : c)));
      setActive((a) => (a?.project_id === project.id ? { ...a, project_id: null } : a));
    } catch (err) {
      showToast(err.message);
    }
  }

  function projectSubmenu(chat) {
    return [
      ...(projects || []).map((p) => ({
        label: p.name, icon: <FolderIcon width={16} height={16} />, checked: chat.project_id === p.id,
        onSelect: () => patchChat(chat, { project_id: chat.project_id === p.id ? null : p.id }),
      })),
      projects?.length ? { divider: true } : null,
      { label: "New project…", icon: <PlusIcon width={16} height={16} />,
        onSelect: async () => { const p = await newProject(); if (p) patchChat(chat, { project_id: p.id }); } },
      chat.project_id ? { label: "Remove from project", onSelect: () => patchChat(chat, { project_id: null }) } : null,
    ];
  }

  /** The chat menu (header title and sidebar ⋯): Pin, Rename, Add to project, Share, Delete. */
  function chatMenuItems(chat) {
    const saved = !chat.temporary;
    return [
      saved ? { label: chat.pinned ? "Unpin" : "Pin", icon: <PinIcon width={18} height={18} />, shortcut: "P",
                onSelect: () => patchChat(chat, { pinned: !chat.pinned }) } : null,
      { label: "Rename", icon: <EditIcon width={18} height={18} />, shortcut: "R", onSelect: () => renameChat(chat) },
      saved && projects ? { label: "Add to project", icon: <FolderIcon width={18} height={18} />,
                            submenu: projectSubmenu(chat) } : null,
      saved ? { label: "Share chat", icon: <ShareIcon width={18} height={18} />, onSelect: () => setShareFor(chat) } : null,
      { divider: true },
      { label: "Delete", icon: <TrashIcon width={18} height={18} />, shortcut: "D", danger: true,
        onSelect: () => deleteChat(chat) },
    ];
  }

  const projectMenuItems = (project) => [
    { label: "Rename", icon: <EditIcon width={18} height={18} />, shortcut: "R", onSelect: () => renameProject(project) },
    { divider: true },
    { label: "Delete project", icon: <TrashIcon width={18} height={18} />, shortcut: "D", danger: true,
      onSelect: () => deleteProject(project) },
  ];

  /** Header icon: start a temporary chat, or leave the one you're in. */
  function toggleTemporary() {
    if (active?.temporary) { newChat(); return; }
    setView("chat");
    // From a saved chat: open a fresh temporary chat. On a new chat: switch it on/off.
    setTemporary((t) => (active ? true : !t));
    setActive(null);
    setChatKey((k) => k + 1);
  }

  const forgetChat = useCallback((id) => {
    setChats((list) => list.filter((c) => c.id !== id));
    setActive((a) => (a && a.id === id ? null : a));
  }, []);

  async function removeOnServer(chat) {
    try {
      await api.deleteConversation(chat.id, chat.token);
      return true;
    } catch (err) {
      // Already gone or link expired: still fine to remove it from this browser.
      return err.status === 404 || err.status === 401 ? true : err.message;
    }
  }

  async function deleteChat(chat) {
    if (!chat.temporary && !window.confirm(`Delete "${chat.title || "New chat"}"? This can't be undone.`)) return;
    const ok = await removeOnServer(chat);
    if (ok !== true) { window.alert(`Couldn't delete the chat: ${ok}`); return; }
    if (active?.id === chat.id) newChat();
    forgetChat(chat.id);
  }

  async function deleteAllChats() {
    if (!window.confirm(`Delete all ${chats.length} chats? This can't be undone.`)) return;
    const results = await Promise.all(chats.map(removeOnServer));
    const failed = chats.filter((_, i) => results[i] !== true);
    setChats(failed); // keep any that couldn't be deleted, so nothing is lost silently
    newChat();
    if (failed.length) window.alert(`${failed.length} chat(s) couldn't be deleted. Try again later.`);
  }

  function handleLogin({ token, agent }) {
    session.set({ token, agent });
    setAgent(agent);
    // Greet with the time of day at the moment of signing in.
    showToast(`${greet(agent.username)} You're signed in.`);
  }

  function showToast(text) {
    clearTimeout(toastTimer.current);
    setToast(text);
    toastTimer.current = setTimeout(() => setToast(null), 5000);
  }

  /** Sign out of agent tools only (Tickets / Analytics). */
  function agentSignOut() {
    session.clear();
    setAgent(null);
    if (view !== "chat") setView("chat");
  }

  const title = view === "tickets" ? "Tickets" : "Analytics";
  const helpdesk = config?.helpdesk ?? null;
  const displayName = user?.name || settings.name || agent?.username;

  if (!config && !user) {
    return <div className="boot" aria-busy="true" />;   // a moment while settings load
  }
  if (config?.require_login && !user) {
    return (
      <>
        <div className="auth-theme"><ThemeToggle theme={theme} onToggle={toggleTheme} /></div>
        <AuthPage onAuthed={handleAuthed} emailEnabled={Boolean(config?.email_enabled)} />
        <HelpDeskButton helpdesk={helpdesk} userName="" onAuthPage />
        {toastEl}
      </>
    );
  }

  return (
    <div className={`layout ${sidebarOpen ? "sidebar-is-open" : ""}`}>
      <Sidebar
        open={sidebarOpen} onToggle={toggleSidebar} chats={chats} activeId={active?.id}
        view={view} onNewChat={newChat} onSelectChat={selectChat}
        onOpenView={(v) => { setView(v); closeOnPhone(); }}
        projects={projects} chatMenuItems={chatMenuItems} projectMenuItems={projectMenuItems}
        onNewProject={newProject}
      />
      {sidebarOpen && <div className="backdrop" onClick={toggleSidebar} aria-hidden="true" />}

      <div className="main">
        <header className="main-header">
          <div className="header-left">
            {!sidebarOpen && (
              <>
                <button className="icon-btn" onClick={toggleSidebar} title="Open sidebar"
                  aria-label="Open sidebar"><SidebarIcon /></button>
                <button className="icon-btn" onClick={newChat} title="New chat"
                  aria-label="New chat"><NewChatIcon /></button>
                <Logo className="logo-header" />
              </>
            )}
            {view === "chat" ? (
              <div className="mode-block">
                <h1 className="mode-heading">AI in Ideal</h1>
                <ModeSwitch value={chatMode} onChange={setChatMode} />
              </div>
            ) : <h1 className="main-title">{title}</h1>}
          </div>
          {view === "chat" && active && (
            <div className="header-center menu-anchor">
              <button ref={titleBtnRef} className={`title-btn ${titleMenu ? "open" : ""}`}
                onClick={() => setTitleMenu((o) => !o)} aria-haspopup="menu" aria-expanded={titleMenu}
                title="Chat options" aria-label={`Chat options: ${active.title || "New chat"}`}>
                {active.temporary && <TemporaryIcon width={15} height={15} />}
                {active.pinned && <PinIcon width={14} height={14} />}
                <span className="title-text">{active.title || "New chat"}</span>
                <ChevronDownIcon width={16} height={16} />
              </button>
              {titleMenu && (
                <Menu items={chatMenuItems(active)} label="Chat options" anchorRef={titleBtnRef}
                  className="menu-left" onClose={() => setTitleMenu(false)} />
              )}
            </div>
          )}
          <div className="header-right">
            {view === "chat" && active && !active.temporary && (
              <button className="share-btn" onClick={() => setShareFor(active)} title="Share chat">
                <ShareIcon width={17} height={17} /> <span>Share</span>
              </button>
            )}
            {view === "chat" && (
              <button className={`icon-btn temp-btn ${temporary || active?.temporary ? "on" : ""}`}
                onClick={toggleTemporary} aria-pressed={Boolean(temporary || active?.temporary)}
                title={active?.temporary ? "Leave temporary chat"
                  : temporary ? "Turn off temporary chat"
                  : active ? "Start a temporary chat (not saved)" : "Temporary chat (not saved)"}
                aria-label="Temporary chat">
                <TemporaryIcon />
              </button>
            )}
            <ThemeToggle theme={theme} onToggle={toggleTheme} />
            <ProfileMenu user={user} agent={agent} name={settings.name}
              onSettings={() => setSettingsOpen(true)} onSignOut={resetAfterSignOut}
              onAgentSignOut={agentSignOut}
              onSignIn={() => { setView("tickets"); closeOnPhone(); }} />
          </div>
        </header>

        {view === "chat" && (
          <ChatView key={chatKey} chat={active} aiMode={aiMode} chatMode={chatMode}
            customerName={user?.name || settings.name} greetName={displayName}
            onModeLoaded={setChatMode}
            onChatChanged={upsertChat} onChatMissing={forgetChat}
            temporary={temporary || Boolean(active?.temporary)}
            webSearch={settings.webSearch}
            onToggleWebSearch={() => setSettings((s) => ({ ...s, webSearch: !s.webSearch }))}
            onChangeMode={setChatMode} projects={projects} projectId={active?.project_id ?? null}
            onPatchChat={patchChat} onNewProject={newProject}
            micLang={settings.micLang} onMicLang={(micLang) => setSettings((s) => ({ ...s, micLang }))}
            onOpenHelpdesk={openHelpdesk} />
        )}
        {view !== "chat" && (
          <div className="panel-wrap">
            {!agent && <LoginForm onLogin={handleLogin} />}
            {agent && view === "tickets" && <TicketsPanel />}
            {agent && view === "analytics" && <AnalyticsPanel />}
          </div>
        )}
      </div>

      {settingsOpen && (
        <SettingsDialog settings={settings} onChange={setSettings} account={user}
          onClose={() => setSettingsOpen(false)} chatCount={chats.length}
          onDeleteAll={deleteAllChats} />
      )}

      {shareFor && (
        <ShareDialog chat={shareFor} onClose={() => setShareFor(null)}
          onChanged={(patch) => mergeChat(shareFor.id, patch)} />
      )}
      {prompt && (
        <PromptDialog {...prompt} onClose={() => { prompt.onCancel?.(); setPrompt(null); }} />
      )}

      <HelpDeskButton helpdesk={helpdesk} userName={displayName} />
      {toastEl}
    </div>
  );
}

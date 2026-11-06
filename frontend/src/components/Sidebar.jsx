import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ChartIcon, ChevronDownIcon, ChevronRightIcon, DotsIcon, FolderIcon, NewChatIcon, PinIcon,
  PlusIcon, SidebarIcon, TicketIcon,
} from "./Icons.jsx";
import Logo from "./Logo.jsx";
import Menu from "./Menu.jsx";

/** A "⋯" button whose menu floats above everything (the chat list scrolls and would clip it). */
function RowMenu({ label, items }) {
  const btnRef = useRef(null);
  const [style, setStyle] = useState(null);

  function toggle() {
    if (style) { setStyle(null); return; }
    const r = btnRef.current.getBoundingClientRect();
    const left = Math.max(8, Math.min(r.left, window.innerWidth - 248));
    const up = window.innerHeight - r.bottom < 280;
    setStyle(up ? { position: "fixed", left, bottom: window.innerHeight - r.top + 4, top: "auto", right: "auto" }
      : { position: "fixed", left, top: r.bottom + 4, right: "auto" });
  }

  return (
    <>
      <button ref={btnRef} className={`row-more ${style ? "open" : ""}`} onClick={toggle}
        aria-haspopup="menu" aria-expanded={Boolean(style)} title="More options"
        aria-label={`More options for ${label}`}>
        <DotsIcon width={16} height={16} />
      </button>
      {style && createPortal(
        <div className="floating-menu" style={style}>
          <Menu items={items} label={label} anchorRef={btnRef} onClose={() => setStyle(null)} />
        </div>,
        document.body)}
    </>
  );
}

function ChatRow({ chat, active, onSelect, menuItems }) {
  const title = chat.title || "New chat";
  return (
    <li className={active ? "active" : ""}>
      <button className="chat-item" onClick={() => onSelect(chat)} title={title}>{title}</button>
      <RowMenu label={title} items={menuItems(chat)} />
    </li>
  );
}

export default function Sidebar({
  open, onToggle, chats, projects, activeId, view, onNewChat, onSelectChat, onOpenView,
  chatMenuItems, projectMenuItems, onNewProject,
}) {
  const [collapsed, setCollapsed] = useState({});   // project id -> true when folded
  const isActive = (c) => view === "chat" && c.id === activeId;
  const pinned = chats.filter((c) => c.pinned);
  // A project lists all its chats; pinned ones also appear under Pinned.
  const inProject = (pid) => chats.filter((c) => c.project_id === pid);
  const known = new Set((projects || []).map((p) => p.id));
  // Chats in a project that no longer exists fall back to Recent.
  const recent = chats.filter((c) => !c.pinned && !(c.project_id && known.has(c.project_id)));
  const row = (c) => (
    <ChatRow key={c.id} chat={c} active={isActive(c)} onSelect={onSelectChat} menuItems={chatMenuItems} />
  );

  return (
    <aside className={`sidebar ${open ? "open" : "closed"}`} aria-label="Chats" aria-hidden={!open}>
      <div className="sidebar-top">
        <Logo className="logo-sidebar" />
        <button className="icon-btn" onClick={onToggle} title="Close sidebar" aria-label="Close sidebar">
          <SidebarIcon />
        </button>
      </div>

      <button className="new-chat" onClick={onNewChat}>
        <NewChatIcon width={18} height={18} /> New chat
      </button>

      <nav className="chat-list">
        {pinned.length > 0 && (
          <>
            <div className="list-label"><PinIcon width={12} height={12} /> Pinned</div>
            <ul>{pinned.map(row)}</ul>
          </>
        )}

        {projects && (
          <>
            <div className="list-label with-action">
              <span>Projects</span>
              <button className="icon-btn small" onClick={onNewProject} title="New project"
                aria-label="New project"><PlusIcon width={15} height={15} /></button>
            </div>
            {projects.length === 0 && <p className="list-empty">Group related chats into a project.</p>}
            <ul>
              {projects.map((p) => {
                const items = inProject(p.id);
                const folded = collapsed[p.id];
                return (
                  <li key={`p-${p.id}`} className="project">
                    <button className="project-item" aria-expanded={!folded}
                      onClick={() => setCollapsed((c) => ({ ...c, [p.id]: !folded }))}>
                      {folded ? <ChevronRightIcon width={14} height={14} /> : <ChevronDownIcon width={14} height={14} />}
                      <FolderIcon width={16} height={16} />
                      <span className="project-name">{p.name}</span>
                      <span className="project-count">{items.length}</span>
                    </button>
                    <RowMenu label={p.name} items={projectMenuItems(p)} />
                    {!folded && items.length > 0 && <ul className="project-chats">{items.map(row)}</ul>}
                  </li>
                );
              })}
            </ul>
          </>
        )}

        <div className="list-label">Recent</div>
        {recent.length === 0 && <p className="list-empty">Your chats will show up here.</p>}
        <ul>{recent.map(row)}</ul>
      </nav>

      <div className="sidebar-bottom">
        <div className="list-label">Agent tools</div>
        <button className={`nav-item ${view === "tickets" ? "active" : ""}`}
          onClick={() => onOpenView("tickets")}>
          <TicketIcon width={18} height={18} /> Tickets
        </button>
        <button className={`nav-item ${view === "analytics" ? "active" : ""}`}
          onClick={() => onOpenView("analytics")}>
          <ChartIcon width={18} height={18} /> Analytics
        </button>
      </div>
    </aside>
  );
}

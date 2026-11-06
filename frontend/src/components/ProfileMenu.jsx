import { useEffect, useRef, useState } from "react";
import { GearIcon, LoginIcon, LogoutIcon, UserIcon } from "./Icons.jsx";

/**
 * Round profile button (top right) with a dropdown: the account, Settings, agent
 * tools sign-in/out, and Sign out of the account.
 */
export default function ProfileMenu({ user, agent, name, onSettings, onSignOut, onAgentSignOut,
  onSignIn }) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const menuRef = useRef(null);

  const displayName = user?.name || agent?.username || name || "Guest";
  const initial = displayName.trim().charAt(0).toUpperCase();

  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector("button")?.focus();
    const onDown = (e) => { if (!wrapRef.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Arrow keys move between menu items.
  function onMenuKey(e) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = [...menuRef.current.querySelectorAll("button")];
    const i = items.indexOf(document.activeElement);
    const next = e.key === "ArrowDown" ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
    items[next]?.focus();
  }

  const pick = (fn) => () => { setOpen(false); fn(); };

  return (
    <div className="profile" ref={wrapRef}>
      <button className="avatar-btn" onClick={() => setOpen((o) => !o)} aria-haspopup="menu"
        aria-expanded={open} aria-label={`Profile: ${displayName}`} title={displayName}>
        {initial && displayName !== "Guest" ? initial : <UserIcon width={18} height={18} />}
      </button>
      {open && (
        <div className="menu" role="menu" ref={menuRef} onKeyDown={onMenuKey}>
          <div className="menu-head">
            <span className="menu-name">{displayName}</span>
            <span className="menu-sub">
              {user?.email || (agent ? "Support agent" : "Ideal Dynamics user")}
            </span>
            {user && agent && <span className="menu-sub">Agent tools: {agent.username}</span>}
          </div>
          <button role="menuitem" className="menu-item" onClick={pick(onSettings)}>
            <GearIcon width={18} height={18} /> Settings
          </button>
          {agent ? (
            <button role="menuitem" className="menu-item" onClick={pick(onAgentSignOut)}>
              <LogoutIcon width={18} height={18} /> {user ? "Sign out of agent tools" : "Sign out"}
            </button>
          ) : (
            <button role="menuitem" className="menu-item" onClick={pick(onSignIn)}>
              <LoginIcon width={18} height={18} /> Agent sign in
            </button>
          )}
          {user && (
            <>
              <div className="menu-sep" role="separator" />
              <button role="menuitem" className="menu-item" onClick={pick(onSignOut)}>
                <LogoutIcon width={18} height={18} /> Sign out
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

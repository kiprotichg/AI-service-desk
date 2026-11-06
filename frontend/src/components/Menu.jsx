import { useEffect, useRef, useState } from "react";
import { CheckIcon, ChevronRightIcon } from "./Icons.jsx";

const isNarrow = () => window.matchMedia("(max-width: 560px)").matches;
const ITEM_SELECTOR = "[data-menu-item]:not(:disabled)";

/**
 * Dropdown menu with optional submenus, check marks and one-letter shortcuts.
 *
 * items: [{ label, icon, onSelect, shortcut ("P", or a hint like "Ctrl+U"), hint,
 *           checked (true/false shows a tick / toggle), danger, disabled,
 *           keepOpen (stay open after picking, for toggles), submenu: [items] }
 *         | { divider: true } | null]
 * The parent positions it (className "menu-up" / "menu-left") and closes it via onClose.
 * Single-letter shortcuts work while the menu is open; Escape closes it.
 */
export default function Menu({ items, onClose, anchorRef, label, className = "", nested = false,
  onDone }) {
  const ref = useRef(null);
  const [openSub, setOpenSub] = useState(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const list = items.filter(Boolean);
  const done = onDone || onClose;   // closes the whole menu, from any level

  useEffect(() => {
    ref.current?.querySelector(ITEM_SELECTOR)?.focus();
    if (nested) return undefined;
    const onDown = (e) => {
      if (ref.current?.contains(e.target) || anchorRef?.current?.contains(e.target)) return;
      closeRef.current();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [nested, anchorRef]);

  function select(item, i) {
    if (item.disabled) return;
    // Hovering already opened it on desktop, so a click there keeps it open;
    // on phones (no hover) a tap toggles it.
    if (item.submenu) { setOpenSub(isNarrow() && openSub === i ? null : i); return; }
    item.onSelect?.();
    if (!item.keepOpen) done();
  }

  function onKeyDown(e) {
    const own = [...ref.current.querySelectorAll(`:scope > .menu-row > ${ITEM_SELECTOR}`)];
    const at = own.indexOf(document.activeElement);
    if (e.key === "Escape" || (nested && e.key === "ArrowLeft")) {
      e.preventDefault();
      e.stopPropagation();
      onClose();
      if (!nested) anchorRef?.current?.focus?.();
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (at === -1) return;   // focus is inside a submenu; it handles its own arrows
      e.preventDefault();
      const next = e.key === "ArrowDown" ? (at + 1) % own.length : (at - 1 + own.length) % own.length;
      own[next]?.focus();
      return;
    }
    if (e.key === "ArrowRight" && at !== -1) {
      const i = Number(own[at].dataset.index);
      if (list[i]?.submenu) { e.preventDefault(); setOpenSub(i); }
      return;
    }
    // One-letter shortcuts (P, R, D...) while the menu is open.
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const i = list.findIndex((it) => it.shortcut?.length === 1 &&
        it.shortcut.toLowerCase() === e.key.toLowerCase());
      if (i !== -1) { e.preventDefault(); e.stopPropagation(); select(list[i], i); }
    }
  }

  return (
    <div className={`menu dropdown ${nested ? "submenu" : className}`} role="menu" aria-label={label}
      ref={ref} onKeyDown={onKeyDown}>
      {list.map((item, i) => (item.divider ? (
        <div key={`sep-${i}`} className="menu-sep" role="separator" />
      ) : (
        <div key={item.label} className="menu-row">
          <button type="button" data-menu-item data-index={i}
            role={item.checked === undefined ? "menuitem" : "menuitemcheckbox"}
            aria-checked={item.checked === undefined ? undefined : Boolean(item.checked)}
            aria-haspopup={item.submenu ? "menu" : undefined}
            aria-expanded={item.submenu ? openSub === i : undefined}
            className={`menu-item ${item.danger ? "danger" : ""}`} disabled={item.disabled}
            onClick={() => select(item, i)}
            onMouseEnter={() => { if (!isNarrow()) setOpenSub(item.submenu ? i : null); }}>
            {item.icon && <span className="menu-icon">{item.icon}</span>}
            <span className="menu-label">
              {item.label}
              {item.hint && <span className="menu-hint">{item.hint}</span>}
            </span>
            {item.shortcut && <kbd className="menu-kbd">{item.shortcut}</kbd>}
            {item.checked && <CheckIcon className="menu-check" width={16} height={16} />}
            {item.submenu && <ChevronRightIcon className="menu-chevron" width={16} height={16} />}
          </button>
          {item.submenu && openSub === i && (
            <Menu nested items={item.submenu} label={item.label} onDone={done}
              className="" onClose={() => {
                setOpenSub(null);
                ref.current?.querySelector(`[data-index="${i}"]`)?.focus();
              }} />
          )}
        </div>
      )))}
    </div>
  );
}

/** "menu-up" when there's more room above the anchor than below it. */
export function placementFor(anchorEl, needed = 320) {
  const r = anchorEl?.getBoundingClientRect();
  if (!r) return "";
  return window.innerHeight - r.bottom < needed && r.top > window.innerHeight - r.bottom ? "menu-up" : "";
}

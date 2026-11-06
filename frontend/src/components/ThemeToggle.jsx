import { MoonIcon, SunIcon } from "./Icons.jsx";

/** Shows the theme you'd switch TO: a moon in light mode, a sun in dark mode. */
export default function ThemeToggle({ theme, onToggle }) {
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button className="icon-btn" onClick={onToggle} title={`Switch to ${next} mode`}
      aria-label={`Switch to ${next} mode`}>
      {theme === "dark" ? <SunIcon /> : <MoonIcon />}
    </button>
  );
}

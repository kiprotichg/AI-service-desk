import { useEffect, useState } from "react";

/**
 * Time-of-day greeting from the user's own clock (their device's time zone):
 * 05:00-11:59 morning, 12:00-16:59 afternoon, otherwise evening.
 */
export function greetingPart(date = new Date()) {
  const h = date.getHours();
  if (h >= 5 && h < 12) return "Good morning";
  if (h >= 12 && h < 17) return "Good afternoon";
  return "Good evening";
}

export function greet(name, date = new Date()) {
  const clean = (name || "").trim();
  return clean ? `${greetingPart(date)}, ${clean}!` : `${greetingPart(date)}!`;
}

/** e.g. "Thursday, 6 November · 9:41 AM", in the user's language and 12/24h style. */
export function formatNow(date) {
  const day = date.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  const time = date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  return `${day} · ${time}`;
}

/**
 * The current time, kept in sync with the clock: it ticks exactly on each new
 * minute, and re-syncs at once when the tab becomes visible again or the computer
 * wakes up (timers pause while asleep, so the greeting would otherwise be stale).
 */
export function useNow() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    let timer;
    const tick = () => {
      const d = new Date();
      setNow(d);
      // Next run just after the next minute starts.
      timer = setTimeout(tick, 60_000 - (d.getSeconds() * 1000 + d.getMilliseconds()) + 50);
    };
    const resync = () => { clearTimeout(timer); tick(); };
    tick();
    document.addEventListener("visibilitychange", resync);
    window.addEventListener("focus", resync);
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", resync);
      window.removeEventListener("focus", resync);
    };
  }, []);

  return now;
}

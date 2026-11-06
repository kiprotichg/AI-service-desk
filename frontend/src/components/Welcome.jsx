import { formatNow, greet, useNow } from "../lib/time.js";

/**
 * Top of every new chat: "Good afternoon, Kip!", the current date and time, and
 * "Welcome to AI in Ideal". Follows the clock live, so it switches from morning to
 * afternoon at noon without a reload. Its own component, so the minute tick only
 * redraws these lines, not the whole chat.
 */
export default function Welcome({ name }) {
  const now = useNow();
  return (
    <>
      <p className="greeting">{greet(name, now)}</p>
      <p className="now">
        <time dateTime={now.toISOString()}>{formatNow(now)}</time>
      </p>
      <h2 className="welcome">Welcome to AI in Ideal</h2>
    </>
  );
}

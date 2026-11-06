import { useState } from "react";
import { api } from "../api/client.js";

export default function LoginForm({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onLogin(await api.login(username.trim(), password));
    } catch (err) {
      setError(err.message);
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card start" onSubmit={submit}>
      <h2>Agent sign in</h2>
      <p className="muted">Tickets and analytics are for support agents only.</p>
      <label htmlFor="username">Username</label>
      <input id="username" value={username} onChange={(e) => setUsername(e.target.value)}
        autoComplete="username" required />
      <label htmlFor="password">Password</label>
      <input id="password" type="password" value={password}
        onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
      <button type="submit" className="primary" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}

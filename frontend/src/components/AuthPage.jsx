import { useEffect, useState } from "react";
import { api } from "../api/client.js";
import Logo from "./Logo.jsx";
import { EyeIcon, EyeOffIcon, GlobeIcon, HeadsetIcon, MailIcon, TrendIcon } from "./Icons.jsx";

// Same rules as the server (it re-checks everything).
const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const PASSWORD_RULES = [
  { test: (p) => p.length >= 8, label: "At least 8 characters" },
  { test: (p) => /[A-Za-z]/.test(p), label: "A letter" },
  { test: (p) => /\d/.test(p), label: "A number" },
];
const passwordOk = (p) => PASSWORD_RULES.every((r) => r.test(p)) && p.length <= 128;
const RESEND_SECONDS = 60;

function PasswordField({ id, label, value, onChange, autoComplete, autoFocus }) {
  const [show, setShow] = useState(false);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="password-wrap">
        <input id={id} type={show ? "text" : "password"} value={value} required
          autoComplete={autoComplete} autoFocus={autoFocus} maxLength={128}
          onChange={(e) => onChange(e.target.value)} />
        <button type="button" className="icon-btn small reveal" onClick={() => setShow((s) => !s)}
          aria-label={show ? "Hide password" : "Show password"} title={show ? "Hide" : "Show"}>
          {show ? <EyeOffIcon width={18} height={18} /> : <EyeIcon width={18} height={18} />}
        </button>
      </div>
    </div>
  );
}

function PasswordRules({ password }) {
  return (
    <ul className="pw-rules" aria-label="Password requirements">
      {PASSWORD_RULES.map((r) => (
        <li key={r.label} className={r.test(password) ? "ok" : ""}>{r.label}</li>
      ))}
    </ul>
  );
}

/**
 * Start page: sign in, create an account, or reset a forgotten password with a
 * code sent by email. Calls onAuthed({token, user}, remember) when signed in.
 */
export default function AuthPage({ onAuthed, emailEnabled }) {
  const [view, setView] = useState("login");      // login | register | forgot | reset
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [code, setCode] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  function go(next) {
    setView(next);
    setError("");
    setNotice("");
    setPassword("");
    setConfirm("");
    if (next !== "reset") setCode("");
  }

  async function run(fn) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const cleanEmail = email.trim();

  function submitLogin(e) {
    e.preventDefault();
    if (!EMAIL.test(cleanEmail)) return setError("Enter a valid email address");
    run(async () => onAuthed(await api.userLogin(cleanEmail, password), remember));
  }

  function submitRegister(e) {
    e.preventDefault();
    if (!name.trim()) return setError("Enter your name");
    if (!EMAIL.test(cleanEmail)) return setError("Enter a valid email address");
    if (!passwordOk(password)) return setError("Choose a password that meets all the requirements");
    if (password !== confirm) return setError("The passwords don't match");
    run(async () => onAuthed(await api.register(name.trim(), cleanEmail, password), remember));
  }

  function sendCode(e) {
    e?.preventDefault();
    if (!EMAIL.test(cleanEmail)) return setError("Enter a valid email address");
    run(async () => {
      const res = await api.forgotPassword(cleanEmail);
      setView("reset");
      setCooldown(RESEND_SECONDS);
      setNotice(res.message);
    });
  }

  function submitReset(e) {
    e.preventDefault();
    if (!/^\d{6}$/.test(code.trim())) return setError("Enter the 6-digit code from the email");
    if (!passwordOk(password)) return setError("Choose a password that meets all the requirements");
    if (password !== confirm) return setError("The passwords don't match");
    run(async () => onAuthed(await api.resetPassword(cleanEmail, code.trim(), password), remember));
  }

  const titles = {
    login: ["Welcome back", "Sign in to continue to AI in Ideal."],
    register: ["Create your account", "It only takes a minute."],
    forgot: ["Forgot your password?", "Enter your email and we'll send you a reset code."],
    reset: ["Reset your password", `Enter the code we sent to ${cleanEmail || "your email"}.`],
  };

  const emailField = (autoFocus) => (
    <div className="field">
      <label htmlFor="auth-email">Email</label>
      <input id="auth-email" type="email" value={email} required autoComplete="email"
        autoFocus={autoFocus} maxLength={254} placeholder="you@company.com"
        onChange={(e) => setEmail(e.target.value)} />
    </div>
  );

  const rememberBox = (
    <label className="check">
      <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
      Keep me signed in
    </label>
  );

  return (
    <div className="auth">
      <aside className="auth-brand">
        <div className="auth-brand-inner">
          <Logo className="auth-logo" />
          <h1>AI in Ideal</h1>
          <p className="auth-tagline">Your assistant for the Ideal Dynamics ERP suite.</p>
          <ul className="auth-features">
            <li><HeadsetIcon width={20} height={20} /><span><strong>Service</strong> requests,
              technicians, contracts and warranties</span></li>
            <li><TrendIcon width={20} height={20} /><span><strong>Sales</strong> quotations,
              orders, invoices and pricing</span></li>
            <li><GlobeIcon width={20} height={20} /><span><strong>General</strong> questions on
              any topic</span></li>
          </ul>
        </div>
      </aside>

      <main className="auth-main">
        <div className="auth-card">
          <Logo className="auth-logo-mobile" />
          <h2>{titles[view][0]}</h2>
          <p className="auth-sub">{titles[view][1]}</p>

          {notice && <div className="auth-notice" role="status"><MailIcon width={18} height={18} />
            <span>{notice}{view === "reset" && !emailEnabled && (
              <> Email isn't set up on this server yet, so the code was written to the
                backend terminal window.</>)}</span></div>}
          {error && <div className="auth-error" role="alert">{error}</div>}

          {view === "login" && (
            <form onSubmit={submitLogin} noValidate>
              {emailField(true)}
              <PasswordField id="auth-password" label="Password" value={password}
                onChange={setPassword} autoComplete="current-password" />
              <div className="auth-row">
                {rememberBox}
                <button type="button" className="text-link" onClick={() => go("forgot")}>
                  Forgot password?</button>
              </div>
              <button className="auth-submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
              <p className="auth-switch">New to AI in Ideal?{" "}
                <button type="button" className="text-link" onClick={() => go("register")}>
                  Create an account</button></p>
            </form>
          )}

          {view === "register" && (
            <form onSubmit={submitRegister} noValidate>
              <div className="field">
                <label htmlFor="auth-name">Full name</label>
                <input id="auth-name" value={name} required autoComplete="name" autoFocus
                  maxLength={80} placeholder="Jane Doe" onChange={(e) => setName(e.target.value)} />
              </div>
              {emailField(false)}
              <PasswordField id="auth-password" label="Password" value={password}
                onChange={setPassword} autoComplete="new-password" />
              <PasswordRules password={password} />
              <PasswordField id="auth-confirm" label="Confirm password" value={confirm}
                onChange={setConfirm} autoComplete="new-password" />
              <div className="auth-row">{rememberBox}</div>
              <button className="auth-submit" disabled={busy}>
                {busy ? "Creating account…" : "Create account"}</button>
              <p className="auth-switch">Already have an account?{" "}
                <button type="button" className="text-link" onClick={() => go("login")}>Sign in</button></p>
            </form>
          )}

          {view === "forgot" && (
            <form onSubmit={sendCode} noValidate>
              {emailField(true)}
              <button className="auth-submit" disabled={busy}>{busy ? "Sending…" : "Send reset code"}</button>
              <p className="auth-switch">Remembered it?{" "}
                <button type="button" className="text-link" onClick={() => go("login")}>Back to sign in</button></p>
            </form>
          )}

          {view === "reset" && (
            <form onSubmit={submitReset} noValidate>
              <div className="field">
                <label htmlFor="auth-code">6-digit code</label>
                <input id="auth-code" className="code-input" value={code} required autoFocus
                  inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="123456"
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
              </div>
              <PasswordField id="auth-password" label="New password" value={password}
                onChange={setPassword} autoComplete="new-password" />
              <PasswordRules password={password} />
              <PasswordField id="auth-confirm" label="Confirm new password" value={confirm}
                onChange={setConfirm} autoComplete="new-password" />
              <button className="auth-submit" disabled={busy}>
                {busy ? "Resetting…" : "Reset password and sign in"}</button>
              <p className="auth-switch">
                Didn't get it?{" "}
                <button type="button" className="text-link" disabled={busy || cooldown > 0}
                  onClick={() => sendCode()}>
                  {cooldown > 0 ? `Resend code in ${cooldown}s` : "Resend code"}</button>
                {" · "}
                <button type="button" className="text-link" onClick={() => go("login")}>Back to sign in</button>
              </p>
            </form>
          )}
        </div>
        <p className="auth-foot">© Ideal Dynamics · AI in Ideal</p>
      </main>
    </div>
  );
}

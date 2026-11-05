import { FormEvent, useState } from "react";
import { api } from "../api";
import { StarMark } from "../icons";

export default function AuthScreen({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      if (mode === "login") await api.post("/api/auth/login", { email, password });
      else await api.post("/api/auth/register", { email, password, display_name: name });
      onDone();
    } catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <div className="auth">
      <div className="wordmark"><StarMark className="" /><span>Astris</span></div>
      <p className="tagline">Your household's money, kept at home.</p>
      <form className="panel stack" onSubmit={submit}>
        <div className="seg" role="group" aria-label="Sign in or create account">
          <button type="button" aria-pressed={mode === "login"} onClick={() => setMode("login")}>Sign in</button>
          <button type="button" aria-pressed={mode === "register"} onClick={() => setMode("register")}>Create account</button>
        </div>
        {mode === "register" && (
          <label>Your name<input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} autoComplete="name" /></label>
        )}
        <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" /></label>
        <label>Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
            minLength={mode === "register" ? 10 : undefined} autoComplete={mode === "login" ? "current-password" : "new-password"} />
        </label>
        {mode === "register" && <span className="muted small">Use at least 10 characters.</span>}
        {error && <div className="error" role="alert">{error}</div>}
        <button className="btn primary block" disabled={busy}>{mode === "login" ? "Sign in" : "Create account"}</button>
      </form>
    </div>
  );
}

import { type FormEvent, type ReactNode, useCallback, useEffect, useState } from "react";

import { api, errorMessage } from "../api/client";
import { SIGNED_OUT_EVENT } from "../api/auth-fetch";
import { Logo } from "../components/Logo";
import "./auth.css";

type Gate = "loading" | "setup" | "sign-in" | "open" | "unreachable";

const MIN_LENGTH = 10;

/** Before YAAP shows anything else: the first-run password screen or the sign-in screen. */
export default function AuthGate({ children }: { children: ReactNode }) {
  const [gate, setGate] = useState<Gate>("loading");

  const check = useCallback(async () => {
    try {
      const { data } = await api.GET("/api/v1/auth/state");
      if (!data) {
        setGate("unreachable");
        return;
      }
      setGate(data.signed_in ? "open" : data.password_set ? "sign-in" : "setup");
    } catch {
      setGate("unreachable");
    }
  }, []);

  useEffect(() => {
    void check();
    // A 401 anywhere (an ended session, a password changed in another browser) shows the sign-in screen again.
    const onSignedOut = () => void check();
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut);
    return () => window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut);
  }, [check]);

  if (gate === "open") return <>{children}</>;
  if (gate === "loading") return <p className="page-message muted">Loading…</p>;
  if (gate === "unreachable")
    return (
      <div className="auth-screen">
        <div className="auth-card">
          <Logo compact className="auth-logo" />
          <p>YAAP does not answer. It may be restarting.</p>
          <button type="button" className="button button-primary" onClick={() => void check()}>
            Try again
          </button>
        </div>
      </div>
    );
  return <PasswordScreen setup={gate === "setup"} onDone={() => setGate("open")} />;
}

function PasswordScreen({ setup, onDone }: { setup: boolean; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (setup) {
      if (password.length < MIN_LENGTH) {
        setError(`Use at least ${MIN_LENGTH} characters.`);
        return;
      }
      if (password !== again) {
        setError("The two passwords differ.");
        return;
      }
    }
    setBusy(true);
    const { error: failure } = setup
      ? await api.POST("/api/v1/auth/setup", { body: { password } })
      : await api.POST("/api/v1/auth/sign-in", { body: { password } });
    setBusy(false);
    if (failure) {
      setError(errorMessage(failure, setup ? "The password was not set." : "Signing in failed."));
      return;
    }
    // Everything loaded before signing in was refused; start the app fresh with the session.
    onDone();
  }

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={submit}>
        <Logo compact className="auth-logo" />
        {setup ? (
          <>
            <h1>Set a password for YAAP</h1>
            <p className="muted">
              YAAP now asks for a password before it shows anything. Choose one of at least {MIN_LENGTH} characters;
              you sign in with it in every browser. A forgotten password is reset from the YAAP folder with{" "}
              <code>docker compose exec -it yaap python -m app.scripts.set_password --reset</code>.
            </p>
          </>
        ) : (
          <h1>Sign in to YAAP</h1>
        )}
        <label className="auth-field">
          <span>Password</span>
          <input
            type="password"
            autoComplete={setup ? "new-password" : "current-password"}
            autoFocus
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {setup && (
          <label className="auth-field">
            <span>The same password again</span>
            <input type="password" autoComplete="new-password" value={again} onChange={(event) => setAgain(event.target.value)} />
          </label>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="button button-primary" disabled={busy || !password}>
          {busy ? "One moment…" : setup ? "Set password and continue" : "Sign in"}
        </button>
      </form>
    </div>
  );
}

/** Sign this browser out and show the sign-in screen. */
export async function signOut(): Promise<void> {
  await api.POST("/api/v1/auth/sign-out");
  window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
}

"use client";

import { useState, useSyncExternalStore, type FormEvent } from "react";
import { apiCommand } from "../lib/api";

export function LoginForm() {
  const [pending, setPending] = useState(false);
  const hydrated = useSyncExternalStore(() => () => undefined, () => true, () => false);
  const [message, setMessage] = useState("Sign in is restricted to the local HTTPS operator origin.");
  const [error, setError] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = form.get("password");
    if (typeof password !== "string" || password.length === 0) return;
    setPending(true);
    setError(false);
    setMessage("Verifying local operator…");
    try {
      await apiCommand("/api/v1/session/login", { password }, { csrf: false });
      setMessage("Session established. Opening the Trading Room…");
      window.location.assign("/");
    } catch (reason: unknown) {
      setError(true);
      setMessage(reason instanceof Error ? reason.message : "Sign in was not accepted.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="form-grid" onSubmit={submit}>
      <label htmlFor="operator-password">Local operator password
        <input id="operator-password" name="password" type="password" autoComplete="current-password" required disabled={!hydrated || pending} />
      </label>
      <button type="submit" disabled={pending || !hydrated}>{pending ? "Signing in…" : "Sign in securely"}</button>
      <p className={`command-result${error ? " error" : ""}`} role="status" aria-live="polite">{message}</p>
    </form>
  );
}

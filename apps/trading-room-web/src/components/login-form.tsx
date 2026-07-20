"use client";

import { useState, useSyncExternalStore, type FormEvent } from "react";
import { apiCommand } from "../lib/api";

export function LoginForm() {
  const [pending, setPending] = useState(false);
  const hydrated = useSyncExternalStore(() => () => undefined, () => true, () => false);
  const [message, setMessage] = useState("로그인은 로컬 HTTPS 운영자 출처에서만 허용됩니다.");
  const [error, setError] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = form.get("password");
    if (typeof password !== "string" || password.length === 0) return;
    setPending(true);
    setError(false);
    setMessage("로컬 운영자를 확인하는 중…");
    try {
      await apiCommand("/api/v1/session/login", { password }, { csrf: false });
      setMessage("세션이 생성되었습니다. 트레이딩룸을 여는 중…");
      window.location.assign("/");
    } catch (reason: unknown) {
      setError(true);
      setMessage(reason instanceof Error ? reason.message : "로그인이 허용되지 않았습니다.");
    } finally {
      setPending(false);
    }
  }

  return (
    <form className="form-grid" onSubmit={submit}>
      <label htmlFor="operator-password">로컬 운영자 비밀번호
        <input id="operator-password" name="password" type="password" autoComplete="current-password" required disabled={!hydrated || pending} />
      </label>
      <button type="submit" disabled={pending || !hydrated}>{pending ? "로그인 중…" : "안전하게 로그인"}</button>
      <p className={`command-result${error ? " error" : ""}`} role="status" aria-live="polite">{message}</p>
    </form>
  );
}

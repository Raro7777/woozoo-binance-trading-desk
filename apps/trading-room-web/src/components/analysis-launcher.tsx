"use client";

import { useState, useSyncExternalStore } from "react";
import { apiCommand, textValue, type JsonRecord } from "../lib/api";

export function AnalysisLauncher() {
  const [pending, setPending] = useState(false);
  const hydrated = useSyncExternalStore(() => () => undefined, () => true, () => false);
  const [message, setMessage] = useState("Choose an allowlisted Spot symbol. Analysis remains advisory.");
  const [error, setError] = useState(false);

  async function launch(symbol: "BTCUSDT" | "ETHUSDT") {
    setPending(true);
    setError(false);
    setMessage(`Requesting an Evidence-bound ${symbol} analysis…`);
    try {
      const result = await apiCommand<JsonRecord>("/api/v1/analysis-runs", { symbol });
      const runId = textValue(result, "run_id");
      if (runId === undefined) throw new Error("The accepted analysis response did not contain a run ID.");
      setMessage("Analysis accepted. Opening authoritative run state…");
      window.location.assign(`/analysis/${encodeURIComponent(runId)}`);
    } catch (reason: unknown) {
      setError(true);
      setMessage(reason instanceof Error ? reason.message : "Analysis is held.");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="panel full" aria-labelledby="new-analysis">
      <h2 id="new-analysis">Start a Paper analysis</h2>
      <p className="muted">The server binds immutable Evidence, deterministic Risk, and an exact Paper preview.</p>
      <div className="actions">
        <button disabled={pending || !hydrated} onClick={() => void launch("BTCUSDT")}>Analyze BTC / USDT</button>
        <button className="secondary" disabled={pending || !hydrated} onClick={() => void launch("ETHUSDT")}>Analyze ETH / USDT</button>
      </div>
      <p className={`command-result${error ? " error" : ""}`} role="status" aria-live="polite">{message}</p>
    </section>
  );
}

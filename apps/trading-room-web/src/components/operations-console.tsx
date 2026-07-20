"use client";

import { useState } from "react";
import { apiVersionedCommand, asRecord, booleanValue, textValue, versionValue, type JsonRecord } from "../lib/api";
import { ResourceBoundary, useResource } from "./resource";
import { Field, Hold, Panel, Status } from "./ui";

export function OperationsConsole() {
  const resource = useResource<JsonRecord>("/api/v1/kill-switch");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("Kill activation and recovery always require explicit operator confirmation.");
  const [error, setError] = useState(false);

  async function command(state: JsonRecord, action: "activate" | "recover") {
    const actionLabel = action === "activate" ? "activate the Kill Switch" : "recover from the Kill state";
    if (!window.confirm(`Explicitly ${actionLabel}?`)) return;
    const reason = window.prompt(action === "activate" ? "Record the incident reason:" : "Record the verified incident resolution:");
    if (reason === null || reason.trim().length === 0) return;
    setPending(true);
    setError(false);
    setMessage(`Submitting ${action}. No state change is assumed…`);
    try {
      const body: Record<string, unknown> = { reason: reason.trim() };
      if (action === "recover") {
        const activationEventId = textValue(state, "activation_event_id");
        if (activationEventId === undefined) throw new Error("Activation event binding is unavailable. Recovery is held.");
        body.activation_event_id = activationEventId;
        body.incident_reference = reason.trim();
      }
      const result = await apiVersionedCommand<JsonRecord>(`/api/v1/kill-switch/${action}`, body, versionValue(state, "version"));
      setMessage(textValue(result, "message", "status") ?? "Command accepted. Refreshing authoritative state…");
      resource.reload();
    } catch (reasonCaught: unknown) {
      setError(true);
      setMessage(reasonCaught instanceof Error ? reasonCaught.message : "The operations command was not accepted.");
    } finally {
      setPending(false);
    }
  }

  return (
    <ResourceBoundary resource={resource}>{(value) => {
      const state = asRecord(value) ?? {};
      const active = booleanValue(state, "active") === true || textValue(state, "status") === "ACTIVE";
      const resourceVersion = versionValue(state, "version");
      const recoveryAllowed = booleanValue(state, "recovery_allowed") === true;
      return (
        <>
          {active && <Hold>The Kill Switch is ACTIVE. New Paper order creation and fills must remain blocked.</Hold>}
          <div className="grid">
            <Panel title="Kill Switch">
              <Status value={active ? "ACTIVE" : "INACTIVE"} />
              <dl className="field-list">
                <Field label="Resource version" value={textValue(state, "resource_version", "version")} mono />
                <Field label="Activated at" value={textValue(state, "activated_at")} />
                <Field label="Activated by" value={textValue(state, "activated_by")} />
                <Field label="Incident" value={textValue(state, "reason", "incident_reason")} />
              </dl>
            </Panel>
            <section className="panel wide" aria-labelledby="recovery-guards">
              <h2 id="recovery-guards">Recovery guards</h2>
              <dl className="field-list">
                <Field label="Data" value={textValue(state, "data_status")} />
                <Field label="Reconciliation" value={textValue(state, "reconciliation_status")} />
                <Field label="Ledger" value={textValue(state, "ledger_status")} />
                <Field label="Open-order cancellation" value={textValue(state, "cancellation_status")} />
              </dl>
              <p className="muted">Recovery is never automatic. The server alone decides whether every recovery guard is satisfied.</p>
            </section>
            <section className="panel full" aria-labelledby="kill-controls">
              <h2 id="kill-controls">Manual controls</h2>
              <div className="actions">
                <button className="danger" disabled={pending || active || resourceVersion === undefined} onClick={() => void command(state, "activate")}>Activate Kill Switch</button>
                <button disabled={pending || !active || !recoveryAllowed || resourceVersion === undefined} onClick={() => void command(state, "recover")}>Recover after verified resolution</button>
              </div>
              <p className={`command-result${error ? " error" : ""}`} role="status" aria-live="polite">{pending ? "Awaiting authoritative command receipt…" : message}</p>
            </section>
          </div>
        </>
      );
    }}</ResourceBoundary>
  );
}

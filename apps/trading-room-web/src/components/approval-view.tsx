"use client";

import { useState } from "react";
import { apiVersionedCommand, asRecord, textValue, versionValue, type JsonRecord } from "../lib/api";
import { approvalActionIssues, renderedPreviewFields } from "../lib/approval-preview";
import { ResourceBoundary, useResource } from "./resource";
import { Field, Hold, Panel, Status } from "./ui";

function stringReasons(record: JsonRecord | undefined): readonly string[] {
  const value = record?.reason_codes;
  return Array.isArray(value) ? value.filter((reason): reason is string => typeof reason === "string") : [];
}

function approvalBody(view: JsonRecord, decision: "APPROVE" | "REJECT", reason: string): JsonRecord {
  return {
    decision,
    proposal_id: textValue(view, "proposal_id"),
    paper_order_preview_hash: textValue(view, "paper_order_preview_hash"),
    reason,
  };
}

export function ApprovalView({ proposalId }: Readonly<{ proposalId: string }>) {
  const resource = useResource<JsonRecord>(`/api/v1/proposals/${encodeURIComponent(proposalId)}/approval-view`);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("Review every server-bound value before deciding.");
  const [error, setError] = useState(false);

  async function decide(view: JsonRecord, decision: "APPROVE" | "REJECT") {
    if (decision === "APPROVE" && !window.confirm("Approve this exact Paper order preview? The authorization may be attempted once.")) return;
    const reason = decision === "REJECT" ? window.prompt("Record a rejection reason:") : undefined;
    if (decision === "REJECT" && (typeof reason !== "string" || reason.trim().length === 0)) return;
    setPending(true);
    setError(false);
    setMessage("Submitting the bound decision. No success is assumed…");
    try {
      const commandReason = decision === "APPROVE" ? "OPERATOR_APPROVED_EXACT_PREVIEW" : reason!.trim();
      const result = await apiVersionedCommand<JsonRecord>("/api/v1/paper-approvals", approvalBody(view, decision, commandReason), versionValue(view, "view_version"));
      setMessage(textValue(result, "message", "status", "result") ?? "Command accepted. Refreshing authoritative state…");
      resource.reload();
    } catch (reasonCaught: unknown) {
      setError(true);
      setMessage(reasonCaught instanceof Error ? reasonCaught.message : "The decision was not accepted.");
    } finally {
      setPending(false);
    }
  }

  async function revoke(view: JsonRecord) {
    const approvalId = textValue(view, "approval_id");
    if (approvalId === undefined || !window.confirm("Revoke this approval? Existing audit history will remain.")) return;
    setPending(true);
    setError(false);
    setMessage("Submitting revocation. No success is assumed…");
    try {
      const result = await apiVersionedCommand<JsonRecord>(`/api/v1/paper-approvals/${encodeURIComponent(approvalId)}/revocations`, {
        reason: "OPERATOR_REVOKED",
      }, versionValue(view, "view_version"));
      setMessage(textValue(result, "message", "status", "result") ?? "Revocation accepted. Refreshing authoritative state…");
      resource.reload();
    } catch (reasonCaught: unknown) {
      setError(true);
      setMessage(reasonCaught instanceof Error ? reasonCaught.message : "The revocation was not accepted.");
    } finally {
      setPending(false);
    }
  }

  return (
    <ResourceBoundary resource={resource}>{(value) => {
      const view = asRecord(value) ?? {};
      const preview = asRecord(view.paper_order_preview);
      const viewStatus = textValue(view, "status") ?? "INVALID";
      const riskStatus = textValue(view, "risk_verdict") ?? "UNKNOWN";
      const approvalStatus = textValue(view, "approval_status") ?? "PENDING";
      const authorizationStatus = textValue(view, "authorization_status") ?? "NOT_ISSUED";
      const resourceVersion = versionValue(view, "view_version");
      const previewIssues = approvalActionIssues(view);
      const actionEnabled = viewStatus === "READY" && view.approval_action_allowed === true && riskStatus === "ALLOWED" && resourceVersion !== undefined && previewIssues.length === 0;
      const reasons = stringReasons(view);
      return (
        <>
          {!actionEnabled && <Hold>This proposal is not READY for approval. Server reasons and safety state are authoritative.</Hold>}
          {previewIssues.length > 0 && <Hold>Exact preview validation failed: {previewIssues.join("; ")}</Hold>}
          <div className="grid">
            <Panel title="Decision state">
              <Status value={viewStatus} />
              <dl className="field-list">
                <Field label="Proposal ID" value={textValue(view, "proposal_id") ?? proposalId} mono />
                <Field label="Proposal hash" value={textValue(view, "proposal_hash")} mono />
                <Field label="View version" value={textValue(view, "view_version")} mono />
                <Field label="TTL seconds" value={textValue(view, "approval_ttl_seconds")} mono />
                <Field label="Expires" value={textValue(view, "approval_expires_at")} />
              </dl>
            </Panel>
            <section className="panel" aria-labelledby="risk-binding">
              <h2 id="risk-binding">Risk binding</h2>
              <Status value={riskStatus} />
              <dl className="field-list">
                <Field label="Decision ID" value={textValue(view, "risk_decision_id")} mono />
                <Field label="Decision hash" value={textValue(view, "risk_decision_hash")} mono />
                <Field label="Input digest" value={textValue(view, "risk_input_digest")} mono />
                <Field label="Policy version" value={textValue(view, "risk_policy_version")} mono />
              </dl>
            </section>
            <section className="panel" aria-labelledby="authorization-state">
              <h2 id="authorization-state">Approval &amp; authorization</h2>
              <p><Status value={approvalStatus} /> <Status value={authorizationStatus} /></p>
              <dl className="field-list">
                <Field label="Approval ID" value={textValue(view, "approval_id")} mono />
                <Field label="Authorization ID" value={textValue(view, "authorization_id")} mono />
                <Field label="Served at" value={textValue(view, "served_at")} />
              </dl>
            </section>
            <section className="panel wide" aria-labelledby="exact-order-preview">
              <h2 id="exact-order-preview">Exact Paper order preview</h2>
              <p className="muted">Values below are displayed verbatim from the server. The browser performs no financial calculation.</p>
              <dl className="field-list">
                {renderedPreviewFields.map(([label, read]) => <Field key={label} label={label} value={preview === undefined ? undefined : read(preview)} mono />)}
                <Field label="Bound preview hash" value={textValue(view, "paper_order_preview_hash")} mono />
              </dl>
            </section>
            <section className="panel" aria-labelledby="decision-reasons">
              <h2 id="decision-reasons">Reasons</h2>
              {reasons.length === 0 ? <p className="muted">No reason codes supplied.</p> : <ul className="reasons">{reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
            </section>
            <section className="panel full" aria-labelledby="operator-decision">
              <h2 id="operator-decision">Operator decision</h2>
              <div className="actions">
                <button disabled={pending || !actionEnabled} onClick={() => void decide(view, "APPROVE")}>Approve exact preview</button>
                <button className="secondary" disabled={pending || !actionEnabled} onClick={() => void decide(view, "REJECT")}>Reject</button>
                <button className="danger" disabled={pending || approvalStatus !== "APPROVED" || resourceVersion === undefined} onClick={() => void revoke(view)}>Revoke approval</button>
              </div>
              <p className={`command-result${error ? " error" : ""}`} role="status" aria-live="polite">{pending ? "Awaiting authoritative command receipt…" : message}</p>
            </section>
          </div>
        </>
      );
    }}</ResourceBoundary>
  );
}

"use client";

import Link from "next/link";
import { asRecord, asRecords, textValue, type JsonRecord } from "../lib/api";
import { ResourceBoundary, useResource } from "./resource";
import { Field, Panel, Status } from "./ui";

export function AnalysisView({ runId }: Readonly<{ runId: string }>) {
  const resource = useResource<JsonRecord>(`/api/v1/analysis-runs/${encodeURIComponent(runId)}`);
  return (
    <ResourceBoundary resource={resource}>{(value) => {
      const run = asRecord(value);
      const proposal = asRecord(run?.proposal);
      const report = asRecord(run?.report);
      const evidence = asRecord(run?.evidence);
      const claims = asRecords(report?.claims);
      const proposalId = textValue(proposal, "proposal_id", "id") ?? textValue(run, "proposal_id");
      return (
        <div className="grid">
          <Panel title="Analysis state">
            <Status value={textValue(run, "status", "outcome") ?? "UNKNOWN"} />
            <dl className="field-list">
              <Field label="Run ID" value={textValue(run, "run_id", "id") ?? runId} mono />
              <Field label="Provider" value={textValue(run, "provider")} />
              <Field label="Started" value={textValue(run, "started_at")} />
              <Field label="Completed" value={textValue(run, "completed_at")} />
            </dl>
          </Panel>
          <section className="panel wide" aria-labelledby="evidence-binding">
            <h2 id="evidence-binding">Immutable Evidence binding</h2>
            <dl className="field-list">
              <Field label="Evidence ID" value={textValue(evidence, "evidence_id", "id") ?? textValue(run, "evidence_id")} mono />
              <Field label="Evidence digest" value={textValue(evidence, "digest", "evidence_digest")} mono />
              <Field label="As of" value={textValue(evidence, "as_of")} />
              <Field label="Knowledge cutoff" value={textValue(evidence, "knowledge_cutoff")} />
            </dl>
          </section>
          <section className="panel full" aria-labelledby="structured-report">
            <h2 id="structured-report">Structured report</h2>
            {textValue(report, "summary") !== undefined && <p>{textValue(report, "summary")}</p>}
            {claims.length === 0 ? <p className="muted">No claim list is available.</p> : (
              <ol>{claims.map((claim, index) => <li key={textValue(claim, "claim_id") ?? index}>{textValue(claim, "text", "claim") ?? "Claim unavailable"}</li>)}</ol>
            )}
          </section>
          <section className="panel full" aria-labelledby="proposal-binding">
            <h2 id="proposal-binding">Trade Proposal</h2>
            <dl className="field-list">
              <Field label="Proposal ID" value={proposalId} mono />
              <Field label="Proposal hash" value={textValue(proposal, "proposal_hash", "hash")} mono />
              <Field label="Risk decision ID" value={textValue(run, "risk_decision_id")} mono />
              <Field label="Status" value={textValue(proposal, "status") ?? (proposalId === undefined ? undefined : "CREATED")} />
            </dl>
            {proposalId !== undefined && <div className="actions"><Link className="button" href={`/proposals/${encodeURIComponent(proposalId)}`}>Open approval view</Link></div>}
          </section>
        </div>
      );
    }}</ResourceBoundary>
  );
}

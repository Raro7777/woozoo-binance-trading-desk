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
      const proposalStatus = textValue(proposal, "status") ?? (proposalId === undefined ? "UNKNOWN" : "CREATED");
      const provider = textValue(run, "provider");
      const providerLabel = provider === "mock" ? "모의 제공자" : provider === undefined ? undefined : `알 수 없는 제공자 (${provider})`;
      return (
        <div className="grid">
          <Panel title="분석 상태">
            <Status value={textValue(run, "status", "outcome") ?? "UNKNOWN"} />
            <dl className="field-list">
              <Field label="실행 ID" value={textValue(run, "run_id", "id") ?? runId} mono />
              <Field label="제공자" value={providerLabel} />
              <Field label="시작 시각" value={textValue(run, "started_at")} />
              <Field label="완료 시각" value={textValue(run, "completed_at")} />
            </dl>
          </Panel>
          <section className="panel wide" aria-labelledby="evidence-binding">
            <h2 id="evidence-binding">변경 불가능한 근거 결합</h2>
            <dl className="field-list">
              <Field label="근거 ID" value={textValue(evidence, "evidence_id", "id") ?? textValue(run, "evidence_id")} mono />
              <Field label="근거 다이제스트" value={textValue(evidence, "digest", "evidence_digest")} mono />
              <Field label="기준 시각" value={textValue(evidence, "as_of")} />
              <Field label="지식 기준 시각" value={textValue(evidence, "knowledge_cutoff")} />
            </dl>
          </section>
          <section className="panel full" aria-labelledby="structured-report">
            <h2 id="structured-report">구조화 보고서</h2>
            {textValue(report, "summary") !== undefined && <p>{textValue(report, "summary")}</p>}
            {claims.length === 0 ? <p className="muted">확인할 주장 목록이 없습니다.</p> : (
              <ol>{claims.map((claim, index) => <li key={textValue(claim, "claim_id") ?? index}>{textValue(claim, "text", "claim") ?? "주장 정보 없음"}</li>)}</ol>
            )}
          </section>
          <section className="panel full" aria-labelledby="proposal-binding">
            <h2 id="proposal-binding">거래 제안</h2>
            <dl className="field-list">
              <Field label="제안 ID" value={proposalId} mono />
              <Field label="제안 해시" value={textValue(proposal, "proposal_hash", "hash")} mono />
              <Field label="위험 판단 ID" value={textValue(run, "risk_decision_id")} mono />
              <div className="field"><dt>상태</dt><dd><Status value={proposalStatus} /></dd></div>
            </dl>
            {proposalId !== undefined && <div className="actions"><Link className="button" href={`/proposals/${encodeURIComponent(proposalId)}`}>승인 화면 열기</Link></div>}
          </section>
        </div>
      );
    }}</ResourceBoundary>
  );
}

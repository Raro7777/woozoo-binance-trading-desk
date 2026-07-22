"use client";

import { useState } from "react";
import { apiVersionedCommand, asRecord, textValue, versionValue, type JsonRecord } from "../lib/api";
import { approvalActionIssues, renderedPreviewFields } from "../lib/approval-preview";
import { ResourceBoundary, useResource } from "./resource";
import { CommandDialog } from "./command-dialog";
import { diagnosticLabel, Field, Hold, Panel, Status } from "./ui";

type ApprovalDialog = Readonly<{
  action: "APPROVE" | "REJECT" | "REVOKE";
  view: JsonRecord;
}>;

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

export function approvalActionAvailability(view: JsonRecord): Readonly<{
  approve: boolean;
  reject: boolean;
}> {
  const viewStatus = textValue(view, "status") ?? "INVALID";
  const riskStatus = textValue(view, "risk_verdict") ?? "UNKNOWN";
  const resourceVersion = versionValue(view, "view_version");
  const commonBindingsValid = riskStatus === "ALLOWED"
    && resourceVersion !== undefined
    && approvalActionIssues(view).length === 0;
  return {
    approve: commonBindingsValid
      && viewStatus === "READY"
      && view.approve_action_allowed === true,
    reject: commonBindingsValid && view.reject_action_allowed === true,
  };
}

export function ApprovalView({ proposalId }: Readonly<{ proposalId: string }>) {
  const resource = useResource<JsonRecord>(`/api/v1/proposals/${encodeURIComponent(proposalId)}/approval-view`);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("결정하기 전에 서버에 결합된 모든 값을 검토하세요.");
  const [error, setError] = useState(false);
  const [dialog, setDialog] = useState<ApprovalDialog>();

  async function decide(view: JsonRecord, decision: "APPROVE" | "REJECT", reason: string) {
    setPending(true);
    setError(false);
    setMessage("결합된 결정을 제출하는 중입니다. 성공으로 미리 간주하지 않습니다…");
    try {
      const commandReason = decision === "APPROVE" ? "OPERATOR_APPROVED_EXACT_PREVIEW" : reason;
      await apiVersionedCommand<JsonRecord>("/api/v1/paper-approvals", approvalBody(view, decision, commandReason), versionValue(view, "view_version"));
      setMessage("결정이 접수되었습니다. 서버 확정 상태를 새로 고치는 중…");
      resource.reload();
    } catch (reasonCaught: unknown) {
      setError(true);
      setMessage(reasonCaught instanceof Error ? reasonCaught.message : "결정이 접수되지 않았습니다.");
    } finally {
      setPending(false);
    }
  }

  async function revoke(view: JsonRecord) {
    const approvalId = textValue(view, "approval_id");
    if (approvalId === undefined) return;
    setPending(true);
    setError(false);
    setMessage("철회를 제출하는 중입니다. 성공으로 미리 간주하지 않습니다…");
    try {
      await apiVersionedCommand<JsonRecord>(`/api/v1/paper-approvals/${encodeURIComponent(approvalId)}/revocations`, {
        reason: "OPERATOR_REVOKED",
      }, versionValue(view, "view_version"));
      setMessage("철회가 접수되었습니다. 서버 확정 상태를 새로 고치는 중…");
      resource.reload();
    } catch (reasonCaught: unknown) {
      setError(true);
      setMessage(reasonCaught instanceof Error ? reasonCaught.message : "철회가 접수되지 않았습니다.");
    } finally {
      setPending(false);
    }
  }

  function confirmDialog(reason: string) {
    const current = dialog;
    setDialog(undefined);
    if (current === undefined) return;
    if (current.action === "REVOKE") void revoke(current.view);
    else void decide(current.view, current.action, reason);
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
      const actionAvailability = approvalActionAvailability(view);
      const reasons = stringReasons(view);
      return (
        <>
          {!actionAvailability.approve && <Hold>이 제안은 승인 준비 상태가 아닙니다. 서버의 사유와 안전 상태를 따릅니다.</Hold>}
          {previewIssues.length > 0 && <Hold>정확한 미리보기 검증 실패: {previewIssues.join("; ")}</Hold>}
          <div className="grid">
            <Panel title="결정 상태">
              <Status value={viewStatus} />
              <dl className="field-list">
                <Field label="제안 ID" value={textValue(view, "proposal_id") ?? proposalId} mono />
                <Field label="제안 해시" value={textValue(view, "proposal_hash")} mono />
                <Field label="화면 버전" value={textValue(view, "view_version")} mono />
                <Field label="유효 시간(초)" value={textValue(view, "approval_ttl_seconds")} mono />
                <Field label="만료 시각" value={textValue(view, "approval_expires_at")} />
              </dl>
            </Panel>
            <section className="panel" aria-labelledby="risk-binding">
              <h2 id="risk-binding">위험 판단 결합</h2>
              <Status value={riskStatus} />
              <dl className="field-list">
                <Field label="판단 ID" value={textValue(view, "risk_decision_id")} mono />
                <Field label="판단 해시" value={textValue(view, "risk_decision_hash")} mono />
                <Field label="입력 다이제스트" value={textValue(view, "risk_input_digest")} mono />
                <Field label="정책 버전" value={textValue(view, "risk_policy_version")} mono />
              </dl>
            </section>
            <section className="panel" aria-labelledby="authorization-state">
              <h2 id="authorization-state">승인 및 실행 권한</h2>
              <p><Status value={approvalStatus} /> <Status value={authorizationStatus} /></p>
              <dl className="field-list">
                <Field label="승인 ID" value={textValue(view, "approval_id")} mono />
                <Field label="실행 권한 ID" value={textValue(view, "authorization_id")} mono />
                <Field label="응답 시각" value={textValue(view, "served_at")} />
              </dl>
            </section>
            <section
              className="panel wide"
              aria-labelledby="exact-order-preview"
              data-canonical-preview={preview === undefined ? undefined : JSON.stringify(preview)}
            >
              <h2 id="exact-order-preview">정확한 모의주문 미리보기</h2>
              <p className="muted">아래 값은 서버 원문 그대로 표시됩니다. 브라우저는 금융 계산을 수행하지 않습니다.</p>
              <dl className="field-list">
                {renderedPreviewFields.map(([label, read]) => <Field key={label} label={label} value={preview === undefined ? undefined : read(preview)} mono />)}
                <Field label="결합된 미리보기 해시" value={textValue(view, "paper_order_preview_hash")} mono />
              </dl>
            </section>
            <section className="panel" aria-labelledby="decision-reasons">
              <h2 id="decision-reasons">사유</h2>
              {reasons.length === 0 ? <p className="muted">제공된 사유 코드가 없습니다.</p> : <ul className="reasons">{reasons.map((reason) => <li key={reason}>{diagnosticLabel(reason)}</li>)}</ul>}
            </section>
            <section className="panel full" aria-labelledby="operator-decision">
              <h2 id="operator-decision">운영자 결정</h2>
              <div className="actions">
                <button disabled={pending || !actionAvailability.approve} onClick={() => setDialog({ action: "APPROVE", view })}>정확한 미리보기 승인</button>
                <button className="secondary" disabled={pending || !actionAvailability.reject} onClick={() => setDialog({ action: "REJECT", view })}>거절</button>
                <button className="danger" disabled={pending || approvalStatus !== "APPROVED" || resourceVersion === undefined} onClick={() => setDialog({ action: "REVOKE", view })}>승인 철회</button>
              </div>
              <p className={`command-result${error ? " error" : ""}`} role="status" aria-live="polite">{pending ? "서버 확정 명령 처리 결과를 기다리는 중…" : message}</p>
            </section>
          </div>
          <CommandDialog
            open={dialog !== undefined}
            title={dialog?.action === "APPROVE" ? "정확한 모의주문 승인" : dialog?.action === "REJECT" ? "모의주문 거절" : "승인 철회"}
            description={dialog?.action === "APPROVE"
              ? "표시된 모의주문 미리보기에 정확히 결합된 일회성 실행 권한을 발급합니다. 실행 권한은 한 번만 시도할 수 있습니다."
              : dialog?.action === "REJECT"
                ? "거절 사유는 변경할 수 없는 감사 기록에 남습니다."
                : "완료된 승인 감사 이력은 유지되며, 발급된 실행 권한만 철회됩니다."}
            confirmLabel={dialog?.action === "APPROVE" ? "승인 제출" : dialog?.action === "REJECT" ? "거절 제출" : "철회 제출"}
            reasonLabel={dialog?.action === "REJECT" ? "거절 사유" : undefined}
            reasonRequired={dialog?.action === "REJECT"}
            danger={dialog?.action !== "APPROVE"}
            onCancel={() => setDialog(undefined)}
            onConfirm={confirmDialog}
          />
        </>
      );
    }}</ResourceBoundary>
  );
}

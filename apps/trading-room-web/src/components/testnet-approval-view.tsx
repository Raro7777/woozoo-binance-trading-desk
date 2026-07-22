"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { apiVersionedCommand, asRecord, booleanValue, textValue, versionValue, type JsonRecord } from "../lib/api";
import { newIntentKey, stringList, testnetApprovalView } from "../lib/testnet";
import { CommandDialog } from "./command-dialog";
import { ResourceBoundary, useResource } from "./resource";
import { diagnosticLabel, Field, Hold, Status } from "./ui";

type Decision = "APPROVE" | "REJECT";

export function TestnetApprovalView({ proposalId }: Readonly<{ proposalId: string }>) {
  const resource = useResource<JsonRecord>(`/api/v1/proposals/${encodeURIComponent(proposalId)}/testnet-approval-view`);
  const intentKey = useRef<string | undefined>(undefined);
  const [decision, setDecision] = useState<Decision>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(false);
  const [message, setMessage] = useState("제안과 별도로 생성된 Testnet 미리보기의 모든 값을 확인하세요.");

  async function submit(view: JsonRecord, nextDecision: Decision, reason: string) {
    intentKey.current = newIntentKey(intentKey.current);
    setPending(true);
    setError(false);
    setMessage("승인 의도를 기록하는 중입니다. 외부 주문이 제출됐다고 미리 간주하지 않습니다…");
    try {
      await apiVersionedCommand<JsonRecord>("/api/v1/testnet-approvals", {
        proposal_id: proposalId,
        decision: nextDecision,
        testnet_order_preview_digest: textValue(view, "testnet_order_preview_digest"),
        approval_input_digest: textValue(view, "approval_input_digest"),
        reason: nextDecision === "APPROVE" ? "OPERATOR_APPROVED_EXACT_TESTNET_PREVIEW" : reason,
      }, versionValue(view, "view_version"), intentKey.current);
      intentKey.current = undefined;
      setMessage("결정이 접수되었습니다. 서버 확정 상태를 다시 확인합니다.");
      resource.reload();
    } catch (caught: unknown) {
      setError(true);
      setMessage(caught instanceof Error ? caught.message : "Testnet 승인 의도가 접수되지 않았습니다.");
    } finally {
      setPending(false);
    }
  }

  return <ResourceBoundary resource={resource}>{(value) => {
    const view = testnetApprovalView(value);
    if (view === undefined) return <Hold>알 수 없는 Testnet 승인 형식입니다. 승인·거절을 모두 차단했습니다.</Hold>;
    const preview = asRecord(view.testnet_order_preview) ?? {};
    const authority = asRecord(view.approval_authority) ?? {};
    const risk = textValue(view, "risk_verdict") ?? "UNKNOWN";
    const approval = textValue(view, "approval_decision") ?? "PENDING";
    const authorization = textValue(view, "authorization_status") ?? "NOT_ISSUED";
    const canApprove = risk === "ALLOWED" && booleanValue(view, "approve_action_allowed") === true && versionValue(view, "view_version") !== undefined;
    const canReject = booleanValue(view, "reject_action_allowed") === true && versionValue(view, "view_version") !== undefined;
    const reasons = stringList(view, "reason_codes");
    const executionId = textValue(view, "execution_id");
    return <>
      {!canApprove && <Hold>이 제안은 Spot Testnet 승인 준비 상태가 아닙니다. 서버의 위험 판단과 안전 차단을 따릅니다.</Hold>}
      <div className="grid">
        <section className="panel" aria-labelledby="testnet-proposal"><h2 id="testnet-proposal">제안·위험 결합</h2><p><Status value={risk} /></p><dl className="field-list"><Field label="제안 ID" value={textValue(view, "proposal_id")} mono /><Field label="위험 판단 ID" value={textValue(view, "risk_decision_id")} mono /><Field label="위험 판단 해시" value={textValue(view, "risk_decision_hash")} mono /><Field label="화면 버전" value={textValue(view, "view_version")} mono /></dl></section>
        <section className="panel" aria-labelledby="testnet-decision"><h2 id="testnet-decision">승인 상태</h2><p><Status value={approval} /> <Status value={authorization} /></p><dl className="field-list"><Field label="승인 ID" value={textValue(view, "approval_id")} mono /><Field label="실행 권한 ID" value={textValue(view, "authorization_id")} mono /><Field label="승인 유효 시간" value="최대 300초" /><Field label="응답 시각" value={textValue(view, "served_at")} /></dl></section>
        <section className="panel" aria-labelledby="testnet-view-reasons"><h2 id="testnet-view-reasons">차단 사유</h2>{reasons.length === 0 ? <p className="muted">서버가 보고한 차단 사유가 없습니다.</p> : <ul className="reasons">{reasons.map((reason) => <li key={reason}>{diagnosticLabel(reason)}</li>)}</ul>}</section>
        <section className="panel full" aria-labelledby="testnet-preview" data-canonical-preview={JSON.stringify(preview)}><h2 id="testnet-preview">정확한 Spot Testnet 주문 미리보기</h2><p className="muted">서버가 계산한 문자열 값을 그대로 표시합니다. 브라우저는 가격·수량·수수료를 다시 계산하지 않습니다.</p><dl className="field-list preview-grid"><Field label="환경" value="Binance Spot Testnet" /><Field label="종목" value={textValue(preview, "symbol")} mono /><Field label="방향" value={textValue(preview, "side") === "BUY" ? "매수" : "매도"} /><Field label="주문 유형" value="지정가" /><Field label="유효 방식" value="취소 전까지 유효" /><Field label="수량" value={textValue(preview, "quantity")} mono /><Field label="지정 가격" value={textValue(preview, "limit_price")} mono /><Field label="최악 명목금액" value={textValue(preview, "worst_case_notional")} mono /><Field label="수수료 예비금" value={textValue(preview, "fee_reserve")} mono /><Field label="클라이언트 주문 ID" value={textValue(preview, "client_order_id")} mono /><Field label="생성 시각" value={textValue(preview, "created_at")} /><Field label="만료 시각" value={textValue(preview, "expires_at")} /><Field label="미리보기 다이제스트" value={textValue(view, "testnet_order_preview_digest")} mono /><Field label="승인 입력 다이제스트" value={textValue(view, "approval_input_digest")} mono /></dl></section>
        <section className="panel full" aria-labelledby="testnet-approval-authority" data-canonical-authority={JSON.stringify(authority)}><h2 id="testnet-approval-authority">승인 권위 스냅샷</h2><p className="muted">이 값 전체가 승인 입력 다이제스트에 결합됩니다. 승인 뒤 하나라도 바뀌면 실행 서비스가 명령을 차단합니다.</p><dl className="field-list preview-grid"><Field label="계정 결합 ID" value={textValue(authority, "account_binding_id")} mono /><Field label="계정 세대 ID" value={textValue(authority, "generation_id")} mono /><Field label="계정 세대" value={String(versionValue(authority, "account_generation") ?? "확인 불가")} /><Field label="세대 버전" value={String(versionValue(authority, "generation_version") ?? "확인 불가")} /><Field label="활성화 ID" value={textValue(authority, "activation_id")} mono /><Field label="활성화 상태" value={textValue(authority, "activation_status")} /><Field label="활성화 버전" value={String(versionValue(authority, "activation_version") ?? "확인 불가")} /><Field label="활성화 만료" value={textValue(authority, "activation_expires_at")} /><Field label="Gateway 인스턴스" value={textValue(authority, "gateway_instance_id")} mono /><Field label="빌드 다이제스트" value={textValue(authority, "build_digest")} mono /><Field label="구성 다이제스트" value={textValue(authority, "configuration_digest")} mono /><Field label="허용 목록 다이제스트" value={textValue(authority, "allowlist_digest")} mono /><Field label="Paper 긴급 중지" value={booleanValue(authority, "paper_kill_active") === false ? "비활성" : "활성 또는 확인 불가"} /><Field label="Paper 긴급 중지 버전" value={String(versionValue(authority, "paper_kill_version") ?? "확인 불가")} /><Field label="Testnet 안전 차단" value={booleanValue(authority, "testnet_barrier_active") === false ? "비활성" : "활성 또는 확인 불가"} /><Field label="Testnet 안전 차단 버전" value={String(versionValue(authority, "testnet_barrier_version") ?? "확인 불가")} /><Field label="조정 체크포인트 ID" value={textValue(authority, "reconciliation_checkpoint_id")} mono /><Field label="조정 체크포인트 다이제스트" value={textValue(authority, "reconciliation_checkpoint_digest")} mono /><Field label="조정 상태" value={textValue(authority, "reconciliation_status")} /><Field label="조정 버전" value={String(versionValue(authority, "reconciliation_version") ?? "확인 불가")} /><Field label="조정 생성 시각" value={textValue(authority, "checkpoint_created_at")} /><Field label="조정 워터마크" value={textValue(authority, "checkpoint_watermark_at")} /><Field label="원장 버전" value={String(versionValue(authority, "ledger_version") ?? "확인 불가")} /><Field label="원장 스냅샷 다이제스트" value={textValue(authority, "ledger_snapshot_digest")} mono /></dl></section>
        <section className="panel full" aria-labelledby="testnet-human-decision"><h2 id="testnet-human-decision">별도 Testnet 결정</h2><p className="muted">모의투자 승인은 재사용되지 않습니다. 승인 후 실행 서비스가 모든 결합과 최신 상태를 다시 검사합니다.</p><div className="actions"><button disabled={pending || !canApprove} onClick={() => setDecision("APPROVE")}>이 Testnet 미리보기 승인</button><button className="secondary" disabled={pending || !canReject} onClick={() => setDecision("REJECT")}>Testnet 실행 거절</button>{executionId !== undefined && <Link className="button secondary" href={`/testnet/executions/${encodeURIComponent(executionId)}`}>실행 상태 보기</Link>}</div><p className={`command-result${error ? " error" : ""}`} role="status" aria-live="polite">{pending ? "서버 확정 결과를 기다리는 중…" : message}</p></section>
      </div>
      <CommandDialog open={decision !== undefined} title={decision === "APPROVE" ? "정확한 Testnet 미리보기 승인" : "Testnet 실행 거절"} description={decision === "APPROVE" ? "표시된 환경·계정 세대·제안·위험 판단·수량·가격·클라이언트 주문 ID에 결합된 일회성 권한을 요청합니다." : "거절 사유는 변경할 수 없는 Testnet 감사 기록에 남습니다."} confirmLabel={decision === "APPROVE" ? "별도 승인 제출" : "거절 제출"} reasonLabel={decision === "REJECT" ? "거절 사유" : undefined} reasonRequired={decision === "REJECT"} danger={decision === "REJECT"} onCancel={() => setDecision(undefined)} onConfirm={(reason) => { const current = decision; setDecision(undefined); if (current !== undefined) void submit(view, current, reason); }} />
    </>;
  }}</ResourceBoundary>;
}

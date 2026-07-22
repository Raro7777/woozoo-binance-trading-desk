"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { apiVersionedCommand, booleanValue, textValue, versionValue, type JsonRecord } from "../lib/api";
import { newIntentKey, stringList, testnetOperatorState } from "../lib/testnet";
import { CommandDialog } from "./command-dialog";
import { ResourceBoundary, useResource } from "./resource";
import { diagnosticLabel, Field, Hold, Status } from "./ui";

type RequestedAction = Readonly<{ action: "activate" | "deactivate" | "confirm-reset"; state: JsonRecord }>;

export function TestnetDesk() {
  const resource = useResource<JsonRecord>("/api/v1/testnet/operator-state");
  const intentKey = useRef<string | undefined>(undefined);
  const [request, setRequest] = useState<RequestedAction>();
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("Testnet은 기본 차단 상태이며 운영자의 별도 활성화가 필요합니다.");
  const [error, setError] = useState(false);

  async function submit(action: RequestedAction["action"], state: JsonRecord, reason: string) {
    const expectedVersion = versionValue(state, "activation_version");
    intentKey.current = newIntentKey(intentKey.current);
    setPending(true);
    setError(false);
    setMessage("의도를 기록하는 중입니다. 외부 효과가 발생했다고 미리 간주하지 않습니다…");
    try {
      if (action === "activate") {
        await apiVersionedCommand<JsonRecord>("/api/v1/testnet-activations", {
          activation_view_digest: textValue(state, "activation_view_digest"),
          reason,
        }, expectedVersion, intentKey.current);
      } else if (action === "deactivate") {
        const activationId = textValue(state, "activation_id");
        if (activationId === undefined) throw new Error("활성화 ID를 확정할 수 없어 비활성화가 보류되었습니다.");
        await apiVersionedCommand<JsonRecord>(`/api/v1/testnet-activations/${encodeURIComponent(activationId)}/deactivations`, { reason }, expectedVersion, intentKey.current);
      } else {
        const checkpointId = textValue(state, "reconciliation_checkpoint_id");
        const checkpointDigest = textValue(state, "reconciliation_checkpoint_digest");
        if (checkpointId === undefined || checkpointDigest === undefined) throw new Error("초기화 확인에 필요한 대사 체크포인트를 확정할 수 없습니다.");
        await apiVersionedCommand<JsonRecord>(`/api/v1/testnet-reconciliation/${encodeURIComponent(checkpointId)}/confirmations`, {
          checkpoint_digest: checkpointDigest,
          reason,
        }, expectedVersion, intentKey.current);
      }
      intentKey.current = undefined;
      setMessage("의도가 접수되었습니다. 서버 확정 상태를 다시 확인합니다.");
      resource.reload();
    } catch (caught: unknown) {
      setError(true);
      setMessage(caught instanceof Error ? caught.message : "Testnet 운영 의도가 접수되지 않았습니다.");
    } finally {
      setPending(false);
    }
  }

  return <ResourceBoundary resource={resource}>{(value) => {
    const state = testnetOperatorState(value);
    if (state === undefined) return <Hold>알 수 없는 Testnet 상태 형식입니다. 모든 효과 작업을 차단했습니다.</Hold>;
    const reasons = stringList(state, "reason_codes");
    const activation = textValue(state, "activation_status") ?? "UNKNOWN";
    const gateway = textValue(state, "gateway_health") ?? "UNKNOWN";
    const barrier = textValue(state, "testnet_barrier_status") ?? "UNKNOWN";
    const reconciliation = textValue(state, "reconciliation_status") ?? "UNKNOWN";
    const commandsAllowed = booleanValue(state, "new_commands_allowed") === true;
    const activateAllowed = booleanValue(state, "activate_action_allowed") === true;
    const deactivateAllowed = booleanValue(state, "deactivate_action_allowed") === true;
    const resetAllowed = booleanValue(state, "reset_confirmation_allowed") === true;
    const safeVersion = versionValue(state, "activation_version") !== undefined;
    return <>
      {!commandsAllowed && <Hold>Spot Testnet 신규 명령은 차단되어 있습니다. 아래 서버 사유가 해소되기 전에는 승인·실행할 수 없습니다.</Hold>}
      <div className="grid">
        <section className="panel" aria-labelledby="testnet-activation"><h2 id="testnet-activation">게이트웨이 활성화</h2><p><Status value={activation} /> <Status value={gateway} /></p><dl className="field-list"><Field label="환경" value="Binance Spot Testnet" /><Field label="계정" value={textValue(state, "account_label")} /><Field label="계정 세대" value={textValue(state, "account_generation")} mono /><Field label="활성화 버전" value={textValue(state, "activation_version")} mono /><Field label="계정 결합 ID" value={textValue(state, "account_binding_id")} mono /></dl></section>
        <section className="panel" aria-labelledby="testnet-safety"><h2 id="testnet-safety">안전 경계</h2><p><Status value={barrier} /> <Status value={reconciliation} /></p><dl className="field-list"><Field label="Testnet 장벽" value={barrier === "ACTIVE" ? "활성 · 신규 명령 차단" : "비활성"} /><Field label="모의투자 킬 스위치" value={booleanValue(state, "paper_kill_active") === true ? "활성" : "비활성"} /><Field label="신규 명령" value={commandsAllowed ? "서버 허용" : "서버 차단"} /><Field label="응답 시각" value={textValue(state, "served_at")} /></dl></section>
        <section className="panel" aria-labelledby="testnet-reasons"><h2 id="testnet-reasons">차단 사유</h2>{reasons.length === 0 ? <p className="muted">서버가 보고한 차단 사유가 없습니다.</p> : <ul className="reasons">{reasons.map((reason) => <li key={reason}>{diagnosticLabel(reason)}</li>)}</ul>}</section>
        <section className="panel full" aria-labelledby="testnet-controls"><h2 id="testnet-controls">운영자 제어</h2><p className="muted">이 화면은 활성화·비활성화·초기화 확인 의도만 기록합니다. 주문 제출·재시도·교체·계정 조회 기능은 없습니다.</p><div className="actions"><button disabled={pending || !activateAllowed || !safeVersion} onClick={() => setRequest({ action: "activate", state })}>Spot Testnet 활성화 요청</button><button className="danger" disabled={pending || !deactivateAllowed || !safeVersion} onClick={() => setRequest({ action: "deactivate", state })}>즉시 비활성화</button><button className="secondary" disabled={pending || !resetAllowed || !safeVersion} onClick={() => setRequest({ action: "confirm-reset", state })}>계정 초기화 확인</button></div><p className={`command-result${error ? " error" : ""}`} role="status" aria-live="polite">{pending ? "서버 확정 결과를 기다리는 중…" : message}</p></section>
        <Link className="panel link-card full" href="/"><h2>연구 제안으로 돌아가기</h2><p>먼저 공개 시장 데이터와 근거로 제안을 만든 뒤, 해당 제안의 별도 Testnet 검토 화면에서 정확한 미리보기를 승인합니다.</p></Link>
      </div>
      <CommandDialog open={request !== undefined} title={request?.action === "activate" ? "Spot Testnet 활성화 요청" : request?.action === "deactivate" ? "Spot Testnet 즉시 비활성화" : "계정 초기화 확인"} description={request?.action === "activate" ? "제한된 Spot Testnet 게이트웨이 활성화 의도를 기록합니다. 준비·대사 조건은 서버가 다시 검증합니다." : request?.action === "deactivate" ? "신규 Testnet 명령을 즉시 차단하는 의도를 기록합니다." : "표시된 대사 체크포인트에 결합해 계정 초기화를 확인합니다. 확인만으로 실행이 재개되지는 않습니다."} confirmLabel="의도 제출" reasonLabel="운영 사유" reasonRequired danger={request?.action !== "activate"} onCancel={() => setRequest(undefined)} onConfirm={(reason) => { const current = request; setRequest(undefined); if (current !== undefined) void submit(current.action, current.state, reason); }} />
    </>;
  }}</ResourceBoundary>;
}

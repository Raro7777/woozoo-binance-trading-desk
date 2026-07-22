"use client";

import { useState } from "react";
import { apiVersionedCommand, asRecord, booleanValue, textValue, versionValue, type JsonRecord } from "../lib/api";
import { ResourceBoundary, useResource } from "./resource";
import { CommandDialog } from "./command-dialog";
import { Field, Hold, Panel, Status, statusLabel } from "./ui";

export function killSwitchPresentation(state: JsonRecord): "ACTIVE" | "INACTIVE" | "UNKNOWN" {
  const activeField = booleanValue(state, "active");
  const rawStatus = textValue(state, "status");
  return activeField === true && (rawStatus === undefined || rawStatus === "ACTIVE")
    ? "ACTIVE"
    : activeField === false && (rawStatus === undefined || rawStatus === "INACTIVE")
      ? "INACTIVE"
      : "UNKNOWN";
}

export function OperationsConsole() {
  const resource = useResource<JsonRecord>("/api/v1/kill-switch");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("킬 스위치 활성화와 복구에는 항상 운영자의 명시적 확인이 필요합니다.");
  const [error, setError] = useState(false);
  const [requestedCommand, setRequestedCommand] = useState<Readonly<{ action: "activate" | "recover"; state: JsonRecord }>>();

  async function command(state: JsonRecord, action: "activate" | "recover", reason: string) {
    setPending(true);
    setError(false);
    setMessage(`${action === "activate" ? "활성화" : "복구"} 명령을 제출하는 중입니다. 상태 변경으로 미리 간주하지 않습니다…`);
    try {
      const body: Record<string, unknown> = { reason };
      if (action === "recover") {
        const activationEventId = textValue(state, "activation_event_id");
        if (activationEventId === undefined) throw new Error("활성화 이벤트 결합을 사용할 수 없어 복구가 보류되었습니다.");
        body.activation_event_id = activationEventId;
        body.incident_reference = reason;
      }
      await apiVersionedCommand<JsonRecord>(`/api/v1/kill-switch/${action}`, body, versionValue(state, "version"));
      setMessage("명령이 접수되었습니다. 서버 확정 상태를 새로 고치는 중…");
      resource.reload();
    } catch (reasonCaught: unknown) {
      setError(true);
      setMessage(reasonCaught instanceof Error ? reasonCaught.message : "운영 명령이 접수되지 않았습니다.");
    } finally {
      setPending(false);
    }
  }

  return (
    <ResourceBoundary resource={resource}>{(value) => {
      const state = asRecord(value) ?? {};
      const killStatus = killSwitchPresentation(state);
      const active = killStatus === "ACTIVE";
      const resourceVersion = versionValue(state, "version");
      const recoveryAllowed = booleanValue(state, "recovery_allowed") === true;
      return (
        <>
          {killStatus !== "INACTIVE" && <Hold>{active
            ? "킬 스위치가 활성 상태입니다. 새 모의주문 생성과 체결은 계속 차단되어야 합니다."
            : "킬 스위치 상태를 확정할 수 없어 모든 수동 제어가 보류되었습니다."}</Hold>}
          <div className="grid">
            <Panel title="킬 스위치">
              <Status value={killStatus} />
              <dl className="field-list">
                <Field label="리소스 버전" value={textValue(state, "resource_version", "version")} mono />
                <Field label="활성화 시각" value={textValue(state, "activated_at")} />
                <Field label="활성화한 운영자" value={textValue(state, "activated_by")} />
                <Field label="사고 내용" value={textValue(state, "reason", "incident_reason")} />
              </dl>
            </Panel>
            <section className="panel wide" aria-labelledby="recovery-guards">
              <h2 id="recovery-guards">복구 보호 조건</h2>
              <dl className="field-list">
                <Field label="데이터" value={statusLabel(textValue(state, "data_status") ?? "UNKNOWN")} />
                <Field label="대사" value={statusLabel(textValue(state, "reconciliation_status") ?? "UNKNOWN")} />
                <Field label="원장" value={statusLabel(textValue(state, "ledger_status") ?? "UNKNOWN")} />
                <Field label="미체결 주문 취소" value={statusLabel(textValue(state, "cancellation_status") ?? "UNKNOWN")} />
              </dl>
              <p className="muted">복구는 자동으로 실행되지 않습니다. 모든 복구 보호 조건의 충족 여부는 서버만 판단합니다.</p>
            </section>
            <section className="panel full" aria-labelledby="kill-controls">
              <h2 id="kill-controls">수동 제어</h2>
              <div className="actions">
                <button className="danger" disabled={pending || killStatus !== "INACTIVE" || resourceVersion === undefined} onClick={() => setRequestedCommand({ action: "activate", state })}>킬 스위치 활성화</button>
                <button disabled={pending || killStatus !== "ACTIVE" || !recoveryAllowed || resourceVersion === undefined} onClick={() => setRequestedCommand({ action: "recover", state })}>해결 검증 후 복구</button>
              </div>
              <p className={`command-result${error ? " error" : ""}`} role="status" aria-live="polite">{pending ? "서버 확정 명령 처리 결과를 기다리는 중…" : message}</p>
            </section>
          </div>
          <CommandDialog
            open={requestedCommand !== undefined}
            title={requestedCommand?.action === "activate" ? "킬 스위치 활성화" : "킬 스위치 복구"}
            description={requestedCommand?.action === "activate"
              ? "새 모의주문과 체결을 차단하고 미체결 주문 취소를 시작합니다."
              : "서버가 모든 복구 보호 조건을 다시 검증한 뒤에만 차단을 해제합니다."}
            confirmLabel={requestedCommand?.action === "activate" ? "활성화 제출" : "복구 제출"}
            reasonLabel={requestedCommand?.action === "activate" ? "사고 사유" : "검증된 사고 해결 내용"}
            reasonRequired
            danger={requestedCommand?.action === "activate"}
            onCancel={() => setRequestedCommand(undefined)}
            onConfirm={(reason) => {
              const request = requestedCommand;
              setRequestedCommand(undefined);
              if (request !== undefined) void command(request.state, request.action, reason);
            }}
          />
        </>
      );
    }}</ResourceBoundary>
  );
}

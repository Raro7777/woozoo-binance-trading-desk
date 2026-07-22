"use client";

import { useRef, useState } from "react";
import {
  apiVersionedCommand,
  asRecord,
  booleanValue,
  textValue,
  versionValue,
  type JsonRecord,
} from "../lib/api";
import { newIntentKey, stringList } from "../lib/testnet";
import { CommandDialog } from "./command-dialog";
import { ResourceBoundary, useResource } from "./resource";
import { diagnosticLabel, Field, Hold, Status } from "./ui";

function CancelControl({ orderId, onAccepted }: Readonly<{ orderId: string; onAccepted: () => void }>) {
  const resource = useResource<JsonRecord>(
    `/api/v1/testnet-orders/${encodeURIComponent(orderId)}/cancel-view`,
  );
  const intentKey = useRef<string | undefined>(undefined);
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("취소는 별도의 일회성 사람 승인으로만 요청할 수 있습니다.");
  const [error, setError] = useState(false);

  return <ResourceBoundary resource={resource}>{(value) => {
    const view = asRecord(value);
    const digest = textValue(view, "order_digest");
    const version = versionValue(view, "view_version");
    const allowed = booleanValue(view, "cancel_action_allowed") === true;
    if (view === undefined || digest === undefined || version === undefined) {
      return <Hold>취소 권위 상태를 확인할 수 없어 취소 요청을 차단했습니다.</Hold>;
    }
    async function submit(reason: string) {
      intentKey.current = newIntentKey(intentKey.current);
      setPending(true);
      setError(false);
      setMessage("서버가 주문 버전과 취소 권위를 다시 검증하고 있습니다.");
      try {
        await apiVersionedCommand<JsonRecord>("/api/v1/testnet-cancellations", {
          order_id: orderId,
          order_digest: digest,
          reason,
        }, version, intentKey.current);
        intentKey.current = undefined;
        setMessage("취소 요청이 접수되었습니다. 실제 상태는 Gateway 영수증으로 확정됩니다.");
        resource.reload();
        onAccepted();
      } catch (caught: unknown) {
        setError(true);
        setMessage(caught instanceof Error ? caught.message : "취소 요청을 접수하지 못했습니다.");
      } finally {
        setPending(false);
      }
    }
    return <section className="panel full" aria-labelledby="testnet-cancel-control">
      <h2 id="testnet-cancel-control">주문 취소 승인</h2>
      <p className="muted">브라우저는 가격이나 수량을 보내지 않습니다. 서버가 표시한 주문 해시와 버전만 제출하며, 실행 서비스가 현재 주문을 다시 검증합니다.</p>
      <dl className="field-list">
        <Field label="주문 상태 해시" value={digest} mono />
        <Field label="주문 버전" value={String(version)} mono />
        <Field label="취소 승인 ID" value={textValue(view, "cancel_authorization_id") ?? "발급되지 않음"} mono />
        <Field label="취소 실행 ID" value={textValue(view, "cancel_execution_id") ?? "생성되지 않음"} mono />
      </dl>
      {stringList(view, "reason_codes").map((reason) => <p className="muted" key={reason}>{diagnosticLabel(reason)}</p>)}
      <div className="actions">
        <button className="danger" disabled={pending || !allowed} onClick={() => setOpen(true)}>
          별도 취소 승인 요청
        </button>
      </div>
      <p className={`command-result${error ? " error" : ""}`} role="status" aria-live="polite">{message}</p>
      <CommandDialog
        open={open}
        title="Spot Testnet 주문 취소 승인"
        description="현재 주문의 서버 해시와 버전에 결속된 일회성 취소 권한을 요청합니다. 자동 안전 취소나 재주문은 수행하지 않습니다."
        confirmLabel="취소 승인 제출"
        cancelLabel="닫기"
        reasonLabel="취소 사유"
        reasonRequired
        danger
        onCancel={() => setOpen(false)}
        onConfirm={(reason) => { setOpen(false); void submit(reason); }}
      />
    </section>;
  }}</ResourceBoundary>;
}

export function TestnetExecutionView({ executionId }: Readonly<{ executionId: string }>) {
  const resource = useResource<JsonRecord>(
    `/api/v1/testnet-executions/${encodeURIComponent(executionId)}`,
  );
  return <ResourceBoundary resource={resource}>{(value) => {
    const execution = asRecord(value);
    const environment = textValue(execution, "environment");
    const commandStatus = textValue(execution, "command_status") ?? "UNKNOWN";
    if (execution === undefined || environment !== "BINANCE_SPOT_TESTNET") {
      return <Hold>알 수 없는 실행 상태입니다. 재시도나 재주문 없이 서버의 권위 상태를 기다립니다.</Hold>;
    }
    const unknown = commandStatus === "UNKNOWN_OUTCOME"
      || textValue(execution, "external_outcome") === "UNKNOWN";
    const orderId = textValue(execution, "order_id");
    return <>
      {unknown && <Hold>제출 결과를 확정할 수 없습니다. 동일한 클라이언트 주문 ID 조회와 권위 있는 조정만 허용하며, 새 주문과 교체 주문은 금지됩니다.</Hold>}
      <div className="grid">
        <section className="panel" aria-labelledby="testnet-command-state">
          <h2 id="testnet-command-state">명령 상태</h2>
          <p><Status value={commandStatus} /></p>
          <dl className="field-list">
            <Field label="실행 ID" value={textValue(execution, "execution_id")} mono />
            <Field label="명령 ID" value={textValue(execution, "command_id")} mono />
            <Field label="클라이언트 주문 ID" value={textValue(execution, "client_order_id")} mono />
            <Field label="외부 결과" value={textValue(execution, "external_outcome")} />
          </dl>
        </section>
        <section className="panel" aria-labelledby="testnet-order-state">
          <h2 id="testnet-order-state">주문 관측</h2>
          <p><Status value={textValue(execution, "order_status") ?? "UNKNOWN"} /></p>
          <dl className="field-list">
            <Field label="주문 ID" value={orderId} mono />
            <Field label="주문 버전" value={textValue(execution, "order_version")} mono />
            <Field label="계정 세대" value={textValue(execution, "account_generation")} mono />
            <Field label="제출 시각" value={textValue(execution, "submitted_at")} />
            <Field label="마지막 관측" value={textValue(execution, "last_observed_at")} />
          </dl>
        </section>
        <section className="panel" aria-labelledby="testnet-reconciliation-state">
          <h2 id="testnet-reconciliation-state">조정 상태</h2>
          <p><Status value={textValue(execution, "reconciliation_status") ?? "UNKNOWN"} /></p>
          <dl className="field-list">
            <Field label="체크포인트" value={textValue(execution, "reconciliation_checkpoint_id")} mono />
            <Field label="초기화 상태" value={textValue(execution, "reset_state")} />
          </dl>
          {stringList(execution, "reason_codes").map((reason) => <p className="muted" key={reason}>{diagnosticLabel(reason)}</p>)}
        </section>
        {orderId === undefined
          ? <section className="panel full"><Hold>주문 권위가 아직 생성되지 않아 취소 요청을 차단했습니다.</Hold></section>
          : <CancelControl orderId={orderId} onAccepted={resource.reload} />}
      </div>
    </>;
  }}</ResourceBoundary>;
}

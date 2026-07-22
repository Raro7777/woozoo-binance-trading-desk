"use client";

import { useState } from "react";
import { apiVersionedCommand, asRecord, asRecords, textValue, versionValue, type JsonRecord } from "../lib/api";
import { ResourceBoundary, useResource } from "./resource";
import { CommandDialog } from "./command-dialog";
import { diagnosticLabel, Field, Hold, Panel, Status, statusLabel } from "./ui";

export function PaperDesk() {
  const resource = useResource<JsonRecord>("/api/v1/paper-portfolio");
  const [message, setMessage] = useState("주문 변경에는 새로운 명령 토큰과 서버 확정 처리 결과가 필요합니다.");
  const [pendingOrder, setPendingOrder] = useState<string>();
  const [error, setError] = useState(false);
  const [confirmOrder, setConfirmOrder] = useState<JsonRecord>();

  async function cancel(order: JsonRecord) {
    const orderId = textValue(order, "order_id", "id");
    if (orderId === undefined) return;
    setPendingOrder(orderId);
    setError(false);
    setMessage("취소를 제출하는 중입니다. 확인될 때까지 기존 주문 상태가 표시됩니다…");
    try {
      await apiVersionedCommand<JsonRecord>(`/api/v1/paper-orders/${encodeURIComponent(orderId)}/cancel`, {
        reason: "OPERATOR_REQUESTED",
      }, versionValue(order, "version"));
      setMessage("취소가 접수되었습니다. 서버 확정 상태를 새로 고치는 중…");
      resource.reload();
    } catch (reason: unknown) {
      setError(true);
      setMessage(reason instanceof Error ? reason.message : "취소가 접수되지 않았습니다.");
    } finally {
      setPendingOrder(undefined);
    }
  }

  return (
    <ResourceBoundary resource={resource}>{(value) => {
      const portfolio = asRecord(value) ?? {};
      const balances = asRecords(portfolio.balances);
      const positions = asRecord(portfolio.positions);
      const positionRows = positions === undefined ? [] : Object.entries(positions).map(([asset, amount]) => ({ asset, amount: asRecord(amount) }));
      const orders = asRecords(portfolio.orders);
      const receipts = asRecords(portfolio.command_receipts);
      const reconciliationHealth = textValue(portfolio, "reconciliation_health") ?? "UNKNOWN";
      const ledgerHealth = textValue(portfolio, "ledger_health") ?? "UNKNOWN";
      return (
        <div className="grid">
          {(reconciliationHealth !== "HEALTHY" || ledgerHealth !== "HEALTHY") && (
            <Hold>서버 확정 대사 또는 원장 상태가 정상이 아니므로 모의 포트폴리오 제어가 보류되었습니다.</Hold>
          )}
          <Panel title="대사 상태">
            <p><Status value={reconciliationHealth} /> <Status value={ledgerHealth} /></p>
            <dl className="field-list">
              <Field label="모의 계정" value={textValue(portfolio, "paper_account_id", "account_id")} mono />
              <Field label="기준 시각" value={textValue(portfolio, "as_of")} />
              <Field label="원장 체크포인트" value={textValue(portfolio, "ledger_checkpoint_id")} mono />
              <Field label="포트폴리오 버전" value={textValue(portfolio, "portfolio_version")} mono />
              <Field label="원장 버전" value={textValue(portfolio, "ledger_version")} mono />
              <Field label="원장 상태" value={statusLabel(ledgerHealth)} />
            </dl>
          </Panel>
          {textValue(portfolio, "available_quote") !== undefined && (
            <section className="panel" aria-label="사용 가능 기준자산 잔고">
              <p className="eyebrow">사용 가능 USDT</p>
              <p className="metric">{textValue(portfolio, "available_quote")}</p>
              <p className="muted">서버가 제공한 정확한 고정 소수점 문자열</p>
            </section>
          )}
          {balances.map((balance) => (
            <section className="panel" key={textValue(balance, "asset") ?? "balance"} aria-label={`${textValue(balance, "asset") ?? "자산"} 잔고`}>
              <p className="eyebrow">{textValue(balance, "asset") ?? "자산"}</p>
              <p className="metric">{textValue(balance, "available", "available_amount") ?? "정보 없음"}</p>
              <dl className="field-list">
                <Field label="보류 금액" value={textValue(balance, "held", "held_amount")} mono />
                <Field label="합계" value={textValue(balance, "total", "total_amount")} mono />
              </dl>
            </section>
          ))}
          {positionRows.map(({ asset, amount }) => (
            <section className="panel" key={asset} aria-label={`${asset} 포지션`}>
              <p className="eyebrow">{asset} 포지션</p>
              <p className="metric">{textValue(amount, "available") ?? "정보 없음"}</p>
              <dl className="field-list"><Field label="보류 금액" value={textValue(amount, "held")} mono /></dl>
            </section>
          ))}
          <section className="panel full" aria-labelledby="paper-orders">
            <h2 id="paper-orders">모의주문</h2>
            <div className="table-wrap" role="region" aria-label="모의주문 표" tabIndex={0}>
              <table>
                <caption>서버가 확정한 주문 생애주기입니다. 금융 문자열은 원문 그대로 표시됩니다.</caption>
                <thead><tr><th>주문</th><th>시장</th><th>매수·매도</th><th>수량</th><th>지정가</th><th>체결량</th><th>상태</th><th>작업</th></tr></thead>
                <tbody>{orders.length === 0 ? <tr><td colSpan={8}>모의주문이 없습니다.</td></tr> : orders.map((order) => {
                  const orderId = textValue(order, "order_id", "id") ?? "알 수 없음";
                  const status = textValue(order, "status") ?? "UNKNOWN";
                  const cancellable = (status === "OPEN" || status === "PARTIALLY_FILLED") && versionValue(order, "version") !== undefined;
                  return <tr key={orderId}>
                    <td className="mono">{orderId}</td><td>{textValue(order, "symbol")}</td><td>{textValue(order, "side") === "BUY" ? "매수" : textValue(order, "side") === "SELL" ? "매도" : "알 수 없음"}</td>
                    <td className="mono">{textValue(order, "quantity")}</td><td className="mono">{textValue(order, "limit_price")}</td>
                    <td className="mono">{textValue(order, "filled_quantity")}</td><td><Status value={status} /></td>
                    <td><button className="secondary" disabled={!cancellable || pendingOrder !== undefined} onClick={() => setConfirmOrder(order)}>{pendingOrder === orderId ? "취소 중…" : "취소"}</button></td>
                  </tr>;
                })}</tbody>
              </table>
            </div>
            <p className={`command-result${error ? " error" : ""}`} role="status" aria-live="polite">{message}</p>
          </section>
          <section className="panel full" aria-labelledby="command-receipts">
            <h2 id="command-receipts">최근 명령 처리 결과</h2>
            {receipts.length === 0 ? <p className="muted">처리 결과가 없습니다.</p> : <dl className="field-list">{receipts.map((receipt, index) => {
              const reason = textValue(receipt, "reason", "reason_code");
              return <Field key={textValue(receipt, "command_id", "receipt_id") ?? index} label={textValue(receipt, "command_id", "receipt_id") ?? "처리 결과"} value={`${statusLabel(textValue(receipt, "status") ?? "UNKNOWN")} · ${reason === undefined ? "사유 없음" : diagnosticLabel(reason)}`} mono />;
            })}</dl>}
          </section>
          <CommandDialog
            open={confirmOrder !== undefined}
            title="모의주문 취소"
            description={`모의주문 ${textValue(confirmOrder, "order_id", "id") ?? "정보 없음"}의 남은 수량을 취소합니다. 서버 처리 결과가 확정될 때까지 현재 상태를 유지합니다.`}
            confirmLabel="취소 제출"
            danger
            onCancel={() => setConfirmOrder(undefined)}
            onConfirm={() => {
              const order = confirmOrder;
              setConfirmOrder(undefined);
              if (order !== undefined) void cancel(order);
            }}
          />
        </div>
      );
    }}</ResourceBoundary>
  );
}

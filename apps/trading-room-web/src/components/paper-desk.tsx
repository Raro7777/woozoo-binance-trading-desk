"use client";

import { useState } from "react";
import { apiVersionedCommand, asRecord, asRecords, textValue, versionValue, type JsonRecord } from "../lib/api";
import { ResourceBoundary, useResource } from "./resource";
import { Field, Hold, Panel, Status } from "./ui";

export function PaperDesk() {
  const resource = useResource<JsonRecord>("/api/v1/paper-portfolio");
  const [message, setMessage] = useState("Order changes require a fresh command token and authoritative receipt.");
  const [pendingOrder, setPendingOrder] = useState<string>();
  const [error, setError] = useState(false);

  async function cancel(order: JsonRecord) {
    const orderId = textValue(order, "order_id", "id");
    if (orderId === undefined || !window.confirm(`Cancel Paper order ${orderId}?`)) return;
    setPendingOrder(orderId);
    setError(false);
    setMessage("Submitting cancellation. Existing order state remains visible until confirmed…");
    try {
      const result = await apiVersionedCommand<JsonRecord>(`/api/v1/paper-orders/${encodeURIComponent(orderId)}/cancel`, {
        reason: "OPERATOR_REQUESTED",
      }, versionValue(order, "version"));
      setMessage(textValue(result, "message", "status") ?? "Cancellation accepted. Refreshing authoritative state…");
      resource.reload();
    } catch (reason: unknown) {
      setError(true);
      setMessage(reason instanceof Error ? reason.message : "Cancellation was not accepted.");
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
            <Hold>Paper portfolio controls are held because authoritative reconciliation or ledger health is not healthy.</Hold>
          )}
          <Panel title="Reconciliation">
            <p><Status value={reconciliationHealth} /> <Status value={ledgerHealth} /></p>
            <dl className="field-list">
              <Field label="Paper account" value={textValue(portfolio, "paper_account_id", "account_id")} mono />
              <Field label="As of" value={textValue(portfolio, "as_of")} />
              <Field label="Ledger checkpoint" value={textValue(portfolio, "ledger_checkpoint_id")} mono />
              <Field label="Portfolio version" value={textValue(portfolio, "portfolio_version")} mono />
              <Field label="Ledger version" value={textValue(portfolio, "ledger_version")} mono />
              <Field label="Ledger health" value={ledgerHealth} />
            </dl>
          </Panel>
          {textValue(portfolio, "available_quote") !== undefined && (
            <section className="panel" aria-label="Available quote balance">
              <p className="eyebrow">USDT available</p>
              <p className="metric">{textValue(portfolio, "available_quote")}</p>
              <p className="muted">Exact server-provided Decimal string</p>
            </section>
          )}
          {balances.map((balance) => (
            <section className="panel" key={textValue(balance, "asset") ?? "balance"} aria-label={`${textValue(balance, "asset") ?? "Asset"} balance`}>
              <p className="eyebrow">{textValue(balance, "asset") ?? "Asset"}</p>
              <p className="metric">{textValue(balance, "available", "available_amount") ?? "Not available"}</p>
              <dl className="field-list">
                <Field label="Held" value={textValue(balance, "held", "held_amount")} mono />
                <Field label="Total" value={textValue(balance, "total", "total_amount")} mono />
              </dl>
            </section>
          ))}
          {positionRows.map(({ asset, amount }) => (
            <section className="panel" key={asset} aria-label={`${asset} position`}>
              <p className="eyebrow">{asset} position</p>
              <p className="metric">{textValue(amount, "available") ?? "Not available"}</p>
              <dl className="field-list"><Field label="Held" value={textValue(amount, "held")} mono /></dl>
            </section>
          ))}
          <section className="panel full" aria-labelledby="paper-orders">
            <h2 id="paper-orders">Paper orders</h2>
            <div className="table-wrap" role="region" aria-label="Paper orders table" tabIndex={0}>
              <table>
                <caption>Authoritative order lifecycle. Financial strings are shown verbatim.</caption>
                <thead><tr><th>Order</th><th>Market</th><th>Side</th><th>Quantity</th><th>Limit</th><th>Filled</th><th>Status</th><th>Action</th></tr></thead>
                <tbody>{orders.length === 0 ? <tr><td colSpan={8}>No Paper orders are available.</td></tr> : orders.map((order) => {
                  const orderId = textValue(order, "order_id", "id") ?? "Unknown";
                  const status = textValue(order, "status") ?? "UNKNOWN";
                  const cancellable = (status === "OPEN" || status === "PARTIALLY_FILLED") && versionValue(order, "version") !== undefined;
                  return <tr key={orderId}>
                    <td className="mono">{orderId}</td><td>{textValue(order, "symbol")}</td><td>{textValue(order, "side")}</td>
                    <td className="mono">{textValue(order, "quantity")}</td><td className="mono">{textValue(order, "limit_price")}</td>
                    <td className="mono">{textValue(order, "filled_quantity")}</td><td><Status value={status} /></td>
                    <td><button className="secondary" disabled={!cancellable || pendingOrder !== undefined} onClick={() => void cancel(order)}>{pendingOrder === orderId ? "Cancelling…" : "Cancel"}</button></td>
                  </tr>;
                })}</tbody>
              </table>
            </div>
            <p className={`command-result${error ? " error" : ""}`} role="status" aria-live="polite">{message}</p>
          </section>
          <section className="panel full" aria-labelledby="command-receipts">
            <h2 id="command-receipts">Recent command receipts</h2>
            {receipts.length === 0 ? <p className="muted">No receipts are available.</p> : <dl className="field-list">{receipts.map((receipt, index) => <Field key={textValue(receipt, "command_id", "receipt_id") ?? index} label={textValue(receipt, "command_id", "receipt_id") ?? "Receipt"} value={`${textValue(receipt, "status") ?? "UNKNOWN"} · ${textValue(receipt, "reason", "reason_code") ?? "No reason"}`} mono />)}</dl>}
          </section>
        </div>
      );
    }}</ResourceBoundary>
  );
}

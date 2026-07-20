"use client";

import Link from "next/link";
import {
  asRecord,
  textValue,
  type JsonRecord,
  type MarketStatusEnvelopeV1,
} from "../lib/api";
import { ResourceBoundary, useResource } from "./resource";
import { Field, Panel, Status } from "./ui";

function MarketCard({ symbol, value }: Readonly<{ symbol: string; value: MarketStatusEnvelopeV1 }>) {
  const { data } = value;
  const watermarkText = `${data.watermark.stream} · #${data.watermark.last_sequence}`;
  return (
    <Panel title={symbol}>
      <div className="hero-status"><Status value={data.quality} /></div>
      <dl className="field-list">
        <Field label="Price" value={data.price} mono />
        <Field label="Event time" value={data.event_time} />
        <Field label="Received at" value={data.received_at} />
        <Field label="Watermark" value={watermarkText} mono />
        <Field label="Connection" value={data.watermark.session_id} mono />
      </dl>
    </Panel>
  );
}

export function MarketStatus() {
  const btc = useResource<MarketStatusEnvelopeV1>("/api/v1/markets/BTCUSDT/status");
  const eth = useResource<MarketStatusEnvelopeV1>("/api/v1/markets/ETHUSDT/status");
  const session = useResource<JsonRecord>("/api/v1/session");
  return (
    <div className="grid" aria-label="Authoritative platform status">
      <section className="panel" aria-labelledby="operator-state">
        <h2 id="operator-state">Operator boundary</h2>
        <ResourceBoundary resource={session}>{(value) => {
          const record = asRecord(value);
          const state = textValue(record, "state", "status") ?? "AUTHENTICATED";
          return <><Status value={state} /><p>Session-bound commands require a fresh one-time token.</p></>;
        }}</ResourceBoundary>
        <div className="actions"><Link className="button secondary" href="/login">Manage session</Link></div>
      </section>
      <ResourceBoundary resource={btc}>{(value) => <MarketCard symbol="BTC / USDT" value={value} />}</ResourceBoundary>
      <ResourceBoundary resource={eth}>{(value) => <MarketCard symbol="ETH / USDT" value={value} />}</ResourceBoundary>
    </div>
  );
}

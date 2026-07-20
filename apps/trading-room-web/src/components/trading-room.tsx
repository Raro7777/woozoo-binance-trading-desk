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
  const streamLabel = data.watermark.stream === "bookTicker" ? "최우선 호가" : "알 수 없는 시장 자료";
  const watermarkText = `${streamLabel} · #${data.watermark.last_sequence}`;
  return (
    <Panel title={symbol}>
      <div className="hero-status"><Status value={data.quality} /></div>
      <dl className="field-list">
        <Field label="가격" value={data.price} mono />
        <Field label="이벤트 시각" value={data.event_time} />
        <Field label="수신 시각" value={data.received_at} />
        <Field label="워터마크" value={watermarkText} mono />
        <Field label="연결" value={data.watermark.session_id} mono />
      </dl>
    </Panel>
  );
}

export function MarketStatus() {
  const btc = useResource<MarketStatusEnvelopeV1>("/api/v1/markets/BTCUSDT/status");
  const eth = useResource<MarketStatusEnvelopeV1>("/api/v1/markets/ETHUSDT/status");
  const session = useResource<JsonRecord>("/api/v1/session");
  return (
    <div className="grid" aria-label="서버 확정 플랫폼 상태">
      <section className="panel" aria-labelledby="operator-state">
        <h2 id="operator-state">운영자 경계</h2>
        <ResourceBoundary resource={session}>{(value) => {
          const record = asRecord(value);
          const state = textValue(record, "state", "status") ?? "UNKNOWN";
          return <><Status value={state} /><p>세션에 결합된 명령에는 새로운 일회용 토큰이 필요합니다.</p></>;
        }}</ResourceBoundary>
        <div className="actions"><Link className="button secondary" href="/login">세션 관리</Link></div>
      </section>
      <ResourceBoundary resource={btc}>{(value) => <MarketCard symbol="BTC / USDT" value={value} />}</ResourceBoundary>
      <ResourceBoundary resource={eth}>{(value) => <MarketCard symbol="ETH / USDT" value={value} />}</ResourceBoundary>
    </div>
  );
}

"use client";

import {
  asRecord,
  asRecords,
  textValue,
  type JsonRecord,
  type MarketStatusEnvelopeV1,
} from "../lib/api";
import { useResource, type ResourceState } from "./resource";
import { Field, Hold, Panel, Status } from "./ui";

type Loaded<T> = ResourceState<T> & Readonly<{ reload: () => void }>;

export function publicDataPresentation(markets: readonly MarketStatusEnvelopeV1[]): "HEALTHY" | "DELAYED" | "STOPPED" {
  const qualities = markets.map((market) => market.data?.quality?.toLowerCase());
  if (qualities.length !== 2 || qualities.some((quality) => quality === undefined || quality === "invalid")) return "STOPPED";
  return qualities.every((quality) => quality === "healthy") ? "HEALTHY" : "DELAYED";
}

export function latestAnalysisAt(auditPage: JsonRecord): string | undefined {
  return asRecords(auditPage.events ?? auditPage.items)
    .filter((event) => textValue(event, "event_type", "type") === "analysis.run.completed.v1")
    .map((event) => textValue(event, "occurred_at"))
    .filter((value): value is string => value !== undefined)
    .sort((left, right) => Date.parse(right) - Date.parse(left))[0];
}

function loadingOrHold(resources: readonly Loaded<unknown>[]): React.ReactNode | undefined {
  if (resources.some((resource) => resource.state === "loading")) {
    return <p className="loading" aria-live="polite">제품 상태를 불러오는 중…</p>;
  }
  const held = resources.find((resource) => resource.state === "hold");
  if (held?.state === "hold") return <Hold>{held.message} 제품 상태를 확정할 수 없어 새 작업을 시작하지 마세요.</Hold>;
  return undefined;
}

function ProductStatusPanel({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <section className="panel full" aria-labelledby="product-status">
      <h2 id="product-status">제품 상태</h2>
      <p className="muted">현재 MVP는 BTC·ETH 기록 재생(Fixture)과 Mock AI를 사용합니다. Testnet은 비활성이며 Mainnet은 지원 안 함 상태입니다.</p>
      {children}
    </section>
  );
}

export function ProductStatus() {
  const health = useResource<JsonRecord>("/api/v1/health");
  const dashboard = useResource<JsonRecord>("/api/v1/trading-room");
  const btc = useResource<MarketStatusEnvelopeV1>("/api/v1/markets/BTCUSDT/status");
  const eth = useResource<MarketStatusEnvelopeV1>("/api/v1/markets/ETHUSDT/status");
  const audit = useResource<JsonRecord>("/api/v1/audit-events");
  const resources: readonly Loaded<unknown>[] = [health, dashboard, btc, eth, audit];
  const boundary = loadingOrHold(resources);
  if (boundary !== undefined) return <ProductStatusPanel>{boundary}</ProductStatusPanel>;

  if (health.state !== "ready" || dashboard.state !== "ready" || btc.state !== "ready" || eth.state !== "ready" || audit.state !== "ready") {
    return <ProductStatusPanel><Hold>제품 상태 응답이 완전하지 않아 새 작업을 시작하지 마세요.</Hold></ProductStatusPanel>;
  }
  const healthData = asRecord(health.value.data) ?? {};
  const dependencies = asRecord(healthData.dependencies) ?? {};
  const postgres = asRecord(dependencies.postgres);
  const redis = asRecord(dependencies.redis);
  const kill = asRecord(dashboard.value.kill_switch) ?? {};
  const lastDataAt = [btc.value.data.received_at, eth.value.data.received_at]
    .sort((left, right) => Date.parse(right) - Date.parse(left))[0];
  const dataState = publicDataPresentation([btc.value, eth.value]);
  const riskState = textValue(dashboard.value, "status") === "READY" ? "HEALTHY" : "BLOCKED";
  const killState = kill.active === true ? "ACTIVE" : kill.active === false ? "INACTIVE" : "UNKNOWN";

  return (
    <ProductStatusPanel>
      <div className="status-grid">
        <dl className="field-list">
          <div className="field"><dt>Binance 공개 데이터</dt><dd><Status value={dataState} /></dd></div>
          <Field label="데이터 원천" value="기록 재생(Fixture)" />
          <Field label="마지막 데이터 수신" value={lastDataAt} />
          <div className="field"><dt>AI 제공자</dt><dd><Status value="MOCK" /></dd></div>
          <Field label="마지막 분석" value={latestAnalysisAt(audit.value) ?? "분석 이력 없음"} />
        </dl>
        <dl className="field-list">
          <div className="field"><dt>거래 모드</dt><dd><Status value="PAPER" /></dd></div>
          <div className="field"><dt>Testnet</dt><dd><Status value="DISABLED" /></dd></div>
          <div className="field"><dt>Mainnet</dt><dd><Status value="UNSUPPORTED" /></dd></div>
          <div className="field"><dt>Risk Engine</dt><dd><Status value={riskState} /></dd></div>
          <div className="field"><dt>Kill Switch</dt><dd><Status value={killState} /></dd></div>
        </dl>
        <dl className="field-list">
          <div className="field"><dt>PostgreSQL</dt><dd><Status value={textValue(postgres, "status") ?? "UNKNOWN"} /></dd></div>
          <div className="field"><dt>Redis</dt><dd><Status value={textValue(redis, "status") ?? "UNKNOWN"} /></dd></div>
          <div className="field"><dt>대사</dt><dd><Status value={textValue(dashboard.value, "reconciliation") ?? "UNKNOWN"} /></dd></div>
          <div className="field"><dt>복식 원장</dt><dd><Status value={textValue(dashboard.value, "ledger") ?? "UNKNOWN"} /></dd></div>
        </dl>
      </div>
    </ProductStatusPanel>
  );
}

"use client";

import { useState, useSyncExternalStore } from "react";
import { apiCommand, textValue, type JsonRecord } from "../lib/api";

export function AnalysisLauncher() {
  const [pending, setPending] = useState(false);
  const hydrated = useSyncExternalStore(() => () => undefined, () => true, () => false);
  const [message, setMessage] = useState("허용된 현물 종목을 선택하세요. 분석 결과는 참고용입니다.");
  const [error, setError] = useState(false);

  async function launch(symbol: "BTCUSDT" | "ETHUSDT") {
    setPending(true);
    setError(false);
    setMessage(`근거에 결합된 ${symbol} 분석을 요청하는 중…`);
    try {
      const result = await apiCommand<JsonRecord>("/api/v1/analysis-runs", { symbol });
      const runId = textValue(result, "run_id");
      if (runId === undefined) throw new Error("수락된 분석 응답에 실행 ID가 없습니다.");
      setMessage("분석이 접수되었습니다. 서버 확정 실행 상태를 여는 중…");
      window.location.assign(`/analysis/${encodeURIComponent(runId)}`);
    } catch (reason: unknown) {
      setError(true);
      setMessage(reason instanceof Error ? reason.message : "분석이 보류되었습니다.");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="panel full" aria-labelledby="new-analysis">
      <h2 id="new-analysis">모의투자 분석 시작</h2>
      <p className="muted">서버가 변경 불가능한 근거, 결정론적 위험 판단과 정확한 모의주문 미리보기를 결합합니다.</p>
      <div className="actions">
        <button disabled={pending || !hydrated} onClick={() => void launch("BTCUSDT")}>BTC / USDT 분석</button>
        <button className="secondary" disabled={pending || !hydrated} onClick={() => void launch("ETHUSDT")}>ETH / USDT 분석</button>
      </div>
      <p className={`command-result${error ? " error" : ""}`} role="status" aria-live="polite">{message}</p>
    </section>
  );
}

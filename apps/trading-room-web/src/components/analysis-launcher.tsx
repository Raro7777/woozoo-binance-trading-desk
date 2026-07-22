"use client";

import { useState, useSyncExternalStore } from "react";
import { ApiError, apiCommand, textValue, type JsonRecord } from "../lib/api";

const transientBookCodes = new Set(["RISK_BOOK_NOT_FOUND", "BTC_BOOK_NOT_FOUND", "ETH_BOOK_NOT_FOUND"]);

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
      const idempotencyKey = crypto.randomUUID();
      let result: JsonRecord | undefined;
      for (let attempt = 0; attempt < 10; attempt += 1) {
        try {
          result = await apiCommand<JsonRecord>("/api/v1/analysis-runs", { symbol }, {
            idempotencyKey,
          });
          break;
        } catch (reason: unknown) {
          if (!(reason instanceof ApiError && reason.code !== undefined && transientBookCodes.has(reason.code) && attempt < 9)) {
            throw reason;
          }
          setMessage("현재 BTC·ETH 호가를 안전하게 동기화하는 중입니다. 잠시만 기다려 주세요…");
          await new Promise((resolveDelay) => setTimeout(resolveDelay, 2_000));
        }
      }
      if (result === undefined) throw new Error("분석 요청이 완료되지 않았습니다.");
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

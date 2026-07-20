"use client";

export default function GlobalError({ reset }: Readonly<{ error: Error & { digest?: string }; reset: () => void }>) {
  return (
    <html lang="ko">
      <head><title>서비스 오류 · 우주 트레이딩룸</title></head>
      <body>
        <main className="global-error-main">
          <section className="error-screen" role="alert" aria-labelledby="global-error-title">
            <p className="eyebrow">안전하게 보류됨</p>
            <h1 id="global-error-title">서비스 화면에 오류가 발생했습니다</h1>
            <p>서버 확정 상태를 확인할 수 없어 모든 작업을 중단했습니다.</p>
            <div className="actions">
              <button onClick={reset}>다시 시도</button>
              <button className="secondary" onClick={() => window.history.back()}>뒤로</button>
            </div>
          </section>
        </main>
      </body>
    </html>
  );
}

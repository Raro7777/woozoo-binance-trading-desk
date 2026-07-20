"use client";

import { useRouter } from "next/navigation";

export default function ErrorScreen({ reset }: Readonly<{ error: Error & { digest?: string }; reset: () => void }>) {
  const router = useRouter();
  return (
    <section className="error-screen" role="alert" aria-labelledby="route-error-title">
      <p className="eyebrow">안전하게 보류됨</p>
      <h1 id="route-error-title">화면을 표시하는 중 오류가 발생했습니다</h1>
      <p>서버 확정 상태를 확인할 수 없어 이 화면의 작업을 중단했습니다.</p>
      <div className="actions">
        <button onClick={reset}>다시 시도</button>
        <button className="secondary" onClick={() => router.back()}>뒤로</button>
      </div>
    </section>
  );
}

import Link from "next/link";

export default function NotFound() {
  return (
    <section className="panel full">
      <p className="eyebrow">404 · 페이지 없음</p>
      <h1>요청한 화면을 찾을 수 없습니다</h1>
      <p>주소를 확인하거나 트레이딩룸 홈으로 돌아가세요.</p>
      <div className="actions">
        <Link className="button" href="/">트레이딩룸 홈으로</Link>
      </div>
    </section>
  );
}

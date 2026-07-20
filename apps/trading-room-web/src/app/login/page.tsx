import type { Metadata } from "next";
import { LoginForm } from "../../components/login-form";
import { PageHeading } from "../../components/ui";

export const metadata: Metadata = { title: "운영자 로그인" };

export default function LoginPage() {
  return (
    <>
      <PageHeading eyebrow="로컬 운영자 경계" title="모의투자 데스크에 로그인">
        비밀번호는 동일 출처의 HTTPS 세션 엔드포인트로만 전송되며 이 화면에는 저장되지 않습니다.
      </PageHeading>
      <div className="grid">
        <section className="panel wide" aria-labelledby="operator-login">
          <h2 id="operator-login">운영자 세션</h2>
          <LoginForm />
        </section>
        <section className="panel" aria-labelledby="session-policy">
          <h2 id="session-policy">세션 정책</h2>
          <p>Secure, HttpOnly, SameSite=Strict 쿠키를 사용합니다. 모든 변경 요청은 새로운 일회용 CSRF 토큰과 정확한 Origin 검증을 거칩니다.</p>
        </section>
      </div>
    </>
  );
}

import type { Metadata } from "next";
import Link from "next/link";
import { headers } from "next/headers";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "우주 트레이딩룸", template: "%s · 우주 트레이딩룸" },
  description: "사람의 승인을 거치는 로컬 모의투자 운영실입니다.",
};

const navigation = [
  ["트레이딩룸", "/"],
  ["모의투자 데스크", "/paper"],
  ["Spot Testnet", "/testnet"],
  ["감사 기록", "/audit"],
  ["운영", "/operations"],
] as const;

export default async function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  if (process.env.WOOZOO_E2E_UI_ERRORS === "enabled") {
    const requestHeaders = await headers();
    if (requestHeaders.get("x-woozoo-e2e-global-error") === "enabled") {
      throw new Error("E2E_GLOBAL_RENDER_ERROR");
    }
  }
  return (
    <html lang="ko">
      <body>
        <a className="skip-link" href="#trading-room-content">트레이딩룸 본문으로 건너뛰기</a>
        <header className="site-header">
          <Link className="brand" href="/" aria-label="우주 트레이딩룸 홈">
            <span className="brand-mark" aria-hidden="true">우주</span>
            <span><strong>우주</strong><small>트레이딩룸</small></span>
          </Link>
          <div className="mode-lock" title="실거래는 금지되며 Spot Testnet은 별도 승인 후에만 사용할 수 있습니다">
            <span aria-hidden="true">●</span> 실거래 금지
          </div>
          <nav aria-label="주요 메뉴">
            {navigation.map(([label, href]) => <Link key={href} href={href}>{label}</Link>)}
          </nav>
          <Link className="operator-link" href="/login">운영자 세션</Link>
        </header>
        <main id="trading-room-content" tabIndex={-1}>{children}</main>
        <footer>
          <p>모의투자 및 Binance Spot Testnet · 별도 사람 승인 필수 · 실거래 금지 · 금융 판단 권한은 서버에 유지</p>
        </footer>
      </body>
    </html>
  );
}

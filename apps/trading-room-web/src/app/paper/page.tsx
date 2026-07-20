import type { Metadata } from "next";
import { PaperDesk } from "../../components/paper-desk";
import { PageHeading } from "../../components/ui";

export const metadata: Metadata = { title: "모의투자 데스크" };

export default function PaperPage() {
  return (
    <>
      <PageHeading eyebrow="결정론적 모의투자 권한" title="포트폴리오, 주문 및 원장">
        모든 금액은 서버가 제공한 정확한 고정 소수점 문자열입니다. 이 화면은 잔고, 수수료, 손익 또는 노출을 자체 계산하지 않습니다.
      </PageHeading>
      <PaperDesk />
    </>
  );
}

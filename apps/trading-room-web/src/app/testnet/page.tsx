import type { Metadata } from "next";
import { TestnetDesk } from "../../components/testnet-desk";
import { PageHeading } from "../../components/ui";

export const metadata: Metadata = { title: "Spot Testnet 운영" };

export default function TestnetPage() {
  return <><PageHeading eyebrow="8단계 · 외부 Testnet 경계" title="Spot Testnet 운영실">실제 자금과 무관한 Binance Spot Testnet 전용 운영 화면입니다. 기본 차단, 별도 사람 승인, 단일 주문 ID 대사 원칙을 적용합니다.</PageHeading><TestnetDesk /></>;
}

import Link from "next/link";
import { AnalysisLauncher } from "../components/analysis-launcher";
import { MarketStatus } from "../components/trading-room";
import { PageHeading } from "../components/ui";

const destinations = [
  ["모의투자 데스크", "/paper", "서버가 확정한 잔고, 주문, 처리 결과와 원장 상태를 확인합니다."],
  ["감사 타임라인", "/audit", "변경할 수 없는 운영자 명령과 도메인 처리 결과를 순서대로 추적합니다."],
  ["운영", "/operations", "킬 스위치와 장애 시 차단되는 서비스 상태를 점검합니다."],
] as const;

export default function TradingRoomPage() {
  return (
    <>
      <PageHeading eyebrow="7단계 · 로컬 운영" title="우주 트레이딩룸">
        근거 기반 연구와 사람의 승인을 거치는 모의투자 실행 환경입니다. 권위 데이터가 없거나 오래되면 화면에 보류로 표시됩니다.
      </PageHeading>
      <MarketStatus />
      <div className="grid"><AnalysisLauncher /></div>
      <div className="grid" aria-label="트레이딩룸 바로가기">
        {destinations.map(([title, href, description]) => (
          <Link className="panel link-card" href={href} key={href}>
            <h2>{title}</h2>
            <p>{description}</p>
          </Link>
        ))}
      </div>
    </>
  );
}

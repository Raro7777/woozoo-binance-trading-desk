import type { Metadata } from "next";
import { TestnetExecutionView } from "../../../../components/testnet-execution-view";
import { PageHeading } from "../../../../components/ui";

export const metadata: Metadata = { title: "Spot Testnet 실행 상태" };

export default async function TestnetExecutionPage({ params }: Readonly<{ params: Promise<{ executionId: string }> }>) {
  const { executionId } = await params;
  return <><PageHeading eyebrow="권위 상태 조회" title="Spot Testnet 실행 상태">응답 유실이나 알 수 없는 결과는 실패로 바꾸지 않습니다. 동일한 클라이언트 주문 ID로 확인될 때까지 대사 상태를 표시합니다.</PageHeading><TestnetExecutionView executionId={executionId} /></>;
}

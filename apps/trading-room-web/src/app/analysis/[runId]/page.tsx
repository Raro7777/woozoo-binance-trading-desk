import type { Metadata } from "next";
import { AnalysisView } from "../../../components/analysis-view";
import { PageHeading } from "../../../components/ui";

export const metadata: Metadata = { title: "분석 실행" };

export default async function AnalysisPage({ params }: Readonly<{ params: Promise<{ runId: string }> }>) {
  const { runId } = await params;
  if (process.env.WOOZOO_E2E_UI_ERRORS === "enabled" && runId === "e2e-render-error") {
    throw new Error("E2E_ROUTE_RENDER_ERROR");
  }
  return (
    <>
      <PageHeading eyebrow="근거에 결합된 분석" title="분석 실행">
        구조화된 인공지능 분석 결과는 참고 자료입니다. 위험 판단, 승인, 권한 부여, 주문과 원장 상태는 결정론적 서버가 확정합니다.
      </PageHeading>
      <AnalysisView runId={runId} />
    </>
  );
}

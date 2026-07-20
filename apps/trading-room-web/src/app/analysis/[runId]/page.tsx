import type { Metadata } from "next";
import { AnalysisView } from "../../../components/analysis-view";
import { PageHeading } from "../../../components/ui";

export const metadata: Metadata = { title: "Analysis run" };

export default async function AnalysisPage({ params }: Readonly<{ params: Promise<{ runId: string }> }>) {
  const { runId } = await params;
  return (
    <>
      <PageHeading eyebrow="Evidence-bound analysis" title="Analysis run">
        Structured AI output is advisory. Risk, approval, authorization, orders, and ledger state remain deterministic server authority.
      </PageHeading>
      <AnalysisView runId={runId} />
    </>
  );
}

import type { Metadata } from "next";
import { TestnetApprovalView } from "../../../../components/testnet-approval-view";
import { PageHeading } from "../../../../components/ui";

export const metadata: Metadata = { title: "Spot Testnet 승인" };

export default async function TestnetProposalPage({ params }: Readonly<{ params: Promise<{ proposalId: string }> }>) {
  const { proposalId } = await params;
  return <><PageHeading eyebrow="별도 사람 승인 경계" title="Spot Testnet 주문 검토">모의투자 승인과 분리된 Testnet 전용 결정입니다. 서버가 만든 정확한 미리보기와 계정 세대, 위험 판단, 대사 상태가 하나의 다이제스트에 결합됩니다.</PageHeading><TestnetApprovalView proposalId={proposalId} /></>;
}

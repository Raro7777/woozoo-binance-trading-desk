import type { Metadata } from "next";
import { ApprovalView } from "../../../components/approval-view";
import { PageHeading } from "../../../components/ui";

export const metadata: Metadata = { title: "제안 승인" };

export default async function ProposalPage({ params }: Readonly<{ params: Promise<{ proposalId: string }> }>) {
  const { proposalId } = await params;
  return (
    <>
      <PageHeading eyebrow="사람 승인 경계" title="정확한 모의주문 검토">
        승인은 하나의 제안, 하나의 위험 판단, 하나의 정책 버전과 서버가 만든 미리보기에 결합됩니다. 실행 권한은 별도로 발급되며 한 번만 사용할 수 있습니다.
      </PageHeading>
      <ApprovalView proposalId={proposalId} />
    </>
  );
}

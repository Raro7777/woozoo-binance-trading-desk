import type { Metadata } from "next";
import { ApprovalView } from "../../../components/approval-view";
import { PageHeading } from "../../../components/ui";

export const metadata: Metadata = { title: "Proposal approval" };

export default async function ProposalPage({ params }: Readonly<{ params: Promise<{ proposalId: string }> }>) {
  const { proposalId } = await params;
  return (
    <>
      <PageHeading eyebrow="Human approval boundary" title="Review the exact Paper order">
        Approval binds one Proposal, one Risk decision, one policy version, and the server-produced preview. Authorization is separate and single-use.
      </PageHeading>
      <ApprovalView proposalId={proposalId} />
    </>
  );
}

import type { Metadata } from "next";
import { AuditTimeline } from "../../components/audit-timeline";
import { PageHeading } from "../../components/ui";

export const metadata: Metadata = { title: "Audit timeline" };

export default function AuditPage() {
  return (
    <>
      <PageHeading eyebrow="Immutable history" title="Audit timeline">
        Follow operator commands, guard outcomes, approvals, authorizations, orders, ledger effects, and Kill events without rewriting completed history.
      </PageHeading>
      <AuditTimeline />
    </>
  );
}

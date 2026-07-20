import type { Metadata } from "next";
import { OperationsConsole } from "../../components/operations-console";
import { PageHeading } from "../../components/ui";

export const metadata: Metadata = { title: "Operations" };

export default function OperationsPage() {
  return (
    <>
      <PageHeading eyebrow="Fail-closed operations" title="Kill Switch and recovery">
        Activation is immediate and recovery is manual, authenticated, audited, and contingent on authoritative server guards.
      </PageHeading>
      <OperationsConsole />
    </>
  );
}

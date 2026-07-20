import type { Metadata } from "next";
import { PaperDesk } from "../../components/paper-desk";
import { PageHeading } from "../../components/ui";

export const metadata: Metadata = { title: "Paper desk" };

export default function PaperPage() {
  return (
    <>
      <PageHeading eyebrow="Deterministic Paper authority" title="Portfolio, orders, and ledger">
        All amounts are exact server-provided Decimal strings. This page never derives balances, fees, PnL, or exposure.
      </PageHeading>
      <PaperDesk />
    </>
  );
}

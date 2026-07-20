import Link from "next/link";
import { AnalysisLauncher } from "../components/analysis-launcher";
import { MarketStatus } from "../components/trading-room";
import { PageHeading } from "../components/ui";

const destinations = [
  ["Paper desk", "/paper", "Inspect authoritative balances, orders, receipts, and ledger state."],
  ["Audit timeline", "/audit", "Trace immutable operator and domain outcomes in order."],
  ["Operations", "/operations", "Monitor the Kill Switch and fail-closed service health."],
] as const;

export default function TradingRoomPage() {
  return (
    <>
      <PageHeading eyebrow="Phase 7 · Local operations" title="Woozoo Trading Room">
        Evidence-led research and human-approved Paper execution. Any missing or stale authority becomes a visible HOLD.
      </PageHeading>
      <MarketStatus />
      <div className="grid"><AnalysisLauncher /></div>
      <div className="grid" aria-label="Trading Room destinations">
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

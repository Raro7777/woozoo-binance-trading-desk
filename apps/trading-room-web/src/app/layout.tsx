import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Woozoo Trading Room", template: "%s · Woozoo Trading Room" },
  description: "A local, human-approved Paper Trading operations room.",
};

const navigation = [
  ["Trading room", "/"],
  ["Paper desk", "/paper"],
  ["Audit", "/audit"],
  ["Operations", "/operations"],
] as const;

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#trading-room-content">Skip to trading room content</a>
        <header className="site-header">
          <Link className="brand" href="/" aria-label="Woozoo Trading Room home">
            <span className="brand-mark" aria-hidden="true">WZ</span>
            <span><strong>Woozoo</strong><small>Trading Room</small></span>
          </Link>
          <div className="mode-lock" title="External exchange execution is unavailable">
            <span aria-hidden="true">●</span> Paper only
          </div>
          <nav aria-label="Primary navigation">
            {navigation.map(([label, href]) => <Link key={href} href={href}>{label}</Link>)}
          </nav>
          <Link className="operator-link" href="/login">Operator session</Link>
        </header>
        <main id="trading-room-content" tabIndex={-1}>{children}</main>
        <footer>
          <p>Local Paper operations · Human approval required · Financial authority remains server-side</p>
        </footer>
      </body>
    </html>
  );
}

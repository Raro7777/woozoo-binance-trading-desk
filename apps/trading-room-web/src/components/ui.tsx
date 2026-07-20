import type { ReactNode } from "react";

export function Hold({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <section className="hold" aria-live="polite" aria-label="Safety hold">
      <strong>HOLD</strong>
      <p>{children}</p>
    </section>
  );
}

export function Status({ value }: Readonly<{ value: string }>) {
  const normalized = value.toLowerCase().replaceAll("_", "-");
  const safeClass = /^(healthy|ready|allowed|approved|issued|open|partial|filled|succeeded)$/.test(normalized)
    ? "positive"
    : /^(invalid|denied|blocked|failed|rejected|revoked|expired|active)$/.test(normalized)
      ? "danger"
      : "neutral";
  return <span className={`status ${safeClass}`}>{value.replaceAll("_", " ")}</span>;
}

export function Field({ label, value, mono = false }: Readonly<{ label: string; value?: string; mono?: boolean }>) {
  return (
    <div className="field">
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{value ?? "Not available"}</dd>
    </div>
  );
}

export function PageHeading({ eyebrow, title, children }: Readonly<{ eyebrow: string; title: string; children: ReactNode }>) {
  return (
    <header className="page-heading">
      <p className="eyebrow">{eyebrow}</p>
      <h1>{title}</h1>
      <p>{children}</p>
    </header>
  );
}

export function Panel({ title, children, id }: Readonly<{ title: string; children: ReactNode; id?: string }>) {
  return (
    <section className="panel" aria-labelledby={id}>
      <h2 id={id}>{title}</h2>
      {children}
    </section>
  );
}

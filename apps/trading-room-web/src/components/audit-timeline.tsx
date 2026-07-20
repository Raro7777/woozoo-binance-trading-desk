"use client";

import { asRecord, asRecords, textValue, type JsonRecord } from "../lib/api";
import { ResourceBoundary, useResource } from "./resource";
import { Status } from "./ui";

export function AuditTimeline() {
  const resource = useResource<JsonRecord>("/api/v1/audit-events");
  return (
    <ResourceBoundary resource={resource}>{(value) => {
      const page = asRecord(value) ?? {};
      const events = asRecords(page.events ?? page.items);
      return (
        <section className="panel full" aria-labelledby="audit-events">
          <h2 id="audit-events">Immutable event timeline</h2>
          <div className="table-wrap" role="region" aria-label="Audit events table" tabIndex={0}>
            <table>
              <caption>Server-ordered events. The cursor is opaque and never interpreted by the browser.</caption>
              <thead><tr><th>Occurred</th><th>Event</th><th>Producer</th><th>Aggregate</th><th>Actor</th><th>Outcome</th></tr></thead>
              <tbody>{events.length === 0 ? <tr><td colSpan={6}>No audit events are available.</td></tr> : events.map((event, index) => <tr key={textValue(event, "event_id") ?? index}>
                <td>{textValue(event, "occurred_at")}</td><td className="mono">{textValue(event, "event_type", "type")}</td>
                <td className="mono">{textValue(event, "producer") ?? "unknown"}</td>
                <td className="mono">{textValue(event, "aggregate_id", "subject_id")}</td><td>{textValue(event, "actor_id") ?? "system"}</td>
                <td><Status value={textValue(event, "outcome", "status") ?? "RECORDED"} /></td>
              </tr>)}</tbody>
            </table>
          </div>
          <p className="muted">Next cursor: <span className="mono">{textValue(page, "next_cursor") ?? "End of timeline"}</span></p>
        </section>
      );
    }}</ResourceBoundary>
  );
}

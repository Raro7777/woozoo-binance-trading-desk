"use client";

import { asRecord, asRecords, textValue, type JsonRecord } from "../lib/api";
import { ResourceBoundary, useResource } from "./resource";
import { diagnosticLabel, Status } from "./ui";

export function AuditTimeline() {
  const resource = useResource<JsonRecord>("/api/v1/audit-events");
  return (
    <ResourceBoundary resource={resource}>{(value) => {
      const page = asRecord(value) ?? {};
      const events = asRecords(page.events ?? page.items);
      return (
        <section className="panel full" aria-labelledby="audit-events">
          <h2 id="audit-events">변경 불가능한 이벤트 타임라인</h2>
          <div className="table-wrap" role="region" aria-label="감사 이벤트 표" tabIndex={0}>
            <table>
              <caption>서버가 정렬한 이벤트입니다. 커서는 불투명하며 브라우저가 해석하지 않습니다.</caption>
              <thead><tr><th>발생 시각</th><th>이벤트</th><th>생성 주체</th><th>집계 대상</th><th>행위자</th><th>결과</th></tr></thead>
              <tbody>{events.length === 0 ? <tr><td colSpan={6}>감사 이벤트가 없습니다.</td></tr> : events.map((event, index) => <tr key={textValue(event, "event_id") ?? index}>
                <td>{textValue(event, "occurred_at")}</td><td>{diagnosticLabel(textValue(event, "event_type", "type") ?? "UNKNOWN_EVENT")}</td>
                <td className="mono">{textValue(event, "producer") ?? "알 수 없음"}</td>
                <td className="mono">{textValue(event, "aggregate_id", "subject_id")}</td><td>{textValue(event, "actor_id") ?? "시스템"}</td>
                <td><Status value={textValue(event, "outcome", "status") ?? "RECORDED"} /></td>
              </tr>)}</tbody>
            </table>
          </div>
          <p className="muted">다음 커서: <span className="mono">{textValue(page, "next_cursor") ?? "타임라인 끝"}</span></p>
        </section>
      );
    }}</ResourceBoundary>
  );
}

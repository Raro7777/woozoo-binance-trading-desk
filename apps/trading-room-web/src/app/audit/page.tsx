import type { Metadata } from "next";
import { AuditTimeline } from "../../components/audit-timeline";
import { PageHeading } from "../../components/ui";

export const metadata: Metadata = { title: "감사 타임라인" };

export default function AuditPage() {
  return (
    <>
      <PageHeading eyebrow="변경 불가능한 이력" title="감사 타임라인">
        완료된 이력을 다시 쓰지 않고 운영자 명령, 보호 조건 결과, 승인, 권한 부여, 주문, 원장 반영과 킬 이벤트를 추적합니다.
      </PageHeading>
      <AuditTimeline />
    </>
  );
}

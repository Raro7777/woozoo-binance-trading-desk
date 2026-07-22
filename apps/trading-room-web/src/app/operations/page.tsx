import type { Metadata } from "next";
import { OperationsConsole } from "../../components/operations-console";
import { PageHeading } from "../../components/ui";

export const metadata: Metadata = { title: "운영" };

export default function OperationsPage() {
  return (
    <>
      <PageHeading eyebrow="장애 시 차단되는 운영" title="킬 스위치 및 복구">
        활성화는 즉시 적용됩니다. 복구는 수동으로 인증·감사되며 서버의 권위 있는 보호 조건을 모두 충족해야 합니다.
      </PageHeading>
      <OperationsConsole />
    </>
  );
}

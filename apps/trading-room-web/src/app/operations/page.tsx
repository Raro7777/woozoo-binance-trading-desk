import type { Metadata } from "next";
import { OperationsConsole } from "../../components/operations-console";
import { ProductStatus } from "../../components/product-status";
import { PageHeading } from "../../components/ui";

export const metadata: Metadata = { title: "운영" };

export default function OperationsPage() {
  return (
    <>
      <PageHeading eyebrow="모의투자 운영 상태" title="제품 상태와 킬 스위치">
        데이터·분석·저장소 상태를 확인하고, 이상 시 새 모의주문을 즉시 차단할 수 있습니다.
      </PageHeading>
      <div className="grid"><ProductStatus /></div>
      <OperationsConsole />
    </>
  );
}

import type { ReactNode } from "react";

export function Hold({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <section className="hold" aria-live="polite" aria-label="안전 보류">
      <strong>보류</strong>
      <p>{children}</p>
    </section>
  );
}

type StatusTone = "positive" | "danger" | "neutral";

const statusPresentations: Readonly<Record<string, Readonly<{ label: string; tone: StatusTone }>>> = {
  ACCEPTED: { label: "접수됨", tone: "positive" },
  ACTIVATED: { label: "활성화됨", tone: "danger" },
  ACTIVE: { label: "활성", tone: "danger" },
  ALLOWED: { label: "허용됨", tone: "positive" },
  ALREADY_ACTIVE: { label: "이미 활성", tone: "danger" },
  ALREADY_REVOKED: { label: "이미 철회됨", tone: "danger" },
  ALREADY_TERMINAL: { label: "이미 종료됨", tone: "neutral" },
  APPROVED: { label: "승인됨", tone: "positive" },
  AUTHENTICATED: { label: "인증됨", tone: "positive" },
  AUTHORIZATION_ISSUED: { label: "실행 권한 발급됨", tone: "positive" },
  BALANCED: { label: "균형", tone: "positive" },
  BLOCKED: { label: "차단됨", tone: "danger" },
  CANCELLED: { label: "취소됨", tone: "neutral" },
  COMPLETE: { label: "완료", tone: "positive" },
  COMPLETED: { label: "완료", tone: "positive" },
  CONSUMED: { label: "사용됨", tone: "positive" },
  CONSUMED_ORDER_CREATED: { label: "사용되어 주문 생성됨", tone: "positive" },
  CREATED: { label: "생성됨", tone: "positive" },
  DEGRADED: { label: "저하됨", tone: "danger" },
  DENIED: { label: "거부됨", tone: "danger" },
  ERROR: { label: "오류", tone: "danger" },
  EXPIRED: { label: "만료됨", tone: "danger" },
  FAILED: { label: "실패", tone: "danger" },
  FILLED: { label: "체결 완료", tone: "positive" },
  HEALTHY: { label: "정상", tone: "positive" },
  HOLD: { label: "보류", tone: "danger" },
  INACTIVE: { label: "비활성", tone: "neutral" },
  INCOMPLETE: { label: "미완료", tone: "danger" },
  INVALID: { label: "유효하지 않음", tone: "danger" },
  INVALIDATED: { label: "무효화됨", tone: "danger" },
  ISSUED: { label: "발급됨", tone: "positive" },
  MISSING: { label: "누락", tone: "danger" },
  NOT_ACTIVE: { label: "활성 아님", tone: "neutral" },
  NOT_ISSUED: { label: "미발급", tone: "neutral" },
  OPEN: { label: "미체결", tone: "positive" },
  PARTIALLY_FILLED: { label: "부분 체결", tone: "positive" },
  PENDING: { label: "대기 중", tone: "neutral" },
  PENDING_APPROVAL: { label: "승인 대기 중", tone: "neutral" },
  PENDING_RISK: { label: "위험 판단 대기 중", tone: "neutral" },
  READY: { label: "준비됨", tone: "positive" },
  RECORDED: { label: "기록됨", tone: "positive" },
  RECONNECTING: { label: "재연결 중", tone: "danger" },
  RECOVERED: { label: "복구됨", tone: "positive" },
  REJECTED: { label: "거절됨", tone: "danger" },
  REVOKED: { label: "철회됨", tone: "danger" },
  STALE: { label: "오래된 데이터", tone: "danger" },
  STOPPED: { label: "중지됨", tone: "danger" },
  SUCCEEDED: { label: "성공", tone: "positive" },
  UNBALANCED: { label: "불균형", tone: "danger" },
  UNHEALTHY: { label: "비정상", tone: "danger" },
  UNKNOWN: { label: "알 수 없음", tone: "danger" },
};

export function statusLabel(value: string): string {
  return statusPresentations[value.toUpperCase()]?.label ?? "알 수 없는 상태";
}

export function statusTone(value: string): StatusTone {
  return statusPresentations[value.toUpperCase()]?.tone ?? "danger";
}

const diagnosticLabels: Readonly<Record<string, string>> = {
  ANALYSIS_COMPLETED: "분석 완료",
  "analysis.run.completed.v1": "분석 실행 완료",
  "analysis.run.held.v1": "분석 실행 보류",
  AUTHORITY_BLOCKED: "서버 권한 조건에 의해 차단됨",
  DATA_INVALID: "데이터가 유효하지 않음",
  DATA_STALE: "데이터가 오래됨",
  EVIDENCE_UNHEALTHY: "근거 데이터가 비정상임",
  "evidence.snapshot.created.v1": "근거 스냅샷 생성",
  KILL_SWITCH_ACTIVATED: "킬 스위치 활성화",
  KILL_SWITCH_ACTIVE: "킬 스위치가 활성 상태임",
  KILL_SWITCH_RECOVERED: "킬 스위치 복구",
  "kill-switch.activated.v1": "킬 스위치 활성화",
  "kill-switch.recovered.v2": "킬 스위치 복구",
  "ledger.transaction.posted.v1": "원장 거래 반영",
  "ledger.transaction.posted.v2": "원장 거래 반영",
  LEDGER_UNHEALTHY: "원장이 비정상임",
  ORDER_CREATED: "주문 생성",
  "market.normalized.recorded.v1": "정규화 시장 데이터 기록",
  "market.quality.changed.v1": "시장 데이터 품질 변경",
  "market.raw.appended.v1": "원시 시장 데이터 추가",
  PAPER_APPROVAL_RECORDED: "모의투자 승인 기록",
  PAPER_APPROVAL_REVOKED: "모의투자 승인 철회",
  "paper.approval.recorded.v1": "모의투자 승인 기록",
  "paper.approval.revoked.v1": "모의투자 승인 철회",
  PAPER_AUTHORIZATION_BLOCKED: "모의투자 실행 권한 차단",
  "paper.authorization.attempted.v1": "모의투자 실행 권한 사용 시도",
  "paper.authorization.blocked.v2": "모의투자 실행 권한 차단",
  "paper.authorization.consumed.v2": "모의투자 실행 권한 사용 완료",
  "paper.authorization.issued.v1": "모의투자 실행 권한 발급",
  PAPER_ORDER_CANCELLED: "모의주문 취소",
  PAPER_ORDER_CANCELLED_BY_KILL: "킬 스위치에 의한 모의주문 취소",
  PAPER_ORDER_CREATED: "모의주문 생성",
  "paper.order.accepted.v1": "모의주문 접수",
  "paper.order.accepted.v2": "모의주문 접수",
  "paper.order.cancelled.v1": "모의주문 취소",
  "paper.order.cancelled.v2": "모의주문 취소",
  "paper.order.filled.v1": "모의주문 체결 완료",
  "paper.order.partially-filled.v1": "모의주문 부분 체결",
  "paper.order.rejected.v1": "모의주문 거절",
  PAPER_WORKER_FAILED: "모의투자 작업자 실패",
  PAPER_WORKER_MISSING: "모의투자 작업자 누락",
  PAPER_WORKER_NOT_READY: "모의투자 작업자 준비 안 됨",
  PAPER_WORKER_STATE_INVALID: "모의투자 작업자 상태가 유효하지 않음",
  PAPER_WORKER_STATE_MISSING: "모의투자 작업자 상태를 찾을 수 없음",
  PAPER_WORKER_STATE_UNAVAILABLE: "모의투자 작업자 상태를 조회할 수 없음",
  PAPER_WORKER_STALE: "모의투자 작업자 상태가 오래됨",
  RECONCILIATION_UNHEALTHY: "대사 상태가 비정상임",
  RISK_ALLOWED: "위험 정책이 허용함",
  "risk.decision.recorded.v1": "위험 판단 기록",
  "risk.decision.recorded.v2": "위험 판단 기록",
  "trade.proposal.created.v1": "거래 제안 생성",
};

export function diagnosticLabel(value: string): string {
  return diagnosticLabels[value] ?? "알 수 없는 진단 정보";
}

export function Status({ value }: Readonly<{ value: string }>) {
  return <span className={`status ${statusTone(value)}`}>{statusLabel(value)}</span>;
}

export function Field({ label, value, mono = false }: Readonly<{ label: string; value?: string; mono?: boolean }>) {
  return (
    <div className="field">
      <dt>{label}</dt>
      <dd className={mono ? "mono" : undefined}>{value ?? "정보 없음"}</dd>
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

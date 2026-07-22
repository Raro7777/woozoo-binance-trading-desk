export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export type JsonRecord = Readonly<Record<string, unknown>>;

export type MarketStatusDataV1 = Readonly<{
  symbol: "BTCUSDT" | "ETHUSDT";
  price: string;
  event_time: string;
  received_at: string;
  quality: "healthy" | "degraded" | "stale" | "invalid" | "reconnecting";
  quality_reasons: readonly string[];
  watermark: Readonly<{
    session_id: string;
    stream: string;
    last_sequence: number;
    observed_at: string;
  }>;
}>;

export type MarketStatusEnvelopeV1 = Readonly<{
  api_version: "v1";
  request_id: string;
  correlation_id: string;
  served_at: string;
  data: MarketStatusDataV1;
  meta: Readonly<{ resource_version: string | null; next_cursor: string | null }>;
}>;

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function responseBody(response: Response): Promise<unknown> {
  try {
    const contentType = response.headers.get("content-type") ?? "";
    return contentType.includes("application/json") ? await response.json() : await response.text();
  } catch {
    throw new ApiError("서버 응답 형식을 해석할 수 없습니다.", 502);
  }
}

async function apiFetch(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(path, init);
  } catch {
    throw new ApiError("제어 API에 연결할 수 없습니다. 연결 상태를 확인한 뒤 다시 시도하세요.", 503);
  }
}

const errorCodeLabels: Readonly<Record<string, string>> = {
  CALLER_UNAUTHORIZED: "요청 권한이 없습니다.",
  COMMAND_GUARD_REJECTED: "서버 안전 조건이 명령을 거부했습니다.",
  CSRF_INVALID: "보안 토큰이 유효하지 않습니다. 세션을 새로 고친 뒤 다시 시도하세요.",
  DEPENDENCY_UNAVAILABLE: "필수 서비스에 연결할 수 없습니다.",
  EVIDENCE_NOT_FOUND: "요청한 근거를 찾을 수 없습니다.",
  EVIDENCE_PROJECTION_UNAVAILABLE: "근거 조회 상태를 사용할 수 없습니다.",
  EVIDENCE_UNAVAILABLE: "정상적인 근거 데이터를 사용할 수 없습니다.",
  HTTPS_REQUIRED: "HTTPS 연결에서만 로그인할 수 있습니다.",
  AUTHENTICATION_FAILED: "비밀번호가 올바르지 않습니다.",
  IDEMPOTENCY_KEY_REQUIRED: "유효한 중복 방지 요청 키가 필요합니다.",
  IDEMPOTENCY_CONFLICT: "같은 요청 키에 서로 다른 내용이 제출되었습니다.",
  MARKET_NOT_FOUND: "요청한 시장을 찾을 수 없습니다.",
  MARKET_PROJECTION_UNAVAILABLE: "시장 조회 상태를 사용할 수 없습니다.",
  ORIGIN_INVALID: "허용되지 않은 요청 출처입니다.",
  PRECONDITION_FAILED: "화면의 버전과 서버 상태가 일치하지 않습니다. 새로 고친 뒤 다시 시도하세요.",
  REQUEST_VALIDATION_FAILED: "요청 값이 유효하지 않습니다.",
  RESET_CONFIRMATION_BLOCKED: "계정 초기화 확인 조건이 충족되지 않았습니다.",
  SCHEMA_INVALID: "요청 형식이 유효하지 않습니다.",
  SESSION_REQUIRED: "운영자 로그인이 필요합니다.",
  TESTNET_DEFAULT_OFF: "Spot Testnet 게이트웨이는 기본 차단 상태입니다.",
  TESTNET_PROJECTION_UNAVAILABLE: "Spot Testnet 운영 상태를 조회할 수 없습니다.",
  PROPOSAL_NOT_FOUND: "Testnet 검토가 가능한 제안을 찾을 수 없습니다.",
  APPROVAL_NOT_FOUND: "Testnet 승인 기록을 찾을 수 없습니다.",
  EXECUTION_NOT_FOUND: "Testnet 실행 기록을 찾을 수 없습니다.",
  RISK_BOOK_NOT_FOUND: "BTC·ETH 현재 호가가 아직 준비되지 않았습니다. 잠시 뒤 다시 시도하세요.",
  PRODUCTION_AUTHORITY_UNAVAILABLE: "분석 또는 위험 판단 서비스가 일시적으로 준비되지 않았습니다. 잠시 뒤 다시 시도하세요.",
  VERSION_MISMATCH: "화면의 상태가 최신 버전이 아닙니다. 새로 고친 뒤 다시 시도하세요.",
};

function errorCode(body: unknown): string | undefined {
  if (isRecord(body)) {
    const nested = isRecord(body.error) ? body.error : isRecord(body.detail) ? body.detail : body;
    const code = nested.code;
    if (typeof code === "string" && code.length > 0) return code;
  }
  return undefined;
}

function errorMessage(body: unknown, fallback: string): string {
  const code = errorCode(body);
  if (code !== undefined) {
    return errorCodeLabels[code] ?? `서버가 요청을 안전하게 보류했습니다 (${code}).`;
  }
  return fallback;
}

export async function apiGet<T = unknown>(path: `/api/v1/${string}`): Promise<T> {
  const response = await apiFetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  const body = await responseBody(response);
  if (!response.ok) {
    throw new ApiError(
      errorMessage(body, `요청에 실패했습니다 (${response.status})`),
      response.status,
      errorCode(body),
    );
  }
  return body as T;
}

async function csrfToken(): Promise<string> {
  const session = await apiGet<JsonRecord>("/api/v1/session");
  const token = session.csrf_token;
  if (typeof token !== "string" || token.length === 0) {
    throw new ApiError("새 명령 토큰을 사용할 수 없습니다. 세션을 새로 고친 뒤 다시 시도하세요.", 409);
  }
  return token;
}

export async function apiCommand<T = unknown>(
  path: `/api/v1/${string}`,
  body: JsonRecord,
  options: Readonly<{ ifMatch?: string; csrf?: boolean; idempotencyKey?: string }> = {},
): Promise<T> {
  const token = options.csrf === false ? undefined : await csrfToken();
  const headers = new Headers({
    Accept: "application/json",
    "Content-Type": "application/json",
    "Idempotency-Key": options.idempotencyKey ?? crypto.randomUUID(),
  });
  if (token !== undefined) headers.set("X-CSRF-Token", token);
  if (options.ifMatch !== undefined) headers.set("If-Match", options.ifMatch);

  const response = await apiFetch(path, {
    method: "POST",
    cache: "no-store",
    credentials: "same-origin",
    headers,
    body: JSON.stringify(body),
  });
  const responseValue = await responseBody(response);
  if (!response.ok) {
    throw new ApiError(
      errorMessage(responseValue, `명령이 접수되지 않았습니다 (${response.status})`),
      response.status,
      errorCode(responseValue),
    );
  }
  return responseValue as T;
}

export async function apiVersionedCommand<T = unknown>(
  path: `/api/v1/${string}`,
  body: JsonRecord,
  expectedVersion: number | undefined,
  idempotencyKey?: string,
): Promise<T> {
  if (expectedVersion === undefined || !Number.isSafeInteger(expectedVersion) || expectedVersion < 0) {
    throw new ApiError("서버 확정 리소스 버전을 사용할 수 없어 명령이 보류되었습니다.", 409);
  }
  return apiCommand<T>(path, { ...body, expected_version: expectedVersion }, {
    ifMatch: String(expectedVersion),
    idempotencyKey,
  });
}

export function asRecord(value: unknown): JsonRecord | undefined {
  return isRecord(value) ? value : undefined;
}

export function asRecords(value: unknown): readonly JsonRecord[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

export function textValue(record: JsonRecord | undefined, ...keys: readonly string[]): string | undefined {
  if (record === undefined) return undefined;
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.length > 0) return value;
    if (typeof value === "number" && Number.isInteger(value)) return String(value);
  }
  return undefined;
}

export function booleanValue(record: JsonRecord | undefined, key: string): boolean | undefined {
  const value = record?.[key];
  return typeof value === "boolean" ? value : undefined;
}

export function versionValue(record: JsonRecord | undefined, ...keys: readonly string[]): number | undefined {
  if (record === undefined) return undefined;
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) return value;
  }
  return undefined;
}

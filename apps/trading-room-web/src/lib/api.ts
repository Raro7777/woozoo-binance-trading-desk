export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
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
  const contentType = response.headers.get("content-type") ?? "";
  return contentType.includes("application/json") ? response.json() : response.text();
}

function errorMessage(body: unknown, fallback: string): string {
  if (isRecord(body)) {
    for (const key of ["message", "detail", "reason"]) {
      const value = body[key];
      if (typeof value === "string" && value.length > 0) return value;
    }
  }
  return typeof body === "string" && body.length > 0 ? body : fallback;
}

export async function apiGet<T = unknown>(path: `/api/v1/${string}`): Promise<T> {
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  const body = await responseBody(response);
  if (!response.ok) throw new ApiError(errorMessage(body, `Request failed (${response.status})`), response.status);
  return body as T;
}

async function csrfToken(): Promise<string> {
  const session = await apiGet<JsonRecord>("/api/v1/session");
  const token = session.csrf_token;
  if (typeof token !== "string" || token.length === 0) {
    throw new ApiError("A fresh command token is unavailable. Refresh the session before retrying.", 409);
  }
  return token;
}

export async function apiCommand<T = unknown>(
  path: `/api/v1/${string}`,
  body: JsonRecord,
  options: Readonly<{ ifMatch?: string; csrf?: boolean }> = {},
): Promise<T> {
  const token = options.csrf === false ? undefined : await csrfToken();
  const headers = new Headers({
    Accept: "application/json",
    "Content-Type": "application/json",
    "Idempotency-Key": crypto.randomUUID(),
  });
  if (token !== undefined) headers.set("X-CSRF-Token", token);
  if (options.ifMatch !== undefined) headers.set("If-Match", options.ifMatch);

  const response = await fetch(path, {
    method: "POST",
    cache: "no-store",
    credentials: "same-origin",
    headers,
    body: JSON.stringify(body),
  });
  const responseValue = await responseBody(response);
  if (!response.ok) {
    throw new ApiError(errorMessage(responseValue, `Command was not accepted (${response.status})`), response.status);
  }
  return responseValue as T;
}

export async function apiVersionedCommand<T = unknown>(
  path: `/api/v1/${string}`,
  body: JsonRecord,
  expectedVersion: number | undefined,
): Promise<T> {
  if (expectedVersion === undefined || !Number.isSafeInteger(expectedVersion) || expectedVersion < 0) {
    throw new ApiError("Authoritative resource version is unavailable. The command is held.", 409);
  }
  return apiCommand<T>(path, { ...body, expected_version: expectedVersion }, { ifMatch: String(expectedVersion) });
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

import { OPERATIONS, type Operation, type OperationId } from './generated/operations';
import type { paths } from './generated/schema';

/**
 * The one place the console talks to the API.
 *
 * "One API. Console and CLI are both generated clients. A hand-written client
 * method fails review." There is no method per operation here, and that is the
 * point: `OPERATIONS` is generated from `openapi.json`, this file performs a
 * request from an entry in it, and there is therefore nowhere for a
 * hand-written method to be written. A reviewer does not have to catch one.
 *
 * The types come from the same document through `openapi-typescript`, so
 * `call('listRuns', …)` is checked against the real response shape and a
 * screen that reads a field the API does not return fails to compile.
 */

/** The response of one operation, as the OpenAPI document declares it. */
export type ResponseOf<Id extends OperationId> = (typeof OPERATIONS)[Id]['path'] extends infer P
  ? P extends keyof paths
    ? Lowercase<(typeof OPERATIONS)[Id]['method']> extends infer M
      ? M extends keyof paths[P]
        ? paths[P][M] extends { responses: infer R }
          ? R extends { 200: { content: { 'application/json': infer B } } }
            ? B
            : R extends { 202: { content: { 'application/json': infer B } } }
              ? B
              : unknown
          : unknown
        : unknown
      : unknown
    : unknown
  : unknown;

/** An RFC 9457 problem document, which is how this API reports every failure. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  code?: string;
  correlationId?: string;
  [key: string]: unknown;
}

/**
 * A failed call, carrying the problem document.
 *
 * AC-U14 requires every error surface to show the title, the available action
 * and a copyable correlation identifier. That is only possible if the client
 * preserves them, so the error is the problem document rather than a string
 * assembled from it.
 */
export class ApiError extends Error {
  readonly problem: Problem;
  readonly status: number;

  constructor(problem: Problem) {
    super(problem.title);
    this.name = 'ApiError';
    this.problem = problem;
    this.status = problem.status;
  }

  /** The identifier an operator quotes when reporting this. */
  get correlationId(): string | undefined {
    return typeof this.problem.correlationId === 'string' ? this.problem.correlationId : undefined;
  }
}

export interface CallOptions {
  /** Path parameters, by the name the OpenAPI path uses. */
  params?: Record<string, string> | undefined;
  /** Query string parameters. Undefined values are omitted, not sent empty. */
  query?: Record<string, string | number | boolean | undefined> | undefined;
  /** The request body, for operations that declare one. */
  body?: unknown;
  /** `If-Match`, for the conditional writes SAD 11E.2 requires. */
  ifMatch?: string | undefined;
  /** `Idempotency-Key`, required on every mutating endpoint. */
  idempotencyKey?: string | undefined;
  signal?: AbortSignal | undefined;
}

/**
 * There is deliberately no `site` option.
 *
 * The API resolves the site from the verified claim and refuses to take it
 * from a header or a query parameter, because the row level security variable
 * is set from whatever resolves there and "a site the caller can name is a
 * site the caller can change". The console switches site by pointing at that
 * site's control plane (`SiteOut.control_plane_uri`), which is what
 * `setBaseUrl` is for.
 */

export interface CallResult<T> {
  data: T;
  /** The `ETag`, when the response carried one, for a later conditional write. */
  etag: string | undefined;
  status: number;
}

/** Where the console talks to. Same origin by default; the dev server proxies. */
export const DEFAULT_BASE_URL = '';

let baseUrl = DEFAULT_BASE_URL;

export function setBaseUrl(url: string): void {
  baseUrl = url.replace(/\/$/, '');
}

/** Build the URL of one operation. Exported because the SSE client needs it too. */
export function urlFor(operation: Operation, options: CallOptions = {}): string {
  let path: string = operation.path;
  for (const name of operation.pathParams) {
    const value = options.params?.[name];
    if (value === undefined) {
      throw new Error(`${operation.operationId} needs the path parameter ${name}`);
    }
    path = path.replace(`{${name}}`, encodeURIComponent(value));
  }
  const query = new URLSearchParams();
  for (const [name, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined) query.set(name, String(value));
  }
  const suffix = query.toString();
  return `${baseUrl}${path}${suffix ? `?${suffix}` : ''}`;
}

/**
 * Perform one operation.
 *
 * A non-2xx response becomes an `ApiError` carrying the problem document. A
 * response that is not a problem document still becomes one, because a screen
 * that has to handle two error shapes handles one of them badly.
 */
export async function call<Id extends OperationId>(
  id: Id,
  options: CallOptions = {},
): Promise<CallResult<ResponseOf<Id>>> {
  const operation: Operation = OPERATIONS[id];
  const headers: Record<string, string> = {
    Accept: 'application/json, application/problem+json',
  };
  if (options.body !== undefined) headers['Content-Type'] = 'application/json';
  if (options.ifMatch !== undefined) headers['If-Match'] = options.ifMatch;
  if (options.idempotencyKey !== undefined) headers['Idempotency-Key'] = options.idempotencyKey;

  const response = await fetch(urlFor(operation, options), {
    method: operation.method,
    headers,
    body: options.body === undefined ? null : JSON.stringify(options.body),
    ...(options.signal ? { signal: options.signal } : {}),
  });

  const etag = response.headers.get('ETag') ?? undefined;
  const text = await response.text();
  const parsed: unknown = text === '' ? null : safeJson(text);

  if (!response.ok) {
    throw new ApiError(asProblem(parsed, response.status, operation));
  }
  return { data: parsed as ResponseOf<Id>, etag, status: response.status };
}

/** A fresh idempotency key. Every mutating call needs one (SAD 11E.2). */
export function idempotencyKey(): string {
  return crypto.randomUUID();
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

function asProblem(parsed: unknown, status: number, operation: Operation): Problem {
  if (parsed !== null && typeof parsed === 'object' && 'title' in parsed) {
    return { status, type: 'about:blank', ...(parsed as Record<string, unknown>) } as Problem;
  }
  // A failure that is not a problem document still has to reach the operator
  // as one: AC-U14 asks every error surface for a title and an action, and a
  // surface that got a bare string cannot provide either.
  return {
    type: 'about:blank',
    title: `${operation.summary || operation.operationId} failed`,
    status,
    detail:
      parsed !== null && typeof parsed === 'object' && 'detail' in parsed
        ? String(parsed.detail)
        : `The API returned ${String(status)} with no problem document.`,
  };
}

export { OPERATIONS };
export type { Operation, OperationId };

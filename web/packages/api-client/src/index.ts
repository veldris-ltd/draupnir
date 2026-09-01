/**
 * The typed client for the DRAUPNIR API.
 *
 * `generated/schema.d.ts` is written by `pnpm run generate:client` from
 * `docs/api/openapi.json`. SAD 11H: the pipeline regenerates it and fails on
 * any diff, because a hand edited client is the most common way a generated
 * interface quietly stops being generated.
 */
import type { paths } from './generated/schema';

export type { paths };

/** A path of the API, keyed exactly as the OpenAPI document keys it. */
export type ApiPath = keyof paths;

/** The success body of `GET /healthz`. */
export type Health = paths['/healthz']['get']['responses']['200']['content']['application/json'];

/** The success body of `GET /readyz`. */
export type Readiness = paths['/readyz']['get']['responses']['200']['content']['application/json'];

/** The base URL the console talks to. */
export const DEFAULT_BASE_URL = '/';

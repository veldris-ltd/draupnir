/**
 * The typed client for the DRAUPNIR API.
 *
 * `generated/schema.d.ts` and `generated/operations.ts` are both written from
 * `docs/api/openapi.json` by the pipeline, which regenerates them and fails on
 * any diff (AC-Q2, SAD 11H). The schema gives the types; the operation table
 * gives the call sites. Between them there is no per-operation method for
 * anyone to hand-write, which is what "a hand-written client method fails
 * review" asks for -- enforced by there being nowhere to write one rather than
 * by a reviewer noticing.
 */
export {
  ApiError,
  DEFAULT_BASE_URL,
  OPERATIONS,
  call,
  idempotencyKey,
  setBaseUrl,
  urlFor,
} from './client';
export type {
  CallOptions,
  CallResult,
  Operation,
  OperationId,
  Problem,
  ResponseOf,
} from './client';

export { GENERATED_FROM_OPENAPI_VERSION } from './generated/operations';

import type { paths } from './generated/schema';

export type { paths };

/** A path of the API, keyed exactly as the OpenAPI document keys it. */
export type ApiPath = keyof paths;

/** The success body of `GET /healthz`. */
export type Health = paths['/healthz']['get']['responses']['200']['content']['application/json'];

/** The success body of `GET /readyz`. */
export type Readiness = paths['/readyz']['get']['responses']['200']['content']['application/json'];

/** One run, as the board and the detail screen receive it. */
export type Run =
  paths['/v1/runs']['get']['responses']['200']['content']['application/json']['items'][number];

/** One registered site, for the switcher. */
export type Site =
  paths['/v1/sites']['get']['responses']['200']['content']['application/json']['items'][number];

/** One model in the registry. */
export type Model =
  paths['/v1/models']['get']['responses']['200']['content']['application/json']['items'][number];

/** One entry of the approval queue, with its gate results. */
export type Approval =
  paths['/v1/gates']['get']['responses']['200']['content']['application/json']['items'][number];

/** One source in the licence register. */
export type Source =
  paths['/v1/sources']['get']['responses']['200']['content']['application/json']['items'][number];

/** A lineage attestation. */
export type Lineage =
  paths['/v1/lineage/{artefact}']['get']['responses']['200']['content']['application/json'];

/** A ledger slice with its verification. */
export type LedgerSlice =
  paths['/v1/ledger']['get']['responses']['200']['content']['application/json'];

/** The rendered plan a dry run returns. */
export type DryRun =
  paths['/v1/runs/dry-run']['post']['responses']['200']['content']['application/json'];

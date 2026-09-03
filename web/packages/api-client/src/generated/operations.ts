/* Generated operation table. Do not edit.
 *
 * Produced by `scripts/generate_ts_operations.py` from `docs/api/openapi.json`.
 * The pipeline regenerates this file and fails on any diff (AC-Q2).
 *
 * There is deliberately no method per operation here and none in `client.ts`
 * either: a generated table plus one generic caller means a hand-written
 * client method is not something a reviewer has to catch, because there is
 * nowhere to write one.
 */

export const GENERATED_FROM_OPENAPI_VERSION = "0.1.0" as const;

/** One operation of the API contract, as a client sees it. */
export interface Operation {
  readonly operationId: string;
  readonly method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  readonly path: string;
  readonly summary: string;
  readonly pathParams: readonly string[];
  readonly hasBody: boolean;
}

export const OPERATIONS = {
  getHealth: {
    operationId: "getHealth",
    method: "GET",
    path: "/healthz",
    summary: "Liveness",
    pathParams: [],
    hasBody: false,
  },
  getMetrics: {
    operationId: "getMetrics",
    method: "GET",
    path: "/metrics",
    summary: "Prometheus metrics",
    pathParams: [],
    hasBody: false,
  },
  getReadiness: {
    operationId: "getReadiness",
    method: "GET",
    path: "/readyz",
    summary: "Readiness",
    pathParams: [],
    hasBody: false,
  },
  getArray: {
    operationId: "getArray",
    method: "GET",
    path: "/v1/arrays",
    summary: "The adapter array and its element states",
    pathParams: [],
    hasBody: false,
  },
  listCorpora: {
    operationId: "listCorpora",
    method: "GET",
    path: "/v1/corpora",
    summary: "Corpora by jurisdiction, with curation progress",
    pathParams: [],
    hasBody: false,
  },
  curateCorpus: {
    operationId: "curateCorpus",
    method: "POST",
    path: "/v1/corpora/{iso3}/curate",
    summary: "Run the curation pipeline",
    pathParams: ["iso3"],
    hasBody: false,
  },
  ingestCorpus: {
    operationId: "ingestCorpus",
    method: "POST",
    path: "/v1/corpora/{iso3}/ingest",
    summary: "Ingest and hash a jurisdiction's sources",
    pathParams: ["iso3"],
    hasBody: false,
  },
  streamSiteEvents: {
    operationId: "streamSiteEvents",
    method: "GET",
    path: "/v1/events",
    summary: "Watch this site's state deltas",
    pathParams: [],
    hasBody: false,
  },
  listGates: {
    operationId: "listGates",
    method: "GET",
    path: "/v1/gates",
    summary: "The approval queue",
    pathParams: [],
    hasBody: false,
  },
  decideGate: {
    operationId: "decideGate",
    method: "POST",
    path: "/v1/gates/{gate_id}/decide",
    summary: "Approve or reject",
    pathParams: ["gate_id"],
    hasBody: true,
  },
  getLedger: {
    operationId: "getLedger",
    method: "GET",
    path: "/v1/ledger",
    summary: "Ledger slice with chain verification",
    pathParams: [],
    hasBody: false,
  },
  getLedgerEntry: {
    operationId: "getLedgerEntry",
    method: "GET",
    path: "/v1/ledger/{entry_hash}",
    summary: "One ledger entry, with its hash recomputed",
    pathParams: ["entry_hash"],
    hasBody: false,
  },
  getLineage: {
    operationId: "getLineage",
    method: "GET",
    path: "/v1/lineage/{artefact}",
    summary: "Full lineage attestation",
    pathParams: ["artefact"],
    hasBody: false,
  },
  exportAttestation: {
    operationId: "exportAttestation",
    method: "GET",
    path: "/v1/lineage/{artefact}/attestation",
    summary: "A signed lineage bundle, for export",
    pathParams: ["artefact"],
    hasBody: false,
  },
  listModels: {
    operationId: "listModels",
    method: "GET",
    path: "/v1/models",
    summary: "The model registry",
    pathParams: [],
    hasBody: false,
  },
  getModel: {
    operationId: "getModel",
    method: "GET",
    path: "/v1/models/{artefact}",
    summary: "One model, its artefacts and its gate results",
    pathParams: ["artefact"],
    hasBody: false,
  },
  listPlugins: {
    operationId: "listPlugins",
    method: "GET",
    path: "/v1/plugins",
    summary: "Installed plug-ins and signature status",
    pathParams: [],
    hasBody: false,
  },
  getPolicy: {
    operationId: "getPolicy",
    method: "GET",
    path: "/v1/policy",
    summary: "The licence policy in force, and the one before it",
    pathParams: [],
    hasBody: false,
  },
  getRelease: {
    operationId: "getRelease",
    method: "GET",
    path: "/v1/releases/{artefact}",
    summary: "The release package",
    pathParams: ["artefact"],
    hasBody: false,
  },
  publishRelease: {
    operationId: "publishRelease",
    method: "POST",
    path: "/v1/releases/{artefact}/publish",
    summary: "Publish a release",
    pathParams: ["artefact"],
    hasBody: false,
  },
  listRetention: {
    operationId: "listRetention",
    method: "GET",
    path: "/v1/retention",
    summary: "Retention actions, due and executed",
    pathParams: [],
    hasBody: false,
  },
  getRoles: {
    operationId: "getRoles",
    method: "GET",
    path: "/v1/roles",
    summary: "Roles, their permissions, and what each route requires",
    pathParams: [],
    hasBody: false,
  },
  listRuns: {
    operationId: "listRuns",
    method: "GET",
    path: "/v1/runs",
    summary: "List runs",
    pathParams: [],
    hasBody: false,
  },
  submitRun: {
    operationId: "submitRun",
    method: "POST",
    path: "/v1/runs",
    summary: "Submit a run specification",
    pathParams: [],
    hasBody: true,
  },
  dryRunSpecification: {
    operationId: "dryRunSpecification",
    method: "POST",
    path: "/v1/runs/dry-run",
    summary: "Render a run specification without submitting it",
    pathParams: [],
    hasBody: true,
  },
  getRun: {
    operationId: "getRun",
    method: "GET",
    path: "/v1/runs/{run_id}",
    summary: "Inspect a run",
    pathParams: ["run_id"],
    hasBody: false,
  },
  cancelRun: {
    operationId: "cancelRun",
    method: "POST",
    path: "/v1/runs/{run_id}/cancel",
    summary: "Cancel a run",
    pathParams: ["run_id"],
    hasBody: true,
  },
  streamRunEvents: {
    operationId: "streamRunEvents",
    method: "GET",
    path: "/v1/runs/{run_id}/events",
    summary: "Watch a run's state deltas",
    pathParams: ["run_id"],
    hasBody: false,
  },
  retryRun: {
    operationId: "retryRun",
    method: "POST",
    path: "/v1/runs/{run_id}/retry",
    summary: "Retry a run",
    pathParams: ["run_id"],
    hasBody: false,
  },
  search: {
    operationId: "search",
    method: "GET",
    path: "/v1/search",
    summary: "Search runs, sources and ledger entries",
    pathParams: [],
    hasBody: false,
  },
  listSites: {
    operationId: "listSites",
    method: "GET",
    path: "/v1/sites",
    summary: "The registered sites",
    pathParams: [],
    hasBody: false,
  },
  listSources: {
    operationId: "listSources",
    method: "GET",
    path: "/v1/sources",
    summary: "List registered sources",
    pathParams: [],
    hasBody: false,
  },
  registerSource: {
    operationId: "registerSource",
    method: "POST",
    path: "/v1/sources",
    summary: "Register a corpus source",
    pathParams: [],
    hasBody: true,
  },
  getSweep: {
    operationId: "getSweep",
    method: "GET",
    path: "/v1/sweeps/{run_id}",
    summary: "A reweighting sweep, as merge points against gates",
    pathParams: ["run_id"],
    hasBody: false,
  },
} as const satisfies Record<string, Operation>;

/** Every operation identifier the API declares. */
export type OperationId = keyof typeof OPERATIONS;

# API conventions

What `draupnir/api/` actually does. Where this differs from the SAD, the code
is right and the specification is amended.

## The nine conventions, and where each is enforced

| Convention | Where it lives | What happens when it is missed |
|---|---|---|
| `/v1` path versioning | `create_app` mounts one `APIRouter(prefix="/v1")` | a router written with its own prefix answers `/v1/v1/...` |
| Role declaration | `@needs` / `@unauthenticated`, checked by `enforce_declarations` | `create_app` raises `UndeclaredRouteError` before a socket is opened |
| Stable `operation_id` | the route decorator | `scripts/generate_cli.py` exits naming the route |
| RFC 9457 problems | `ProblemError`, `EXCEPTION_HANDLERS`, `DEFAULT_RESPONSES` | a `{"detail": …}` body, and a generated client with an untyped error path |
| camelCase on the wire | `Wire`, via `alias_generator=to_camel` | the console reads `undefined` and nothing says why |
| Cursor pagination | `PageSize`, `Cursor`, `PageMeta` | offset paging skips rows under concurrent writes |
| `Idempotency-Key` | `require_idempotency_key` | a retry acts twice, or every call is refused 428 |
| ETag / `If-Match` | `require(...)`, `as_problem` | a lost update, silently |
| RFC 3339 with an offset | `deps.now()`, ruff's `DTZ` rules | a naive datetime, which ruff refuses |

## Dependency aliases

All in `draupnir.api.deps`. Use the alias; do not re-declare the parameter.

```python
Reading  # the read model, whichever implementation is installed
Context  # request id, site id, actor -- no authorisation
Guarded  # the same, after the guard has run. Use this one.
PageSize  # a validated limit, 1..200
Cursor  # the opaque cursor from a previous page's nextCursor
IfMatch  # the If-Match header
IdempotencyKey  # the Idempotency-Key header
```

`Guarded` rather than `Context` in a handler: the guard runs as a dependency,
so taking `Context` gets you a handler that runs unauthenticated.

Helpers, in the order a mutation uses them:

```python
require_idempotency_key(key)  # 428 if absent
replay_or_reserve(key, ctx, payload)  # returns the stored body on a replay
require(subject, state, if_match)  # 428 / 412 through as_problem
accepted(run_id, detail=...)  # the 202 body of AC-B9
complete(key, ctx, status=..., body=...)  # record what to replay
release(key, ctx)  # a failed request did not act
```

`release` on the failure path is not optional. Without it the key is burnt on
a request that did nothing, and the caller can never retry it.

## The read model

`draupnir.api.reading.ReadModel` is a Protocol with two implementations:

- `EmptyReadModel` answers nothing. It is the default, and it is what the
  contract tests run against — so "does a 404 carry a problem document" needs
  no PostgreSQL.
- `DatabaseReadModel` reads through `_scoped()`, which sets
  `draupnir.site_id` on the session. Row-level security shows zero rows to a
  session that has not set it, so AC-B10 and AC-F18 are enforced by the
  database rather than by remembering a `WHERE` clause.

Adding a read means adding the method in three places: the Protocol and both
implementations. There is no base class, on purpose — a base class with a
default implementation is how one of the two silently stops being updated.

SQL is assembled from literal fragments with bound values, and the first string
literal of each assembled query carries
`# noqa: S608 -- literal fragments, bound values`.

A parameter compared against a nullable column needs a cast:
`CAST(:jurisdiction AS text)`. Without it PostgreSQL raises
`AmbiguousParameterError` on `:x IS NULL OR col = :x`.

## Wire models

Every model extends `Wire`: `alias_generator=to_camel`, `populate_by_name=True`,
`extra="forbid"`. Construct with snake_case in Python; it serialises camelCase.

Every field carries a `description`. They are what the generated client's
documentation and the console's tooltips are made of, so writing them here is
what stops them being written twice.

Models shared by two routers live in `draupnir/api/schemas.py`. Models used by
one live in that router — `health.py` is the shape of it. Move a model when the
second router needs it, not before.

Field types worth knowing, because guessing them has cost time:

- `artefact_kind` is one of `corpus_raw`, `corpus_curated`, `base_model`,
  `substrate`, `adapter`, `merged`, `quantised`, `report`.
- A ledger entry's `subject_id` is a `str`, not a `UUID`. A site id is `sindri`
  and a plug-in is `hamarr.llamafactory/v1`.
- `created_at` and `updated_at` are nullable. A placeholder `datetime.min`
  renders as year 1 and looks like data.

## What the surface contract asserts

`tests/contract/test_api_surface.py`, and a new endpoint has to satisfy all of
it:

- Every route declares a role, and one that does not stops `create_app`.
- Every operation has an `operationId` and a `default` response.
- The exported `docs/api/openapi.json` has the same paths as the application.
- Every error is a problem document with a stable type URI; no bare 500.
- A mutating endpoint without an `Idempotency-Key` is refused 428.
- A conditional write without `If-Match` is refused; a stale one is 412.
- A collection returns a cursor-shaped page; an invalid page size is a problem
  document.
- The published permissions table comes from the same attribute the guard
  reads, so the two cannot drift.

## Generated artefacts

Three files are generated from the OpenAPI document, and a contract test
compares the export against the application:

```
docs/api/openapi.json                                make openapi
draupnirctl/_generated.py                            make clients
web/packages/api-client/src/generated/operations.ts  make clients
```

`scripts/generate_cli.py` and `scripts/generate_ts_operations.py` share
`collect()`, so the two clients cannot disagree about what the API offers.
Neither client has a method per operation: there is a generated table and one
generic caller, which makes "a hand-written client method fails review" true by
there being nowhere to write one.

## Two live exceptions

`GET /v1/sites` is the one read not scoped to a single site. It returns the
registry — identifier, name, location, endpoint, anchor state — and no run,
corpus, artefact or ledger data. It is the list of scopes, not an aggregate
across them, which is the distinction AC-U11 draws.

`DRAUPNIR_DEV=1` installs a development principal. It verifies nothing, accepts
no token, cannot be reached by a request, and logs a warning naming itself on
every startup that installs it. It exists because without it a running stack
answers 401 to everything and the four journeys need a full identity
deployment to run at all.

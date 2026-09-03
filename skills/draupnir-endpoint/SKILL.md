---
name: draupnir-endpoint
description: >
  Add an operation to the DRAUPNIR API with the conventions of SAD 11E.2
  already applied — role declaration, operation id, camelCase wire models,
  problem documents, cursor pagination, Idempotency-Key and If-Match. Use when
  adding, changing or reviewing an HTTP endpoint: "add an endpoint", "new API
  route", "expose X over the API", "the console needs Y".
---

# draupnir-endpoint

Scaffold an operation that starts the application, generates both clients and
passes the API surface contract without an edit.

## Why a scaffold rather than a description

The nine conventions of SAD 11E.2 are individually obvious and collectively
easy to half-apply. Two of them fail loudly — a missing role declaration stops
`create_app`, a missing `operationId` stops the client generator — and the rest
fail quietly, at the far end, in somebody else's code:

- `/healthz` answered `site_id` while every other endpoint answered `siteId`,
  so "one API" was false in the first place every client looks.
- The CLI sent no `Idempotency-Key`, so every mutating command would have been
  refused 428 the first time anyone ran one.

Both were found in the console build, not in review. The scaffold makes them
unwritable.

**Do not hand-write an endpoint from this document.** Run the scaffold, then
write the handler body — which is the part that is actually about your feature.

## Use it

```bash
python skills/draupnir-endpoint/scripts/new_endpoint.py --router retention --shape collection --path /retention --operation-id listRetention --summary "Corpora and their deletion dates" --permission READ --model Retention
```

Three shapes, and the shape is the decision that matters:

| `--shape` | Produces | Use for |
|---|---|---|
| `collection` | `GET`, cursor paginated, `PageMeta` | a list of anything |
| `item` | `GET` one, path parameter, 404 as a problem | a single resource |
| `mutation` | `POST`, `Idempotency-Key`, `If-Match`, 202 + run id | anything that changes state |

It writes:

```
draupnir/api/routers/retention.py            the router, its wire models, the operation
tests/contract/test_retention_endpoint.py    the surface checks for it
draupnir/api/app.py                          the include_router line
```

Then, in one command, because the generated artefacts are checked against the
application:

```bash
make openapi && make clients && make test-contract
```

## What the generated operation already gets right

| Convention | How |
|---|---|
| AC-B6, role declaration | `@needs(Permission.X)` on the endpoint. Without it `enforce_declarations` stops `create_app`, before a socket is opened. |
| AC-N10, generated clients | An explicit `operation_id`. It is the name of the generated CLI command and of the TypeScript operation; the generator exits if one is missing. |
| camelCase on the wire | Every model extends `Wire`, which carries the alias generator. Nothing is spelled twice. |
| AC-B2, problem documents | The handler raises `ProblemError`, never `HTTPException`. The `default` response is declared once on the application. |
| AC-B3, cursor pagination | `PageSize` and `Cursor` dependencies, and a `PageMeta` with `nextCursor`. There is no offset to reach for. |
| AC-B1, idempotency | A mutation calls `require_idempotency_key` first, so a request without one is refused 428 rather than acting twice. |
| AC-B4, conditional writes | `require(...)` against `If-Match`, and a stale tag is 412. |
| AC-B9, nothing blocks | A mutation returns 202 with an identifier. No HTTP request waits on training. |
| SAD 9.4 | The permission is one of the ten in `Permission`, and the roles that satisfy it are published in the OpenAPI description from the same attribute the guard reads. |

## What you then write

The handler body, and the read it depends on.

The generated read returns an empty page, which is what `EmptyReadModel`
answers and is therefore not a fabrication. To make it return something, add
the method to the `ReadModel` Protocol in `draupnir/api/reading.py` and to both
implementations — `EmptyReadModel` and `DatabaseReadModel`. The scaffold prints
the signature to add.

`DatabaseReadModel` reads through `_scoped()`, which sets `draupnir.site_id`.
That is not a convenience: row-level security shows zero rows to a session that
has not set it, so site scoping is enforced by the database rather than by
remembering a `WHERE` clause (AC-B10, AC-F18).

## Refusals

**Do not raise `HTTPException`.** It produces a `{"detail": …}` body, which is
not a problem document, and AC-B2 says every error is one with a stable type
URI. Raise `ProblemError`.

**Do not add an offset parameter.** Not as an option, not "just for the admin
screen". A page that renumbers under concurrent writes silently skips rows.

**Do not make `Idempotency-Key` optional on a mutation.** The retry that
actually duplicated something is the one that omitted the header.

**Do not add an unscoped aggregate read.** `GET /v1/sites` is the single
exception and it returns the registry — identifiers, names, anchor states — and
no run, corpus, artefact or ledger data at all. It is the list of scopes, not
an aggregate across them (AC-U11).

**Do not hand-edit `docs/api/openapi.json`, `draupnirctl/_generated.py` or
`web/packages/api-client/src/generated/operations.ts`.** All three are
generated, and a contract test compares the exported document against the
application.

## References

- `references/conventions.md` — the nine conventions, the dependency aliases,
  the read model, and what the surface contract actually asserts.

## Verified

`tests/contract/test_skills.py` scaffolds an operation into a temporary
directory, mounts it on a real `FastAPI` application, runs
`enforce_declarations` over it and drives it with a `TestClient`: unauthorised
requests, the OpenAPI operation's shape, and — for a mutation — the 428 when
the key is absent. No edit in between.

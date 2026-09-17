# Remedial fixes

A full-platform implementation audit of DRAUPNIR at `e38758a` (branch `dev`),
and the work needed to close what it found.

> **What this document is.** Every stage of the pipeline was run, every test
> level executed, and the tree read module by module against
> `docs/build/draupnir-sad.md`, `docs/build/draupnir-ux.md` and
> `docs/acceptance/`. Each finding below is something that was *observed* —
> reproduced by running a command or by following a call path to its end — not
> something inferred from a name or a docstring. The reproduction is given so
> that each one can be disagreed with.
>
> Each finding carries a **Prompt** — an instruction that can be handed to an
> implementer or an agent without this document for context — and **Acceptance
> criteria** that are testable and say where in the pipeline they are gated. A
> fix with no gate is a fix that regresses.

---

## 1  What already passes

This is not a failing repository, and the findings below should be read against
what it does. Every one of these was executed during the audit:

| Stage | Result |
|---|---|
| `lint` (ruff format + check) | pass |
| `typecheck` (mypy --strict) | pass |
| `lint-web` (prettier, eslint, token linter, contrast, `tsc --noEmit`) | pass — 133 files, no hard-coded colour, spacing or radius; 74 contrast pairings recomputed |
| `imports` (import-linter) | pass — 7 contracts, 187 files, 917 dependencies |
| `test-unit` | **940 passed**, 91.99% over the measured set |
| `test-property` | 14 passed |
| `test-contract` | 239 passed, 88.40% over the routers |
| `test-integration` | 103 passed against real PostgreSQL and MinIO, 87.77% |
| `test-frontend` | 984 passed |
| `test-e2e` | 38 passed — the four journeys plus the screen sweep |
| `test-a11y` | 70 passed — axe over 23 routes and 220 stories |
| `test-visual` | 8 shards passed |
| `acceptance` | 90 criteria, every one with a status and evidence |
| `procedure` (M1–M10) | run `01a07e67…` reached RELEASED |
| `audit` (pip-audit) | no known Python vulnerabilities |
| `crypto-inventory` | 8 entries, 7 in use |

The ledger, the state machine, the projector, the placement and array algebra,
the gate evaluation, the design system and the accessibility work are all real,
tested and load-bearing. **The findings below are almost entirely of one kind:
a correct, well-tested component that nothing in a running system calls.**

---

## 2  The shape of the problem

Twenty-three of the 102 non-`__init__` modules under `draupnir/` have no
importer anywhere outside `tests/`. Reproduce with:

```bash
python - <<'PY'
import ast
from pathlib import Path
mods = {'.'.join(p.with_suffix('').parts): p
        for p in Path('draupnir').rglob('*.py')
        if '__pycache__' not in p.parts and p.name != '__init__.py'}
def imports_of(p):
    out = set()
    for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            out.add(n.module); out |= {f'{n.module}.{a.name}' for a in n.names}
    return out
src = {p: imports_of(p) for d in ('draupnir','tests','plugins','scripts','draupnirctl','tools')
       for p in Path(d).rglob('*.py') if '__pycache__' not in p.parts}
for mod, p in sorted(mods.items()):
    init = p.parent / '__init__.py'
    if not [s for s, i in src.items()
            if s not in (p, init) and s.parts[0] != 'tests' and mod in i]:
        print(mod)
PY
```

Output:

```
draupnir.api.assurance                 draupnir.raun.regression
draupnir.brisingamen.merge             draupnir.raun.transitions
draupnir.core.infrastructure.models    draupnir.skidbladnir.formats
draupnir.gleipnir.copyright            draupnir.skidbladnir.publish
draupnir.hamarr.config                 draupnir.svalinn.egress
draupnir.hodd.quota                    draupnir.svalinn.integrity
draupnir.interfaces.testing.suite      draupnir.svalinn.inventory
draupnir.megingjord.anchors            draupnir.svalinn.pki
draupnir.megingjord.registry           draupnir.svalinn.sandbox
draupnir.motsognir.arrays              draupnir.svalinn.scanning
draupnir.motsognir.retry               draupnir.svalinn.secrets
draupnir.worker.__main__               draupnir.svalinn.signing
```

`worker.__main__` is an entry point and `interfaces.testing.suite` is the
published conformance harness; both are correct. `core.infrastructure.models`
is reached through SQLAlchemy metadata, and `svalinn.inventory` is reached from
`tasks.py crypto-inventory`. The remaining nineteen are the platform's
security, federation, publication and array controls, and none of them is on a
path a request or a worker tick can take.

`docs/acceptance/imhotep-reconciliation.md` marks all of these IMPLEMENTED. The
distinction it uses — "built, and exercised by something that runs in the
pipeline" — is satisfied by a unit test, which is why the marks are defensible
and the system is still not wired. **A third mark is needed: built, tested, and
reachable from a running deployment.**

---

## 2A  A companion register: the estate

This register asks whether the platform is internally complete. A second one,
[remedial-fixes-estate.md](remedial-fixes-estate.md), asks whether it runs on
the Sindri Forge as VLD-INF-SINDRI-001 Rev 3.3 and VLD-WIR-SINDRI-001 Rev 2.0
actually build and wire it. Twenty-one findings, of which one is a blocker:
ALVISS is a Mac mini running macOS, and `deploy/install.sh` requires systemd
and Podman.

Several findings appear in both registers from different directions and should
be fixed once. RF-13 here (the 56-element array is not built) is RF-E10 there
(the estate's whole placement strategy is `--array=0-55%3`); RF-28 here (CON-B
carries no thermal reading) is RF-E15 there (the reading is in Prometheus on
REGIN and DRAUPNIR is not connected to it); RF-04 here (nothing publishes an
image) is RF-E20 there (no registry exists on the estate to publish to).

---

## 3  Severity

| | Meaning |
|---|---|
| **P1** | A correctly-configured production deployment does not function |
| **P2** | A control the documentation says is enforced is not enforced |
| **P3** | A specified capability is absent or simulated |
| **P4** | A stated API or edge contract is not met |
| **P5** | A build gate does not gate what it claims to |
| **P6** | Documentation states something the code does not do |

---

## 4  Findings

### RF-01 — P1 — There is no authentication layer

> **Status: done.**
>
> `draupnir/api/authentication.py` verifies a bearer token against
> MEGINGJORD's JWKS and does **nothing else** — it sets
> `request.state.claims` or leaves the attribute unset, and never returns 401
> or inspects a role. `guards` already decides authorisation from a
> declaration on each route and startup checks every route carries one; a
> middleware that also made access decisions would be a second answer to the
> same question, and the two would eventually disagree.
>
> **A library rather than `cryptography` directly.** Hand-rolled JWS
> verification is where algorithm-confusion bugs come from. The `alg` header
> is attacker controlled, so the header is read for `kid` only and
> `jwt.decode` is given an explicit asymmetric allow-list
> (`identity.TOKEN_ALGORITHMS`). Both confusion tests were verified to bite:
> adding `HS256` to that list makes a hand-forged token — HMAC over the
> issuer's *public* key, which is public — verify as an admin.
>
> The `alg: none` and HMAC forgeries are built by hand rather than with
> `jwt.encode`, because PyJWT refuses to *sign* that way. That refusal is a
> defence on the signing side and says nothing about what a verifier accepts,
> so the attacker's half had to be written out.
>
> **The JWKS fetch goes through the egress broker.** `PyJWKClient` would have
> fetched it with its own urllib call — outbound traffic no allow list had
> decided, which is threat T11 and exactly what RF-E17 was about. Owning the
> fetch also means owning the cache, which mattered: an unknown `kid`
> triggers a refresh, because that is what key rotation looks like from here
> — and an unauthenticated caller controls `kid`. `MIN_REFRESH_SECONDS`
> bounds it, so a token carrying a random `kid` each time cannot be used to
> hammer the identity provider from outside. A key the issuer *withdrew*
> stops verifying, because the set is replaced wholesale rather than merged.
>
> **`create_app()` refuses to start** with neither an issuer nor
> `DRAUPNIR_DEV`, naming the settings. An API that starts and answers 401 to
> everything looks like a broken deployment: it gets debugged for an
> afternoon and then somebody sets `DRAUPNIR_DEV` to make it stop.
>
> **Two consequences worth stating.** The module-level `app = create_app()`
> became a PEP 562 `__getattr__`, because a module that raises on import
> cannot be imported by a test, a generator or the acceptance pack — none of
> which want a running application and all of which want the module. And
> `tests/conftest.py` configures the session with an issuer, because 124
> tests build an app: that is the fail-closed control working, and the fix is
> for the session to say it is configured to authenticate, which it is.
>
> **AC-S16 gained a direction it did not have.** The inventory checked itself
> against the *envelope's* algorithms. Token verification algorithms are
> algorithms in use on every request and were invisible to it; `validate()`
> now checks both vocabularies.
>
> One test change outside the finding: `test_no_script_spells_a_hostname`
> exempts `megingjord.veldris.internal`, which is deliberately not
> site-scoped (SAD 11A — one registry for the Forge Matrix, on Veldris_NXT).
> A second test keeps the exemption narrow by asserting a site-scoped
> spelling is still a mistake and that the installer and the allow list agree.
>
> `development.py` is untouched.

`draupnir/api/deps.py:113` resolves the caller from `request.state.claims`,
"which the OIDC middleware sets after verifying a token against MEGINGJORD's
JWKS". **No such middleware exists.** Nothing in the tree reads an
`Authorization` header, fetches a JWKS document, or verifies a JWT; grep for
`jwks`, `oidc` and `Authorization` across `draupnir/` returns only docstrings
and `draupnir/api/development.py:97`, which sets a fixed claim set when
`DRAUPNIR_DEV=1`. `DRAUPNIR_DEV` appears nowhere in `deploy/`, `docker/` or
`.env.example`, so a Sindri deployment has no principal on any request.

Reproduce:

```bash
python -c "
import os; os.environ.pop('DRAUPNIR_DEV', None)
from fastapi.testclient import TestClient
from draupnir.api.app import create_app
c = TestClient(create_app())
for p in ('/v1/runs','/v1/sites','/v1/models'): print(p, c.get(p).status_code)"
```

→ `401`, `401`, `401`. The control plane answers 401 to every versioned route in
the only configuration it is meant to be deployed in.

`draupnir/svalinn/identity.py` already turns verified claims into a `Principal`
and already reads `amr` for the hardware factor. What is missing is only the
half that holds the token.

**Prompt**

> Implement OIDC bearer-token authentication for the DRAUPNIR API as ASGI
> middleware in a new `draupnir/api/authentication.py`, and install it in
> `create_app()` in `draupnir/api/app.py` ahead of `development.install(app)`.
>
> The middleware must: read the `Authorization: Bearer` header; fetch and cache
> MEGINGJORD's JWKS from the issuer's discovery document, with a bounded cache
> and a refresh on unknown `kid`; verify signature, `iss`, `aud`, `exp`, `nbf`
> and `iat` with leeway of at most 60 seconds; reject any token whose `alg` is
> not in an allow-list of asymmetric algorithms (never `none`, never HMAC); and
> set `request.state.claims` to the verified claim set and nothing else. A token
> that fails any check must leave `request.state.claims` unset so the existing
> `guard` returns 401 — the middleware must not itself decide authorisation.
>
> Configure the issuer, audience, JWKS URL and clock leeway in
> `draupnir/core/infrastructure/config.py` under the existing `DRAUPNIR_`
> prefix, and add them to `.env.example` and the `deploy/` config templates.
> When no issuer is configured and `DRAUPNIR_DEV` is not set, `create_app()`
> must refuse to start with a message naming the missing setting — the same
> fail-closed shape `enforce_declarations` already uses for role declarations.
> A control plane that starts with no way to authenticate anybody is worse than
> one that refuses to start.
>
> Do not weaken `development.py`. It stays exactly as it is: off unless
> `DRAUPNIR_DEV=1`, reading no header, and now shadowed by real middleware when
> both are somehow present.

**Acceptance criteria**

- `tests/contract/test_authentication.py` proves: a request with no header is
  401; a token signed by a key not in the JWKS is 401; an expired token is 401;
  a token with `alg: none` is 401; a token with the wrong `aud` is 401; a valid
  token yields a `Principal` whose roles and `amr` reach `guard`.
- A test asserts `create_app()` raises when no issuer is configured and
  `DRAUPNIR_DEV` is unset, and that the message names the setting.
- A test asserts no token value ever reaches a log line, by running the existing
  redaction assertions over the authentication path.
- `docs/DEPLOYMENT.md` gains a section on registering the control plane as an
  OIDC client with MEGINGJORD, with the settings named.
- Gated in stage 2.3 (`test-contract`).

---

### RF-02 — P1 — No signature verifier is wired, so no plug-in loads in production

> **Status: done. All ten installed drivers now load with
> `DRAUPNIR_DEV` unset and `verified=True`.** That third configuration did
> not exist before.
>
> **The digest was the substantive fix.** `PkiVerifier.verify` checked the
> signature over `found.sha256` — the digest recorded *at signing time* — and
> never looked at the installed files. A distribution modified after signing
> verified, while the refusal it never reached claimed "the distribution has
> been modified since signing". The control described a check it was not
> performing. `digest_of` now walks the distribution through
> `importlib.metadata`, in canonical order, with the path in the digest so a
> moved file is a change; `verify` compares before returning verified.
>
> The two failures are now told apart, because they send somebody to
> different places: an altered *signature record* says so, and a modified
> *distribution* says the signature itself is valid — which is the fact that
> makes it a separate check rather than the same one.
>
> **The signer and the verifier share one function.** `sign_artefacts.py
> --distributions` calls `digest_of`. A signer and a verifier that each
> hashed a distribution their own way would agree until the day they did not,
> and the disagreement would present as tampering.
>
> **`discover()` has no default verifier.** It is positional and required, so
> a call site that omits it is a `TypeError` at the call. A default is what
> let both production call sites silently take a verifier that verifies
> nothing.
>
> **The trust store fails closed.** An unreadable one raises rather than
> producing an empty one — an empty trust store is not a strict verifier, it
> refuses everything, and the fix somebody reaches for is `DRAUPNIR_DEV=1`,
> which turns a missing directory into an estate that loads unsigned code. A
> *missing manifest* is deliberately not an error: a forge that has signed
> nothing yet refuses each plug-in for the honest reason that no signature
> exists for it.
>
> **Two things found while doing it.** The composition helper first went into
> `core/plugins.py` and broke two import contracts — the core may not name a
> security implementation (SAD 5.2) — so it lives in `svalinn/pki.py`, which
> is above the core and owns verification. And importing it into
> `routers/plugins.py` as `registry` collided with that module's own
> `registry()`, which made it call itself: every dry-run returned 500 until
> the import was renamed.
>
> **The test session now signs its own plug-ins.** `tests/conftest.py`
> generates a key, signs every installed distribution with the real signer,
> and points the settings at the result — so the suite exercises the control
> end to end rather than the concession. `test_reference_drivers.py` loads
> through `PkiVerifier` with `DRAUPNIR_DEV` explicitly unset.
>
> The escape hatch is kept and still loud: a test asserts development mode
> loads drivers *and* reports them unverified, so the concession stays
> distinguishable from the control.

`draupnir/core/plugins.py:271` defaults to `UnverifiedVerifier()`, which reports
every distribution unverified by design. The real verifier,
`draupnir/svalinn/pki.py:208 PkiVerifier`, is referenced only by
`tests/unit/test_svalinn_security.py`. Both call sites that build a registry —
`draupnir/api/routers/plugins.py:39` and `draupnir/procedures/sindri.py:835` —
call `PluginRegistry.discover()` with no verifier.

The consequence is binary. With `DRAUPNIR_DEV` unset:

```bash
python -c "
import os; os.environ.pop('DRAUPNIR_DEV', None)
from draupnir.core.plugins import PluginRegistry
r = PluginRegistry.discover(); print('failures:', len(r.failures))"
```

→ `failures: 9`. All nine reference drivers are refused. `POST
/v1/runs/dry-run` returns 422 "No installed driver can render this
specification", and the worker cannot place anything. With `DRAUPNIR_DEV=1`,
`make procedure` logs `plugin.unverified` nine times and loads all of them.
**There is no third configuration.**

Two further defects sit inside `PkiVerifier.verify` itself. It verifies the
signature over `found.sha256` — the digest *recorded at signing time* — and
never re-hashes the installed distribution, so a modified distribution with an
intact signature record still verifies, despite the refusal message saying "the
distribution has been modified since signing". And nothing populates
`signatures` or `trust_store`: `scripts/sign_artefacts.py` signs the SBOM
directory, not plug-in distributions.

**Prompt**

> Make plug-in signature verification a working control end to end.
>
> 1. In `draupnir/svalinn/pki.py`, change `PkiVerifier.verify` to compute the
>    digest of the *installed* distribution — walk the distribution's files
>    through `importlib.metadata`, hash them in a canonical order, and compare
>    against the signed digest before checking the signature. A mismatch is a
>    refusal naming the file that differs. The current code cannot detect the
>    tampering its own refusal message describes.
> 2. Add `PkiVerifier.from_settings()` that loads the trust store from a
>    directory of PEM public keys and the signature records from a signed
>    manifest, both named in `config.py`. Loading must fail closed: an
>    unreadable trust store is a refusal, never an empty one.
> 3. Replace both `PluginRegistry.discover()` call sites with one composition
>    helper that passes the configured verifier, and make `discover()` require a
>    verifier argument so a future call site cannot omit it.
> 4. Extend `scripts/sign_artefacts.py` with a `--distributions` mode that signs
>    installed plug-in distributions into the manifest `PkiVerifier` reads, and
>    add it to `.github/workflows/ci.yaml` stage 3.4.
>
> Keep the `DRAUPNIR_DEV=1` escape hatch and keep it loud.

**Acceptance criteria**

- `tests/unit/test_svalinn_security.py` gains: a distribution whose bytes were
  modified after signing is refused even though the signature record verifies;
  an unreadable trust store is a refusal, not an empty trust store.
- `tests/contract/test_reference_drivers.py` loads all nine reference drivers
  through `PkiVerifier` with a test trust store, with `DRAUPNIR_DEV` unset, and
  asserts nine successes and zero `LoadFailure`.
- A test asserts `PluginRegistry.discover()` cannot be called without a verifier
  — a `TypeError` at the call, not a default.
- Gated in stage 2.3.

---

### RF-03 — P1 — There is no ingress: no reverse proxy, no TLS, and `/auth/login` does not exist

> **Status: done.**
>
> **The console image is now the reverse proxy.** `/v1/`, `/auth/`,
> `/healthz`, `/readyz` and `/openapi.json` are proxied to the API unit, so
> the same-origin calls `client.ts` has always made now reach it. Before
> this a console request to `/v1/runs` matched `location /`, fell through to
> `try_files … /index.html`, and was answered with the SPA's own HTML — a
> 200 carrying a document where JSON was expected. The file's own header
> said "the API is reached through the reverse proxy in front of both", and
> there was no such proxy.
>
> The event streams get `proxy_buffering off` and a day-long read timeout. A
> buffered SSE response arrives when the connection closes, which for a
> stream is never, so the run board would have rendered empty and reported
> nothing wrong.
>
> **`/metrics` returns 404 here rather than being proxied**, and the reason
> is written beside it. SAD 8.1 leaves it unauthenticated *because* it is
> loopback-bound; proxying it would publish it on whatever the console is
> reachable from and retire that justification silently — the endpoint would
> go on saying it needs no credential, and it would stop being true.
>
> **The sign-in button now reaches something.** `/auth/login` and
> `/auth/callback` implement the authorisation-code flow with PKCE and a
> double-submit state cookie. State as a double-submit rather than a signed
> value is deliberate: it is one fewer secret to distribute, rotate and lose,
> and it defends the same thing.
>
> **The session is the token, in an HttpOnly cookie, and there is no session
> store.** Giving a static bundle a token to hold means putting a bearer
> credential where JavaScript can read it, which makes every XSS bug a
> credential disclosure. A server-side session table would add state to a
> control plane whose recovery story is that it holds none — and it would be
> process-local across the two to four processes SAD 5.1 specifies, which is
> RF-14's defect acquired on purpose. The middleware verifies a cookie
> exactly as it verifies a header, header first.
>
> `return_to` is checked rather than trusted. An open redirect on a sign-in
> endpoint is worth more than most: the link is one the user meant to click,
> from a host they trust. The refusal path also puts no reason in the URL — a
> sign-in page rendering attacker-chosen text is a phishing surface on the
> one page where trust matters most.
>
> **`install.sh --check` refuses to commission without a certificate**, and
> separately when a configured path is absent — the second is the worse case,
> because the first check passes. A refusal rather than a warning because the
> failure is silent: the session cookie is `Secure`, a browser will not send
> one over plain HTTP, so signing in appears to work and every request after
> it is anonymous.
>
> **The inventory's TLS row is derived.** It read `in use: yes`
> unconditionally while nothing in `deploy/` terminated TLS. That is worse
> than a hand-written claim, because a generated document carries the
> authority of having been derived from the code — which is the whole of what
> AC-S16 establishes. On this machine the artefact now reports seven
> algorithms in use rather than eight.
>
> One test needed narrowing after it failed on its own subject: the CSP
> assertion checks the directive rather than the file, because the comment
> above it explains that the console needs neither `unsafe-inline` nor
> `unsafe-eval`.

Three facts that only matter together:

1. `web/packages/api-client/src/client.ts:104` sets `DEFAULT_BASE_URL = ''` —
   the console calls the API same-origin.
2. `deploy/units/draupnir-run.sh` publishes the API on `127.0.0.1:8000` and the
   console on `127.0.0.1:8080`. They are different origins. `docker/nginx.conf`
   has no `proxy_pass`; its `location /` is `try_files $uri $uri/ /index.html`,
   so a console request to `/v1/runs` is answered with the SPA's HTML.
3. `grep -in "proxy\|nginx\|tls\|https\|443" deploy/install.sh docs/DEPLOYMENT.md`
   returns nothing. Nothing installs or configures a proxy, and nothing
   terminates TLS.

So the deployed console cannot reach the deployed API, and everything is
plaintext HTTP. SAD 9.5 says "TLS 1.3 only. mTLS between control plane
components"; `sbom/crypto-inventory.md` declares TLS 1.3 **in use: yes**.

Separately, `web/apps/console/src/screens/SignIn.tsx:78` navigates to
`/auth/login?return_to=…`. That path exists nowhere in the tree — not as an API
route, not in the nginx config. The sign-in button is a dead link.

**Prompt**

> Give the control plane an ingress.
>
> 1. Extend `docker/nginx.conf` so the console image also reverse-proxies the
>    API: `location /v1/`, `/healthz`, `/readyz` and `/openapi.json` to the API
>    unit, with SSE-safe settings on `/v1/events` and `/v1/runs/*/events`
>    (`proxy_buffering off`, a long `proxy_read_timeout`, HTTP/1.1). Do **not**
>    proxy `/metrics`: `draupnir/api/routers/health.py:91` justifies leaving it
>    unauthenticated on the grounds that it is loopback-bound, and proxying it
>    would silently retire that justification.
> 2. Add a Content-Security-Policy header alongside the existing
>    `X-Content-Type-Options`, `X-Frame-Options` and `Referrer-Policy`, and a
>    `Strict-Transport-Security` header.
> 3. Implement the OIDC authorisation-code flow endpoints the console already
>    links to — `GET /auth/login` and `GET /auth/callback` — as an unversioned
>    router in `draupnir/api/routers/auth.py`, declared `@unauthenticated` with
>    a stated reason. `login` builds the authorisation URL with PKCE and a
>    signed state cookie; `callback` exchanges the code and sets the session as
>    an `HttpOnly; Secure; SameSite=Lax` cookie. Then have the middleware of
>    RF-01 accept that cookie as well as a bearer header, so the console needs
>    no token handling of its own.
> 4. Extend `deploy/install.sh` to install the proxy configuration and to
>    require a certificate and key path, refusing to commission without them.
>    Document obtaining them from the Veldris internal CA in
>    `docs/DEPLOYMENT.md`.
> 5. Correct `draupnir/svalinn/inventory.py` so the TLS row's "in use" is
>    derived from whether TLS is configured, not asserted — an inventory
>    generated from constants that claims a transport nobody terminates is
>    exactly the failure AC-S16 exists to prevent.

**Acceptance criteria**

- `tests/contract/test_deploy.py` asserts the nginx config proxies `/v1/`,
  carries a CSP and an HSTS header, and does not proxy `/metrics`.
- A Playwright test asserts that activating "Continue to MEGINGJORD" on
  `/signin` reaches an endpoint that exists (against a stub issuer), rather than
  a 404.
- `tests/contract/test_authentication.py` asserts a session cookie is accepted
  and that it carries `HttpOnly`, `Secure` and `SameSite`.
- `install.sh --check` fails with a named reason when no certificate is
  configured; asserted in `tests/contract/test_deploy.py`.
- The crypto inventory's TLS row reads `no` in a configuration with no TLS, and
  a test asserts it.
- Gated in stages 2.3 and 2.7.

---

### RF-04 — P1 — Nothing publishes an image, and rollback names a revision that is not one

> **Status: done, except image signing, which is recorded as outstanding
> rather than claimed.**
>
> **Stage 3 now pushes what stage 4 pulls.** Both images are pushed to
> `vars.DRAUPNIR_REGISTRY` on `main`, tagged with the commit SHA. The *push*
> is conditional and the *build* is not: a pull request from a fork has no
> credentials, and making the build conditional too would mean a fork's
> change to a Dockerfile was never compiled — which is the check most worth
> keeping for exactly those changes. `--load` is kept alongside `--push`,
> because stage 3.1a starts the image it built and a pushed image is not a
> loaded one.
>
> **The rollback revision comes from the host.** `deploy/current-revision.sh`
> reads the tag out of the state file `draupnir-run.sh` writes *after* an
> image resolves, so a rollout that named an image which could not be pulled
> leaves the last good revision in place. It prints a bare tag and puts every
> message on stderr — a message on stdout would be captured as though it were
> a revision, which is the class of fault this replaces.
>
> That fault was not only a formatting one. `draupnirctl version` was the
> **wrong question**: it is a client on the runner, and its version is the
> version of the thing asking rather than of the thing running on ALVISS. A
> `--revision` flag now exists for callers that want a machine-readable
> answer, and its help says plainly that it is still not the right answer for
> a rollback.
>
> **`rollback.sh` validates before it changes anything.** `draupnir_is_tag`
> holds the OCI grammar in one place and both scripts use it; the refusal
> names what arrived and names `current-revision.sh` as the command that
> would have produced a real one. Checked at the top rather than discovered
> at the pull, because a rollback runs when a deployment has already gone
> wrong and a confusing error is most expensive exactly then.
>
> **`rollout.sh` no longer claims a signature.** It said it "pulls the signed
> image"; nothing signs a container image and nothing verifies one at pull.
> The word is removed and the outstanding work is recorded in the script, so
> the claim cannot be quietly restored — a script that describes a control it
> does not perform is worse than one that performs no control, because the
> first is read as evidence. **Image signing remains outstanding**, and the
> prompt offered exactly this alternative.
>
> A third test in the deploy suite needed narrowing to check directives
> rather than the whole file — RF-E23's `cacheonly` assertion failed on the
> comment explaining why `cacheonly` was replaced. Three occurrences is
> itself worth noticing: to a substring search, a prose mention and a
> directive are identical.
>
> Still true and not this finding's to fix: **the estate has no registry** at
> all (RF-E20). The default name resolves to nothing, so `--registry` must
> name a host that exists until that is settled.

`.github/workflows/ci.yaml` stage 3.1 builds both images with `--output
type=cacheonly` and tags them `draupnir-api:${GITHUB_SHA}` locally.
`tasks.py:878` does the same. **No step anywhere pushes to
`registry.veldris.internal`.** `deploy/rollout.sh` then runs `podman pull
registry.veldris.internal/draupnir-api:<revision>`, which cannot succeed, and
`draupnir-run.sh` uses `--pull=never`, so the unit cannot recover either. Stage
4 of SAD 11H is unrunnable.

`.github/workflows/deploy.yaml` compounds it. The rollback revision is captured
as:

```yaml
previous="$(uv run draupnirctl version 2>/dev/null || true)"
```

`draupnirctl/cli.py:152` prints `draupnirctl 0.1.0 (OpenAPI 1.0.0)`. That whole
string is passed to `rollback.sh`, which builds the image reference
`registry.veldris.internal/draupnir-api:draupnirctl 0.1.0 (OpenAPI 1.0.0)`.
Rollback — the step that runs when a deployment has already gone wrong — cannot
work.

Also: `deploy/rollout.sh:4` says it "pulls the signed image for the revision";
nothing signs an image and nothing verifies a signature at pull.

**Prompt**

> Make stage 3 produce artefacts stage 4 can consume.
>
> 1. In `.github/workflows/ci.yaml`, replace `--output type=cacheonly` with a
>    push to `${{ vars.DRAUPNIR_REGISTRY }}` tagged with `${GITHUB_SHA}`, on
>    `main` only, with registry credentials from the environment. Keep
>    `cacheonly` for pull requests so a fork still builds.
> 2. Sign each pushed image with the same internal PKI key stage 3.4 uses, and
>    have `deploy/rollout.sh` verify the signature before `podman pull` — or, if
>    image signing is deferred, delete the word "signed" from `rollout.sh:4` so
>    the script does not describe a control it does not perform.
> 3. Add `deploy/current-revision.sh`, which reads the revision the units are
>    actually running from `${STATE_DIR}/image-draupnir-api` — the file
>    `draupnir-run.sh` already writes — and prints the tag alone. Use it in
>    `deploy.yaml`'s "Record the current revision for rollback" step instead of
>    `draupnirctl version`.
> 4. Make `rollback.sh` validate its argument against the image-tag grammar and
>    refuse anything containing whitespace or parentheses, with a message naming
>    what it received.
> 5. Add a `version --revision` flag to `draupnirctl` that prints the build
>    revision alone, so a caller wanting a machine-readable answer has one.

**Acceptance criteria**

- `tests/contract/test_deploy.py` asserts: the CI workflow pushes on `main`;
  `rollout.sh` and `rollback.sh` resolve the same image reference for the same
  revision; `rollback.sh` exits non-zero with a named reason for an argument
  containing a space.
- A shell-level test drives `current-revision.sh` against a fixture state
  directory and asserts it prints a bare tag.
- `docs/DEPLOYMENT.md` records the registry, the credential, and the rollback
  revision source.
- Gated in stage 2.3.

---

### RF-05 — P2 — `publishRelease` enforces none of the controls its docstring names

> **Status: done.**
>
> **What landed.** `publishRelease` now calls `skidbladnir.publish` before
> anything is recorded. Every refusal becomes a 409 with a distinct problem
> code naming which control refused — `artefact-mismatch`,
> `artefact-ungated`, `release-unapproved`, `anchor-behind` — and a store
> outage becomes a 503, because a refusal is final and an outage is a retry,
> and conflating them tells an operator a correct release was rejected.
>
> **The seam is the interesting part.** `publish()` needs a full
> `ReleasePackage` — model card, SBOM, attestation, summary, annex — which
> lives on the vault and which the API does not have. That is *why* the
> handler called none of it rather than part of it. So the rules moved into
> `admissible()`, which `publish()` now calls and then adds the package
> check to: one implementation of each rule, and the two paths cannot drift.
> A second copy in the router would have agreed on the day it was written.
>
> AC-S13 had no implementation anywhere — `publish()` never checked an
> anchor. `StaleAnchorError` is its own type because the remedy is a wait
> rather than a fix: the release is correct and the federation has not
> caught up, and reporting it as an evidence problem would send somebody to
> re-run an evaluation that passed.
>
> **The bytes are fetched from the object store, not the vault.** The vault
> is mounted into the worker only (RF-E04) and that decision stands; the
> artefact store is on ANDVARI and the API has credentials for it. An
> artefact whose location the chain never recorded is a **refusal**, not a
> guess: building a path from a naming convention would hash whatever
> happened to be there, which is the opposite of AC-S8.
>
> **The admitted path exists, and it is the test that gives the refusals
> their meaning.** `tests/integration/test_release_publication.py` walks a run
> through the real guards to a state a release can be published from: an
> artefact in a real HODD vault whose bytes hash to the digest the gates
> passed, gate evidence for every format that was built, a signed approval
> naming those bytes, and a countersigned anchor at or beyond the release's
> sequence. Then it publishes, and the API answers 202.
>
> That test is the one that matters. "Everything is refused" passes just as
> well against a handler that refuses unconditionally, and a register entry
> claiming four controls with only refusals to show for it is claiming
> something it has not demonstrated. Each of the four refusals is then the
> same fixture with exactly one control withdrawn — bytes that changed after
> the gate, a built format nobody evaluated, no approval, an anchor behind the
> release — so a refusal is attributable to the control that produced it
> rather than to the fixture being wrong somewhere.
>
> **The vault is real and the driver is the deployment's.** `_fetch` built a
> MinIO client inline, so a forge whose artefacts are on the NFS vault — which
> is how Sindri is configured — had its publication path reach for an object
> store that need not exist. It goes through `hodd.stores.store_for` now, the
> same factory the worker uses, so "which driver is this deployment's" has one
> answer.
>
> `draupnir.skidbladnir.publish` leaves the orphan list.

`draupnir/api/routers/approvals.py:230` states: "Every refusal in
`skidbladnir.publish` applies: the bytes are re-hashed and compared against the
gate evidence (AC-S8), every built format must have passing evidence (AC-F9),
the approval must be present and signed, and the federation must have
countersigned an anchor at or beyond this release's sequence (AC-S13)."

The handler does none of it. It reads one entry from the chain
(`writing.released_entry_for`) and, if it exists, records a `published` entry.
`draupnir.skidbladnir.publish` is never imported outside its own tests. There is
no re-hash, no per-format evidence iteration, no signature verification, and no
anchor check. AC-S8, AC-F9 and AC-S13 are enforced by a module that the only
publication path does not call.

**Prompt**

> Route `publishRelease` through `draupnir.skidbladnir.publish`.
>
> In `draupnir/api/routers/approvals.py`, after the approval entry is found and
> before any entry is recorded, call the publication check with: the artefact
> bytes resolved through the store driver, the gate evidence from the chain, the
> set of formats actually built for the run, the approval record, and the site's
> latest countersigned anchor sequence. Every refusal it raises becomes a 409
> problem document naming which of AC-S8, AC-F9, AC-S13 or the approval check
> refused, and no ledger entry is written.
>
> The check must be driven by what was *built* rather than by what was
> evaluated — iterating the evidence would confirm that everything evaluated
> passed, which is vacuously true of an empty set. `skidbladnir.publish` already
> has this property; preserve it across the seam.
>
> If the artefact bytes cannot be resolved because the store is unreachable,
> that is a 503 and a refusal, never a publication.

**Acceptance criteria**

- `tests/integration/test_api_writes.py` gains four refusals, each asserting a
  409 and a distinct problem `code`: bytes whose digest differs from the gate
  evidence; a built format with no passing evidence; an approval whose signature
  does not verify; a stale or absent federation anchor.
- A test asserts that each refusal leaves the ledger length unchanged.
- A test asserts the happy path records exactly one `published` entry.
- `draupnir.skidbladnir.publish` leaves the orphan list of section 2.
- Gated in stage 2.4.

---

### RF-06 — P2 — An approval's signature is never verified, and the approver-role fact is hard-coded

> **Status: done.**
>
> The signature is verified against the approver's registered key over
> `Approval.signing_payload()` — which already included the sole-approver
> exception, so suppressing the flag invalidates the signature. Both halves
> are needed and neither works alone: computing the exception without
> signing it would let a replayed signature carry the wrong flag, and
> signing it without computing it would let the approver choose. A test
> signs a payload claiming no exception when one applies and asserts the
> refusal.
>
> **A design flaw I introduced and had to fix.** The first version kept the
> server's `now()` as `decided_at` — which is *inside* the signed payload,
> so no client could ever have produced a signature that verified. The
> approver now supplies the instant they signed over, and it is bounded to
> five minutes: a signature prepared far in advance or replayed long
> afterwards is refused, which is why the instant is in the payload at all.
>
> **Three refusals, three statuses**, because they are three problems. No
> registered key is a **409** — the estate is not set up to accept that
> approver's decision, and no signature they could produce would change it.
> A signature that does not verify is a **422**. An unreadable key store is
> a **503**, and the store fails closed for RF-02's reason: an empty one
> refuses every approval, which reads as a broken deployment and gets worked
> around. Keys are read per call rather than cached, because a revocation
> that took effect at the next restart is a key somebody decided to stop
> trusting and which is still trusted.
>
> `approver_has_role` is derived from the verified claims. The route guard
> refuses a caller without the role first, so in practice it is always true
> — it is computed anyway, because a fact in the chain should be a
> measurement rather than a restatement of an assumption made elsewhere, and
> a second layer that agrees by construction is not a second layer.
>
> **The guard requires a verified-signature fact and the transition records
> it.** An absent fact raises `MissingFactError` rather than reading as
> false, which is the stricter answer: a caller omitting it is saying
> nothing, and a guard that quietly read silence as `False` would be
> indistinguishable from one that read it as `True` the day somebody
> inverted a default. It is a required record as well as a guard fact,
> because a control that is checked and not recorded is one nobody can show
> was checked.
>
> The test session registers real approver keys and signs real decisions
> through `Approval.signing_payload()`, so the suite exercises the
> verification rather than stepping around it — the same arrangement as
> RF-02's signed plug-ins.

`draupnir/core/domain/states.py:268` — the `approver-signed` guard — ends:

```python
return _outcome(name, bool(context.require(name, "signature")), "the approval is not signed")
```

Any non-empty string passes. `draupnir/api/routers/approvals.py:165` passes
`body.signature` straight through, unverified, and line 164 supplies
`"approver_has_role": True` as a literal.

The reconciliation records fixing exactly this shape for the sole-approver
exception — "computed, never supplied" — and the same defect remains one field
away. `draupnir/svalinn/signing.py` holds Ed25519 verification and is an orphan.

The route guard `@needs(Permission.DECIDE_GATE)` does correctly enforce the
hardware-factor requirement through `svalinn.authz.decide` →
`require_hardware_mfa`, so AC-S15's second factor *is* enforced. What is not
enforced is that the signature is a signature.

**Prompt**

> Verify approval signatures, and compute the approver-role fact.
>
> 1. Define the signed payload for a gate decision as the canonical bytes of
>    `{run_id, artefact_sha256, decision, approver, decided_at,
>    sole_approver_exception}` — the sole-approver exception inside the signed
>    payload, so suppressing it invalidates the signature, which is the property
>    the release path already relies on.
> 2. In `draupnir/api/routers/approvals.py`, verify `body.signature` over those
>    bytes against the approver's registered public key before the transition. A
>    signature that does not verify is 422 with a problem document naming the
>    payload it was checked against. An approver with no registered key is 409.
> 3. Replace the literal `"approver_has_role": True` with a value derived from
>    `ctx.principal`'s roles.
> 4. Strengthen the `approver-signed` guard so it requires a verified-signature
>    fact rather than a truthy string, and add that fact to the transition's
>    required ledger fields so `missing_records` refuses a transition without it.

**Acceptance criteria**

- `tests/unit/test_states.py` asserts the `approver-signed` guard refuses a
  transition whose signature fact is absent or false, and that
  `"signature": "x"` no longer passes.
- `tests/integration/test_api_transitions.py` asserts: a decision with a
  signature over different bytes is 422; a decision whose payload omits the
  sole-approver exception fails verification; the happy path records the
  verified fact in the ledger entry.
- A test asserts `approver_has_role` in the recorded entry is false for a
  principal without the role — reachable only by calling the orchestrator
  directly, since the route guard refuses first, so assert both layers.
- Gated in stages 2.1 and 2.4.

---

### RF-07 — P2 — Nothing anchors the chain, so the anchor control alarms permanently and never blocks

> **Status: done.**
>
> `gullinbursti/federation.py` submits the chain head to MEGINGJORD through
> the egress broker, and `duties.anchor` performs it on `ANCHOR_INTERVAL`.
> The outcome is recorded to the chain — **both outcomes**, not only the
> successful one: a chain that recorded only successes could not tell "never
> tried" from "tried and was refused", which are the two states an operator
> most needs told apart during an outage. `Orchestrator.publication_facts`
> reads `anchored_through` back out of exactly these entries, so RF-05's
> AC-S13 check now has something to read.
>
> **Decision S8 is in the message, not only in the behaviour.** A rejection
> alarms and says "training and evaluation continue; release is unavailable"
> — an operator reading "the registry did not anchor" without that sentence
> would reasonably stop submitting work.
>
> **`sealed` refuses a signature, and that turned out to be right.** A
> 128-character Ed25519 signature is refused as an encoded run: the check
> admits hashes, names, timestamps and numbers, and a signature is none of
> those. Widening it to admit one would have weakened a content control in
> order to carry a credential — the check cannot tell a signature from a
> slice of a weight tensor and should not try. So the signature travels as a
> header over the sealed bytes, and the body stays inspectable by anything
> reading federation traffic (AC-S14).
>
> **Countersigned and unsigned is not countersigned.** A registry answering
> `countersigned` with no countersignature is refused, because accepting it
> would record an anchor nobody can verify — worse than no anchor, since the
> freshness duty would go quiet and the truncation SAD 11A.3 exists to
> detect would go unnoticed.
>
> An unreachable registry is a receipt rather than an exception. The
> vocabulary has no `unreachable`, deliberately: the agent's queue does not
> care *why* a head was not anchored, only that it was not, so the reason is
> what an operator reads and `diverged` passes through unchanged as the one
> outcome that means something different.
>
> **The worker holds the agent and reaches the registry.** `Worker._agent` and
> `Worker._registry` are built once and kept for the life of the process,
> because the agent *is* the queue: a head submitted during a partition waits
> in it, and an agent rebuilt every tick would drop what accumulated and leave
> AC-S13's reconnect path with nothing to drain. The registry is injectable
> the way the scheduler is, so a tick can be driven against a stand-in without
> a network — `tests/integration/test_worker_loop.py` anchors through a real
> tick and asserts the duty stops alarming, and drives a refusing registry to
> assert that it alarms with Decision S8's sentence in it.
>
> **Three defects the wiring turned up, each of which made the fix inert.**
>
> 1. `Orchestrator._anchored_through` probed the payload for
>    `{"anchored_through": None}`. That is a JSONB containment match for the
>    *value* null, and the duty records an integer — so it matched nothing, the
>    answer was zero however many times the chain had been countersigned, and
>    every publication was refused for a reason the refusal did not name. It
>    reads the entries by transition and subject now.
> 2. `BrokeredClient` had only `get`. The one caller that submits rather than
>    fetches had no brokered client it could be given, so the anchor path was
>    either unwired or would have taken a raw client around the broker
>    entirely — threat T11 exactly. Outbound submission is the direction that
>    matters most here: a fetch leaks a hostname, a post leaks a body.
> 3. Nothing signed the head. MEGINGJORD refuses an unsigned one in as many
>    words — "an unsigned head is not a claim about a chain, it is a packet" —
>    so every submission would have been rejected on arrival, and the rejection
>    would have read like a broken tunnel rather than a missing key. The worker
>    loads the site key and the key names itself: the identifier is derived
>    from the public half rather than configured beside it.
>
> **A failure alarms only once the last good anchor is stale.** SAD 11A.3 asks
> for an alarm when the last *successful* anchor exceeds the interval, not when
> an attempt fails. A link blinking between two ticks would otherwise alarm
> about a chain anchored ninety seconds ago, and an alarm firing on a condition
> an operator cannot act on is one they learn to close without reading. The
> judgement is `freshness`, so one place decides how old is too old — and
> `last_anchored_at` comes out of the chain now rather than off
> `site.last_anchored_at`, a column nothing has ever written. A rejection is
> recorded but does not refresh the clock: letting it would silence the alarm
> that says the chain's end is unprotected, which is the state a rejection puts
> the forge in.
>
> `megingjord.anchors` leaves the orphan list.

`draupnir/megingjord/anchors.py` and `draupnir/megingjord/registry.py` are
orphans. `draupnir/gullinbursti/agent.py` is imported outside tests for exactly
one constant (`ANCHOR_INTERVAL`, at `draupnir/worker/duties.py:43`); the
`Gullinbursti` agent itself is never constructed. There is no outbound HTTP
client anywhere in `draupnir/` — `grep "import httpx\|import requests\|urlopen"`
over `draupnir/`, `plugins/` and `scripts/` returns only `scripts/smoke.py` and
`draupnirctl`.

So `last_anchored_at` is always `None`, and `duties.freshness` returns its "this
site has never anchored its chain" alarm on every tick, forever. Its own message
says "Publication is refused while the anchor is stale" — and RF-05 shows
publication checks nothing. SAD 11A.3 makes the anchor the thing that detects
truncation; nothing detects truncation.

**Prompt**

> Build the GULLINBURSTI anchoring duty.
>
> 1. Add an outbound HTTP client to `draupnir/gullinbursti/` that submits the
>    signed chain head to MEGINGJORD and stores the countersignature. Every call
>    must go through `draupnir.svalinn.egress`'s allow-list with a declared
>    destination, purpose, run and approving policy (see RF-09) — this is the
>    first real outbound call in the system and it should establish the pattern.
> 2. Add an `ANCHOR` duty to `draupnir/worker/duties.py` and its timetable entry
>    in `draupnir/worker/loop.py`, on the interval `ANCHOR_INTERVAL` already
>    defines. Record the submission and the countersignature as ledger entries,
>    so anchor state is derived from the chain rather than from a side table.
> 3. Have `freshness` read the last countersigned anchor from the chain.
> 4. Every payload must be built through `core.domain.federation.sealed`, which
>    already refuses anything that is not a hash, a name, a timestamp or a
>    number. Do not add a second construction path.
> 5. Handle partition per Decision S8: a failed anchor is a finding and a
>    read-only state for release, never a failed run.

**Acceptance criteria**

- `tests/integration/test_worker_loop.py` asserts a tick submits an anchor
  against a stub registry, records it, and that `freshness` then reports no
  alarm.
- A test asserts a partitioned registry produces a finding, blocks release, and
  does not stop training (Decision S8) — extend the existing degraded-mode test.
- A test asserts the anchor payload passes through `sealed`, and that a payload
  carrying a weight or a corpus reference is refused.
- `draupnir.megingjord.anchors`, `draupnir.megingjord.registry` and
  `draupnir.gullinbursti.agent` leave the orphan list.
- Gated in stage 2.4.

---

### RF-08 — P2 — Artefacts never reach HODD or MinIO

> **Status: done.**
>
> **The seal is now the bucket's.** `ObjectStoreDriver._sealed` was an
> in-process `set`, so a seal did not survive a restart, was invisible to
> the second and third API processes SAD 5.1 specifies, and stopped HODD
> rather than stopping a write. Sealing places a **legal hold** on the
> object; `is_sealed` asks the bucket. A legal hold rather than a retention
> period because the two answer different questions: a retention period
> requires guessing a date at seal time, and a legal hold says "not until
> somebody with the authority lifts it" — which is what a ledgered
> retention action is.
>
> **A bucket with no object lock is refused at construction**, rather than
> degrading to an in-memory set. A driver that accepts an unlockable bucket
> reports every artefact sealed and protects none of them, and reports it
> convincingly — `is_sealed` answers `True` for anything that process
> sealed. `is_sealed` also fails **closed**: the caller uses it to decide
> whether an overwrite is permitted, so answering "unsealed" to a question
> the bucket could not answer is exactly the overwrite AC-S8 exists to stop.
>
> A unit test asserts two drivers over one bucket agree about a seal. The
> in-process set could not pass it, and the second driver is not
> hypothetical: it is the ordinary case under SAD 5.1.
>
> **`hodd.stores.store_for` is the composition root's factory**, building a
> real `minio.Minio` client from the configuration that already held the
> endpoint and credentials, and reusing `require_vault` for the POSIX path
> rather than re-checking the same property a second way.
>
> **The worker stages what it produces.** All three producers in
> `worker/stages.py` — the adapter checkpoint, the merged artefact and each
> quantised format — put their bytes into HODD at the `hodd://` address
> `stores.artefact_uri` derives, seal them there, and record the address in
> the transition payload beside the digest.
>
> **The vault write happens before the transition, and the order is the
> point.** A transition recorded first and a put attempted afterwards leaves a
> chain saying TRAINED about a checkpoint at an address holding nothing, which
> is the one failure the provenance argument cannot survive: a publication
> re-hashes bytes that are not there and the refusal names the wrong cause. So
> a vault that will not take the artefact defers the run instead.
>
> **Sealed at the put, not at the approval.** AC-S8 re-hashes at publication to
> *detect* a post-gate modification (T8); a seal placed only once a release is
> approved leaves the whole window between evaluation and approval open, which
> is the window an insider has time to act in.
>
> **Capacity is checked before the write.** `quota.room_for` measures rather
> than projects, because by staging time the file is on disk and its size is a
> fact — projecting it again would replace a measurement with a guess that is
> deliberately several times too large, refusing writes the vault could take.
> The same reserve arithmetic either way, and a shortfall defers the run rather
> than filling the vault: a put that fills it has already caused the harm, and
> on a copy-on-write filesystem a full vault cannot even be emptied to recover.
>
> **Idempotent by digest, and only by digest.** A stage whose transition failed
> re-runs next tick and finds its own bytes already at the address; putting
> them again would be refused as an overwrite of a sealed artefact and the run
> would defer for ever — a livelock produced entirely by the fix. Bytes that
> *differ* are not accepted: that is two artefacts at one address, and the seal
> refusing it is the seal working.
>
> **A forge with no vault configured says so.** The digest is still recorded,
> so the run trains and evaluates; the address is empty, so nothing can be
> released. That is Decision S8's shape and it is said in the outcome rather
> than discovered at publication. A vault that is *configured and unavailable*
> is not softened to the same thing — the run defers and the next tick asks
> again, which is what a dropped NFS export coming back should look like.
>
> **The MinIO tests are against MinIO.** `tests/integration/test_object_store.py`
> puts an artefact through the driver into a bucket with object locking on,
> seals it from one driver, and asserts a *second* driver over the same bucket
> both sees the seal and is refused the overwrite. A stub can be written to
> agree with itself; only the real thing can be wrong about it.
>
> `hodd.quota` leaves the orphan list.

`draupnir/hodd/stores.py:342 ObjectStoreDriver` takes `client: Any` and is
constructed only in `tests/unit/test_hodd_stores.py`, with `client=object()`.
Nothing in the application builds a MinIO client, despite `config.py:44`
carrying the endpoint and credentials.
`tests/integration/test_object_store.py` exercises MinIO directly and never the
driver.

`grep "\.put(\|\.seal(\|ingest("` over `draupnir/worker`, `draupnir/api`,
`draupnir/procedures` and `draupnir/skidbladnir` finds one match, and it is the
name of an HTTP route. The worker writes artefacts to a local scratch directory
(`draupnir/worker/stages.py:108`) and records their digests; nothing stages them
into the vault or the object store. `draupnir/hodd/quota.py` is an orphan, so no
quota is checked before writing.

`ObjectStoreDriver._sealed` is an in-process `set`, so a seal does not survive a
restart and is invisible to a second API process. The docstring is honest that
"object locking or a versioned bucket is the real mechanism"; neither is
configured.

**Prompt**

> Wire the artefact store into the run pipeline.
>
> 1. Add a store factory in the composition root that builds either
>    `PosixStoreDriver` or `ObjectStoreDriver` (with a real `minio.Minio` client
>    from `config.py`) according to configuration, and refuses to start when the
>    configured store cannot be reached — reusing the vault-marker check
>    `hodd.reconcile.require_vault` already performs.
> 2. In `draupnir/worker/stages.py`, after each artefact's digest is computed,
>    put it into the store under its `hodd://` address, seal it, and record the
>    address in the transition payload. Check the quota through
>    `draupnir.hodd.quota` before the put and defer the run rather than filling
>    the vault.
> 3. Replace `ObjectStoreDriver._sealed` with the bucket's own object-lock or
>    versioning state, so a seal is a property of the store rather than of a
>    process. Where object lock is unavailable, refuse to construct the driver
>    rather than degrading silently to an in-memory set.
> 4. Add `ObjectStoreDriver` to `tests/integration/test_object_store.py` against
>    the real MinIO container, including the overwrite refusal.

**Acceptance criteria**

- `tests/integration/test_object_store.py` round-trips through
  `ObjectStoreDriver`, and asserts a sealed artefact cannot be overwritten by a
  *second driver instance* — the assertion the current in-process set cannot
  pass.
- `tests/integration/test_worker_loop.py` asserts an advanced run leaves its
  artefact addressable by `hodd://` URI, and that the recorded digest matches
  the stored bytes.
- A test asserts a full vault defers the run instead of writing.
- `draupnir.hodd.quota` leaves the orphan list.
- Gated in stage 2.4.

---

### RF-09 — P2 — The secrets broker, the egress allow-list and the sandbox profile are not on any call path

> **Status: done.**
>
> All five modules have callers outside their own tests, and the callers are
> obliged rather than optional. A security module nobody calls is worse than
> an absent one because it reads as coverage; a module called from one place
> that a caller may skip is the same thing with more steps, so each of these
> sits where the work has to pass through it.
>
> **Secrets.** `execution.submit` is the one place every plan passes through,
> and it takes a `LeakCheck` — a protocol, not an import, because SVALINN and
> MOTSOGNIR are siblings in the layering and neither may import the other.
> The worker holds a `SecretsBroker` for the life of the process and hands it
> in at every submission. The check is on the **rendered plan** rather than
> on its environment mapping: a secret interpolated into a command string is
> the case a values-only check misses and the one that actually happens.
>
> A job's environment is built by `brokered_environment` and by nothing else,
> so it carries lease references and there is no code path by which a value
> could be written into one — the function reads `lease.reference` and never
> touches `reveal`. `BASE_ENVIRONMENT` is merged under it rather than
> replaced, so supplying leases cannot silently drop the determinism settings
> SAD 6.2 makes the recorded plan reproducible with.
>
> **The broker is built even where the store is empty**, which it is at
> Sindri. An empty broker's check passes vacuously and that is the point: the
> check is on the path from the day it is written rather than from the day
> somebody remembers to add it, so the first secret this estate brokers is
> covered by it rather than covered later.
>
> **Egress.** The undecided call was the OIDC token exchange — the one
> outbound call in the system that carries a credential, an authorisation
> code and sometimes a client secret. It is decided by the same broker
> against the same list before a socket is opened. `BrokeredClient` is not
> used there because the exchange is form-encoded and asynchronous and that
> wrapper is neither; what matters is the decision, not the wrapper.
>
> The test enumerates rather than asserts, which is what the criterion asks:
> it walks the source for every `httpx.Client`, `AsyncClient` and
> `urlopen` and fails on any built in a function that does not also consult
> the broker. An assertion that "outbound calls are brokered" is a statement
> about the author's intention on the day; this finds the ones that are not,
> so a client added next year fails here rather than in a packet capture. The
> teacher-destination test stays, and stays negative (AC-S3, T3).
>
> **Sandbox.** Every plan the worker builds now carries the profile
> `svalinn.sandbox` generates, and so does every plan the procedures build —
> a walkthrough whose plans were unconfined while the worker's were would be
> showing a system that is not the one deployed. `JobPlan.sandbox` holds the
> rendered payload rather than a `SandboxProfile`, for the same sibling-layer
> reason, and it is in `canonical()`, so two renders of one specification
> still agree and now agree about the confinement too.
>
> **This collided with RF-E16's test, and the collision was worth having.**
> That test asserted nothing on the dispatch path *imports* `sandbox`,
> because Sindri runs `python train.py` inside `/forge/venv` under `slurmd`
> and cannot honour a container profile. Carrying a profile and applying one
> are different things and that difference is the whole of RF-E16: a plan
> naming a profile the appliance ignores is an honest specification
> travelling with the work, while a control plane that *rendered* the profile
> into runtime arguments would be claiming a confinement the estate does not
> have. So the check moved from the import to `SandboxProfile.render`, which
> is the only method that could confine anything, and it still fails the day
> a container executor is built.
>
> **Integrity.** `verify_before_load` runs on the adapter before the merge is
> dispatched. That stage read `checkpoint_sha256` out of the chain, passed it
> into the sweep as the adapter's identity, and never hashed the file — so a
> merge recorded as being over one checkpoint could be over another, and the
> sweep hash AC-F8 asks a reader to reconstruct from would be a statement
> about bytes nobody checked. It is at the merge and not at dispatch because
> the staged corpus is *derived* from the recorded curation digest rather
> than being those bytes: an integrity check there would compare a file
> against the hash of something else and fail on every run, which is worse
> than no check. That is said in the code rather than left to be rediscovered.
>
> **Scanning.** `Ingestor` takes the scanner and runs it over the staged tree
> **before the rename**, which is the point of no return: everything below
> that line is sealed, lineaged, and reversible only by an approved retention
> action. A scanner that ran after it would report rather than refuse. It is
> injected, not imported — HODD and SVALINN are siblings — and
> `scripts/vault_admin.py` is the composition root that supplies it, being
> the only thing in the repository that constructs an `Ingestor`.
>
> `secrets`, `egress`, `sandbox`, `scanning` and `integrity` all leave the
> orphan list.

Five security modules with good tests and no callers outside them:

| Module | Only importer |
|---|---|
| `draupnir/svalinn/secrets.py` | `tests/unit/test_svalinn_security.py` |
| `draupnir/svalinn/egress.py` | `tests/unit/test_svalinn_security.py` |
| `draupnir/svalinn/sandbox.py` | `tests/unit/test_svalinn_security.py` |
| `draupnir/svalinn/scanning.py` | `tests/unit/test_svalinn_security.py` |
| `draupnir/svalinn/integrity.py` | `tests/unit/test_svalinn_security.py` |

The README's claims — "the job environment carries lease references, so there is
no point at which the control plane could write a secret into a job environment
file"; "every outbound call declares a destination, a purpose, a run and an
approving policy, and an undeclared destination fails" — are true of the
libraries and true of nothing that runs. `draupnir/svalinn/sandbox.py:85`
comments that the plan carries "lease references, never values (see
`secrets.brokered_environment`)", but `execution.stand_in_plan`
(`draupnir/motsognir/execution.py:105`) builds `environment={"PYTHONHASHSEED":
"0"}`, and no plan is ever passed through the sandbox profile.

The reconciliation marks the sandbox DEVIATED because applying it needs the
appliance's kernel. That is fair for *applying* it. It does not cover the fact
that nothing composes it into a plan in the first place, which is control-plane
work and can be done here.

**Prompt**

> Put the cross-cutting security controls on the paths they exist for.
>
> 1. **Secrets.** Construct a `SecretsBroker` in the composition root from the
>    configured secret source. In `draupnir/motsognir/execution.py`, make every
>    `JobPlan`'s `environment` pass through `secrets.brokered_environment`, and
>    add a check in `submit()` that calls `broker.assert_no_secrets("job plan",
>    plan)` and refuses the submission if any brokered value appears verbatim.
> 2. **Egress.** Route every outbound call — the anchoring client of RF-07, the
>    JWKS fetch of RF-01, the transparency-log submission — through the
>    allow-list, each declaring destination, purpose, run and approving policy.
>    Keep the teacher-model destination absent, and keep the test that fails if
>    anybody adds it.
> 3. **Sandbox.** Compose the executor profile into every `JobPlan` the worker
>    dispatches, so the profile the driver is handed is the profile
>    `svalinn.sandbox` generates. Applying it stays the appliance's job; *the
>    plan carrying it* is the control plane's, and it is missing.
> 4. **Integrity and scanning.** Call `integrity.verify_inputs` before a run is
>    dispatched and `scanning` on ingest — or, if either is genuinely out of
>    scope for this release, delete the module and record the decision. A
>    security module nobody calls is worse than an absent one, because it reads
>    as coverage.

**Acceptance criteria**

- A test asserts a job plan whose environment contains a brokered secret value
  is refused at submission, naming the variable.
- A test asserts every outbound destination in the running system is on the
  allow-list, by enumerating the call sites rather than by assertion.
- A test asserts every plan the worker dispatches carries the sandbox profile,
  and that the profile has no `allow_network` field to set.
- `secrets.py`, `egress.py` and `sandbox.py` leave the orphan list;
  `scanning.py` and `integrity.py` either leave it or are deleted with a
  recorded reason.
- Gated in stages 2.1 and 2.4.

---

### RF-10 — P3 — The worker dispatches a stand-in, not the run's specification

> **Status: done.**
>
> **The prerequisite the finding does not state: the chain never recorded
> the specification.** Registration wrote `spec_hash`, `kind`, `name`, the
> input digests and the retry budget, and nothing else — so SAD 6.2's "the
> specification is the unit of reproduction" was false of the ledger, and
> the worker had nothing to render a job from. A run could be checked for
> having been tampered with and could not be reproduced.
>
> The normalised form is recorded, not what was posted: `spec_hash` is
> computed over `RunSpec.as_mapping()`, so the chain is self-verifying —
> read the specification back, hash it, and it equals the hash beside it. A
> run curated by the Sindri procedure records it at QUEUED rather than at
> registration, because the specification is compiled once the corpus
> exists; `facts_of` reads it from whichever entry carried it, since keying
> on the transition would have found one of the two paths and silently not
> the other.
>
> **The worker plans through the registry.** Dispatch, merge and each
> export resolve the driver the specification names, `validate`, then
> `render` — the same three steps `dryRunSpecification` takes, through the
> same `svalinn.pki.registry`. `render` is pure by Decision S5 and the
> conformance harness enforces it, so the two produce the same bytes; the
> test compares them rather than assuming it, because "should" is what this
> finding was made of.
>
> What the estate adds afterwards is its own and is not the driver's to
> know: the accelerator this forge offers (a specification is portable
> across the Forge Matrix and must not name one forge's hardware), the
> confinement profile of RF-09, and the lease references a job redeems at
> start. The comparison is over the driver's half, and the test says so.
>
> **Two things a real driver made visible.** The filename: LLaMA-Factory
> writes `adapter_model.safetensors`, and the worker looked for the
> stand-in's `adapter.safetensors` — it would have reported "exited zero and
> wrote nothing" about a job that wrote exactly what it said it would. The
> plan's `expected_artefacts` is recorded at TRAINING and read back at
> TRAINED. And the executor: the chain recorded `"executor": "stand-in"` as
> a literal whatever had run, which was true then and is the kind of truth
> that quietly stops being one. It records which driver rendered the job.
>
> **Evaluation was worse than a stub, and this is the half that matters.**
> `_measurements` derived a score per gate from the artefact's SHA-256 and
> `_baselines` returned `measurement * 0.95` — so every run was judged
> against ninety-five per cent of its own score, and every run passed. That
> is not a lenient baseline; it is no baseline, expressed in a way that
> always passes. Six gates, four of them relative, decided nothing.
>
> Measurements now come from the `draupnir.eval` driver's `collect_gates`,
> the value and not the verdict — a driver that filled `passed` in would be
> a driver that could pass its own evaluation, which is why `lm-eval` leaves
> it unset. The verdict is GLEIPNIR's, reached through
> `api.assurance.gleipnir_judge`, the seam that already existed for exactly
> this and that nothing called. **A gate with no measurement is not a gate
> that passed**: the run defers rather than being judged on the rest, which
> would report a pass for a suite that did not run.
>
> **A run with no baseline is refused, never given one.** `Gate.holds`
> already refuses a relative comparison with no baseline, and deriving one
> from the run under judgement turns that refusal into a pass. The refusal
> is a deferral rather than a failure: a run that could not be judged has
> not been measured, and recording a gate failure against a model nobody
> evaluated is a claim the chain would carry for as long as it exists.
>
> **Baselines are a matter of record.** `_remember_baseline` built a
> `BaselineRegistry` and dropped it on the floor — nothing held it and
> nothing read it, which is *why* the worker derived one from the run's own
> score. A capture is recorded against the baseline as subject, keyed by
> suite, artefact kind and jurisdiction so that re-capturing appends to the
> history of what it replaces rather than starting a second one nobody joins
> up. The worker rebuilds them from the chain each tick, because a baseline
> can be captured while a worker is running and one held from start-up would
> judge against a number that has moved.
>
> Read by subject *type* rather than by transition: `(subject_type,
> subject_id)` is the index this table carries, and a transition predicate
> would be a sequential scan of a chain AC-N5 sizes at a hundred thousand
> entries.
>
> **The stand-in stays, and announces itself.** `DRAUPNIR_WORKER_STAND_IN`
> is off by default, is asked for by name, logs `executor.stand-in` and
> `gates.stand-in` at warning level on every use in the shape
> `plugin.unverified` uses, and the chain records `development-stand-in` as
> the executor — so a run that was simulated says so for as long as the
> chain does, which is longer than any log. A simulation nobody is told
> about is the problem; one that announces itself is a development tool.
>
> The two integration tests that drive a run to approval ask for it by name,
> which is the property this finding wanted: there is no path by which a
> simulation happens because nobody chose otherwise.
>
> **One thing found and not fixed here.** `draupnir.hamarr.config.prepare`
> is called by nothing but its own tests, so the tier table is not consulted
> and `save_steps` is never derived — which means SAD 6.2's worked example
> is refused by its own training driver on both the dry-run and the dispatch
> path. That is RF-11, and it is left to RF-11.
>
> `draupnir.api.assurance` and `draupnir.raun.baselines` leave the orphan
> list.

`draupnir/worker/stages.py:159` — every dispatch, for every run — builds:

```python
plan = execution.stand_in_plan(output, [corpus], workdir=workdir, ...)
```

`stand_in_plan` (`draupnir/motsognir/execution.py:105`) runs `sys.executable -c
<STAND_IN>`. The run's specification — base model, corpus, hyperparameters,
jurisdiction — is not consulted, and neither is the plug-in registry. The same
is true of merge (`stages.py:415`) and export.

Evaluation is synthetic: `stages.py:628 _measurements` derives a score per gate
from the artefact's SHA-256, and `_baselines` returns `measurement * 0.95` so
"a healthy run reaches approval". The docstrings are candid about both. The
consequence is not.

`POST /v1/runs/dry-run` **does** use the real driver
(`draupnir/api/routers/runs.py:551`), renders the real command and returns it to
the operator. AC-F14's stated property — "the plan shown here is the plan that
would actually be run rather than an approximation of it" — is therefore false
of the running system: the console shows a LLaMA-Factory invocation and the
worker runs a Python one-liner.

**Prompt**

> Make the worker dispatch what the dry run rendered.
>
> 1. In `draupnir/worker/stages.py`, replace `stand_in_plan` in `dispatch`,
>    `merge_and_quantise` and the export path with plan rendering through the
>    plug-in registry: resolve the driver for the run's specification exactly as
>    `dryRunSpecification` does, call `validate` then `render`, and submit the
>    result. Because `render` is pure (Decision S5, enforced by the conformance
>    harness), the plan the worker submits and the plan the dry run showed are
>    the same bytes — assert that rather than assuming it.
> 2. Replace `_measurements` and `_baselines` with a call to the `draupnir.eval`
>    driver through `draupnir.api.assurance.gleipnir_judge`, which is the seam
>    that already exists for this and is currently an orphan. Baselines come
>    from the recorded base-model measurement; a run with no baseline is deferred
>    with a reason, never given a baseline computed from its own score.
> 3. Keep `stand_in_plan` and the synthetic measurements available under an
>    explicit development flag for `make procedure` on a machine with no GPU, and
>    log at warning level when they are used, in the same shape as
>    `plugin.unverified`. A simulation nobody is told about is the problem; a
>    simulation that announces itself is a development tool.

**Acceptance criteria**

- A test submits a specification, reads the dry-run plan, advances the run one
  tick, and asserts the dispatched plan is byte-identical to the dry-run plan.
- A test asserts a run whose gate has no recorded baseline is deferred with a
  named reason rather than approved against a derived one.
- A test asserts the development stand-in emits a warning naming itself, and
  that it is off by default.
- `draupnir.api.assurance` leaves the orphan list.
- Gated in stage 2.4.

---

### RF-11 — P3 — `submitRun` validates nothing, and the tier table is not consulted

> **Status: done.**
>
> **One door.** `admit` settles and validates a specification, and both
> `dryRunSpecification` and `submitRun` go through it. A specification the
> dry run accepts and the submission refuses is as bad as the reverse, and
> two implementations of "is this runnable" agree only on the day they are
> written — so there is one, and a parametrised test walks every refusal it
> can produce through both handlers, with a hypothesis property over
> generated specifications for the cases nobody thought of.
>
> Six refusals, in the order that makes each legible: the specification
> cannot be read; the tier table has drifted; the jurisdiction is not in the
> programme; the specification contradicts the tier; no installed driver can
> render it; the driver refuses it, in the driver's own words and all of
> them at once.
>
> **The jurisdiction check and the consistency check are separate**, because
> the operator's next action differs: one is a jurisdiction that does not
> belong here at all, and the other is two fields in a specification that
> disagree. Reporting both as `jurisdiction-unassigned` would send somebody
> looking for a country that is in fact listed.
>
> **A drifted table and an unreadable trust store are 503, not 422.** Both
> are deployment faults: nothing is wrong with the specification in front of
> the operator, retrying will work once the fault is fixed, and 503 is what
> says so. This mattered immediately — routing submission through the
> registry exposed RF-02's `TrustStoreError` on a path that had never built
> a registry, and as a 500 it would have read as a bug in the submission.
>
> **The settled specification is what is recorded.** `prepare` fills in the
> checkpoint interval HAMARR derives from the run's time budget, and the
> identity is computed over the form that will be rendered — so the chain's
> `spec_hash` is the hash of the specification the chain holds, and the
> worker renders exactly what was hashed (RF-10). The checkpoint policy is
> recorded beside it, so a reader can tell an authored interval from a
> derived one without re-deriving it.
>
> **SAD 6.2's worked example is internally inconsistent, and is now
> refused.** It declares GBR as Tier A and points at
> `MIDGARD-CORE-QWEN36-35B-A3B-v1.0`, which section 13.5 assigns to Tier B.
> `draupnir/hamarr/config.py` already recorded the resolution — the rule is
> authoritative and the example is stale — and enforcing it means the
> example as transcribed produces a 422 naming the mismatch. There is a test
> that asserts exactly that, and `tests/specs.py` supplies the corrected form
> for the many tests that are about idempotency, identity and the ledger and
> should not fail when the worked example is corrected.
>
> That is worth stating plainly: **the specification in the architecture
> document does not pass its own submission check.** Not because the check
> is wrong — base selection following from the tier is what makes fifty-six
> models comparable — but because the example predates section 13.5. The
> document is the thing to correct.
>
> **A refusal costs nothing.** The integration tests assert the chain is
> exactly as long after a refused submission as before it, and that the
> idempotency key is released so an operator who corrects a specification
> and retries is not told they have already submitted it. A ledger entry is
> the expensive half: the chain is append only, so a run recorded in error
> is in the record for ever and has to be explained rather than removed.
>
> The array clause is covered by something stronger than it asks for:
> `tiers.validate()` runs on **every** submission, not only on a
> fifty-six-element array, so a table that drifted is caught by the next run
> anybody submits rather than by the next array anybody submits.
>
> `draupnir.hamarr.tiers` and `draupnir.hamarr.config` leave the orphan
> list.

`draupnir/api/routers/runs.py:102` says "Validate, hash into a run identity, and
queue." The only check is `if not body.specification` at line 113. The driver's
`validate` is never called, so a specification that `dryRunSpecification`
refuses with 422 is accepted by `submitRun` with 202 and fails later, having
consumed a run identifier, a ledger entry and a place in the queue.

`draupnir/hamarr/tiers.py` — the tier table AC-F16 requires to "validate at
submission that the two lists enumerate all fifty six with no duplicate and no
omission" — is imported only by `tests/unit/test_tiers.py` and
`tests/unit/test_placement_and_arrays.py`.

**Prompt**

> Validate at submission.
>
> In `draupnir/api/routers/runs.py:submit`, before the run is registered:
> resolve the driver for the specification and call `validate`, returning 422
> with the driver's own problems if any; and validate the jurisdiction against
> `draupnir.hamarr.tiers`, raising on an unknown jurisdiction rather than
> resolving a default tier. Factor the driver resolution and validation shared
> with `dry_run` into one helper so the two cannot diverge — a specification the
> dry run accepts and the submission refuses is as bad as the reverse.
>
> Where the run is a 56-element array submission, validate that the two tier
> lists enumerate all fifty-six with no duplicate and no omission, per AC-F16.

**Acceptance criteria**

- `tests/integration/test_api_writes.py` asserts a specification the driver
  refuses is 422 at `POST /v1/runs`, and that no ledger entry is written.
- A test asserts an unknown jurisdiction is 422 naming the jurisdiction, and
  that no default tier is resolved.
- A test asserts a tier list with a duplicate or an omission is refused, naming
  which.
- A property test asserts `dryRunSpecification` and `submitRun` agree on
  acceptance for every generated specification.
- `draupnir.hamarr.tiers` leaves the orphan list.
- Gated in stages 2.2 and 2.4.

---

### RF-12 — P3 — Corpus ingest and curation are accepted and never performed

> **Status: done.**
>
> **The curation pipeline did not exist.** The finding says to perform the
> work "through `draupnir.hodd.ingest` and the curation pipeline".
> `hodd.ingest` existed and was called by nothing; there was no curation
> pipeline anywhere. The Sindri procedure dispatched the stand-in executor
> over the raw tree and recorded `{"dedupe": 0.82, "quality": 0.61,
> "decontaminate": 0.99}` as literals, beside
> `decontamination_confirmed=True` — three numbers nobody had measured and
> a guard nobody had evaluated. SAD 6.1 makes that flag a condition of
> reaching CURATED.
>
> So `draupnir/hodd/curation.py` is new, and it is deliberate about its
> limits. Three stages: deduplicate byte-identical documents, filter
> fragments below a length, remove any document containing an
> evaluation-set string. No language identification, no quality classifier,
> no near-duplicate detection — each of those is a decision about the corpus
> somebody should make deliberately, and near-duplicate detection needs a
> threshold somebody has to defend. A threshold nobody has defended is worse
> than none.
>
> **Curation refuses without evaluation sets, and that is the point.** The
> other two stages improve a corpus; decontamination is the difference
> between an evaluation score that means something and one that does not. A
> model trained on its own test set scores well and has learned nothing, and
> every gate downstream is then measuring the contamination. `curate` raises
> rather than proceeding, before it does any work, so a missing evaluation
> set is reported before minutes of hashing rather than after — and
> `decontamination_confirmed` is now earned rather than asserted. Whole
> documents are removed rather than the offending line: a document
> containing a test item is a document *about* that test item, and excising
> the line leaves the context that makes it findable.
>
> **The chain is the queue.** An `ingest-accepted` entry is outstanding
> until an entry naming its sequence number says otherwise, and both
> outcomes are recorded — a failure is an entry too. A worker holds nothing
> between ticks (SAD 11.2 row 1), so a restarted worker finds the same queue
> by reading the same chain.
>
> **Keyed by the accepted entry's sequence, not by the jurisdiction.** A
> corpus is re-ingested deliberately — new sources arrive, a licence is
> cleared — and keying on the jurisdiction would make the second request a
> silent no-op that still returned 202, which is the finding again in a
> different shape.
>
> **Atomicity is `hodd.ingest`'s and the duty does not undo it.** The duty
> does no partial work of its own: it hands the whole tree over and records
> what came back. A crash at any of the steps before the rename leaves a
> staged tree nothing references and no artefact at the address, the request
> is still outstanding, and the next tick does it properly — tested by
> crashing at each point, restarting, and asserting exactly one registered
> corpus and nothing left staged.
>
> **One window a crash can leave open**, and it needed handling. An attempt
> that published the artefact and died before recording the outcome leaves a
> sealed artefact and an outstanding request; retrying would be refused by
> the seal for ever, which is a livelock caused entirely by the seal doing
> its job. That case closes the request as completed and says why in the
> entry.
>
> **Where the bytes come from: an incoming directory, not a fetch.**
> Retrieving a corpus is outbound traffic to a host no allow-list entry
> covers, and threat T11 makes that the broker's decision rather than a
> background job's. On an air-gapped forge the curator copies the files onto
> the mount, which is what VLD-INF-SINDRI-001 describes. A jurisdiction with
> no directory is a failure entry naming the path, because that is the thing
> the curator has to do something about.
>
> **A failure alarms.** A curator waiting on an ingest that will never
> complete is exactly who an alarm is for, and an accepted entry nobody
> closed would be retried every minute while telling them nothing. An empty
> queue records nothing at all: a duty that logged "nothing to do" every
> minute would be the noise SAD 11.3's recording rule exists to avoid.
>
> **The procedure curates too.** M3's three literals are gone; it runs the
> same pipeline against an evaluation set it stages, so the numbers in the
> walkthrough's evidence pack came from counting. A walkthrough demonstrating
> a control by asserting it is the thing this register keeps finding.
>
> **The two halves are joined by a test.** The handler records and the
> worker consumes, and they are different deployable units — so the
> transition string is the whole of the contract between them, and a
> contract test asserts the handler writes the one the queue reads. A rename
> on either side would restore the finding exactly: a 202, a run identifier,
> and nothing ever happening.
>
> `draupnir.hodd.ingest` leaves the orphan list.

`POST /v1/corpora/{iso3}/ingest` (`draupnir/api/routers/corpora.py:183`)
documents itself as "Stage, hash, publish, seal and register" and returns 202.
What it does is record an `ingest-accepted` ledger entry. `curateCorpus` is the
same shape. Nothing consumes either entry: `draupnir/worker/stages.py:538
STAGES` maps run states only, and `draupnir/worker/duties.py` has duties for
chain verification, the fabric probe, vault capacity, anchor freshness and the
retention sweep — none for corpora.

So a curator registers sources, presses Ingest, gets a 202 and a run identifier,
and nothing ever happens. The corpus never reaches `CORPUS_REGISTERED`.
`draupnir/hodd/ingest.py` — which is atomic, and each of whose four failure
points is tested by crashing there — is never invoked by the running system.

**Prompt**

> Give the accepted corpus work something that performs it.
>
> Add `INGEST` and `CURATE` duties to `draupnir/worker/`, driven by unconsumed
> `ingest-accepted` and `curate-accepted` entries in the chain. Each duty reads
> the entry, performs the work through `draupnir.hodd.ingest` and the curation
> pipeline, and records the outcome — including a failure — as a further
> `corpus` entry, so the chain is the queue and nothing is held between ticks
> (SAD 11.2 row 1).
>
> Preserve ingest's atomicity: a duty that crashes mid-ingest must leave no
> half-registered corpus, which is the property `hodd.ingest` already has and the
> duty must not undo. Make the duty idempotent against a re-tick after a crash
> by keying on the accepted entry's sequence.

**Acceptance criteria**

- `tests/integration/test_worker_loop.py` asserts a `POST
  /v1/corpora/{iso3}/ingest` followed by worker ticks reaches
  `CORPUS_REGISTERED`, with the corpus hashed and registered.
- A test asserts a crash at each of ingest's four failure points, followed by a
  restart and a further tick, leaves exactly one registered corpus.
- A test asserts a failed ingest records a failure entry rather than silently
  leaving the accepted entry unconsumed.
- Gated in stage 2.4.

---

### RF-13 — P3 — The fifty-six element array is not built

> **Status: done, with one part refused on purpose and recorded as such.**
>
> **The array is a subject in the chain.** `POST /v1/arrays` records an
> accepted submission, a worker duty submits it, and the outcome — the
> `--array` directive, the job identifier, the driver, and one record per
> element — is the entry. `GET /v1/arrays` folds that back. One entry for
> the array rather than fifty-six for its elements: fifty-six would be
> fifty-six things to read back and join up, and what an operator asks
> about is the array.
>
> **`getArray` read runs.** It listed the runs at the site, sorted them and
> numbered them `0..n`, so `size` was the number of runs rather than
> fifty-six, `attempts` was `max(1, 4 - retry_budget)` — a formula rather
> than a count — and a site with sixty runs from other work reported a
> sixty-element array. A site that has submitted no array now says so,
> which is the answer the old handler could not give because it always had
> a number.
>
> **One submission, not fifty-six.** The scheduler holds all N and runs M
> of them, so utilisation does not depend on the control plane being awake
> (SAD 11.2). Fifty-six separate submissions would be fifty-six independent
> schedules, `%3` would mean nothing, and a control plane that stopped
> would stop the estate. The plan carries an `ArraySpec` rather than a
> rendered string, so MOTSOGNIR states what the array is once and the
> driver states how its scheduler spells it.
>
> **Losing an appliance reduces concurrency.** `placement.plan` already did
> that for the adapter partition and already refused for the ring, and
> nothing called it with an array. A test now asserts both halves against
> one estate with one appliance down: the array is placed at `0-55%2` and
> the ring run is refused. That difference is the point — an array is N
> independent elements and a ring job is one job that needs the whole ring.
>
> **The requeue is the driver's requeue, and at Sindri it refuses.** This
> is the part worth reading carefully. The finding asks for a resubmission
> of `--array=<index>`; AC-F6's own note says a resubmission is not a
> requeue — it produces a new job identifier, which severs the element from
> its array and loses both the `%M` throttle and the accounting record that
> ties the fifty-six together. `motsognir.slurmrest/v1` already refuses to
> approximate it, because slurmrestd v0.0.40 exposes submit, read and
> cancel and no requeue.
>
> So the operation calls the driver's `requeue` and never resubmits in its
> place. Where the transport can do it (`motsognir.slurm/v1`, `scontrol
> requeue <job>_<index>`) element seventeen goes back on the queue and the
> other fifty-five are untouched. Where it cannot — which is this estate
> today — the refusal is **written to the chain carrying the driver's own
> message**, which names what to run on REGIN. That is the honest outcome:
> S12's button exists, does the right thing where the right thing is
> available, and tells the operator exactly what to do where it is not,
> rather than quietly doing something else and calling it a requeue.
>
> AC-F6 stays DEVIATED, and its note now says what changed: the operation
> and the record exist; the transport still cannot requeue.
>
> **`ArraySpec` gained `indices`**, so a submission can name the elements it
> places rather than always rendering `0-(N-1)%M`. `size` stays the array's
> size: this is a submission of part of an array, not a smaller array. No
> throttle on an explicit list — a submission of one element has nothing to
> throttle, and `--array=17%1` would say otherwise to anyone reading the
> job.
>
> **One queue implementation.** RF-12 built "the chain is the queue" for
> corpora and this needed the same thing, so `worker/accepted.py` is the one
> implementation and both use it. Two would have agreed on the day they
> were written.
>
> **Every element names its jurisdiction.** Slurm addresses elements by
> index, so element seventeen has to mean the same jurisdiction to the
> board, the requeue and an auditor a year later — and the subjects are
> validated against the tier table before any of them is submitted (RF-11),
> because an element for a jurisdiction nobody assigned would train a
> fifty-seventh model.
>
> `draupnir.motsognir.arrays` leaves the orphan list. `draupnir.motsognir.
> retry` does not: its backoff and its "not worth retrying" exit codes are
> for an automatic retry policy, and what RF-13 built is an operator-driven
> requeue. Naming it here rather than claiming otherwise.

`draupnir/motsognir/arrays.py` and `draupnir/motsognir/retry.py` — the
`--array=0-55%3` submission and the single-element `--array=<index>` retry the
README describes at length — are orphans.

`GET /v1/arrays` (`draupnir/api/routers/models.py:151`) does not read an array.
It lists the runs at the site, sorts them, and numbers them `0..n`. `size` is
the number of runs, not 56; `attempts` is `max(1, 4 -
run.retry_budget_remaining)`, which is a formula rather than a count of
attempts. S12's stated primary action — "Requeue a single element" — has no API
operation.

**Prompt**

> Build the array as a first-class object.
>
> 1. Add an array submission path that takes one specification and submits
>    fifty-six elements as a single scheduler array through
>    `draupnir.motsognir.arrays`, recording the array and its elements in the
>    chain as one subject with fifty-six members.
> 2. Change `getArray` to read that object rather than deriving one from the run
>    list, and report a real attempt count from the chain.
> 3. Add a per-element requeue operation that resubmits `--array=<index>` alone
>    through `draupnir.motsognir.retry`, leaving the other fifty-five untouched,
>    and wire it to S12's requeue control.
> 4. Reduce concurrency rather than refusing when an appliance is lost, which is
>    the behaviour `arrays.py` already implements and nothing calls.

**Acceptance criteria**

- A test asserts a 56-element submission produces one scheduler submission with
  `--array=0-55%3`, not fifty-six submissions.
- A test asserts requeueing element 17 resubmits `--array=17` and does not touch
  the other fifty-five.
- A test asserts losing an appliance reduces array concurrency and does not
  refuse the array, and that the same loss *does* refuse a ring run.
- `getArray` reports `size == 56` for a 56-element array regardless of how many
  runs exist at the site.
- `draupnir.motsognir.arrays` and `draupnir.motsognir.retry` leave the orphan
  list.
- Gated in stages 2.1 and 2.4.

---

### RF-14 — P4 — The idempotency store is process-local

> **Status: done.**
>
> `idempotency_key` is a table, keyed on `(site_id, actor, key)`, with row
> level security and FORCE like every other scoped table. The in-memory
> store stays and keeps its interface exactly: it is what the contract
> tests run against, because a test of "does 409 carry a problem document"
> should not need PostgreSQL, and because the three-way outcome is a
> property of the rule rather than of the storage.
>
> **The reservation is one statement.** `INSERT ... ON CONFLICT ... DO
> UPDATE ... WHERE created_at <= cutoff` claims a fresh key, takes over an
> expired one, and declines to touch a live one — atomically. Whichever
> transaction commits first owns the key and the other is told so by the
> database. A read-then-write would be a race between the read and the
> write, which is the failure the key exists to prevent, reintroduced by
> the fix for it. A test reserves the same key from four stores and asserts
> exactly one winner.
>
> **Two independent stores over one database are two API processes**, and
> that is how the tests are written. The in-memory store cannot pass any of
> them, which is the point: SAD 5.1 specifies two to four processes, and
> "a second click while the first request is still running is refused rather
> than acting twice" was true of one process and false of the deployment.
>
> **FORCE matters here as much as anywhere.** Without it the application's
> own role is exempt from the policy, and a key from one forge would
> resolve at another — so the table joins `SITE_SCOPED_TABLES` and the
> existing schema assertions cover it rather than a new test asserting the
> same thing in a second place.
>
> **The sweep is on the worker's timetable, hourly.** On a request path it
> would make one unlucky caller pay for everybody else's expired keys, and
> would do nothing at all on a quiet estate — which is exactly when the
> table grows without anybody looking. A sweep that finds nothing records
> nothing: a duty logging "swept nothing" hourly is the noise SAD 11.3's
> recording rule exists to avoid. A sweep that *fails* is not an alarm
> either, and that is deliberate — keys expire by age whether or not
> anything deletes them, because `reserve` takes over an expired row, so a
> failed sweep costs disk and never correctness.
>
> **Synchronous, and it says so.** The handlers call `reserve` and
> `complete` synchronously from async endpoints, and keeping the interface
> identical was the instruction — a store that had to be awaited would
> change every call site and every test. Each call is a single primary-key
> statement against a table with one row per outstanding request. That is a
> real cost on the event loop and it is named in the module rather than
> left for somebody to find with a profiler.
>
> **Two things the fix did to neighbouring tests, both worth naming.**
>
> The migration scaffolding test counted the migrations in the repository and
> asserted five. It counts them now and asserts the three it scaffolds follow
> whatever is there, because a test about scaffolding should not fail when
> somebody adds a migration.
>
> And the sweep test ticks a worker, which records duties to that site's
> chain. Written against `sindri` those commits collided with the fixed
> sequence numbers `test_repositories` and `test_projection` build their chains
> from — twenty-one failures in the full suite and none in isolation, which is
> the most annoying shape a test failure has. It has a forge of its own now, as
> the worker tests do: a site is an installation, and a test estate is an
> installation like any other (Decision S12).
>
> **The table is modelled as well as migrated.**
> `test_models_match_schema.py` asserts every migrated table has a model and
> every column agrees, so the declarative model gained `IdempotencyKey` rather
> than the table being an exception to a rule the repository otherwise keeps.

`draupnir/api/deps.py:52` is `STORE = IdempotencyStore()`, and
`draupnir/api/idempotency.py:123` backs it with `records: dict[...]`. There is
no idempotency table in `migrations/versions/`.

SAD 5.1 specifies "two to four" API processes. A key reserved in process A is
unknown to process B, so the documented behaviour — "a second click while the
first request is still running is refused rather than acting twice" — fails
precisely under the concurrency the control exists for, and every reservation is
lost on restart. `POST /v1/runs` is partly protected by AC-F2's identity check;
`registerSource`, `ingestCorpus`, `curateCorpus`, `decideGate`, `cancelRun`,
`retryRun` and `publishRelease` are not protected at all.

**Prompt**

> Move the idempotency store into PostgreSQL.
>
> Add a migration creating an `idempotency_key` table keyed on `(site_id, actor,
> key)` with the request fingerprint, the reserved-at timestamp, the completed
> response and its status, and an expiry. Enable row level security with FORCE,
> as every other scoped table has. Reserve with an `INSERT … ON CONFLICT DO
> NOTHING` so the reservation is atomic across processes; a losing insert is the
> in-flight 409. Sweep expired records from the worker's timetable rather than
> from a request path.
>
> Keep `IdempotencyStore`'s interface and its three refusals exactly as they are
> — the in-memory implementation stays for unit tests — and add the
> database-backed one behind the same protocol.

**Acceptance criteria**

- `tests/integration/test_api_writes.py` asserts a key reserved through one
  session and replayed through a *second independent session* returns the first
  response, and that an in-flight key in another session is 409.
- A test asserts a key with a different body fingerprint is 422.
- A test asserts records expire and are swept by the worker.
- A test asserts the table has RLS with FORCE, alongside the existing
  `tests/integration/test_schema_constraints.py` assertions.
- Gated in stage 2.4.

---

### RF-15 — P4 — The event stream is process-local, carries one event kind, and the per-run stream is not a stream

Three defects in one subsystem.

**Process-local.** `draupnir/api/routers/runs.py:82` holds `STREAMS: dict[str,
EventStream]` in module state, and `EventStream` is asyncio queues plus a
2,048-entry in-memory buffer. Line 81's comment — "a deployment fans out from
the ledger's notification channel into the same shape" — describes something not
built: `grep "pg_notify\|add_listener\|LISTEN"` over `draupnir/` and
`migrations/` returns nothing.

**One event kind.** `grep "\.publish("` finds exactly one publisher,
`runs.py:180`, in `submitRun`. No state transition, gate decision, cancellation,
retry or progress update ever reaches the stream — and the worker, which is a
*separate process*, is the thing that performs all of them. AC-U4 and AC-N3 pass
in e2e only because the test submits through the same process that serves the
stream.

**Not a stream.** `streamRunEvents` (`runs.py:480`) takes `run_id`, uses it only
in a log line, yields the site stream's buffered frames plus one keep-alive
comment, and closes. It neither filters by run nor stays open.

**Prompt**

> Make the event stream real.
>
> 1. Publish deltas from a PostgreSQL `NOTIFY` fired by the ledger append, and
>    have each API process `LISTEN` and fan out into its local `EventStream`.
>    The buffer and the `Last-Event-ID` resynchronisation logic stay as they are;
>    what changes is where events come from. Sequence numbers must be the
>    ledger's, not a per-process counter, or a reconnect to a different process
>    resynchronises against the wrong ordinal.
> 2. Publish a delta for every transition and every recorded entry, not only for
>    submission. Keep the existing refusal of an event listing no changed fields.
> 3. Fix `streamRunEvents` to filter by `run_id` and to stay open using
>    `live_frames`, as `streamSiteEvents` does. Assert the filter: a delta for
>    another run must not appear on a run's stream.

**Acceptance criteria**

- A test asserts a transition performed by a worker process appears on a stream
  opened against a *separate* API process within five seconds.
- A test asserts `GET /v1/runs/{id}/events` yields only deltas whose subject is
  that run, and that the connection stays open past the buffered frames.
- A test asserts sequence numbers are ledger sequence numbers, and that
  `Last-Event-ID` across a process change resynchronises correctly.
- A test asserts an event with no changed fields is still refused.
- Gated in stage 2.4.

> **Status: done.**
>
> **The chain is the source, because it already was.** Every change this
> system makes is an entry in `ledger_entry`, appended in the transaction
> that made it. Migration 0004 adds an `AFTER INSERT FOR EACH ROW` trigger
> that fires `pg_notify`, so a notification happens exactly when something
> happened, exactly once, and only if the transaction committed — a listener
> cannot see an event for a change that was rolled back, and there is a test
> that rolls one back and then commits another to prove the silence was the
> rollback rather than a listener that was never receiving.
>
> **What travels is identity, not payload.** `pg_notify` refuses anything
> over 8,000 bytes and a ledger payload has no bound: a merge configuration
> or a set of gate results would exceed it, and a notification that is
> sometimes delivered is worse than one that never is. So the trigger sends
> site, sequence, subject, transition, actor and instant, and a consumer
> needing more re-reads the entry it names.
>
> **The translation is in Python, not in SQL.** What kind of event a
> transition is, and what a console should merge, is application vocabulary —
> SAD 6.1's states, the run board's fields. A trigger that knew them would be
> a second place they are written down, and the one that drifts is always the
> one in the database.
>
> **Sequence numbers are the ledger's**, which is the whole reason for doing
> it this way. `id: 41` means entry 41 of this site's chain in every process,
> so a console that the proxy moves to a different upstream is answered
> against the same ordinal. This was the defect that told nobody: two
> processes counting independently both produce plausible small integers, so
> a client reconnecting elsewhere was answered confidently with the wrong
> events, or with none, and had no way to detect either. A test appends from
> another process, reads the frame's `id`, and looks that number up in the
> chain.
>
> **`submitRun`'s hand-written publish is gone.** It was the only publisher,
> and leaving it would have put one event on the stream twice — once by hand
> and once by the trigger. `EventStream.publish` drops an event whose
> sequence it has already seen, which matters for a reconnect that replays
> rather than for the fan-out: two processes both listening is how this is
> meant to work.
>
> **`streamRunEvents` now filters and stays open.** It took `run_id` and used
> it only in a log line, so a console watching one run received every other
> run's changes and had to filter client side, which nothing told it to do;
> and it yielded the backlog and closed, so it was a page of history to poll
> rather than a stream. The backlog is filtered as well as the live half —
> filtering only the live half would put every other run's history on the
> stream at the moment a console connected.
>
> **A listener that dies takes every console with it, so it says so.** The
> board degrades to whatever it last held, which looks like a system that has
> stopped rather than one whose notifications have. Losing the connection is
> logged at error level and retried after a delay, because reconnecting
> immediately in a loop against a database that is down is how a control
> plane turns an outage into two. An entry the listener cannot translate is
> skipped and logged rather than allowed to drop the connection: the refusal
> of an event with no changed fields exists to stop a publisher sending a
> refresh instruction, and one unrecognised entry must not stop a board
> updating.
>
> **Found while implementing: `TestClient` cannot read a live stream.** It
> buffers a whole response before returning, so a request to a stream that
> stays open never returns at all. That is why the one existing stream test
> passed — the stream closed, which was the defect. The three contract tests
> that read a stream are gone, replaced by a comment saying why, and what a
> stream *does* is asserted in `tests/integration/test_event_stream.py`
> against a live database. That is also the only place the interesting claim
> can be made: the appends there are committed by a *separate operating
> system process*, because the thing RF-15 says was broken is that the worker
> is a different program.
>
> **AC-N3's five seconds is measured, and separated from the timeout.** The
> budget is asserted once, on delivery, timed from the moment the appending
> process has committed — so what is timed is the notification path and not a
> Python interpreter starting up. Everything else waits on a much longer
> ceiling whose only job is to turn a hang into a failure. Conflating the two
> made the suite fail when Docker was merely busy, which is worse than no
> test because it teaches people to re-run it.
>
> `live_frames` is now annotated `AsyncGenerator` rather than `AsyncIterator`,
> because a caller that stops reading early has to be able to `aclose()` it —
> the subscriber is removed by `subscribe()`'s `finally`, and leaving that to
> the garbage collector holds a queue per departed console.
>
> AC-U4 and AC-N3 now cite the integration suite alongside the journey. Both
> passed in end-to-end only because the test submitted through the same
> process that served the stream, so the one arrangement never exercised was
> the one that runs in production.

---

### RF-16 — P4 — Two paginated collections ignore their cursor

`draupnir/api/reading.py:373` (`approvals`) and `:488` (`models`) both begin
`del cursor` and both return `next_cursor=None`. Both operations advertise a
`cursor` query parameter in the OpenAPI document. A site with more approvals or
models than `limit` silently truncates, with nothing in the response to say so
and no way to reach the rest.

This contradicts AC-B3 and the README's "Pagination is cursor based everywhere.
A cursor is a position in the `(created_at, id)` order rather than a count of
rows skipped".

**Prompt**

> Implement cursor pagination for `listApprovals` and `listModels` in
> `draupnir/api/reading.py`, using the same `(created_at, id)` cursor the runs,
> sources and ledger queries already use. Return a `nextCursor` when a further
> page exists and `null` when it does not.
>
> Then add a contract test that enumerates every operation in the OpenAPI
> document declaring a `cursor` parameter and asserts each one honours it, so the
> next collection cannot ship without pagination. A convention followed query by
> query is a convention the next query skips.

**Acceptance criteria**

- `tests/integration/test_repositories.py` asserts that paging through approvals
  and models with `limit=2` reaches every row exactly once.
- A test asserts a row inserted mid-pagination changes what comes next and never
  removes something that was going to come — the property the README claims.
- `tests/contract/test_api_surface.py` asserts every cursor-declaring operation
  returns a distinct second page.
- Gated in stages 2.3 and 2.4.

> **Status: done.**
>
> **Both collections now page like the other three.** `approvals` keys on
> `(started_at, r.id)` ascending — ascending because the queue is oldest
> first, so the comparison is `>` where every other collection uses `<`. The
> tiebreak is the run's identifier rather than the instant alone, because
> `started_at` is not unique: two runs submitted in the same transaction
> share it, and a cursor on a non-unique column either repeats a row or skips
> one. It is also trimmed to the page *before* the gate results are read,
> since loading the over-fetched row's gates is a second query's worth of
> work for a row nobody is shown.
>
> **`models` lost its `a.uri` tiebreak**, and that is a real change rather
> than a tidy-up. Ordering the unpublished tail alphabetically read better,
> but a keyset cursor needs a unique second column and `uri` is not one.
> `NULLS LAST` became `COALESCE(..., '-infinity')` for the same reason the
> runs query does it — the cursor comparison has to have a value to compare.
>
> **The enumeration is the guard, not a list.** Both the contract test and
> the integration suite read the cursor-declaring operations out of
> `docs/api/openapi.json`, so a sixth collection that advertises a cursor and
> ignores it fails without anybody remembering to add it. That is the whole
> point: this defect existed because the convention was followed query by
> query, so the next query skipped it. A list maintained by hand would have
> the same property.
>
> **Found while implementing: a malformed cursor was silently ignored.**
> `reading.Cursor.decode` returned `None` for anything it could not parse and
> every caller read that as "start from the beginning", so a client whose
> cursor was corrupted in transit was served page one and told nothing — then
> pages forward, corrupts it again, and loops, with every response it gets a
> well formed page. `pagination.Cursor` in the same codebase refuses exactly
> this, in as many words, in its docstring. Two cursor implementations
> disagreeing about it is how the quieter one wins. It is now a 422
> `invalid-cursor`, alongside the existing `invalid-page-size`.
>
> The audit view had its own version of the same thing: its cursor is a
> ledger sequence number, and `int(cursor) if cursor.isdigit() else None`
> made anything else mean "start from the newest". Of everywhere in this
> system to quietly show the wrong window, the chain is the worst one. It
> refuses too. Its cursor stays a sequence number rather than becoming a
> `(created_at, id)` pair — a chain is already totally ordered by `seq`, and
> a compound cursor would be a second ordering to keep in step with the
> first.
>
> **Where the tests live differs from the register's suggestion.** The
> acceptance criteria name `tests/integration/test_repositories.py`, which
> tests repositories over synchronous connections; the cursor lives in the
> read model, which is asynchronous and reached over HTTP. So the behaviour
> is asserted in a new `tests/integration/test_pagination.py`, against a real
> API process with real rows in PostgreSQL — every collection paged to the
> end at `limit=2`, every row reached exactly once, and a row inserted behind
> the boundary shown to remove nothing that was still coming.
>
> The contract file keeps the half that needs no database, and it is
> structural rather than behavioural on purpose: a read model stubbed in the
> contract suite pages perfectly while the query it stands for ignores its
> cursor, which is precisely what was happening. It asks the syntax tree
> whether each cursor-taking read ever *reads* the name — `del cursor`, the
> idiom both broken reads used, is a statement about a name rather than a
> string to grep for. Run against the previous commit it names `approvals`
> and `models` and nothing else.
>
> AC-B3 claimed pagination was keyset based "throughout" while two of the
> five collections discarded their cursor. It now says so accurately, and
> cites the integration suite where the property is actually demonstrated.

---

### RF-17 — P4 — `readyz` checks one dependency and builds an engine per probe

`draupnir/api/routers/health.py:63` calls `create_engine()` and
`engine.dispose()` on every probe, so a readiness check every few seconds
constructs and tears down a connection pool at that rate rather than using the
pooled engine the lifespan already owns.

It reports one check, `database`. SAD 11.2 has nine degraded modes; the object
store, the HODD vault, the scheduler and the federation link are all invisible
to readiness. `docs/runbook.md` sends an operator to `/readyz` for exactly the
situations it cannot report.

**Prompt**

> Rework `/readyz`.
>
> Use the engine the application lifespan already created rather than building
> one per probe. Add checks for the object store, the HODD vault (through
> `hodd.reconcile.require_vault`, so a dropped mount is distinguished from a
> directory somebody created on the mount point), the scheduler driver and the
> federation link. Each check reports independently, has its own timeout, and a
> failure degrades rather than raises, which is the existing and correct
> behaviour. Keep the response free of run, artefact and ledger content.
>
> Cross-reference the check names against the nine rows of SAD 11.2 in
> `docs/runbook.md`, so an operator reading a degraded probe finds the row.

**Acceptance criteria**

- A test asserts `/readyz` does not construct a new engine, by counting engine
  creations across ten probes.
- `tests/integration/test_degraded_modes.py` asserts each injected fault appears
  as a named `false` check, for every row of SAD 11.2 that has one.
- A test asserts one slow dependency does not delay the probe beyond its
  timeout.
- Gated in stage 2.4.

> **Status: done.**
>
> **The probe builds nothing.** `draupnir/api/readiness.py` holds a
> `Dependencies` the lifespan wires once, with the engine the read model is
> already using. An engine is a connection pool; built and disposed per probe
> it pools nothing, and an orchestrator checking every few seconds was
> constructing and tearing one down at that rate.
>
> **Five checks, and each one is an operator's index into the runbook.**
> `database`, `vault`, `object_store`, `scheduler`, `federation`. The vault
> goes through `hodd.reconcile.require_vault` rather than `is_dir()`, because
> the failure a bare check calls healthy is the dangerous one: a directory
> somebody created on the mount point looks exactly like a mounted vault and
> is empty. `docs/runbook.md` gained a "Reading `/readyz`" section mapping
> each name to its row of SAD 11.2, `readiness.RUNBOOK_SECTIONS` is the same
> join in code, and a test asserts every section it names exists — a mapping
> kept in prose drifts the first time somebody renumbers a section.
>
> **A dependency that is not configured is absent, not `false`.** This is the
> decision the rest of the design follows from. Reporting `false` for
> something a forge does not deploy would leave it permanently degraded, an
> orchestrator acting on readiness would never bring it into service, and an
> operator would learn to ignore the probe — which costs it the only thing it
> is for. So `status` is computed from what was actually checked. The object
> store appears only where it is the store in use: `hodd.stores.store_for`
> picks the vault when one is configured and never opens a bucket, so a bucket
> check would be reporting on something that cannot affect service.
>
> **Reachability, not capability.** The scheduler and federation checks accept
> any HTTP answer below 500, including 401 and 404. `slurmrestd` answering
> "unauthorised" is `slurmrestd` answering, and these rows are about the link.
> Naming an endpoint path here would put the driver's `API_VERSION` in a
> second place and make the probe fail on a version bump that broke nothing.
>
> **Two brokered clients, not one.** Found while wiring it: the broker
> approves a *destination under a policy*, and REGIN is `scheduling/2026.01`
> while MEGINGJORD is `federation/2026.01`. A single client would have been
> refused for one of them — and worse, a client that could reach both is
> exactly what the allow list's two separate entries exist to prevent. The
> allow list's literals became `SCHEDULING_PURPOSE`/`SCHEDULING_POLICY`
> alongside the federation pair, and both purposes now say the probe is one of
> the reasons this control plane reaches them: a purpose is evidence, and a
> call the document does not describe is a call nobody approved.
>
> **`Settings` gained `registry_url`.** The worker has carried it since RF-07;
> the API needs it to say whether the wide-area link is up.
> `deploy/install.sh` already writes it into the one environment file both
> units read, so this names a setting that was there rather than adding one.
>
> **Each check has its own timeout and they run concurrently.** Four
> dependencies at two seconds each in sequence is eight, which is longer than
> the interval an orchestrator probes on — the probes overlap and the pile-up
> reads as the API being slow. Each check also has its own `except`:
> `gather` propagates the first exception and discards the rest, so one
> raising check would cost the operator the four answers that arrived.
>
> **Found while writing the test: the obvious engine count is vacuous.**
> Patching `database.create_engine` counts nothing, because the old
> `health.py` did `from ... import create_engine` at module scope and held its
> own reference — and patching `sqlalchemy.ext.asyncio.create_async_engine`
> fails the same way one level down, since `database.py` binds it at import
> too. The counter goes on the name *as the caller resolves it*. Against the
> previous commit the test now reports "ten readiness probes built 10
> engines"; the first two versions of it passed while ten pools were being
> built.
>
> The faults are injected for real in `tests/integration/test_degraded_modes.py`
> — a scheduler URL nothing is listening on, an absent vault root, a mount
> point holding a directory somebody made, and the MEGINGJORD name that does
> not resolve because the WireGuard link is not built. Row 5's existing test
> now asserts `checks["database"] is False` by name rather than searching the
> body for the word "degraded", which sent an operator to the logs.

---

### RF-18 — P4 — `/metrics` exposes no DRAUPNIR metric, and traces go nowhere

`GET /metrics` returns `prometheus_client.generate_latest()`. Nothing registers
a collector:

```bash
python -c "
from fastapi.testclient import TestClient
from draupnir.api.app import create_app
b = TestClient(create_app()).get('/metrics').text
print(sorted({l.split(' ')[2] for l in b.splitlines() if l.startswith('# HELP')}))"
```

→ `['python_gc_collections_total', 'python_gc_objects_collected_total',
'python_gc_objects_uncollectable_total', 'python_info']`.

`draupnir/api/telemetry.py` implements its own `Span` and `Tracer` with no
OpenTelemetry integration and no exporter, so spans are constructed and
discarded. `opentelemetry-instrumentation` is in the lock file and unused.

SAD 8.1 lists `/metrics` as an operational endpoint and SAD 11.3 gives eight
signals a source and a surface. The signals that reach a structlog finding do
so; nothing reaches a scrape.

**Prompt**

> Give the observability endpoints something to report.
>
> 1. Register Prometheus collectors for the SAD 11.3 signals that are the
>    control plane's own: run state and queue depth, gate pass rates and margins,
>    vault capacity, chain-verification outcome, anchor age, and request latency
>    and status by route. Label by nothing unbounded — no actor, no artefact, no
>    run identifier — which is the constraint the endpoint's docstring already
>    states and which should be tested rather than described.
> 2. Wire `telemetry.Tracer` to an OTLP exporter behind configuration, so a span
>    reaches a collector when one is configured and is discarded when it is not.
>    Keep the redaction in the emitter.
> 3. If OTLP export is out of scope for this release, remove the OpenTelemetry
>    dependency and say so in the SBOM rationale, rather than shipping an
>    instrumentation library nothing instruments.

**Acceptance criteria**

- A test asserts `/metrics` exposes each named signal after the relevant action,
  and that no metric carries an unbounded label — enumerate the label sets and
  fail on `actor`, `run_id` or `artefact`.
- A test asserts a span reaches a stub OTLP collector when configured.
- A test asserts every metric name appears in the SAD 11.3 signal table, so a
  metric added without a documented surface fails the build.
- Gated in stages 2.1 and 2.3.

> **Status: done.**
>
> **Eight metrics where there were none.** `draupnir/api/metrics.py` carries
> the SAD 11.3 signals that are the control plane's own — runs by state (the
> queue depth being the QUEUED one), gate results and margins by gate, vault
> capacity, chain integrity, anchor age, duty alarms — and a request latency
> histogram by method, route template and status.
>
> **Most of them are columns; two are not, and that is what migration 0005 is
> for.** Verifying the chain is the hourly duty's entire cost, so doing it
> inside a scrape would make Prometheus's interval the rate at which this site
> rehashes its ledger. Asking an NFS mount how full it is can block
> uninterruptibly, and a scrape that blocks is a target Prometheus marks down —
> reporting the control plane as gone because the vault was slow. So the worker
> writes what it measured to `duty_measurement` and the API reads it: one row
> per site per duty, overwritten in place, in the duty's own transaction so a
> reading and the alarm entry that may accompany it commit together.
>
> **Not the ledger.** The chain records state transitions and a vault at forty
> per cent is not one. SAD 11.3's recording rule is exactly why duties log what
> they find and record only alarms; appending a reading every fifteen minutes
> would add thirty-five thousand entries a year per forge, all saying nothing
> happened.
>
> **The cardinality rule is enforced rather than described.** The endpoint's
> docstring already said a metric labelled by actor is unbounded and one
> labelled by artefact leaks what is being built — a statement about metrics
> that did not exist. `LABELS` is now the complete permitted set, and tests
> read a live exposition and fail on anything outside it. The trap it actually
> guards is the request histogram: labelling by *path* puts a run identifier in
> a label, which is both a series per run forever and a list of this forge's
> work on an endpoint SAD 8.1 serves without a credential.
>
> **Site-wide gauges are duplicated across processes, and the `HELP` text says
> so.** SAD 5.1 runs two to four API processes and each reports the same value,
> so a dashboard that sums them reports three times the queue depth. Aggregate
> with `max by`; the request histogram is the exception and aggregates with
> `sum by`, because it describes the work one process did.
>
> **The spans go somewhere now.** `draupnir/api/tracing.py` posts OTLP over
> HTTP with JSON through the egress broker. Not the SDK exporter, and that is
> not a preference about dependencies: every outbound call in this system goes
> through `BrokeredClient` — the JWKS fetch, the telemetry read, the anchor
> submission, RF-17's readiness probes — and an SDK exporter holds its own
> transport and opens its own socket. Wiring one in would create the single
> call in the process that no allow list decided, which is threat T11 exactly.
> REGIN gains a third destination under its own policy, so a driver holding the
> scheduling approval cannot spend it on the collector.
>
> **The three OpenTelemetry distributions are gone**, with the reasoning in
> `pyproject.toml` where the other dependency arguments live. Nothing imported
> them; shipping an instrumentation library that instruments nothing puts six
> distributions in the image and the SBOM to be scanned, patched and explained,
> in exchange for nothing. Removing them took six out of the lock file.
>
> **Found while implementing: the tracer nested concurrent requests into each
> other.** `telemetry.TRACER` was a module global holding one open-span stack,
> so whichever request opened a span first became the parent of whatever any
> other request opened next. That was invisible while the spans were collected
> and discarded — and it becomes a trace showing one operator's approval under
> another operator's submission the moment anything exports them. The tracer is
> now a context variable set per request, with the process-wide one still there
> for the worker and the procedures.
>
> **Found while implementing: the tracer was also a leak.** A list that grew
> for the life of every process this system has ever run, because nothing ever
> drained it. `drain` clears unconditionally and exports optionally, which is
> the right way round.
>
> **Found while testing: registering a collector calls `collect()`.**
> `CollectorRegistry.register` builds its name index that way unless a
> collector offers `describe()` — so *starting* the process performed a
> database query, and against a database that is down it waited out the
> connection timeout. RF-17's readiness test, which starts an API pointed at a
> port nothing is listening on precisely to prove the process still answers,
> went from seconds to over a minute and then failed. Which is the failure SAD
> 11.2 exists to prevent, arriving through the observability code. `describe()`
> fixed it; startup is back to 1.2 seconds.
>
> **Found while testing: the scrape read every site's runs.** `read_facts`
> relied on row level security alone, and RLS does not apply to a superuser or
> a role holding `BYPASSRLS` — the integration container's default role is one,
> and the first run of the test reported two forges' queued runs added
> together. Every other read in `reading.py` names the site in its `WHERE` as
> well as scoping the session; this now does too.
>
> **Found while testing: a mounted route's template loses its prefix.**
> `path_format` on a route under the `/v1` mount reports `/runs/{run_id}`, so
> the label would have merged two API versions' latencies into one series the
> day a `/v2` existed. The template is rebuilt from the path and the matched
> parameters, whole segments only.
>
> **Two of SAD 11E.4's five named metrics are still not exposed**, and the
> reason is the same for both. *Transition latency* and *driver failure rate*
> are properties of work the **worker** performs, and the worker has no scrape
> surface. The two signals it measures reach a scrape through
> `duty_measurement` because they are periodic readings with somewhere natural
> to be written; a latency histogram and a failure counter are distributions,
> and a table holding the latest value would lose the shape that makes them
> worth having. Giving the worker its own endpoint needs a port, a binding
> decision against SAD 8.1's loopback rule, and a second scrape target — which
> is a piece of work, not a line. `metrics.UNEXPOSED` names both so the gap is
> in the code rather than only here.
>
> SAD 11.3 has no row for the control plane's own request path, so an operator
> asking "is the API slow, and which route" had no signal to read — a question
> with an acceptance criterion behind it, since AC-N4 requires a 500-run list
> under 300 ms at the 95th percentile. Written up in
> `docs/fixes/proposed-sad-amendments.md` as a ninth row rather than quietly
> mapped onto a row that means something else.

---

### RF-19 — P5 — Eleven coverage targets collect nothing

`tasks.py:712–719` passes `--cov=draupnir/api/concurrency.py`, `context.py`,
`events.py`, `guards.py`, `idempotency.py`, `pagination.py` and `telemetry.py`;
`tasks.py:747–751` passes `--cov=draupnir/api/app.py`, `deps.py`, `problems.py`
and `schemas.py`. Coverage treats a `--cov` argument as a module or package
name, not a file path, so all eleven silently measure nothing.

Reproduce — the contract run prints it directly:

```
CoverageWarning: Module draupnir/api/app.py was never imported. (module-not-imported)
```

and the unit run's `coverage.xml` contains 47 files, none of them the seven
named. The comment at `tasks.py:709` says the edge's pure mechanisms "are unit
testable and are tested here"; they are tested, and the 90% floor is computed
without them. The floor is therefore lower than it reads.

**Prompt**

> Fix the coverage targets in `tasks.py`.
>
> Replace every `--cov=<path/to/file.py>` with the dotted module form
> (`--cov=draupnir.api.idempotency`), and add a guard: after the run, parse
> `coverage.xml` and fail if any explicitly named target is absent from the
> report. A coverage target that measures nothing is worse than an absent one,
> because the percentage still prints.
>
> Re-check the resulting figure against the 90% and 85% floors, and raise the
> floors to the achieved level rather than lowering them to fit.

**Acceptance criteria**

- `tests/unit/test_distribution.py`, or the nearest existing meta-test, asserts
  every `--cov` argument in `tasks.py` names an importable module.
- The unit run's `coverage.xml` contains all seven `draupnir.api` mechanism
  modules; the contract run's contains all four.
- Both stages still pass at or above their floors, with the floors updated.
- Gated in stages 2.1 and 2.3.

> **Status: done.**
>
> **Dotted module names, and a guard that they measured something.** The
> targets are declared once, in `UNIT_COVERAGE`, `CONTRACT_COVERAGE` and
> `INTEGRATION_COVERAGE`; `coverage_flags` builds the `--cov` arguments from
> them and `verify_coverage` checks the report against the same tuple. That
> arrangement is the point rather than tidiness — a list of flags and a list of
> things to verify that could drift apart is the defect one rename away from
> returning, and a test asserts they are still built from one source.
>
> **Verified against the report, not against the warning.** Coverage does say
> `module-not-imported`, on standard error, in a run that then prints a
> percentage and exits zero. That is a thing a pipeline scrolls past, and it
> did, for eleven targets. The guard parses the XML and names what is missing.
>
> **The floors are the achieved figures.** Unit 90 to **91** (91.16 measured),
> contract 85 to **87** (87.16), integration 80 to **81** (81.25). Raised to
> what is reached rather than fitted to it, which is what the register asked
> for — and the numbers moved *up* when the broken targets were fixed, which is
> the counter-intuitive part worth stating: a target measuring nothing shrinks
> the denominator, so the percentage a broken target produces is higher than
> the truth, not lower.
>
> **The integration stage was included too**, though its targets were
> directories and already worked. A directory can go stale as easily as a path
> can be wrong, the guard costs one XML parse, and a stage exempt from a check
> is where the next instance of the defect will be.
>
> **Three reports rather than one.** Each stage writes its own, for the reason
> each already has its own data file: two stages writing one file contend for
> it, and on Windows the loser reports a corrupt database rather than a
> coverage failure. `coverage.xml` keeps the name the pipeline already
> collects; the other two are added to the evidence upload and to
> `.gitignore`.
>
> **The file is read as well as the lists.** `test_no_stage_passes_a_coverage_path_anywhere_in_the_file`
> greps `tasks.py` for a `--cov=` argument that looks like a path, so a new
> stage written inline — bypassing the lists entirely — brings the defect back
> loudly rather than quietly.
>
> AC-N8 claimed "90 per cent statement coverage on the core". The figure was
> real but the denominator was not what the criterion described: the edge's
> pure mechanisms were named as targets and excluded from the measurement. It
> now states all three stages' figures and says that a target measuring nothing
> is refused.

---

### RF-20 — P5 — HODD and GLEIPNIR are under no coverage floor

`test_unit` names `core/domain` and eight feature modules; `test_contract` names
the routers; `test_integration` names `core/application`, `core/infrastructure`
and `procedures`. `draupnir/hodd/` and `draupnir/gleipnir/` appear in none of
them, and neither does `draupnir/interfaces/` or `draupnir/worker/`.

They have unit tests — six files for HODD, three for GLEIPNIR — but no measured
floor, so coverage can fall to zero in the two modules that hold the licence
register, the retention rule, the policy gate and the release sign-off without
any stage noticing.

`draupnir/core/infrastructure/database.py` is reported at **0%** by the
integration stage, which does name it.

**Prompt**

> Bring every shipped package under a coverage floor.
>
> Add `draupnir/hodd`, `draupnir/gleipnir`, `draupnir/interfaces` and
> `draupnir/worker` to the appropriate stage's `--cov` list, at the floor their
> current coverage supports, and add a check that fails the build if any package
> under `draupnir/` is named by no stage. The set of measured packages should be
> derived from the tree rather than listed by hand, so a new module arrives
> measured.
>
> Cover `draupnir/core/infrastructure/database.py`, currently at 0%, or state in
> the module docstring why the engine factory is not exercisable and add it to an
> explicit exclusion list that the check reads.

**Acceptance criteria**

- A meta-test enumerates packages under `draupnir/` and asserts each is either
  in a stage's `--cov` list or in the documented exclusion list.
- Every stage passes at its floor with the additions in place.
- Gated in stages 2.1, 2.3 and 2.4.

> **Status: done.**
>
> **Every module the distribution ships is now under a floor**, and the set is
> derived from the tree rather than listed: `test_every_shipped_module_is_under_a_coverage_floor`
> walks `draupnir/**/*.py` and fails on anything no stage names. That check
> found four packages the register did not — `draupnir/api/assurance.py`, the
> seam where GLEIPNIR's gate definitions meet RAUN's execution, and
> `draupnir/core/plugins.py`, five hundred lines deciding which drivers this
> forge will run and verifying their signatures, plus two package docstrings.
> Which is the argument for deriving it: the register listed four packages from
> memory and the tree knew about six.
>
> **Placement follows where a thing is exercised**, which is the reasoning
> already in `tasks.py` for the API edge. HODD, GLEIPNIR and the driver
> interfaces go to the unit stage. The worker goes to integration: measuring it
> at the unit level would report the tick loop as uncovered while
> `test_worker_loop.py` drives a run from QUEUED to AWAITING_APPROVAL through
> every line of it.
>
> **`reading.py` was the real find.** It is the largest module in the edge and
> the contract stage measured it at **29 per cent** — not because it is
> untested, but because everything that exercises it drives a real API
> *subprocess*, and coverage cannot see inside a process it did not start. A
> module can be exercised on every request a forge serves and measured at zero.
> `tests/integration/test_read_model.py` calls `DatabaseReadModel` in process
> across every collection it serves, and `reading` and `writing` move to the
> stage that has a database.
>
> **`database.py` and `worker/__main__.py` were zero for the same reason.** One
> builds every engine in the process and the other *is* the worker as the unit
> file starts it. `tests/integration/test_entry_points.py` covers both, and
> `site_scoped_session` was worth testing rather than assuming: the whole of
> SAD 11C's isolation is that session variable, RF-18 found a query relying on
> the policy alone reporting two forges' rows added together, and there is now
> a test that the scope dies with its transaction — a pooled connection
> carrying one forge's scope to the next request is the failure that would be
> invisible in testing and unrecoverable in deployment.
>
> **Two floors read lower and the coverage is larger.** Unit 91 to 90,
> integration 81 to 77, contract unchanged at 87. The denominators changed, so
> the percentages are not comparable across this commit; the absolute figures
> are, and they went up in every stage — the unit stage from 4,788 statements
> covered to 6,661, the integration stage from 788 to 2,313. `tasks.py` records
> those numbers beside the floors, because a floor that drops in the commit
> after one that raised it needs the reason written where somebody will find
> it.
>
> **What stops a floor being lowered to fit a result is not a number.** It is
> `COVERAGE_EXCLUSIONS` and the tree check: the measured set cannot shrink
> without a decision recorded with its reason, and three further tests refuse
> an exclusion that is measured anyway, an exclusion naming a file that no
> longer exists, and a module measured by two stages at once.
>
> **Two modules are the weakest left and are not this finding's to fix.**
> `worker/stages.py` at 61.5 per cent and `core/application/orchestrator.py` at
> 59.2 per cent are the two largest gaps under the new floors. They are covered
> in the sense that matters — the integration suite drives real runs through
> both — and what is missing is the failure paths. Worth a finding of its own
> rather than a floor set high enough to force it.
>
> One note on the run itself. The full suite reported twenty-three failures in
> `tests/unit/test_worker.py` and `test_worker_planning.py` at one point, all
> of which passed in isolation and in fixed order. That run took 468 seconds
> against a usual 225 and the machine's disk failed minutes later; re-run on a
> healthy disk the same 2,145 tests pass. Recorded because a reader finding
> that in the history should know it was the hardware rather than a flake
> somebody decided to ignore.

---

### RF-21 — P5 — The breaking-change gate has no baseline

`python tasks.py openapi-diff` prints:

```
no baseline at …/docs/api/openapi.released.json; treating this build as the first release
```

`docs/api/` contains only `openapi.json`. The gate has never had anything to
compare against and cannot fail, so SAD 11E.2's "additive changes only within a
version" is unenforced.

**Prompt**

> Commit `docs/api/openapi.released.json` as the current released contract, and
> change `scripts/openapi_diff.py` so an absent baseline is a *failure* on the
> `main` branch rather than a pass. The first-release path should require an
> explicit `--first-release` flag, so "there is no baseline" is a deliberate
> statement rather than the default answer.
>
> Add the baseline's promotion to the release procedure in `docs/DEPLOYMENT.md`:
> the released document is updated when a version ships, not when a route
> changes.

**Acceptance criteria**

- A test asserts the gate fails when the baseline is absent and
  `--first-release` was not passed.
- A test plants a breaking change — a removed operation, a removed response
  field, a widened path parameter — and asserts each is caught.
- A test asserts an additive change passes.
- `docs/api/openapi.released.json` exists and matches the current document.
- Gated in stage 2.5.

> **Status: done.**
>
> **The baseline exists.** Nothing has been released — no tags, version 0.1.0 —
> so the current document *is* the contract to establish, and
> `docs/api/openapi.released.json` is committed as it.
>
> **A missing baseline is a failure now.** It was a pass, and the file had
> never existed, so the gate ran green on every build from the first commit
> without once having anything to compare against. That is worse than no gate:
> it appears in the pipeline, it appears in the evidence pack, and it reads as
> an assurance nobody was providing. `--first-release` makes the empty case a
> deliberate statement somebody types once, into a shell, on the build that
> establishes the contract.
>
> **Found while implementing: a parameter's schema was never compared.** The
> gate compared whether a parameter was *required* and nothing else — so the
> register's own example, a widened path parameter, went through unremarked
> along with a query parameter losing an accepted value. `_compare_schema` now
> runs over parameter schemas in the request direction, which brings the
> existing type and enum rules with it.
>
> **And that exposed a rule the gate did not have.** `run_id` is
> `{"type": "string", "format": "uuid"}`; widening it drops the *format*, not
> the type, so even with the schema comparison in place the planted change
> still passed. `format` is now judged in both directions, which is the one
> place this module is deliberately not asymmetric — and it is worth saying
> why. The server validates on it, so `/v1/runs/not-a-uuid` being a 422 rather
> than a request reaching a handler is a consequence of that keyword; and the
> typed client is generated from this document (AC-N10), so a console compiled
> against a `UUID` argument no longer matches a signature that says `string`.
> Dropping it widens what the server accepts, which for a request is
> ordinarily additive; it breaks somebody anyway, in the other direction.
>
> **The gate's own entry point had no tests.** Every existing test called
> `diff` directly, so `main` — the argument parsing, the file reading, the exit
> code a pipeline acts on — was exercised by nothing, which is precisely where
> the defect was. There are now tests for a missing baseline with and without
> the flag, and for a breaking and an additive change between two real files.
>
> **The promotion is a release step and `docs/DEPLOYMENT.md` says so.** The
> timing is the whole point and it is easy to get backwards: promoting on every
> change that touches a route makes the gate compare each build against itself,
> which passes unconditionally — the same nothing it was doing before, reached
> by a different route. The baseline is meant to lag, because it is what
> somebody's client was built against.
>
> The suite asserts the two documents this repository carries, and asserts the
> weaker of the two things it could: that the current document does not *break*
> the released one, rather than that they are equal. Equality would forbid the
> additive change the versioning policy exists to permit.
>
> AC-B5 said the gate "compares the exported document against the released
> baseline". It now does.

---

### RF-22 — P5 — The visual regression gate never gates in CI

`web/e2e/visual/storybook.spec.ts:82` writes a baseline when none exists for the
platform, annotates, and passes. Every one of the 220 committed baselines is
`…-visual-win32.png`; `.github/workflows/ci.yaml` runs on `ubuntu-24.04-arm`. So
on every CI run, every story has no baseline, 220 baselines are recorded into a
container that is then discarded, and the stage passes green having compared
nothing.

The rationale in the spec's header is sound — a missing baseline should not make
the first build on a new platform red through no fault of the change under test.
It just means the gate has never run where it matters.

**Prompt**

> Make the visual gate gate on the platform CI runs on.
>
> Generate and commit `linux` baselines for all 220 stories on the CI
> architecture — through a one-off workflow dispatch that uploads them as an
> artefact for a human to commit, not through a bot that commits to `main`.
>
> Then change the spec so that a missing baseline is a failure when
> `process.env.CI` is set, and a recorded baseline only on a developer machine.
> Keep the annotation naming the file to commit.

**Acceptance criteria**

- `web/e2e/visual/storybook.spec.ts-snapshots/` contains a `-linux.png` baseline
  for every story in `index.json`.
- A test asserts the spec fails rather than records when `CI` is set and a
  baseline is missing.
- A deliberate one-pixel change to a component fails the stage on CI — verified
  once by hand and recorded, since a gate nobody has watched fail is a gate
  nobody knows works.
- Gated in stage 2.9.

> **Status: done — the gate is fixed, the baselines are committed, and what it
> forgave is RF-47, also done.**
>
> **The spec can fail now.** A missing baseline is a failure when `CI` is set
> and a recorded file only on a developer machine, which is where the original
> reasoning holds: a designer adding a story should get a file to look at and
> commit, not a red run telling them to dispatch a workflow.
>
> **The decision is a function, and that is the point.** `web/e2e/visual/baseline.ts`
> holds `decide({ exists, ci, bootstrap })` because the defect was in the one
> part of the system a unit test cannot reach — a Playwright spec runs under
> Playwright or not at all, and `web/vitest.config.ts` excludes `e2e/**`. Five
> cases are asserted, including that a baseline which exists is never
> overwritten even while bootstrapping: a run that regenerated every baseline
> from the current code would be a gate approving whatever it was pointed at.
>
> **`bootstrap` is a separate signal from `ci`, deliberately.** If recording
> were what CI did when it found nothing to compare against, this finding would
> simply return in a new costume. It is set in exactly one place —
> `.github/workflows/visual-baselines.yaml` — which records the baselines on
> the pipeline's own architecture and uploads them as an artefact. **It commits
> nothing.** A workflow that pushed regenerated baselines to a branch would
> mean the first change altering a component's rendering also rewrote the
> evidence it was checked against, and every diff after that would be green.
>
> **Watched fail, and watched pass.** The 220 baselines were moved aside and
> the stage run with `CI=1`: it fails, naming the story, the platform, that the
> reader's change did not cause it, and the workflow that fixes it. Restored,
> the same run passes all eight shards against the committed baselines. Both
> halves matter — a gate that fails on everything is as useless as one that
> fails on nothing.
>
> **The `-linux` baselines, since committed.** A screenshot is only meaningful
> on the platform that will diff against it. One taken on a developer's machine
> and named `-linux` would be win32 pixels wearing another platform's name, and
> would fail every CI run for reasons unrelated to any change — which is how a
> gate gets switched off. They had to come from a run on `ubuntu-24.04-arm`,
> which is what the dispatch workflow is for.
>
> They now exist: 220 of them, recorded by that workflow on the runner,
> downloaded, compared story by story against their win32 counterparts, and
> committed. The artefact's win32 files were byte for byte the committed ones,
> so the recording path compared what already existed rather than rewriting it,
> which is the property the workflow claims and had never been checked. One
> story differs in height between the platforms — the spec editor's read-only
> view, 778px against 730px — which is text metrics wrapping a line
> differently, and is the whole reason a baseline belongs to one platform.
>
> Getting the workflow to run at all took one change: GitHub offers "Run
> workflow" only for workflows on the default branch, and this one was added on
> `dev` with `main` 42 commits behind, so the dispatch answered 404. A push to a
> `visual-baselines/**` branch now starts it, which runs the file that was
> pushed. It still commits nothing.
>
> **Stage 2.9 then passed on the runner for the first time in the project's
> life**, and the pipeline reached six steps it had never executed — one of
> which, stage 3.1a, failed immediately and is RF-48.
>
> The third acceptance criterion — a deliberate one-pixel change watched
> failing on CI — turned out not to depend on the baselines alone. With them
> committed, the one-pixel change was pushed and opened as a pull request, and
> stage 2.9 passed it: the comparison had a 1% pixel budget, which is 9,344
> pixels on one of these stories. That is RF-47. With the budget removed the
> same change failed stage 2.9 on CI, naming the two capacity gauge stories at
> 1,006 pixels each while 218 others matched exactly, so the criterion is met
> and its evidence is recorded under RF-47.
>
> **A completeness check that holds today.** `web/tests/visual-baseline-coverage.test.ts`
> asserts every platform with any baselines carries the *same set of stories*
> as every other. Not "some baselines exist": a platform with 219 of 220 is the
> failure that matters, because the ungated story is silently ungated while the
> stage stays green — RF-22 again in miniature. Platforms are compared against
> each other rather than against `index.json` because the index needs a built
> Storybook, which is gitignored, so a check that read it would only run where
> somebody had already built one.
>
> AC-Q5 said IMPLEMENTED and said 175 snapshots. There are 220, and the gate
> had never compared any of them. It stays DEVIATED with the reason carried
> forward to RF-47: the baselines have landed and the gate now compares on the
> platform that diffs them, but a one-pixel regression has not yet been watched
> failing, which is what the criterion asks for.

---

### RF-23 — P5 — `clients-check` fails on any CRLF checkout

`tasks.py:531` compares `path.read_bytes()` before and after regeneration.
`.gitattributes` is `* text=auto` and this machine has `core.autocrlf=true`, so
the checked-out generated files are CRLF and the generators write LF. The gate
fails every time on Windows:

```
These files do not match what the generator produces from the OpenAPI document:
  web/packages/api-client/src/generated/schema.d.ts
  web/packages/api-client/src/generated/operations.ts
```

`git diff --stat` on those files reports no content change. The same affects
`module-readmes`, which rewrote fifteen READMEs with no content difference.

The repository has already met this once — commit `a18f7c7`, "Let Prettier
accept the line endings Git checks out" — so this is the same class of bug in a
second place. README and `make.ps1` both present Windows as a supported
development path; `make static` and `make ci` cannot pass on it.

**Prompt**

> Make the drift gates line-ending agnostic.
>
> In `tasks.py`, compare generated files after normalising line endings — read as
> text with `newline=''` and compare with `\r\n` collapsed to `\n` — rather than
> comparing raw bytes. Do the same in `scripts/module_readmes.py`, which
> currently rewrites every README on a CRLF checkout.
>
> Alternatively, mark the generated paths `-text` in `.gitattributes` so Git
> checks them out with LF everywhere, and keep the byte comparison. Either is
> defensible; pick one and state which in a comment, because the next person will
> otherwise fix it the other way in a third place.

**Acceptance criteria**

- `python tasks.py clients-check` passes on a CRLF checkout with no changes
  applied, and still fails when a generated file is genuinely hand-edited.
- `python tasks.py module-readmes` leaves the working tree clean on a CRLF
  checkout.
- A test covers both: a normalise-only difference passes, a content difference
  fails.
- Gated in stage 3.2.

> **Status: done, and the gate was hiding something bigger.**
>
> **Both fixes, because they answer different questions.** The register offered
> them as alternatives and asked for one to be chosen and stated. Neither alone
> is enough: normalising only the comparison leaves two developers committing
> different bytes for the same document, and fixing only the generators leaves
> the gate wrong about a file an editor converted. So every generator writes
> `newline="\n"` explicitly, and `tasks.content_of` normalises before
> comparing. The reasoning is in `content_of`'s docstring, where the next
> person to meet this will be standing.
>
> **The root cause was on the write side, not the checkout side.**
> `Path.write_text` with no `newline` translates every newline to
> `os.linesep`, which on Windows is CRLF. Five of the nine generators passed
> `newline="\n"` and four did not — so the same document had different bytes
> depending on who ran the generator, while `git status` reported nothing,
> because the index normalises. One of the four writes a manifest that is then
> **signed**, where a byte difference is a verification failure with no
> explanation anywhere near it.
>
> **`clients-check` could not run at all, and had not since RF-01.** Exporting
> the document builds the application, and `create_app` refuses without a way
> to authenticate a caller — RF-01's refusal, and a correct one. But an export
> serves no request, so there is no caller to authenticate, and the refusal
> made `python tasks.py openapi` impossible to run anywhere the variable was
> not already set: on a developer machine, and in CI stages 2.5 and 3.2.
>
> **In that window both clients fell three operations behind.**
> `submitArray` and `requeueArrayElement` from RF-13, and `listCorpora`, were
> in the OpenAPI document and in neither generated client. AC-Q2 — "a drifted
> client fails the build" — was unenforced for the whole series, and the
> failure it exists to catch happened during it. The clients are regenerated
> in this commit.
>
> `render()` now builds in the development posture, which is safe rather than
> convenient: the paths, schemas and security schemes are identical either way,
> and a test exports the document under both and compares the bytes. If a
> change ever makes the contract depend on how a caller is authenticated, that
> test fails rather than the export quietly publishing one of two contracts.
>
> **`module-readmes` rewrote all fifteen on every run.** It already wrote LF
> and already read with universal newlines, so its check was sound; what it did
> not do was compare before writing. On a CRLF checkout that left fifteen
> modified files behind with no content change in any of them — `make static`
> dirtying the tree it was there to check. It now writes only what differs, and
> two consecutive runs leave nothing.
>
> **The tests are structural where that is the only thing that works.**
> `test_every_generator_writes_lf_on_every_platform` parses each script and
> looks for a `write_text` with no `newline`, over a list derived from the
> `scripts/` directory, so a tenth generator arrives checked. And
> `test_the_platform_actually_translates_so_this_matters` asserts the premise:
> on Linux `os.linesep` is `\n` and this whole class of bug is invisible, which
> is exactly why it survived — the pipeline runs on Linux and never saw it.

---

### RF-24 — P5 — `tasks.py ci` is not the pipeline

The README's central claim about the build is: "`tasks.py` is the single entry
point: the pipeline runs the same commands a developer does, so a stage that
passes locally and fails in CI is a bug in the task rather than a difference
between two scripts."

`tasks.py:1071 ci()` runs twelve tasks. `.github/workflows/ci.yaml` runs
seventeen steps. `ci()` omits `audit`, `sbom`, `acceptance` and the image build,
and has no equivalent of stage 3.4's artefact signing. A developer who runs
`make ci` and gets green can still be red on `audit`, `sbom` or `acceptance`.

**Prompt**

> Make `tasks.py ci` the pipeline.
>
> Add `audit`, `sbom`, `acceptance`, `images` and an artefact-signing step to
> `ci()`, in the workflow's order, so the local command and the workflow run the
> same list. Where a step cannot run locally — signing needs the key — it should
> report itself as skipped with the reason, in the same shape the workflow's
> stage 3.4 already uses.
>
> Then add a test that reads `.github/workflows/ci.yaml`, extracts the `python
> tasks.py <task>` invocations, and asserts the set equals what `ci()` calls. The
> claim in the README should be enforced rather than asserted, which is the
> standard the rest of this repository holds itself to.

**Acceptance criteria**

- `tests/unit/test_documentation.py` asserts the workflow's task set and `ci()`'s
  task set are equal.
- `python tasks.py ci` runs every stage the workflow runs.
- Gated in stage 2.1.

> **Status: done.**
>
> **The pipeline is a list now.** `PIPELINE` in `tasks.py` names twenty-five
> stages in the workflow's order; `ci()` dispatches over it, and the test reads
> the same list. Written out as calls, the two agreed until somebody added a
> stage to one of them — which is exactly what had happened. `ci()` ran twelve
> of the workflow's twenty-one task invocations.
>
> **What `ci()` was missing:** `acceptance`, `egress-policy`, `openapi` and
> `con-a`. The register also named `audit` and `sbom`, and those were already
> there — `static()` gained them at some point, so the register's list was one
> item stale in that direction. Worth saying, because it is the same class of
> drift the finding is about.
>
> **What the workflow was missing was more interesting.**
> `crypto-inventory` ran in `make ci` and nowhere in the pipeline — and AC-S16
> calls the cryptographic inventory *a build artefact*. The pipeline's evidence
> upload collects the `sbom/` directory, which had never contained one. A
> criterion satisfied on developer machines and nowhere else.
>
> **Signing was two steps doing one job by hand.** Stages 3.4a and 3.4 each
> invoked `scripts.sign_artefacts` directly, with different arguments, neither
> through a task — which is the second kind of drift this finding is about, and
> the more insidious one: a task and a workflow step that do the same thing
> differently. They are one step now, calling `tasks.py sign`, and the
> main-branch enforcement stays where it belongs, in the workflow, because a
> missing key is a property of *where the build is running* rather than of the
> signing.
>
> **A difference may be deliberate, and `LOCAL_ONLY` is where it says so.**
> `images` differs because the workflow tags for the registry, pushes on main
> and uses a GitHub Actions cache, none of which belongs in a command a
> developer runs — the *build* is the same and is what `make ci` checks.
> `build-web` differs because `docker/web.Dockerfile` compiles the console
> inside the image, so the workflow already builds it and doing it again first
> would be the same work twice; a developer has no image build, so `make ci`
> does it directly. A test asserts each entry has a reason of more than a
> handful of words, because an exemption without an argument is just a list of
> things somebody stopped checking.
>
> **`sign` is deliberately not in that map**, and the distinction is the point:
> the workflow invokes it, and the *task* reports itself skipped without a key.
> A stage that behaves differently in two places is a difference. A stage that
> says so is a task.
>
> The order is asserted as well as the set. Running the acceptance pack before
> the tests that produce its citations, or the client check before the document
> it regenerates from, would pass a set comparison and fail in ways that read
> as flakiness.

---

### RF-25 — P5 — The secret scan cannot run on the documented Windows path

`python tasks.py secrets` — and therefore `static` and `ci` — fails here:

```
docker: Error response from daemon: error while creating mount source path
'/run/desktop/mnt/host/d/repos/veldris/draupnir': mkdir /run/desktop/mnt/host/d: file exists
```

This is a Docker Desktop drive-sharing problem on this machine rather than a
defect in the repository, and it does not affect CI, where gitleaks runs on
Linux. It is recorded because it stops `make static` completing on a documented
development platform, and because the task has no fallback.

**Prompt**

> Make `tasks.py secrets` degrade usefully.
>
> Prefer a locally installed `gitleaks` binary when one is on `PATH`; fall back
> to the pinned container; and when the container cannot mount the working tree,
> report the mount failure and the two ways to fix it — enable file sharing for
> the drive in Docker Desktop, or install gitleaks locally — rather than a bare
> exit code 125. Never fall through to a pass: an unrunnable secret scan is a
> failure and should stay one.
>
> Add gitleaks to `tasks.py bootstrap` so a developer machine acquires it once.

**Acceptance criteria**

- A test asserts the task prefers a local binary when present.
- A test asserts a mount failure produces a message naming both remedies and a
  non-zero exit.
- `docs/CONTRIBUTING.md` records the Docker Desktop drive-sharing requirement.
- Gated in stage 1.4.

> **Status: done.**
>
> **Half the prompt was already true.** The task has preferred a local
> `gitleaks` over the container since before this register was written, so what
> was missing was the diagnosis, the bootstrap step and the documentation. Said
> plainly because the register asked for all four and a reader comparing the
> two should not have to work out which.
>
> **The mount failure now explains itself.** `mount_remedy` recognises Docker
> Desktop's message — matched on the parts that are not the developer's own
> path — and answers with both remedies: share the drive in Docker Desktop's
> file-sharing settings, or install the binary and remove the container from
> the path entirely. Both, because which is available depends on the machine:
> a managed laptop may refuse either one.
>
> It also says, first, that this is not a finding. The reader's first thought
> on a red secret scan is that it found a secret; it did not, it could not
> start, and saying so is the difference between checking a setting and
> searching your own diff.
>
> **The rule that keeps that honest** is the test asserting a real finding is
> never given the mount advice. Drive-sharing guidance shown for a leaked key
> would be the worst outcome available here: the one failure this stage exists
> to surface, explained away as configuration.
>
> **It never passes.** Neither binary nor Docker is a `Failure`, not a skip —
> AC-Q3 asks for a scan over the working tree and the whole history, and one
> that could not start has found nothing in the way an empty room has found
> nothing. A green stage there is the shape of an assurance without the
> substance, and it appears on exactly the machines least likely to be watched.
>
> **`bootstrap` acquires it**, through `winget`, `scoop` or `brew`, and is
> never fatal about it: a machine with none of those still has the container,
> and a bootstrap that refused to finish over a tool with a working fallback
> would be worse than the problem. It says what it did either way.
>
> **The arguments are shared between the two paths**, which is a small thing
> with a nasty failure behind it. A fallback that scanned differently would
> only ever run on the machines without the binary, so the divergence would
> present as one developer's clean scan and another's finding on the same
> commit.
>
> The symptom itself did not reproduce here: the container mounts this working
> tree and the scan completes. Recorded rather than glossed, because the fix is
> warranted anyway — the mechanism is real on Windows, README and `make.ps1`
> both present it as a supported path, and a task whose only failure mode is
> `exit 125` is a task nobody can act on whether or not it fires today.
>
> **It reproduced later, and the paragraph above is no longer true.** During
> RF-27, after drive D: dropped out of Windows and came back, Docker Desktop
> refused every bind mount from this checkout with exactly the message this
> finding quotes — `mkdir /run/desktop/mnt/host/d: file exists` — and the
> development stack could not start. Restarting Docker Desktop cleared it. So
> the mechanism is real on this machine, triggered by the drive rather than by
> any setting, and the remedy `mount_remedy` gives does not mention the one that
> worked. That is worth adding to the message; it is recorded here rather than
> changed under RF-27.

---

### RF-26 — P5 — Two frontend advisories sit below the audit threshold

`python tasks.py audit` runs `pnpm audit --audit-level high` and reports:

```
2 vulnerabilities found. Severity: 2 moderate
```

They are `yaml` (stack overflow via deeply nested collections, `>=2.0.0
<2.8.3`) and `uuid` (missing buffer bounds check in v3/v5/v6, `<11.1.1`), both
transitive build-time dependencies. Neither is in the shipped console bundle,
which is why `--audit-level high` is a defensible threshold — but the finding
should be a recorded acceptance rather than an invisible one.

**Prompt**

> Resolve or record the two moderate frontend advisories. Bump `yaml` to
> `>=2.8.3` and `uuid` to `>=11.1.1` through pnpm overrides if the tree allows;
> if it does not, add an explicit allow-list entry to the audit task naming each
> advisory, why it does not reach the shipped bundle, and when the exception
> expires. An advisory below a threshold is invisible; an advisory on an expiring
> allow-list is a decision.

**Acceptance criteria**

- `pnpm audit --audit-level moderate` is clean, or the two advisories are on an
  allow-list with a stated reason and an expiry date that the task enforces.
- A test asserts an expired allow-list entry fails the build.
- Gated in stage 1.5a.

> **Status: done.**
>
> **There were three, and the third had already turned the stage red.** By the
> time this was worked, `pnpm audit` also reported GHSA-2883-xcg3-v3hh against
> `js-yaml` 4.3.1 — severity *high*, reached through `openapi-typescript` →
> `@redocly/openapi-core`, fixed in 4.3.2. So stage 1.5a was failing at the
> `high` threshold it had, not merely quiet below it. The existing `js-yaml`
> override (`^4.1.1`) was a fix for an earlier advisory, and was satisfied by
> the vulnerable version.
>
> **All three are fixed, none accepted.** Through `pnpm.overrides`:
> `js-yaml` to `^4.3.2`; `yaml` to `^2.8.3`; and `uuid` to `^11.1.1` scoped to
> `@storybook/addon-actions>uuid`, because that one crosses a major version
> (the parent asks for `^9.0.0`) and should reach nobody who did not need it.
> addon-actions calls only `v4`, through the named export and through
> `require('uuid').v4`, both of which uuid 11 keeps; the Storybook build and the
> 995 frontend tests pass on it. `pnpm audit` now reports nothing at any
> severity, low included.
>
> One of those did not do what it says. `yaml` was never a real dependency
> here: it was vite's *optional* peer, installed automatically, which vite
> loads only to read a YAML-format PostCSS config. Under the override pnpm no
> longer installs it at all, rather than installing 2.8.3. Nothing in the
> workspace imports `yaml` and there is no PostCSS config of any format, so
> nothing is lost; and should somebody add one, vite will ask for `yaml` and the
> override will hold it above the vulnerable range.
>
> **The threshold is now moderate, and nothing below it goes unmentioned.**
> The task reads `pnpm audit --json` instead of trusting its exit code, which
> only knows the `--audit-level` line. Every advisory is either a failure or a
> printed line — `below the moderate threshold: …` or `accepted until …
> because …` — so a low-severity advisory is visible in the log rather than
> absent from it.
>
> **The allow-list exists, is empty, and bites.** `AUDIT_EXCEPTIONS` in
> `tasks.py` takes the GHSA identifier, the package, the reason and an expiry.
> The build fails on an expired entry (the criterion, tested); on an expiry
> more than 90 days out, because an exception written to expire in 2099
> satisfies an expiry rule and defeats it; on an entry with no reason; and on an
> entry whose advisory the audit no longer reports, because exceptions that
> outlive their advisories accumulate into a list nobody reads. Entries match on
> advisory *and* package, so a slip excuses nothing rather than something else.
> Empty, because nothing needed accepting — the mechanism is for the next
> advisory whose fix a parent package has not yet shipped, so that it has
> somewhere to go other than the threshold.
>
> **An audit that produced no report fails** — the registry unreachable, say.
> It has not found anything.
>
> The Python half needed nothing: `pip-audit --strict` has no severity
> threshold and fails on any known vulnerability, so there was no invisible
> band on that side. Stage 1.5a still calls `python tasks.py audit`, unchanged.

---

### RF-27 — P6 — Nine screens have a primary action with no API operation behind it

`docs/build/draupnir-ux.md:314–344` gives every screen a primary action. The
OpenAPI document has 34 operations. Cross-referencing them:

| Screen | Primary action in the UX inventory | Operation |
|---|---|---|
| S05 Curation run | Re-run a stage | none |
| S06 Retention schedule | Approve a retention action | none (`GET /v1/retention` only) |
| S12 Array monitor | Requeue a single element | none (see RF-13) |
| S15 Sweep comparison | Select a merge point | none (`GET /v1/sweeps/{run_id}` only) |
| S17 Release package | Download the package | none |
| S22 Sites | Register a site | none (`GET /v1/sites` only) |
| S23 Plug-ins | Enable or disable | none |
| S24 Policy | Publish a policy version | none |
| S25 Users and roles | Assign a role | none |

The console does not offer these controls either — `grep -i "register a
site\|assign a role\|publish a policy\|enable or disable"` over
`web/apps/console/src/screens/` returns nothing — so the screens are consistent
with the API and inconsistent with the UX specification. Nine screens are
read-only views of a surface the specification says should act.

This is not contradicted by SAD 8.1, which lists thirteen operation groups and
is fully implemented. The gap is between the UX inventory and the API surface.

**Prompt**

> Reconcile the UX screen inventory with the API surface.
>
> For each of the nine rows above, decide and record one of two things: build the
> operation and its console control, or amend `docs/build/draupnir-ux.md` to
> state that the screen is read-only in this release and why. Do not leave a
> screen whose specification names an action it cannot perform.
>
> Where an operation is built, it takes the same nine conventions of SAD 11E.2 as
> every other mutating route — `Idempotency-Key`, `If-Match`, a role
> declaration, a problem-document error path — and the console control takes the
> same seven states as every other JARNGREIPR composite.
>
> Then add a test that reads the screen inventory from `draupnir-ux.md` and
> asserts every named primary action maps to an operation in `openapi.json` or to
> a recorded read-only exemption, so the two documents cannot drift again.

**Acceptance criteria**

- The inventory-to-operation test exists and passes.
- Each newly built operation has a contract test asserting all nine conventions.
- Each newly built control has a Playwright test performing the action.
- Each read-only exemption is stated in `draupnir-ux.md` with a reason.
- Gated in stages 2.3 and 2.7.

> **Status: done, in four commits, with one criterion met by a proposal
> rather than by editing the specification.**
>
> **Five screens were not what the finding said.** Four of the nine rows hid a
> defect behind the missing control, and one was already half built:
>
> - **S06**'s "Approve deletion" button closed its dialog and did nothing.
>   `listRetention` read `retention_action`, which nothing wrote, and
>   `hodd.retention.execute` was called by unit tests alone — while AC-F19 and
>   AC-F20 were marked IMPLEMENTED.
> - **S15** compared five points `getSweep` invented by scaling one run's gate
>   values, and called the first that passed "selected". The worker built a
>   five point sweep, merged once, and recorded how many points it had — while
>   AC-F8 was marked IMPLEMENTED.
> - **S17**'s five addresses pointed at documents nothing generated or stored.
> - **S12**'s operation existed from RF-13; the screen had no control for it,
>   and the worker would requeue a *completed* element, training it again.
> - **S05** cannot re-run a stage for a reason that is not a missing operation:
>   a curated corpus is sealed at its address, so re-curation is refused by the
>   store.
>
> **Built: S06, S12, S15, S17.**
>
> - `approveRetention` records the decision and deletes nothing. The retention
>   duty carries approved actions out through `hodd.retention.carry_out`,
>   asking the store whether the curated manifest is held and refusing,
>   naming the releases, where it is not (AC-F20). Retention is folded from
>   the chain; `retention_action` stays unwritten, recorded as a deviation.
> - S12's control is offered only on an element that stopped without
>   completing, and the worker refuses the rest.
> - The worker merges and re-gates every sweep point — the mergekit driver now
>   renders each point's weight, which it never did — records the sweep, and
>   waits at MERGED. `selectMergePoint` chooses among the points RAUN passed;
>   the worker quantises that point's verified bytes, and the model card
>   carries the whole comparison. Procedure M7 stands in for the operator and
>   says so on the record.
> - `downloadReleaseDocument` serves the five package documents, generated by
>   SKIDBLADNIR and GLEIPNIR and dated by the release, so the same download is
>   the same bytes.
>
> Every new conditional operation computes its entity tag over state that
> changes, and each has a contract test for every convention of SAD 11E.2.
> Deletion approval needs a hardware authenticator; `APPROVE_RETENTION` and
> `SELECT_MERGE_POINT` are permissions §9.4 did not have. Each control has a
> Playwright journey that performs the action.
>
> **Read only: S05, S22, S23, S24, S25**, each with a reason: curation output
> is sealed; sites are registered in MEGINGJORD at commissioning; plug-ins are
> installed signed at deployment; policy versions are published by MEGINGJORD;
> roles come from the identity provider.
>
> **The criterion met differently.** "Each read-only exemption is stated in
> `draupnir-ux.md`" would mean editing VLD-UX-DRAUPNIR-001, which is issued for
> build under its owner's name, like the SAD. The exemptions and proposed rows
> are in `docs/fixes/proposed-ux-amendments.md`; the permissions, the retention
> deviation, the new operations and MERGED's wait are amendments 6 to 9 in
> `proposed-sad-amendments.md`. `docs/api/screen-actions.json` records what
> performs every row of section 8, and `tests/unit/test_screen_inventory.py`
> fails if a screen, its action text or an operation drifts — and fails until
> the register follows once the owner applies the amended rows.
>
> **Found on the way, and recorded as RF-32 to RF-36** rather than fixed here:
> conditional writes whose entity tag cannot go stale, and a console that sends
> none, so it cannot cancel, retry, decide or publish; a release record only the
> seed writes; releases that do not record their licence policy version; a
> console default specification its own API refuses, which has kept J2 red since
> RF-11; and a journey stack that does not route `/auth`. Also corrected: the
> Tier-B-for-GBR journey failures and the sign-in failure are not RF-27's, and
> RF-25's claim that its symptom did not reproduce is no longer true.
>
> **Two commits in this series were made without the integration suite**,
> because Docker Desktop could not mount D:. The S12 guard broke an integration
> test and a seed variable failed mypy; both were fixed in the S06 commit and
> said so there.
>
> Python: 2,087 unit, property and contract tests and 221 integration tests
> pass. Frontend: 995. Journeys: 43 pass; the three that fail are RF-35 and
> RF-36.
>
> **Amended after RF-45.** Re-checked once RF-32 to RF-45 had closed. The
> four screens built and the five read only are as recorded: nothing since
> added an operation for S05, S22, S23, S24 or S25, and
> `tests/unit/test_screen_inventory.py` still holds `draupnir-ux.md` to
> `docs/api/screen-actions.json`. One screen outside the nine changed: S13's
> approval, which could not succeed until RF-40. The three journeys this
> block records as failing were closed by RF-35 and RF-36, and the journeys
> now pass 53 of 53.

---

### RF-28 — P6 — CON-B reports neither thermal nor fabric bandwidth

SAD 11.3 surfaces "appliance thermal and throttle" on CON-B dashboard 1 and the
"fabric bandwidth probe" on dashboard 2.

`web/apps/console/src/screens/Kiosk.tsx:104 Thermal` derives its tiles from
`listRuns`, showing "under load" or "idle" per node. There is no temperature, no
throttle state and no DCGM integration anywhere in the tree, and no API
operation carries either. `Kiosk.tsx:138 Fabric` shows the federation anchor
state and a sentence about the bandwidth probe; it shows no bandwidth.

The empty-state copy is careful and correct — "with nothing placed there is
nothing to report, which is not the same as everything being cool" — but the
populated state is not what the dashboard is named for. The reconciliation marks
11.3 DEVIATED for the probe alone; the thermal signal is not mentioned.

**Prompt**

> Carry the two CON-B signals to the panel.
>
> Add an operation exposing appliance telemetry — temperature, throttle reason,
> utilisation — read from the DCGM exporter on each appliance through the egress
> allow-list, and the fabric probe's last reading with its baseline comparison.
> Render both on CON-B dashboards 1 and 2.
>
> Where the estate cannot supply a reading, say **unmeasured** and say why, in
> words, on the panel — never a zero and never a green tile. A panel whose
> numbers stopped is worse than a blank one, which is the principle CON-B's own
> staleness banner is built on; the same principle applies to a signal that never
> started.
>
> Update `docs/acceptance/imhotep-reconciliation.md` §11.3 to mark the thermal
> signal alongside the probe.

**Acceptance criteria**

- A Playwright test asserts dashboard 1 renders a temperature and a throttle
  state when the operation returns them, and the word "unmeasured" with a reason
  when it does not.
- The same for dashboard 2's bandwidth against its baseline.
- An a11y test covers both populated states.
- The reconciliation states the thermal deviation.
- Gated in stages 2.7 and 2.8.

> **Status: done. The thermal half was already delivered by RF-E15; the fabric
> half could not have shown a number on any estate, for two reasons.**
>
> **Thermal.** RF-E15 added `GET /v1/estate/telemetry`, read from the DCGM
> exporter through Prometheus behind the egress allow-list, and dashboard 1
> renders a temperature and a throttle state, or *unmeasured* and the reason.
> Its journeys meet the first criterion as they stand, and are left alone.
>
> **Fabric: the reading had no source.** The estate read asked Prometheus for
> the probe's bandwidth, and nothing exported it: the worker recorded the probe
> on the chain, and `/metrics` did not carry it. `/metrics` now exposes
> `draupnir_fabric_bus_bandwidth_gbps` and `draupnir_fabric_baseline_gbps` from
> the probe's last recorded measurement. Each is omitted rather than zero when
> there is nothing to report, and a baseline of zero, which is the installer's
> "not commissioned", counts as none. The estate read carries the baseline
> beside the bandwidth.
>
> **Fabric: the baseline never reached the worker.** `install.sh` writes
> `DRAUPNIR_FABRIC_BASELINE_GBPS`, beside the other probe settings. The worker
> read `DRAUPNIR_WORKER_FABRIC_BASELINE_GBPS`, as did the runbook, so an
> installed site's baseline was always zero and the 80 per cent alarm could
> never be judged. The worker reads the installer's name and still honours the
> old one; a unit test reads the installer's text so the two cannot drift again.
>
> **Also fixed on the way:** the collector dropped any sample without an
> `instance` label. That is right for a per-appliance reading, but wrong for an
> estate-wide gauge arriving through a recording rule or a federated scrape.
>
> **Dashboard 2** shows the bandwidth, the commissioned baseline, and the
> reading as a share of it. Below the floor it says "below the 80 per cent
> floor" in words as well as tone. With a reading but no baseline, it says the
> alarm cannot be judged and why. `/kiosk?dashboard=fabric` holds one dashboard
> instead of rotating, so an operator can link to it and a scan or journey need
> not wait out the rotation.
>
> **Criteria.**
>
> - Dashboard 2 has four new journeys:
>   - against baseline;
>   - below the floor;
>   - with no baseline;
>   - a linked dashboard holding through six rotation periods.
> - Both populated dashboards have an axe scan.
> - The reconciliation's §11.3 row states both deviations, thermal and fabric.
>
> **What stays deviated**, and says so on the panel: the probe runs only where
> `all_reduce_perf` is configured, and the reading reaches Prometheus only once
> ALVISS is a scrape target (RF-E15's fabric half). Until then dashboard 2 reads
> unmeasured, with the reason.
>
> Python: 2,100 unit, property and contract tests, and 221
> integration tests, pass. CON-B's journeys and scans: 12 of 12. The web
> typecheck, lint and formatting are clean.
>
> **Amended after RF-45.** Re-checked once RF-32 to RF-45 had closed: nothing
> since changed CON-B, the fabric metrics or the baseline setting. RF-43 added
> a worker setting of its own, `DRAUPNIR_POLICY_DRIVER`, which `install.sh`
> writes and a unit test holds to the installer's text, the way this finding
> holds the fabric baseline.

---

### RF-29 — P6 — The open keyboard finding K-1 is still open

`docs/acceptance/keyboard-pass.md:75` records K-1 as **Open**: an unavailable
control uses `disabled`, which removes it from the tab ring, so a keyboard or
screen-reader user never receives the explanation `Button` already carries in its
`title` and visually-hidden span. It affects `/corpora/register`'s wizard
navigation and `/gates`'s decision controls — the latter being the screen where
knowing *why* a control is unavailable matters most.

The record names the fix (`aria-disabled="true"` without `disabled`) and states
why it was deferred: `components.test.tsx:327` asserts the current behaviour
across every component, and two journey specs assert `toBeDisabled()`. That is a
real cost, and it is the reason to do it deliberately rather than never.

**Prompt**

> Close K-1.
>
> In JARNGREIPR, change every acting control in the `readOnly`, `denied`, `error`
> and `partitioned` states to render `aria-disabled="true"` with a no-op handler,
> rather than `disabled`, so the control stays focusable and its existing
> explanation reaches assistive technology. Keep the visual treatment and keep the
> handler inert.
>
> Update `components.test.tsx`'s "disables every acting control in the %s state"
> assertion to check `aria-disabled` and an inert handler, and update the two
> journey specs asserting `toBeDisabled()`. Re-run the keyboard pass and update
> `docs/acceptance/keyboard-pass.md` with the new result.

**Acceptance criteria**

- Every acting control in a replacing state is focusable and announces its
  unavailability and its reason.
- Activating it changes nothing — asserted, not assumed.
- The keyboard walk on `/corpora/register` and `/gates` reaches the wizard
  navigation and the decision controls.
- `keyboard-pass.md` records K-1 as closed, with the evidence.
- Gated in stages 2.6 and 2.8.

> **Status: done.**
>
> **The change.** Every acting control in JARNGREIPR — Button, Toggle, the
> tag's remove button, tabs, text input, text area, select, combobox, checkbox
> and radio — is `aria-disabled="true"`, never `disabled`, in any state but
> `ready`. That is wider than the four states the prompt names: `isInert`
> treats all six the same, and a loading or empty control has the same dead
> end. The explanation reaches assistive technology through the title and
> hidden text Button already had, and through the field description for the
> inputs. The styles select on the ARIA state, so the look is unchanged:
> stage 2.9 compared every Storybook story against its baseline and all eight
> shards passed.
>
> **Keeping it inert, asserted.** The browser no longer refuses activation, so
> the component does:
> - a click is cancelled, which also stops a submit button submitting;
> - a select refuses the pointer and every key but Tab;
> - text fields are read-only;
> - every change handler returns without acting.
>
> A new block in `components.test.tsx` reaches each control by Tab in all six
> non-ready states. It tries a click, Enter, Space, typing, arrow keys and a
> direct change event, and asserts that no handler ran, no value changed and no
> form submitted.
>
> That test found a defect in the first version. Cancelling a checkbox's click
> as well as holding its controlled value let the browser restore a tick React
> had already cleared, because React drives a checkbox's change from its click.
> Checkbox and radio are now held by the controlled value alone.
>
> **The two journey specs.** J1 asserts `aria-disabled` rather than
> `toBeDisabled`, and presses Enter and Space on the unavailable Continue to
> show the wizard does not advance. J3 asserts the decision control is
> available by the same attribute. Three component-test assertions were
> tightened for the same reason: `toBeEnabled` reads only the attribute no
> control now sets, so it would have passed for an unavailable one.
>
> **The keyboard walk**, re-run:
> - It reaches Back and Continue on `/corpora/register`, both unavailable and
>   each giving its reason.
> - It now walks the approval screen, `/gates/:id`, which the first pass never
>   did, and reaches Sign and approve and Reject. The gate evidence is on
>   screen when that page loads, so those two were available when reached.
>   The unavailable case is shown by the wizard and the component tests.
> - It fails if either screen stops reaching those controls, or if an
>   unavailable stop does not say why.
> - Unavailable stops meet the same named and visible-focus checks as any
>   other.
> - It names a stop by its content before its title, as the accessible name
>   computation does. The old order named an unavailable button by its reason,
>   so the record could not say which control it was.
>
> `keyboard-pass.md` records K-1 as closed with this evidence, keeping the
> original finding. The reconciliation no longer says a finding is open.
>
> **Found on the way, and fixed** because it was mine and one line: RF-27's S06
> journey, on any run after the first against the same database, asserted the
> word "approver", which the retention table never renders. It now asserts the
> row no longer says "not approved" and that the action is unavailable.
>
> **Left for RF-30:** `keyboard-pass.md` and the reconciliation still say
> "twenty-four components" where the test table has thirty. That is RF-30's
> finding, so it is not corrected here, and the new text does not repeat it.
>
> Frontend: 1,055 Vitest tests. Stage 2.8: 73 of 73, including the walk and
> the axe sweep over every story. Stage 2.9: 8 of 8. Journeys: 47 of 50.
> The three that fail are the same three as before this change: J2's two,
> RF-35, and sign-in, RF-36.
>
> **Amended after RF-45.** RF-40 and RF-41 added two reasons a decision on
> `/gates/:id` is unavailable: no signing agent answering with the approver's
> key, and no recorded evidence. Both follow this finding's rule — the control
> stays a focus stop and carries the reason as its accessible description.
> `keyboard-pass.md` said the approval screen's controls were always available
> when the walk reached them; it now says what can make them unavailable. The
> three journeys recorded above as failing were closed by RF-35 and RF-36.

---

### RF-30 — P6 — Three documents state counts and controls the code does not have

**Component and story counts.** `web/storybook-static/index.json` holds 220
stories over 32 titles: 19 primitives, 11 composites, one `Example/PoolStatus`
and one `Tokens/Run state`. The documents say:

| Document | Claim |
|---|---|
| `README.md:175` | "sixteen primitives and eight composites" |
| `README.md:179` | "168 Storybook stories, 24 components at seven states each" |
| `README.md:190` | "axe runs over … all 168 stories" |
| `imhotep-reconciliation.md:302` | "twenty-four components at seven states each" |
| `imhotep-reconciliation.md:305` | "175 Storybook stories" |
| `keyboard-pass.md:103,138` | "twenty-four components", "175 stories" |

Three documents, three different numbers, none of them 220.

**The reconciliation over-claims in two places.** §9.1–9.5 is marked IMPLEMENTED
with the sandbox as the sole deviation; SAD 9.5's "TLS 1.3 only, mTLS between
control plane components" is not built at all (RF-03). §8.1 says every mutating
endpoint "records through the orchestrator, in one transaction, or refuses and
says why", which is true, but the publish row's stated controls are not enforced
(RF-05).

**`PoolStatus` is filed under `src/example/`.** It is a real component with real
stories, an a11y test and seven states, living in a directory whose name says it
is a sample, and it is counted in the axe sweep and the visual baselines.

**Prompt**

> Correct the counts and the marks, and make them uncorrectable by hand.
>
> 1. Replace every hard-coded component and story count in `README.md`,
>    `docs/acceptance/imhotep-reconciliation.md` and
>    `docs/acceptance/keyboard-pass.md` with the real figures, and add a test
>    that reads `storybook-static/index.json` and fails when a document's figure
>    disagrees. A number written in three places is a number that is wrong in two
>    of them.
> 2. Amend the reconciliation: mark SAD 9.5 transport security NOT BUILT with the
>    reason, and note against §8.1 that the publish endpoint's controls are in
>    `skidbladnir.publish` and not on the request path until RF-05 lands.
> 3. Move `PoolStatus` out of `src/example/` into the composites, or rename its
>    story title so the design system's inventory is not padded by a sample.
> 4. Add the third mark this audit's section 2 argues for — *reachable* — to the
>    reconciliation's vocabulary table, and re-mark the nineteen orphan modules
>    against it.

**Acceptance criteria**

- The count test exists and passes; changing a story count fails the build until
  the documents are updated.
- The reconciliation carries the two corrected marks.
- `Example/` no longer appears in the Storybook index.
- The reconciliation's vocabulary has three marks, and the orphan modules are
  marked against the third.
- Gated in stage 2.1.

> **Status: done, with three departures from the prompt, each because the
> repository had moved since the audit.**
>
> **1. Counts.** `README.md`, the JARNGREIPR README, the reconciliation and the
> keyboard pass now state the real figures: 19 primitives, 11 composites,
> 30 components and 220 stories. The JARNGREIPR README was a fourth document
> stating a fifth figure, "eighteen primitives, eight composites". The
> reconciliation's "thirty-four operations" was wrong too; there are 43.
> `tests/unit/test_stated_figures.py` reads every figure in the four
> documents and fails when one disagrees, and the operation count against
> `openapi.json`.
>
> *Departure:* the test counts stories from their source, not from
> `storybook-static/index.json`. The index is a build output, ignored by git
> and absent in stage 2.1, so a test that reads it gates only for whoever
> built Storybook first. Where a current index exists, the source count is
> checked against it; it was, after a rebuild, and they agree. A stale index
> is skipped, not failed.
>
> **2. The reconciliation's marks.**
> - **Transport security is NOT BUILT** (NOT BUILT 2). Nothing terminates TLS:
>   the console proxy and the API both listen in plain HTTP, and nothing uses
>   mTLS. The certificate `install.sh` requires is checked for and never
>   served.
> - **The crypto inventory claimed the transport.** Its TLS row, which RF-03
>   derived from the certificate *setting*, reported "TLS 1.3 only. mTLS
>   between control plane components" as in use on any deployment with a
>   certificate path, and a test asserted it. The row now says the transport
>   is not built.
> - **Building it is RF-37**, recorded rather than done.
>
> *Departure:* the prompt asked to note that publish's controls are "not on
> the request path until RF-05 lands". RF-05 has landed, so §8.1 now says
> they are on it, with the refusal codes. §8.1's table also gained the four
> writing endpoints added since it was written.
>
> **3. `PoolStatus`.** It is not a padded composite but the
> `jarngreipr-component` skill's worked example, generated by its scaffold
> and compared byte for byte by `tests/contract/test_skills.py`. Moving it
> into the composites would count a component the UX specification does not
> have. The scaffold now files the example under `Skill examples/`, the
> committed story is regenerated to match, and its seven visual baselines
> are renamed. `Example/` no longer appears in the index, and a test says so.
>
> **4. REACHABLE.**
> - `scripts/reachability.py` derives it statically, from what a deployment
>   runs: the API application, the worker, `draupnirctl`, every plug-in entry
>   point, and the migrations.
> - `tests/unit/test_reachability.py` holds the reconciliation's table to it,
>   and fails when a platform module nothing reaches is not named there.
>
> *Departure:* the vocabulary already had three marks, so REACHABLE makes
> four. It is the third of the three properties section 2 asks of a control:
> built, tested and reachable.
>
> **What re-marking found.**
> - **Section 2's own count was off.** Its list has 24 modules, not
>   twenty-three, so 20 after the four it sets aside, not nineteen.
> - **Of those 20, 13 are now reachable** — most wired by earlier fixes in
>   this register.
> - **Seven are not:** `brisingamen.merge`, `megingjord.anchors`,
>   `megingjord.registry`, `motsognir.retry`, `raun.regression`,
>   `raun.transitions`, `skidbladnir.formats`.
> - **The first analysis missed modules imported only by other orphans or by
>   scripts.** `megingjord` as a package — nothing a deployment runs imports
>   any of MEGINGJORD — plus `brisingamen.routes`, `gullinbursti.roster`,
>   `motsognir.supply_adapters` and `svalinn.containment`.
> - **One of section 2's reasons does not hold.** `core.infrastructure.models`
>   is not "reached through SQLAlchemy metadata"; only tests import it.
>
> Each is marked, with the ones unreachable by design said to be. The
> unreachable controls are not wired here: which of them a deployment should
> reach is a design question for each, and several are the subject of findings
> already in this register.
>
> Python: the affected unit, contract and documentation tests, 129, pass, and
> the acceptance pack checks. Unit, property and contract in full:
> 2,114. Visual stage: 8 of 8 shards, the renamed baselines
> compared. JARNGREIPR's Vitest: 1,028.
>
> **Re-run after RF-45.** `python -m scripts.reachability` was run again on
> 17 September 2026, once RF-32 to RF-45 had landed. **No mark changed**: the
> same 21 modules are unreachable, and every module those findings added is
> reachable, `gleipnir.clearance` and the projections of RF-41 and RF-42
> among them.
>
> What had gone stale was the prose beside the tables:
> - the list of roots named neither `draupnir.api.serve` (RF-37) nor the
>   approver's signing agent (RF-40), both of which the analysis already
>   counted;
> - the summary said eleven modules were unreachable by design and fifteen
>   were platform code — 26, against 21 marks. The marks say 8 and 13.
>
> Both are now derived. `tests/unit/test_reachability.py` fails if a root the
> script starts from is missing from the section, or if a summary count
> disagrees with the marks it summarises.
>
> The reconciliation's other sections were brought up to RF-45 in the same
> pass:
> - §5.1, the worker's corpus half (RF-43);
> - §7, `gate_result` and `source` projected, and migration 0007 (RF-41,
>   RF-42);
> - §8.1, which entries a projection folds, and the source's residency;
> - §8.2, GLEIPNIR's policy installed as a driver (RF-43);
> - §9.1–9.5, stage 3.1a's evidence (RF-44, RF-45).
>
> Results: the unit stage, 1,739 at 90.20%, the two new reachability tests
> among them; the acceptance pack regenerates unchanged. The first run of it
> failed on this machine rather than on the change: Windows Smart App Control
> had begun refusing `psycopg_binary`'s unsigned `pq` extension, so the one
> unit test that starts the application's lifespan could not import a
> PostgreSQL driver and coverage read 89.99%. The user turned Smart App
> Control off, and the stage passed.

---

### RF-31 — P6 — Committed acceptance evidence is non-deterministic

`docs/acceptance/evidence/keyboard-pass.json` is committed and is rewritten by
every a11y run with a fresh `performedAt` and whatever the seeded database
happened to hold — a 50-line diff after this audit's run, from ledger sequence
`339` to `354` plus a wall-clock timestamp. `python tasks.py acceptance` is
otherwise idempotent.

That makes the evidence file a permanent source of working-tree noise, and it
weakens the evidence: a file that changes on every run is one nobody reads a
diff of.

**Prompt**

> Decide what the committed keyboard evidence is for.
>
> Either freeze it — generate it against a fixed seed and a fixed clock so a
> re-run reproduces the committed bytes, and add it to the drift gates alongside
> the generated clients — or stop committing it and publish it as a CI artefact
> like the Playwright report. Do not leave a committed file that a routine test
> run rewrites.
>
> The same question applies to anything else under `docs/acceptance/evidence/`
> written by a test rather than by a generator; audit the directory and apply one
> rule.

**Acceptance criteria**

- Either: a second `test-a11y` run leaves the working tree clean, and a hand edit
  to the evidence file fails a drift gate.
- Or: the file is not tracked, is uploaded as CI evidence, and
  `docs/acceptance/README.md` says where to find it.
- One rule applies to every file in `docs/acceptance/evidence/`.
- Gated in stage 2.8.

> **Status: done, by the second option, applied to both files.**
>
> **Why not freeze.** The keyboard record reads a live console over a
> database that the journeys mutate: ledger rows, dates and digests on
> `/audit`, alert counts, run names. Reproducing its bytes would mean
> freezing the clock, the seed *and* every run before it, and a frozen
> record of a fixed fixture is weaker evidence than a fresh record of the
> real stack.
>
> **The audit.** `docs/acceptance/evidence/` held two files, and both are run
> records, not generator output:
> - `keyboard-pass.json`, written by the keyboard walk;
> - `procedure-m1-m10.json`, written by `make procedure`, with its own start
>   and end times, run identity and approval identity.
>
> The second never churned only because nobody re-ran it — which is also why
> nothing in the pipeline produced it at all.
>
> **The rule:** nothing under `docs/acceptance/evidence/` is committed. Git
> ignores the directory, and both files are untracked.
> - **The pipeline writes both on every build.** The keyboard walk already ran
>   at stage 2.8. `tasks.py procedure` is new in the workflow, after it,
>   against the stack stages 2.7 and 2.8 bring up, and in `tasks.PIPELINE`,
>   which RF-24's test requires to match.
> - **They're uploaded** in the `acceptance-evidence-<commit>` artefact beside
>   the SBOM and the Playwright report.
> - **The documents say where to look.** `docs/acceptance/README.md`, which is
>   generated, `keyboard-pass.md` and the runbook all say where the records
>   are. So does `scripts/procedure.py`, whose docstring had also claimed that
>   `AC-F12.md` quotes the record; it does not.
>
> **Gates.**
> - `test-a11y` and `procedure` check, after writing, that git sees no change
>   under the directory, and fail naming the file if it does — so a record
>   committed again, or an ignore rule that stops covering it, fails stage
>   2.8.
> - `tests/unit/test_evidence_records.py` asserts nothing there is tracked,
>   that the two records and an invented third are ignored, that the workflow
>   writes and uploads them, and that the README says where they are.
>
> **Verified.** Before committing, the untracking showed as two staged
> deletions, which the new check would itself report. So the runs were checked
> directly:
> - after two keyboard walks, each of which rewrote its record, git showed
>   nothing under the directory beyond those deletions — the second run
>   passed all 73 a11y tests;
> - after a procedure run that rewrote its record, git showed nothing under
>   the directory beyond those deletions.
>
> **Found on the way, not fixed here:** one a11y run failed a Storybook axe
> shard with "Axe is already running". Storybook's own a11y addon runs axe
> inside the preview, and `AxeBuilder` started its own run on the same frame.
> It is intermittent — the next run passed that shard — and it has nothing to
> do with the evidence. But a gate that can fail on a race is one that gets
> re-run until green, which is how real failures get waved through.
>
> Python: 2,106 unit and contract tests pass, including the pipeline
> consistency tests `procedure` now has to satisfy. The acceptance pack
> checks: 90 criteria.
>
> **Amended after RF-45.** Re-checked once RF-32 to RF-45 had closed. The
> rule stands: nothing under `docs/acceptance/evidence/` is committed, and no
> stage added since writes there. RF-44 added a stage 3.1a check,
> `python tasks.py images-transport`; it records nothing, and
> `tests/unit/test_documentation.py` holds it to the workflow the way it holds
> `procedure`.

---

### RF-32 — P2 — A conditional write is conditional on nothing

*Found while working RF-27.*

`cancelRun`, `retryRun`, `decideGate` and `publishRelease` check `If-Match`
with `concurrency.require(resource, {"id": ...}, if_match)` — an entity tag
computed over the identifier alone, which never changes. `getRun` returns an
`ETag` over `{id, state}`, and the gate and release reads return none. So:

- a client that sends back the tag `getRun` gave it is refused with 412 on every
  cancel and retry, because the two tags are computed over different things;
- a client that sends no tag is refused with 428;
- `If-Match: *` passes, and so does any tag computed from the identifier, which
  can never be stale.

AC-B4's "a stale write returns 412" is therefore unreachable by any client that
behaves correctly. And the console sends no `If-Match` on any of the four, so it
cannot cancel, retry, decide or publish at all. J3 opens both decision dialogs
and confirms neither, and J2 opens the cancel dialog and stops, which is how a
console with four dead controls passed its journeys.

RF-27's two new conditional operations, `approveRetention` and
`selectMergePoint`, compute their tag over state that changes, return it on the
read, and are tested stale. The four above were not changed.

**Prompt**

> Make every conditional write conditional on the state it would change.
>
> For each route that calls `concurrency.require`, compute the tag from that
> state — a run's state and retry count, a gate's decision, a release's
> publication — and return the same tag as `ETag` on the read an operator acts
> from, adding one where the read has none. Have the console send it.
>
> For each route, add a contract test that reads, lets the state move, and
> asserts the stale write is refused with 412. Extend J2 and J3 to confirm a
> cancel and a decision end to end, rather than stopping at the dialog.

**Acceptance criteria**

- For each of the four routes, the tag the read returns is the tag the write
  checks, and a contract test asserts a stale write is 412.
- The console sends `If-Match` on all four actions.
- A journey confirms a cancel and a decision and observes the result.
- Gated in stages 2.3 and 2.7.

> **Status: done, for all four writes, with a decision confirmed as a rejection
> and approval left to RF-40.**
>
> **Runs and gates.** One function, `concurrency.run_version`, gives the state
> a run write changes: the state and the retry count.
> - **The retry count as well as the state**, because a requeued run comes
>   back to EVALUATING. A tag over the state alone would let a retry read before
>   somebody else's requeue pass after it.
> - **Every run read carries the tag.** `RunOut` carries it as a computed
>   `etag`, so the board and `getRun` return it in the body as well as the
>   header. The console reads bodies, and a computed field cannot be forgotten
>   by a constructor.
> - **So does the approval queue.** Each queue item carries its run's tag,
>   because a gate is a run awaiting approval.
> - **The writes check after reading.** `cancelRun`, `retryRun` and
>   `decideGate` now check the tag after reading the run's facts, not before,
>   and compute it with the same function.
>
> **Releases.** `concurrency.release_version` is the approval and any
> publication already recorded. The publish action sits on the lineage screen,
> so `getLineage` returns the tag. Both `getLineage` and `publishRelease` ask for
> it through one writer question, `writing.publication_version_of`, so the two
> cannot compute it differently.
>
> **The console sends each tag** from the read it acts on: the run it shows,
> the queue entry the evidence came from, and the lineage the publish panel sits
> under.
>
> **Tests.**
> - **`tests/contract/test_conditional_writes.py`**, over doubles that share
>   one chain, reads, lets the state move and shows the stale write is refused
>   with 412 on all four routes — and that the same tag was accepted while
>   current:
>   - a cancel repeated with its first tag;
>   - a retry after somebody else requeued it, where only the retry count
>     moved;
>   - a decision after somebody else decided;
>   - a publication after somebody else published.
>
>   A tag over the identifier alone no longer passes.
> - **Against a real chain**, `test_release_publication.py` refuses a second
>   publication sent with a tag read before the first.
> - **The existing tests.** The contract and integration tests sent the
>   identifier-only tag, because that is what the handlers checked. They now
>   send what a read returns.
>
> **Journeys.**
> - **J2** confirms a cancel on the seeded training run and observes it fail.
> - **J3** confirms a rejection and observes the artefact quarantined and gone
>   from the queue.
>
> **J3 rejects rather than approves:** a console approval cannot succeed for
> reasons of its own, recorded as RF-40.
>
> **The seed gains `cim-nzl-v0.1`**, a second artefact awaiting approval, for
> J3 to decide. Otherwise deciding would consume the gate the other approval
> tests and stage 2.8's keyboard walk use. It is last in the plan, so every run
> before it is generated as before. The seed's run count is updated from 12 to
> 13 where it is stated.
>
> **Found on the way, and recorded, not fixed:**
> - **RF-39:** `draupnirctl` never sends `If-Match`, so its four conditional
>   commands are refused with 428 every time.
> - **RF-40:** the console approves with no `decidedAt` and the placeholder
>   signature `console-session`, so it is refused before its signature is
>   checked, and the placeholder could not verify anyway.
>
> AC-B4's entry now says the 412 is reachable by a correct client.
>
> **Results.**
> - Python: 2,130 unit, property and contract tests and 222 integration tests
>   pass. On the first full run, one failure was the acceptance pack's
>   freshness check, which had started before the pack was regenerated for the
>   new tests; it passes against the regenerated pack.
> - Frontend: 1,055.
> - Journeys, against a freshly reseeded stack: 49 of 52, including both new
>   confirmations. The three failures are RF-35's two and RF-36's, as before.
> - The OpenAPI diff is additive only.

---

### RF-33 — P3 — The release package is read from a table only the seed writes

*Found while working RF-27.*

`publishRelease` records a `published` ledger entry and writes no `release`
row, and nothing else in the application writes that table. `getRelease`, the
approval shown on a lineage, and S17's document download all read it. On the
seeded stack the three releases are there because the seed inserts them. On an
estate a published artefact has no release package to read or download, and
its lineage shows no approval.

**Prompt**

> Record the release where publication happens.
>
> Either write the release record in the transaction that records `published`
> — the package's document references, the signature, and the approval it rests
> on — or fold the package from the chain, as RF-27 did for retention, and stop
> reading the table. Either way, what the console reads about a release must
> come from what publication recorded.

**Acceptance criteria**

- An integration test publishes through the API, then reads `getRelease` and
  downloads a document of the package.
- The lineage of a published artefact carries its approval.
- The seed is no longer the only thing that makes a release readable.
- Gated in stage 2.4.

> **Status: done, by projecting the release from the chain — and two tables
> beside it that were in the same state.**
>
> **Why not write the row at publication.** `release` has non-null foreign
> keys to `artefact` and `approval`, and nothing in the application wrote
> either. On an estate a release row written by `publishRelease` would have
> nothing to point at. S17's download needed an artefact row too, through the
> lineage and model reads it renders from. So `artefact`, `approval` and
> `release` are projections now, as `run` has always been.
>
> **How.**
> - **`core/domain/releases.py` folds the chain**, purely and never reading its
>   own output:
>   - artefacts from the `artefacts` lists entries record;
>   - approvals from the decision that moved a run out of AWAITING_APPROVAL;
>   - releases from `published` entries, bound to the approval they name by
>     sequence number.
>
>   `anchored_at` is derived from a countersigned anchor that covers the
>   publication. The document addresses are the paths
>   `downloadReleaseDocument` serves.
> - **`ReleaseProjection` writes the three tables from it.** It is advanced on
>   every append beside the run projection through `ChainProjections`, which
>   keeps the orchestrator's one projection port unchanged. It re-folds only
>   when an entry since its checkpoint could change a row.
> - **An artefact is projected only when its record carries an address, a
>   digest, a kind and a size.** A row states that these bytes are at that
>   address, so an incomplete record is skipped, not completed with a guess.
>   The worker recorded only `{uri, sha256}`, so it now records kind and size
>   for everything it stages, including the adapter at TRAINED, which it had
>   not listed. The Sindri procedure hashes files it keeps in a work
>   directory, so its chain names no stored artefact, and none is projected.
>
> **Found on the way, and fixed here because the criterion needs it.** A
> decision through the API recorded no artefact. The publication path finds an
> approval by the artefact digest the approval recorded, so on an estate the
> worker would produce, the API would approve, and `publishRelease` would
> refuse with `release-unapproved`. `RunFacts` now carries the artefact the run
> awaits approval on, and both decisions record it.
>
> **The seed** writes the facts the worker and API record — stored artefacts,
> the artefact awaiting approval, and decisions naming theirs — plus a
> publication and an anchor covering it. It rebuilds the projection instead of
> inserting rows. It now has 1 release, not 3. The three it inserted paired
> artefacts with approvals by list position, including approvals of runs still
> awaiting one, which no chain could produce. The stated counts are corrected
> in the README, `CONTRIBUTING.md` (which also still said 12 runs), the task
> description and the seed.
>
> **Tests.**
> - `tests/unit/test_release_projection.py` pins the fold: what becomes a row,
>   what is refused, binding, anchoring and document names.
> - `test_release_publication.py` publishes through the API and then reads
>   `getRelease`, downloads the model card and reads the lineage's approval.
>   Nothing in it inserts a row.
>
> **Left for RF-41:** `gate_result` is still written only by the seed.
>
> **Results.**
> - Python: 2,149 unit, property and contract tests, and 223 integration
>   tests. On the first integration run, the new release test failed on its
>   own assertion: it expected the publisher as the approver, where the package
>   correctly reports who approved. Corrected, it passes.
> - On a freshly reseeded stack, whose summary now reports 13 artefacts,
>   2 approvals and 1 release, all projected:
>   - journeys: 49 of 52, and the three that fail are RF-35's two and RF-36's,
>     as before;
>   - the a11y stage: 73 of 73, with the RF-31 check that no record changed
>     the tree.

---

### RF-34 — P3 — A release does not record the licence policy it was judged under

*Found while working RF-27.*

SAD 10.2: existing releases keep the version in force at their release date.
Neither a source registration nor a release records which GLEIPNIR licence
policy version the corpus was judged under, so `copyright.for_release` cannot be
called with the version that applied. S17's copyright policy document is
therefore rendered under the version in force now. It names that version, which
is honest, and it is not what 10.2 asks for.

**Prompt**

> Record the licence policy version where the licence decision is taken, and
> carry it to the release.
>
> GLEIPNIR's decision already carries `policyVersion`. Record it on the source,
> carry it to the release, and render the copyright policy with it. Refuse to
> render one for a release that records no version, rather than substituting
> today's.

**Acceptance criteria**

- A source registration and a release record the licence policy version.
- The downloaded copyright policy names the recorded version.
- A release that records none is refused, not rendered under the current policy.
- Gated in stage 2.3.

> **Status: done, with the version recorded where the licence decision is
> actually taken, which is not the source registration.**
>
> **Where the decision is.** `registerSource` records facts and decides
> nothing. Its handler says so: whether a licence permits anything is
> GLEIPNIR's question, asked later. The decision is taken when a run's corpus
> moves CORPUS_REGISTERED → LICENCE_CLEARED, and SAD 6.1 already requires that
> entry to record `policy_version`. Recording a version on a registration would
> state a decision that has not been taken. So the version travels from the
> decision to the release. Each source's decision in that entry now names its
> `policyVersion` too, as `PolicyDecision` already carried it.
>
> **Carried to the release.**
> - `PublicationFacts` reads the version from the run's LICENCE_CLEARED entry.
> - `publishRelease` records it on the `published` entry.
> - RF-33's projection writes it to `release.licence_policy_version`, added by
>   migration 0006, nullable because releases published before recorded none.
> - `getRelease` returns it.
>
> **Rendered with it, or refused.**
> - The copyright policy document is rendered under the recorded version.
> - A release that records none, or one recording a version this build does
>   not hold, is refused with 409 `licence-policy-unrecorded` rather than
>   rendered under today's policy.
> - The model card and training summary state the version as not recorded
>   instead of refusing, since the module's rule is that an absence is stated.
>
> **The seed** recorded `gleipnir-policy/2026.01` at LICENCE_CLEARED, a version
> the policy registry does not hold, so its release could not have rendered
> the policy it was cleared under. It now records the real version, on the
> decision and on the publication.
>
> **Tests.**
> - The document contract test's release records the *previous* policy
>   version, so a document rendered under today's policy fails.
> - New tests refuse a release recording none, and one recording an unheld
>   version.
> - A further test shows the card and summary still download without claiming
>   a version.
> - The publication integration test reads the version back from `getRelease`
>   and from the downloaded policy, carried from the chain's decision.
>
> **Found on the way, and recorded, not fixed:**
> - RF-42: `source` is written only by the seed.
> - RF-43: nothing outside the demonstration procedure takes the licence
>   decision at all.
>
> **Results.**
> - Python: 2,154 unit, property and contract tests, and 223 integration.
> - On a freshly reseeded stack, migrated through 0006 (1 release, recording
>   `gleipnir-licence/2026.01`):
>   - journeys: 49 of 52, the three failures being RF-35's two and RF-36's;
>   - a11y: 73 of 73.

---

### RF-35 — P4 — The console's default specification is refused by its own API

*Found while working RF-27.*

The compose screen (`web/apps/console/src/screens/Runs.tsx`) starts from a GBR
specification naming `MIDGARD-CORE-QWEN36-35B-A3B-v1.0`, the Tier B base. Since
RF-11 (`50275f8`) the API refuses a specification whose base contradicts its
jurisdiction's tier, and GBR is Tier A. So an operator's first dry run on the
screen is a 422, and J2's two submission journeys have failed since RF-11. Nobody
saw, because the journeys were not run between RF-11 and RF-27.

**Prompt**

> Make the default specification one the API accepts, and derive it rather
> than hard-coding it. The base follows from the jurisdiction's tier, so the
> console should take it from what the API validates against — the tier table,
> or a default the API provides — and J2's fixture should do the same. Add a
> check that the console's default specification validates.

**Acceptance criteria**

- A dry run of the default specification succeeds.
- J2's submission journeys pass.
- A test fails if the default names a base its jurisdiction's tier refuses.
- Gated in stage 2.7.

> **Status — done.**
>
> **The base was wrong in two ways, not one.** It was the Tier B base for a
> Tier A jurisdiction. It was also addressed at no site:
> `hamarr.config.prepare` expects `tiers.base_artefact`'s
> `hodd://<site>/models/core/<base>`, and the default said
> `hodd://models/core/…`. Correcting only the model name would have been
> refused too. The address depends on deployment configuration, so no base
> written into the console could be right at every site.
>
> **Derived, from the table the API validates against.**
> - `scripts/generate_ts_tiers.py` writes
>   `web/packages/api-client/src/generated/tiers.ts` from
>   `draupnir/hamarr/tiers.py`: each jurisdiction's tier and each tier's base.
> - The script refuses to write a table that does not enumerate CIM-56.
> - `tasks.py clients` runs it, and the file is in `clients-check`'s list, so
>   a hand edit or a stale table fails that gate like the rest of the
>   generated client.
> - `web/apps/console/src/specification.ts` builds the default: tier from the
>   jurisdiction, base from the tier, and site from `/healthz`, which the
>   shell already reads.
> - It throws on a jurisdiction outside the programme rather than guessing a
>   tier.
> - The compose screen waits for the site before composing, rather than
>   composing a base at no site.
> - J2's fixture no longer holds its own copy. It takes the console's default
>   at the site the stack reports, so the journey and the screen cannot
>   disagree.
>
> **Left alone:** `release.route: 'tier-a'`, which nothing refuses at
> admission and which is not this finding.
>
> **Two more failures were behind the base.** The criterion is that J2's
> submission journeys pass, and with the base right they still did not.
> - *The checkpoint interval.* The default authored `save_steps: 500`. At the
>   assumed twelve seconds a step that leaves a hundred minutes unwritten, and
>   `prepare` refuses anything over thirty.
>   - `admit` caught tier and configuration refusals but not
>     `CheckpointError`, so this refusal reached the operator as a 500
>     `internal-error` asking them to report a fault.
>   - `admit` now answers it with 422 `specification-rejected`.
>   - The default authors no interval, so HAMARR derives one as it does for
>     any specification that names none.
> - *The board labelled a new run by its identifier.* Since RF-15 a board
>   delta is built from the ledger's notification, which carries the new
>   state and not the run's name. The board's merge fell back to the
>   identifier, so J2's "reflects a new run within five seconds" waited for a
>   name that never appeared.
>   - This was hidden because the journey had failed earlier since RF-11.
>   - When a delta names a run the board does not hold, the board now reads
>     that one run with `getRun` and replaces the placeholder.
>   - That is one run and not the list, so AC-U4's "no full list poll" still
>     holds, and J2's poll-counting journey still counts only `GET /v1/runs`.
>
> **Tests.**
> - `tests/contract/test_run_admission.py` adds the over-budget interval to
>   the cases both handlers must refuse alike, as `specification-rejected`.
> - `web/apps/console/src/specification.test.ts` (Vitest, stage 2.6):
>   - the GBR default names `hodd://sindri/models/core/MIDGARD-CORE-GEMMA3-27B-v1.0`;
>   - for each of the 56 jurisdictions, the default declares that
>     jurisdiction's tier and names that tier's base;
>   - an unknown jurisdiction is refused.
> - `tests/unit/test_generated_tiers.py`:
>   - the committed table is the generator's output;
>   - it matches `tiers.py`;
>   - the console builds the address the way `base_artefact` does;
>   - a drifted table is not written;
>   - the old default is refused by `prepare`.
> - J2's two submission journeys, stage 2.7: the default dry runs and submits.
>
> **Results.**
> - Python: 2,161 unit, property and contract tests, and 223 integration.
> - Vitest: 1,114. Typecheck, lint and formatting are clean, and
>   `clients-check` reports the clients current with the tier table among
>   them.
> - Journeys: 51 of 52, both of J2's submission journeys passing. The one
>   failure is RF-03's sign-in, which is RF-36.
> - a11y: 73 of 73.

---

### RF-36 — P5 — The journey stack does not route `/auth`, so sign-in cannot pass

*Found while working RF-27.*

`web/scripts/serve-console.mjs` serves the built console for the journeys and
proxies `/v1`, `/healthz` and `/readyz` to the API — the three prefixes
`vite.config.ts` proxied before RF-03 added `/auth/login`. The RF-03 journey
requests `/auth/login` from the console's origin, reaches the static server's
fallback, and receives 200 where it asserts a 303. The served console cannot
begin a sign-in, and the journey that exists to prove it can has not passed
since RF-03.

**Prompt**

> Route `/auth` to the API wherever the console is served — `serve-console.mjs`
> and the Vite development proxy — alongside the other three prefixes. Keep one
> list of proxied prefixes, or test that both proxies use the same one, so a
> route added to one and not the other fails a build.

**Acceptance criteria**

- The RF-03 journeys pass against the served console.
- Both proxies route the same prefixes, and a test says so.
- Gated in stage 2.7.

> **Status — done.**
>
> **One list.**
> - `web/scripts/proxied-prefixes.json` holds the prefixes the console's
>   servers route to the API: `/v1`, `/auth`, `/openapi.json`, `/healthz` and
>   `/readyz`.
> - `serve-console.mjs` and `apps/console/vite.config.ts` both read it, and
>   neither writes a prefix of its own. A route added for one server is
>   therefore added for both.
> - The file is JSON rather than a module so both can read it without a
>   build step. The script is plain Node; the config is TypeScript bundled by
>   Vite.
>
> **Held to nginx as well.** The prompt asks for "wherever the console is
> served", and the deployed console is served by `docker/nginx.conf`, which
> already routed `/auth/` and `/openapi.json`.
> - `/openapi.json` is added to the list, so the development servers
>   now match what is deployed.
> - `/metrics` stays out, because nginx refuses it on purpose (SAD 8.1:
>   loopback only).
>
> **Tests.** `web/tests/proxied-prefixes.test.ts`, run by Vitest in stage 2.6,
> checks that:
> - the list includes `/auth`;
> - each server reads the list, and contains no quoted prefix of its own;
> - every listed prefix is one nginx passes to the API;
> - every location nginx passes to the API falls under a listed prefix;
> - `/metrics` is not listed.
>
> The RF-03 journeys exercise the served console in stage 2.7.
>
> **Results.**
> - Vitest: 1,119, the five new tests among them. Typecheck, lint and
>   formatting are clean.
> - Journeys: 52 of 52, both of RF-03's sign-in journeys passing against the
>   served console. This is the first full pass since RF-03.
> - a11y: 73 of 73.

---

### RF-37 — P2 — SAD 9.5's transport security is not built

SAD 9.5 specifies "TLS 1.3 only", with mTLS between control plane components
and between GULLINBURSTI and MEGINGJORD. Nothing in the repository terminates
TLS or presents a client certificate:
- `docker/nginx.conf` listens on 8080 in plain HTTP;
- the API listens in plain HTTP behind it;
- no configuration anywhere names a protocol version or verifies a client.

RF-03 made `install.sh --check` refuse to commission without a readable
certificate and key, and made the cryptographic inventory's TLS row read that
setting. Nothing reads the certificate. So a commissioned deployment was
required to hold a certificate it never served, and on any machine with the
setting present the inventory reported "TLS 1.3 only. mTLS between control
plane components" as in use. The inventory is a generated artefact carrying
AC-S16's authority, and it described two controls nobody built.

Found by RF-30 while marking the reconciliation's section 9. RF-30 corrected
the claims: the reconciliation marks the transport NOT BUILT, and the inventory
row says a certificate is configured and the transport is not built. The
transport itself is this finding.

**Prompt**

> Terminate TLS 1.3, and only TLS 1.3, at the console proxy with the certificate
> `install.sh` already requires. Serve the API to the proxy over mTLS, and use
> mTLS for GULLINBURSTI's calls to MEGINGJORD, with certificates from the
> internal signing CA of Decision S9.
>
> Derive the inventory's TLS row from what the proxy configuration actually
> declares, not from whether a certificate path is set.
>
> Where the estate supplies the certificates, say where they come from in
> `docs/runbook.md`, and have `install.sh --check` verify that the proxy loads
> them rather than that the files exist.

**Acceptance criteria**

- A contract test starts the proxy and refuses a TLS 1.2 handshake.
- The API refuses a connection that presents no client certificate.
- The inventory's TLS row reports in use only when the proxy configuration
  terminates TLS 1.3, and a test sets and unsets that.
- The reconciliation's NOT BUILT 2 is closed with the evidence.
- Gated in stages 2.3 and 2.4.

> **Status — done.**
>
> Two choices were put to the user and taken as recommended:
> - the proxy test starts the web image's own nginx base with testcontainers
>   in stage 2.3;
> - with no certificates the proxy and the API refuse to start unless
>   `DRAUPNIR_DEV` is set.
>
> **Built.**
> - **The console proxy.** `docker/nginx.conf` terminates TLS 1.3 only, on
>   8443, with no plain HTTP listener. It passes to the API over TLS 1.3,
>   verifies the API's certificate against the internal CA under the name
>   `draupnir-api`, and presents its own certificate.
> - **The API.** `draupnir/api/serve.py` is now the API image's command. It
>   serves TLS 1.3 only and requires a client certificate from the internal
>   CA. It refuses to start without that material unless `DRAUPNIR_DEV` is
>   set, and refuses a half-configured set even then. The development stack
>   and the journeys still run `uvicorn` against the application.
> - **GULLINBURSTI.** The worker's MEGINGJORD client presents the site
>   certificate and verifies MEGINGJORD against the internal CA. Without the
>   material it submits nothing and logs `worker.federation.unauthenticated`,
>   so the anchor duty reports no federation link.
> - **One statement of the policy.** `draupnir/svalinn/transport.py` holds
>   the two contexts, both pinned to TLS 1.3 at floor and ceiling, and
>   `declared_by`, which reads what an nginx configuration declares.
>
> **The inventory's TLS row** is derived from `docker/nginx.conf`.
> - It is in use only when every listener is TLS and TLS 1.3 is the only
>   protocol named.
> - mTLS is named only when every upstream is HTTPS, verified, restricted to
>   TLS 1.3 and presented with a client certificate.
>
> **Installer and units.**
> - `install.sh --check` loads the material rather than looking for it.
>   - It runs `nginx -t` in the console image, with each file mounted where
>     `docker/nginx.conf` reads it.
>   - It loads the API's and GULLINBURSTI's material in the API image through
>     `python -m draupnir.svalinn.transport`, which also checks that the
>     internal CA issued it.
> - Both checks run inside the image because under rootless podman the files
>   belong to the container's subordinate uid, not the service account. A
>   host-side `openssl` would read them as the wrong user. For the same
>   reason `draupnir-run.sh` tests that each file exists, not that it is
>   readable.
> - `draupnir-run.sh` mounts each file read-only at the name its reader
>   expects, and publishes the console on 8443.
> - `docs/runbook.md` gains a Certificates section: which certificates, what
>   each carries, who reads it, and what happens without it.
> - `docs/DEPLOYMENT.md`'s commissioning checks now go through the console
>   over TLS.
>
> **Tests.**
> - **Stage 2.3,** `tests/contract/test_transport.py`.
>   - The proxy, started in the nginx image `web.Dockerfile` builds on,
>     completes a TLS 1.3 handshake, refuses TLS 1.2, serves the console with
>     HSTS, and does not start without its certificates.
>   - The API refuses a connection with no client certificate, one with a
>     certificate from another CA, and TLS 1.2. It also refuses to start
>     unconfigured.
>   - The installer's material check refuses a certificate from another CA,
>     and a key that is not the certificate's.
> - **Stage 2.4,** `tests/integration/test_transport.py`.
>   - A request passes through the proxy to the API over mTLS.
>   - The same proxy without its client certificate is refused by the API,
>     asserted on nginx's log as well as on the 502.
>   - GULLINBURSTI's client is accepted by a stand-in MEGINGJORD that refuses
>     a forge presenting no certificate.
> - **Unit.** The TLS row reports in use for a TLS 1.3 configuration, not for
>   one that also admits TLS 1.2, and not once the file is gone.
>
> **The reconciliation** marks transport security IMPLEMENTED under 9.1–9.5
> with this evidence. NOT BUILT 2 is closed, and one item remains not built.
>
> **Found by starting the proxy, and fixed.** In nginx a `location` with an
> `add_header` of its own inherits none of the server's. So since RF-03 the
> console's document and assets were served without HSTS, the content
> security policy or X-Frame-Options. Both locations now set caching with
> `expires` instead.
>
> **Found, and recorded, not fixed:** RF-44. Each unit is a separate rootless
> container, so the proxy's upstream `127.0.0.1:8000` is the web container's
> own loopback rather than the API.
>
> **Results.**
> - The coverage-gated stages, each above its floor:
>   - unit: 1,660 tests at 90.21% (floor 90);
>   - contract: 516 tests at 89.30% (floor 87), including the proxy's TLS
>     tests against the pulled `cgr.dev/chainguard/nginx` base;
>   - integration: 227 tests at 77.29% (floor 77).
> - Unit, property and contract together: 2,188 tests. The one failure first
>   seen, `draupnir/api/serve.py` under no coverage floor, was fixed by
>   measuring it in the contract stage.
> - mypy across `draupnir` and `scripts` is clean. The import contracts hold
>   (7 kept). Shell syntax is clean; `shellcheck` is not installed on this
>   machine, so the shell lint did not run.
> - The acceptance pack is current (AC-S16 updated). The generated inventory's
>   TLS row reads in use, with mTLS to the API.
> - The console journeys and the a11y sweep were not re-run. They serve the
>   console from `serve-console.mjs` and run `uvicorn` directly, neither of
>   which RF-37 changes.

---

### RF-38 — P5 — The Storybook axe sweep races Storybook's own axe run

`web/e2e/a11y/components.spec.ts` loads each story's `iframe.html` and runs
`AxeBuilder` over it. `@storybook/addon-a11y`, registered in
`web/.storybook/main.ts`, runs axe on the same page itself, in an `afterEach`
hook once the story renders. When the addon's run had not finished, axe refused
the sweep's:

```
Error: frame.evaluate: Error: Axe is already running. Use `await axe.run()` to
wait for the previous run to finish before starting a new run.
  at AxeBuilder.analyze … at web/e2e/a11y/components.spec.ts:58
```

The stack runs through Storybook's bundled `assets/axe-*.js`, not an axe the
sweep injected. Seen on 14 September 2026 while RF-31 ran the a11y project:
shard 6 of 8 failed in one run of 73 tests, and identical runs immediately
before and after passed it.

A gate that fails on a race gets re-run until it is green, and a habit of
re-running is how a real violation gets waved through with the flake.

**Prompt**

> Make the sweep deterministic without weakening it. Stop the addon's automatic
> run on the pages the sweep loads, or wait for it to finish before `AxeBuilder`
> starts. Do not add retries. Keep the rule tags and the serious-or-critical
> threshold unchanged, and verify by running the a11y project several times in
> a row.

**Acceptance criteria**

- No axe run but the sweep's starts on a page the sweep loads, and a change
  that brings one back fails the shard every time rather than occasionally.
- The rule tags and the threshold are unchanged.
- The a11y project passes several consecutive runs.
- Gated in stage 2.8.

> **Status: done, by turning the addon's automatic run off on the sweep's
> pages.**
>
> **Confirmed before changing anything.** The addon's preview code (8.6.18)
> runs axe in `afterEach` unless the story's `a11y` parameter says `manual`,
> `disable` or `test: "off"`, or the `a11y.manual` global is set. A probe
> against the built Storybook loaded one story twice:
> - with `globals=a11y.manual:!true` on the URL, the addon fetched no axe
>   chunk and `window.axe` was undefined after rendering;
> - without it, the addon fetched its axe and `window.axe` was set.
>
> **The fix.** `components.spec.ts` loads every story with that global, so
> `AxeBuilder`'s run is the only one on the page.
> - No retries.
> - The rule tags and the serious-or-critical threshold are unchanged. The
>   tags are five, not the four the finding named: `wcag22aa` was already
>   there, and it stays.
> - Waiting for the addon's run was rejected: it depends on Storybook
>   internals to know when a run has finished, and it runs axe twice per story
>   for a result nobody reads.
> - Developers' Storybook is untouched: the global is only on the sweep's URLs,
>   and the addon's panel was never part of the gate.
>
> **Guarded, so it fails every time rather than sometimes.** The sweep records
> requests for the addon's bundled `assets/axe-*.js`, and fails the shard,
> naming the story and why, if one is made. `AxeBuilder` injects its own axe by
> evaluation, so any such request is the addon.
>
> **Verified.**
> - **Five consecutive runs** of `tasks.py test-a11y` passed 73 of 73 each,
>   with no "Axe is already running" and no guard trip.
> - **With the global removed**, the sweep failed all 8 shards, each on the
>   guard's message. The global was then put back.

---

### RF-39 — P4 — `draupnirctl` cannot perform a conditional write

*Found while working RF-32.*

`draupnirctl` is a generated client of the API, and every command goes through
one generic caller in `draupnirctl/cli.py`. It sends an `Idempotency-Key` on
every mutating request and never sends `If-Match`, and no command takes a tag.
`cancel-run`, `retry-run`, `decide-gate` and `publish-release` are conditional
writes, so the API refuses each of them with 428 every time.

Before RF-32 a caller could have reached them only with a tag computed over the
identifier, and nothing in the CLI computed one either. The console had the same
defect, and RF-32 fixed it there.

**Prompt**

> Give the generic caller an `--if-match` option on every operation that takes
> `If-Match`, and a way to act on what was just read: for a conditional command
> given no tag, read the resource's `ETag` first and send it, saying so. That
> way the command is conditional on what the operator last saw, rather than on
> nothing.

**Acceptance criteria**

- Each of the four conditional commands succeeds against a current resource, and
  is refused with 412 when the tag is stale.
- The generated command table carries the option, and the drift gate covers it.
- Gated in stage 2.3.

> **Status — done.**
>
> **Six commands, not four.** The OpenAPI document gives `If-Match` to six
> operations: `cancelRun`, `retryRun`, `decideGate`, `publishRelease`,
> `approveRetention` and `selectMergePoint`. The finding named the first four.
> `approve-retention` and `select-merge-point`, the two actions RF-27 added,
> were refused with 428 in the same way.
>
> **Where a tag comes from is the document's, not the CLI's.** A hand-kept map
> in `cli.py` would be a hand-written client method under another name. So
> `precondition_read` in `draupnir/api/concurrency.py` writes an
> `x-draupnir-precondition` extension beside each conditional route, naming
> the read that supplies its tag:
> - `cancelRun` and `retryRun` read `getRun`;
> - `decideGate` reads `getRun`, with `gate_id` passed as `run_id`, because a
>   gate is a run and the tag checked is the run's;
> - `publishRelease` reads `getLineage`;
> - `selectMergePoint` reads `getSweep`;
> - `approveRetention` reads the `listRetention` entry whose `id` is the
>   action, since no read returns a single action.
>
> **Generator.** `scripts/generate_cli.py` carries `if_match` and a
> `Precondition` into the command table. It refuses a document in which an
> operation takes `If-Match` and:
> - names no read;
> - names a read that is not a GET; or
> - names a read whose path parameters it cannot supply.
>
> `clients-check` compares the table, so the drift gate covers the option.
>
> **CLI.**
> - Every conditional command takes `--if-match`.
> - Given no tag, it calls the declared read, sends the tag that read returned,
>   and says on standard error which tag it sent and where from.
> - If the read fails, or the list holds no such entry, it sends nothing.
> - `--if-match` on an unconditional command is refused.
>
> **Tests,** stage 2.3, `tests/contract/test_cli_conditional_writes.py`. The
> commands run through Typer, with their HTTP routed into the application over
> the doubles the API's own conditional-write tests use. For each of the six:
> - given no tag, the command reads it and the write is accepted;
> - given a tag the state has since moved past, the write is refused with 412.
>
> Publication is the one exception to the first point. Its precondition
> passes, and then the publication is refused for a reason of its own,
> because the doubles hold no publication facts. A real publication through
> the API is `tests/integration/test_release_publication.py`'s.
>
> The table test derives the set of conditional operations from
> `docs/api/openapi.json`, so a seventh arrives covered or fails.
>
> **Results.**
> - The coverage-gated stages, each above its floor:
>   - unit: 1,660 tests at 90.22% (floor 90);
>   - property: 14;
>   - contract: 525 tests at 89.30% (floor 87), the CLI's nine among them;
>   - integration: 227 tests at 77.29% (floor 77).
> - mypy across `draupnir`, `scripts` and `draupnirctl` is clean. The import
>   contracts hold (7 kept).
> - The OpenAPI diff is additive only: six `x-draupnir-precondition`
>   extensions.
> - Regenerating the clients a second time changes nothing further. The
>   acceptance pack is current.
> - The first contract and integration runs failed on a stopped Docker daemon,
>   not on this change: every error was the Docker connection. Both passed once
>   Docker Desktop was running.

---

### RF-40 — P3 — The console's approval can never be accepted

*Found while working RF-32.*

S13's "Sign and approve" calls `decideGate` with
`signature: 'console-session'` and no `decidedAt`. The API refuses an approval
with no `decidedAt` as 422 `decision-undated`, before it looks at the signature
at all. Were the date supplied, the placeholder would then have to verify
against the approver's registered key (RF-06), and a literal string cannot. So
the approver's primary action cannot succeed from the console.

RF-32 made the console send the tag every decision needs. A rejection needs no
signature, so J3 now confirms one end to end. It still only opens the approval
dialog, because the approval cannot be confirmed.

**Prompt**

> Sign the approval where the approver's key is. Decide with the approver how the
> console reaches it — a hardware key through WebAuthn, or a local signing agent —
> build the signing payload the API verifies (`Approval.signing_payload()`),
> date it with the instant signed over, and send both. Where no key is reachable,
> say so on S13 rather than offering a control that cannot succeed.

**Acceptance criteria**

- An approval confirmed in the console is accepted and verified, and J3 confirms
  one end to end.
- An approver with no reachable key is told so before the dialog, not refused
  after it.
- Gated in stage 2.7.

> **Status — done.**
>
> **How the console reaches the key** was put to the user, as the prompt asks,
> and they chose a **local signing agent** over WebAuthn. The API's verifier is
> unchanged: an Ed25519 signature over `Approval.signing_payload()`, checked
> against the approver's registered public key.
>
> **The agent.** `draupnir/gleipnir/signing_agent.py` runs on the approver's
> own machine and holds their key.
> - **What it signs.** It builds the payload itself from the approval's fields
>   with `Approval.signing_payload()`, and dates it with the instant it signs.
>   It never signs bytes a caller supplies. It signs only approvals, and only
>   for the approver it was started for.
> - **Who it answers.** It listens on `127.0.0.1:47920` and answers only the
>   console origins it was started with. Its preflight grants those origins
>   CORS and Private Network Access.
> - **Where it lives.** It is in GLEIPNIR, which owns release sign-off. The
>   import contracts keep SVALINN and GLEIPNIR from importing each other, and
>   the agent needs the approval payload.
>
> **The API.** Each `listGates` row gains `signing`: the approver, the policy
> version and the sole approver flag, which are the payload fields only the
> server knows.
> - The flag is computed as `decideGate` computes it, from the submitter in
>   the run's facts, so the flag the agent signs is the flag the decision checks.
> - *Found in passing, and fixed:* the read model gave every queue row
>   `submittedBy: curator@veldris.internal`. The row now takes the submitter
>   from the same facts where they are read.
>
> **The console.**
> - **Before the dialog.** S13 asks the agent who it is, and states the answer
>   in the `signing-agent` line: still looking, no agent answering, nothing to
>   sign, the agent holds another approver's key, or which key it will sign
>   with.
> - **When approval is unavailable.** "Sign and approve" is read-only, and the
>   same reason is its accessible description.
> - **Confirming.** The agent signs, and the console sends the signature and
>   `decidedAt`. A rejection needs no agent.
> - **CSP.** The console proxy's `connect-src` names the agent's loopback
>   origin.
>
> **The journey stack.**
> - `scripts/development_approver.py` makes a development approver key once,
>   in the git-ignored `.dev/`, and registers its public half for
>   `dev@veldris.internal`.
> - `tasks.py test-e2e` and `test-a11y` pass Playwright the key store and the
>   agent's command. Playwright starts the agent beside the API.
> - The seed gains a third gate awaiting approval, `cim-fji-v0.1`, which J3
>   approves. `cim-aus-v0.1` stays pending, and `cim-nzl-v0.1` stays J3's
>   rejection.
>
> **Operators.** `docs/runbook.md`, Approving from the console, covers the key,
> its registration, starting the agent, and what S13 shows.
>
> **Tests.**
> - `tests/unit/test_signing_agent.py`.
>   - What the agent signs verifies as the API verifies it, and not once the
>     sole approver flag is changed.
>   - It refuses another approver, a rejection and malformed fields, and a key
>     that is not Ed25519 or a start with no origins.
>   - Over HTTP it refuses another origin, and answers the Private Network
>     Access preflight.
> - `tests/contract/test_console_approval_signing.py`.
>   - An approval the agent signs is accepted by `decideGate` and releases the
>     run.
>   - A signature over the wrong flag, or from another approver's key, is
>     refused with 422.
>   - The console's loop runs through the API: read the row, have the agent
>     sign its `signing` block, decide.
> - `Gates.test.tsx` and `signing.test.ts` (Vitest).
>   - With no agent answering, S13 says so before the dialog, approval is
>     read-only, and the button carries the reason.
>   - An agent holding the approver's key is named, and one holding another's
>     is said to.
> - J3, stage 2.7, confirms an approval end to end: it names the key before
>   the dialog, the decision returns 201, and the run is RELEASED.
>
> **Results.**
> - On a reset and reseeded development database (14 runs, 15 artefacts,
>   2 approvals, 1 release):
>   - journeys: 53 of 53, J3's end-to-end approval among them;
>   - a11y: 73 of 73.
> - The coverage-gated stages, each above its floor:
>   - unit: 1,678 tests at 90.04% (floor 90);
>   - contract: 529 tests at 89.15% (floor 87);
>   - integration: 227 tests at 77.29% (floor 77).
> - The unit stage's one failure was the reachability check. The signing agent
>   is started on the approver's machine, and nothing a deployment runs imports
>   it, so it is now a named entry point beside `draupnir.api.serve`.
> - Vitest: 13 across S13, the signing client and the shell. Typecheck, lint
>   and formatting are clean. The OpenAPI diff is additive only.
> - Two failures in the first runs were this machine's, not the change's:
>   - a `D:` drive briefly unreadable to Node;
>   - Docker Desktop's stale share of that drive, which failed the proxy's
>     bind mounts.
>
>   The contract stage passed once Docker Desktop was restarted, with the
>   user's agreement.

---

### RF-41 — P3 — Gate results are read from a table only the seed writes

*Found while working RF-33.*

`gate_result` is written by `scripts/seed.py` and by nothing in the
application. Three things read it:
- the approval queue, whose evidence table S13 puts above the decision
  controls (AC-U13);
- the model detail's gate list;
- `/metrics`, for gate pass rates and margins.

The worker records every gate outcome in the chain — the EVALUATING→MERGED
`gate_results`, each sweep point's evidence, and QUANTISED→AWAITING_APPROVAL's
`format_gate_results` — with the value, baseline, margin and suite version. So
on a seeded stack an approver reads the evidence before deciding. On an estate
the evidence table is empty, and the decision controls become available once
that empty table has been on screen.

RF-33 projected `artefact`, `approval` and `release` from the chain and left
this table, by agreement, for its own finding.

**Prompt**

> Project `gate_result` from the gate outcomes the chain records, beside
> RF-33's release projection. A projected row carries what the entry recorded
> — value, baseline, margin, suite version, pass or fail — and an outcome
> recording less than that is not a row. Have the seed record gate outcomes in
> the worker's shape and stop inserting rows.
>
> On S13, an artefact awaiting approval with no recorded evidence says so, and
> the decision is not offered on the strength of an empty table.

**Acceptance criteria**

- An integration test takes a run through evaluation with the worker's
  payloads, then reads its gates from the approval queue, the model detail and
  `/metrics`, with no row inserted.
- S13 with no evidence states that there is none and offers no decision.
- Gated in stages 2.4 and 2.7.

> **Status — done.**
>
> **The fold.** `draupnir/core/domain/gate_results.py` folds `gate_result`
> from the outcomes the chain records, beside RF-33's releases, and like them
> never reads the table it produces. It reads two payloads:
> - EVALUATING's `gate_results`, recorded on both the pass and the requeue;
> - QUANTISED→AWAITING_APPROVAL's `format_gate_results`, one evaluation per
>   built format.
>
> **What a row is.** An outcome that recorded a numeric value, a pass or
> fail, a suite version, and the baseline and margin keys. A gate with an
> absolute threshold records those two as null, so they must be present, not
> non-null. An outcome recording less is not a row; the seed's old
> `{"passed": true}` is the example.
>
> **Which outcome a row holds,** since the table keeps one per run, gate and
> suite version:
> - the latest entry wins, because a requeued run is evaluated again;
> - within a re-gate of several formats, the weakest outcome wins: failing
>   before passing, then the smallest margin. The row is what an approver
>   reads, and the best of three formats would hide a near miss.
>
> A merge sweep's per-point evidence is not folded. Those points are
> candidates; the chosen point's re-gate is recorded by later entries.
>
> **The projection.** `GateResultProjection` advances on every append, after
> the run registry and the releases. A rebuild clears the site's rows through
> its runs, because `gate_result` has no site column. The row identifier is
> derived from site, run, gate and suite version, so a rebuild reproduces it.
>
> **The seed** records each evaluation in the worker's shape:
> - EVALUATING→MERGED records a full `gate_results`;
> - QUANTISED→AWAITING_APPROVAL records `format_gate_results`;
> - it inserts no gate row, and rebuilds the projection instead.
>
> A run resting at EVALUATING has no outcome recorded yet, so it now shows no
> gates rather than invented ones.
>
> **S13.** An artefact awaiting approval with no recorded evidence says so, in
> the `no-evidence` line. Neither decision is offered: "Sign and approve" and
> "Reject" are both read-only, and each carries that reason as its accessible
> description. The decision's outcome is rendered outside the pending approval,
> because the approval leaves the queue when it is refreshed and used to take
> the message with it.
>
> **The run registry's rebuild.** `RunProjection.rebuild` deleted the site's
> runs and wrote them again. The projected gate results name those runs, so
> the delete was refused. It now rewrites every run the chain holds in place
> (a run's identity is its specification, so it is the same row) and removes
> only runs the chain no longer holds, together with their gate results.
>
> **The run board's event feed.** Found by this fix's journeys: J2 failed
> beside the other journeys and passed alone. `useEvents` kept the latest
> delta as state, and two frames dispatched in one task render once, so the
> board, which merged from `feed.last`, never saw the first. A new run whose
> delta arrived beside another run's never appeared. The hook now hands every
> delta to an `onDelta` callback, and the board merges from that.
>
> **Tests.**
> - `tests/unit/test_gate_result_projection.py`:
>   - the worker's evaluation becomes one row per gate, and an absolute gate
>     is still a row;
>   - an unmeasured outcome, one without its baseline key, and one without a
>     suite version are not rows;
>   - the latest evaluation wins, the weakest format wins within a re-gate,
>     and a failing format wins over any passing one;
>   - identifiers survive a rebuild.
> - `tests/unit/test_seed.py`: the dataset carries no gate rows, and folding
>   its chains yields rows for exactly the runs past evaluation.
> - `tests/integration/test_gate_result_projection.py` (stage 2.4) walks a
>   run through the real orchestrator with the worker's payloads and inserts
>   no row. It reads the gates back from:
>   - the approval queue;
>   - the model detail;
>   - `/metrics`.
>
>   It then rebuilds the run registry and finds the rows still there.
>   `test_procedures.py`'s rebuild of a released run is the same check against
>   the whole procedure.
> - `Gates.test.tsx` (Vitest, stage 2.6): with no evidence, S13 says so and
>   both decisions are read-only. `useEvents.test.ts`: two frames dispatched
>   in one task both reach the caller.
> - Stage 2.7: J3's evidence journeys read the seeded queue's gates, now
>   projected rather than inserted. J3's signed approval reads the outcome
>   after the approved artefact has left the refreshed queue. The seed holds
>   no artefact awaiting approval
>   without evidence, so the no-evidence state is gated by Vitest rather than a
>   journey.
>
> **Results.**
> - On a reset and reseeded development database (14 runs, 15 artefacts,
>   42 gate results, all projected, 2 approvals, 1 release):
>   - journeys: 53 of 53;
>   - a11y: 73 of 73.
> - The coverage-gated stages, each above its floor:
>   - unit: 1,687 tests at 90.04% (floor 90);
>   - contract: 529 tests at 89.15% (floor 87);
>   - integration: 228 tests at 77.49% (floor 77).
> - Vitest: 1,128. Typecheck, lint and formatting are clean. mypy across
>   `draupnir`, `scripts` and `draupnirctl` is clean, and the import contracts
>   hold (7 kept).
> - The first full run failed three ways, each now fixed:
>   - `test_procedures.py`'s projection rebuild, refused by the new rows'
>     foreign key (the run registry's rebuild);
>   - J3's signed approval, whose outcome left with the refreshed queue;
>   - J2's five-second board, which passed alone and failed beside the other
>     journeys (the run board's event feed).

---

### RF-42 — P3 — The licence register is read from a table only the seed writes

*Found while working RF-34.*

`registerSource` records a `source` entry in the chain and writes no `source`
row. Nothing else in the application writes that table; `scripts/seed.py` does.
Three things read it:
- `listSources`, S04's licence register;
- the lineage, which walks back to the sources of a jurisdiction;
- through the lineage, the training content summary and SBOM of every release
  package.

On a seeded stack a registered source appears in the register and a release's
lineage reaches its licensed roots. On an estate a source registered through
the console appears nowhere. A release's lineage reports no source, and its
training content summary is refused because the lineage holds none.

It is the state RF-33 found `artefact`, `approval` and `release` in, and RF-41
found `gate_result` in.

**Prompt**

> Project `source` from the entries `registerSource` records, beside RF-33's
> projections, so a registered source is read back from the chain. A source's
> state follows the licence decision taken on its corpus, where one has been
> recorded. Have the seed register its sources as the API does and stop
> inserting rows.

**Acceptance criteria**

- An integration test registers a source through the API and reads it from
  `listSources` and from a lineage, with no row inserted.
- Gated in stage 2.4.

> **Status — done.**
>
> **The fold.** `draupnir/core/domain/sources.py` folds the register from the
> `registered` entries `registerSource` records, beside RF-33's releases and
> RF-41's gate results, and like them never reads the table it produces. A row
> is a registration that recorded the facts HODD holds: a jurisdiction, an
> address, a declared licence, the attribution and personal data
> determinations, a digest, and an offset-aware retrieval time. A personal
> data determination without its DPIA reference is not a row. The table and the
> API both refuse one, so an entry carrying it is skipped rather than allowed
> to stop the fold.
>
> **What state a source holds.** Registered as DRAFT, a source follows its
> corpus. SAD 6.1 records a corpus's progress as transitions of the runs that
> consume it, and a run's corpus is the jurisdiction its name encodes. Each of
> these transitions, on a run of the source's jurisdiction at the source's
> site, moves the source to the same state:
> - DRAFT→CORPUS_REGISTERED;
> - CORPUS_REGISTERED→LICENCE_CLEARED;
> - CORPUS_REGISTERED→QUARANTINED;
> - LICENCE_CLEARED→CURATED.
>
> The latest wins. Only transitions recorded after the registration count: a
> licence decision taken before a source existed did not judge it.
>
> On an estate the procedure is still the only thing that takes a licence
> decision, which is RF-43. Until that is fixed a source registered there stays
> DRAFT, and that is what the chain says.
>
> **The projection.** `SourceProjection` advances on every append, after the
> gate results, so a source is in the register when `registerSource` returns.
> `source` has no row level security. Migration 0007 therefore adds a nullable
> `site_id`, and a rebuild clears only its own site's rows.
>
> **What the API records.** `registerSource` accepted `residencyConstraint` and
> recorded nothing of it, so a register folded from the chain would have shown
> every source unconstrained. It now records it. `jurisdiction_of` moved from
> `api.reading` to the domain so the fold can use it; the worker's retention
> duty imports it from there.
>
> **The seed.** It registers its sources as the API does, inserts no row, and
> rebuilds the projection. The seeded states are now the chain's rather than
> ones chosen beside it. `cim-fra-v0.1` is recorded as the licence refusal its
> record always described (CORPUS_REGISTERED→QUARANTINED, naming the failing
> source), instead of an approval rejected for one. Its source is therefore
> quarantined, and the seed holds 13 artefacts, 36 gate results and 1 approval.
> J3 still rejects an approval of its own at run time.
>
> **Tests.**
> - `tests/unit/test_source_projection.py`:
>   - a registration becomes a DRAFT row holding what it recorded;
>   - a source follows its corpus through the licence decision and curation,
>     and a refusal quarantines it;
>   - only its own jurisdiction's corpus moves it, and a decision recorded
>     before its registration does not;
>   - a run whose name encodes no jurisdiction moves nothing;
>   - each registration recording less than the facts is not a row;
>   - the seed's former `DRAFT->…` source entries are not registrations.
> - `tests/unit/test_seed.py`: the dataset carries no source rows, and folding
>   its chains yields all six with the chain's states.
> - `tests/integration/test_source_projection.py` (stage 2.4) registers two
>   sources through a real API process, with a run of their jurisdiction walked
>   through the orchestrator between the two registrations, and inserts no
>   row. It reads both from:
>   - `listSources`, where the earlier source is CURATED and the later DRAFT;
>   - the lineage of the artefact the run stored.
>
>   It also checks that the rows carry their site and residency, that a
>   rebuild reproduces them, and that the append itself projects a
>   registration.
>
> **Results.**
> - On a reset and reseeded development database, migrated through 0007
>   (6 sources, all projected: 3 curated, 2 draft, 1 quarantined; 14 runs,
>   13 artefacts, 36 gate results, 1 approval, 1 release):
>   - journeys: 53 of 53;
>   - a11y: 73 of 73.
> - The coverage-gated stages, each above its floor:
>   - unit: 1,706 tests at 90.08% (floor 90);
>   - contract: 529 tests at 89.15% (floor 87);
>   - integration: 230 tests at 77.57% (floor 77).
> - Vitest: 1,128. mypy across `draupnir`, `scripts` and `draupnirctl` is
>   clean, and the import contracts hold (7 kept). The acceptance pack is
>   regenerated, AC-Q6 now naming migration 0007.
> - A first integration run of a hand-picked subset of files, in an order the
>   stage does not use, failed `test_schema_constraints` on a ledger sequence
>   an earlier file had written at the same site. In the stage's own order,
>   all 230 pass.

---

### RF-43 — P1 — Nothing but the demonstration procedure takes a run's licence decision

*Found while working RF-34.*

SAD 6.1 moves a run DRAFT → CORPUS_REGISTERED → LICENCE_CLEARED before
curation. The LICENCE_CLEARED entry is where GLEIPNIR's licence policy is
applied and its version recorded. In the application:
- `submitRun` registers a run at DRAFT;
- the worker's stages begin at QUEUED;
- its curation duty waits for LICENCE_CLEARED → CURATED.

Neither the worker nor the API evaluates the licence policy: no stage, duty or
route calls a policy driver or `licence.by_version`.

Only `procedures/sindri.py` M1 and M2 do: they register sources, run the
policy over them, and move the run through the corpus states. So on an estate
a run submitted through the console or `draupnirctl` stays at DRAFT. The
journeys and the seed never meet this, because the seed writes chains that
have already been through those transitions, and the procedure walks its own
run.

RF-34 records the version where the decision is taken. This is that nothing
takes it outside a demonstration.

**Prompt**

> Give the worker the corpus half of SAD 6.1. A DRAFT run whose corpus sources
> are registered moves to CORPUS_REGISTERED. A CORPUS_REGISTERED run has
> GLEIPNIR's licence policy applied, through the `draupnir.policy` driver the
> deployment installs, to every source and to the base model. It moves to
> LICENCE_CLEARED recording the policy version and each decision, or to
> QUARANTINED naming the refusing rule (AC-S2).
>
> Take the logic from the procedure's M1 and M2 rather than writing it twice,
> and have the procedure call it.

**Acceptance criteria**

- An integration test submits a run through the API with registered sources
  and observes the worker take it to LICENCE_CLEARED, with the policy version
  recorded, and a run with a refused licence to QUARANTINED.
- The procedure and the worker share one implementation.
- Gated in stage 2.4.

> **Status — done.**
>
> **One implementation, in two halves.** The import contracts make the worker
> and the procedure independent siblings, so what they share sits a layer
> below both. It is split along Decision S4:
> - **HODD records.** `LicenceRegister.corpus_registration` builds what
>   DRAFT→CORPUS_REGISTERED records: every source's digest, a corpus digest,
>   the curator, and the guard's `sources_without_declaration`.
>   `LicenceRegister.from_projection` builds a register from the sources a
>   site's chain projects (RF-42).
> - **GLEIPNIR judges.** `draupnir/gleipnir/clearance.py` applies a
>   `draupnir.policy` driver to every source and to the base model, from the
>   facts it is handed. It returns where the corpus goes and what the
>   transition records.
>
> The procedure's M1 and M2 call both, and so does the worker.
>
> **What the decision does.**
> - **Every subject permitted:** LICENCE_CLEARED, recording the policy version
>   and each decision (subject, licence, verdict, rule, policy version).
> - **Any refusal:** QUARANTINED, naming the failing source or base and the
>   refusing rule (AC-S2). A verdict the decision does not recognise counts as a
>   refusal.
> - **An approval owed:** a source holding personal data leaves the corpus at
>   CORPUS_REGISTERED and records nothing. GLEIPNIR's own mapping puts such a
>   source there; the procedure used to quarantine it, recording a refusal
>   nobody made.
> - **No declared licence for the base:** the corpus waits, and says so.
>
> **The base model.** Nothing recorded a base's licence, and the procedure
> asserted `base_model_cleared`. As decided with the user, `hamarr.tiers`
> declares one per base, and the policy judges it the way it judges a source:
> - the Qwen base is declared `Apache-2.0`, as SAD's base selection states;
> - the Gemma base is declared `LicenseRef-Gemma-Terms-of-Use`.
>
> The licence policy in force has no rule for Gemma's terms and refuses them by
> default. **Every Tier A run is therefore quarantined at its licence decision**
> until a policy version decides otherwise. That is the policy's answer rather
> than this table's, and changing it is a policy change.
>
> **The driver.** Also as decided with the user, GLEIPNIR's policy in force is
> installed as a first-party `draupnir.policy` driver, `gleipnir.licence/v1`,
> through this distribution's entry point. The worker resolves the driver named
> by `DRAUPNIR_POLICY_DRIVER`, which defaults to it, through the production
> registry, whether or not the development executor is in use. The only
> driver installed before was the SPDX reference driver. Its version is not one
> `licence.by_version` holds, so a release it cleared would have had its
> copyright policy refused (RF-34). A driver that cannot be resolved defers the
> decision with the reason; it is never taken some other way.
>
> Installing the entry point needs the environment re-synced
> (`uv sync --all-groups`). On an estate the distribution is signed like any
> other plug-in's (SAD 9.3).
>
> **The worker.** DRAFT and CORPUS_REGISTERED join the stages table, worked
> before QUEUED, which stays last.
> - **DRAFT:** a run whose specification's jurisdiction has registered sources
>   at the site is moved to CORPUS_REGISTERED. One with none is left alone,
>   recording nothing.
> - **CORPUS_REGISTERED:** the decision above is taken, judging the base the
>   specification names.
>
> Curation from LICENCE_CLEARED remains a curator's.
>
> **Tests.**
> - `tests/unit/test_licence_clearance.py`, through GLEIPNIR's installed driver
>   and a driver whose verdicts the test chooses:
>   - clearance, and what it records;
>   - a refusal naming its rule, and a refusal by default;
>   - a refused base;
>   - an approval owed, and a refusal that wins over one;
>   - an unrecognised verdict;
>   - an undeclared base;
>   - no sources.
> - `tests/unit/test_corpus_registration.py`: the registration record, the
>   register built from a projection, and every base's declaration by name and
>   by address.
> - `tests/unit/test_worker_corpus.py`: both stages over each thing the chain
>   can hold, and the driver setting.
> - `tests/unit/test_worker.py`: the order, now including the corpus half.
> - `tests/integration/test_worker_licence_decision.py` (stage 2.4) registers
>   sources and submits four runs through a real API process. A real worker,
>   resolving the installed driver, takes each as far as its corpus allows:
>   - NZL is cleared with the policy version recorded, including the base's
>     decision;
>   - FJI is quarantined on `licence-refused`;
>   - GBR is quarantined on its base;
>   - TON stays at DRAFT;
>   - the register follows each decision (RF-42);
>   - a further tick records nothing.
> - The procedure's integration tests exercise the shared M1 and M2.
>
> **Results.**
> - The coverage-gated stages, each above its floor:
>   - unit: 1,737 tests at 90.20% (floor 90);
>   - contract: 529 tests at 89.15% (floor 87);
>   - integration: 232 tests at 77.52% (floor 77).
> - On the development database: journeys 53 of 53, a11y 73 of 73. Neither the
>   seed nor the console changed, so neither was reseeded or rebuilt.
> - mypy across `draupnir`, `scripts` and `draupnirctl` is clean, the import
>   contracts hold (7 kept), and `install.sh` parses. The acceptance pack is
>   regenerated: AC-S2 now cites the clearance module, the worker's stages and
>   both licence-decision tests.
> - The entry point needed the environment re-synced
>   (`uv sync --all-groups --frozen`); `uv.lock` is unchanged. Discovery then
>   resolves `gleipnir.licence/v1` beside the reference `gleipnir.spdx/v1`.
> - Two first-run failures were the tests' own and are fixed: a `Verdict`
>   member that does not exist, and a `RunFacts` built through `__dict__`, which
>   a slotted dataclass does not have.
> - The contract stage's four proxy tests failed on this machine's stale Docker
>   drive share, as in RF-40 ("mkdir /run/desktop/mnt/host/d: file exists").
>   With the user's agreement Docker Desktop was restarted, and
>   `tests/contract/test_transport.py` then passed 16 of 16.

---

### RF-44 — P1 — The console proxy cannot reach the API: its upstream is its own loopback

*Found while working RF-37.*

`docker/nginx.conf` sends the API's paths to `127.0.0.1:8000`: `http` since
RF-03, `https` since RF-37. `deploy/units/draupnir-run.sh` starts each unit as
its own rootless container, with no `--pod` and no `--network`, so each has its
own network namespace. Inside `draupnir-web`, 127.0.0.1 is the web container
itself. The API container publishes on the host's loopback, which the web
container cannot reach at that address.

So on a commissioned host every request the console proxies fails with 502,
and has since RF-03 put the proxy there. Nothing caught it:
- no test starts the units together;
- stage 3.1a starts the API image without serving it;
- the journeys serve the console from `serve-console.mjs` on the host.

RF-37's integration test reaches the API from the proxy container through
`host.docker.internal`, which is how it observes mTLS end to end. Its docstring
says the deployed upstream is not the address it uses.

**Prompt**

> Make the console proxy reach the API on a commissioned host. Either:
> - run the units in one pod, so they share a loopback; or
> - give the proxy an address that reaches the API from its container, such as
>   `host.containers.internal` where the API publishes on the host.
>
> Keep the name the proxy verifies the API's certificate under,
> `draupnir-api`, independent of the address. Say which option you chose, and
> why, in `deploy/README.md`.

**Acceptance criteria**

- A test starts the web and API images the way `draupnir-run.sh` starts them,
  and a request through the proxy reaches the API over mTLS.
- `docker/nginx.conf`'s upstream is the address that test uses.
- Gated in stage 3.1a, which already starts a built image.

> **Status — done.**
>
> **The option, and why.** The prompt offered a pod or a reachable address.
> This is the address: the units share a container network `draupnir-run.sh`
> creates, the API answers on `10.89.100.10` and the console on
> `10.89.100.20`, and `deploy/lib.sh` is where both are decided. Three reasons,
> written out in `deploy/README.md`:
> - **A pod has a lifecycle neither service manager owns.** systemd and launchd
>   start the three units independently and in no order; a pod would have to
>   exist before the first one starts, and the published ports would move from
>   the units to it.
> - **`host.containers.internal` does not reach this API.** It publishes on the
>   host's loopback by design, which the host gateway address does not reach.
>   Reaching it that way would mean publishing the API beyond loopback, and the
>   binding is what SAD 8.1 rests `/metrics` on.
> - **An address rather than a name, because the proxy must start without the
>   API.** nginx resolves a name in a `proxy_pass` variable only through a
>   `resolver`, and this nginx refuses `resolver local=on`; an `upstream` block
>   resolves at start and then refuses to start while the API is down. The
>   console's error surface when the API is gone (AC-U14) is the one thing an
>   operator has then, and the unit templates say so in as many words.
>
> The name the API's certificate is verified under is untouched:
> `proxy_ssl_name draupnir-api`, independent of the address, as the prompt asks.
>
> **What changed.**
> - `deploy/lib.sh`: the network, its subnet, and each unit's address.
> - `deploy/units/draupnir-run.sh`: creates the network if it is absent --
>   asking first and tolerating the creation's own failure, so two units
>   starting together is one network and one harmless refusal -- and joins it
>   with the unit's address. The worker joins nothing: it reaches the database,
>   the vault and MEGINGJORD, and no unit reaches it.
> - `docker/nginx.conf`: the upstream is the API's address.
> - `docs/runbook.md`: what a console that loads while every request in it
>   answers 502 means, and the three commands that tell an operator which of
>   the two it is.
>
> **The gate.** `python tasks.py images-transport`, stage 3.1a beside the step
> that starts the API image alone. It starts both built images on that network
> at those addresses, with a throwaway estate's TLS material mounted where both
> read it, and asks the console for `/healthz` -- a path only the API answers.
> The API image serves a stand-in application through `draupnir.api.serve`,
> which is the image's own command: the hop is what is under test, and
> `create_app` would open a database stage 3 does not have. The addresses come
> from `deploy/lib.sh` rather than from a second copy here.
>
> It reports itself skipped, with the reason, when the images are not loaded:
> the pipeline builds them with `--load` at stage 3.1, and a developer's
> `make ci` builds them to the cache.
>
> **Tests.**
> - `tests/contract/test_deploy.py`: the API and the console join the network
>   and answer on the addresses `lib.sh` gives them, driven through the wrapper
>   rather than read; the worker joins no network; the wrapper creates the
>   network it needs; and `docker/nginx.conf`'s upstream is the address the API
>   answers on, which is the whole of the defect.
> - Stage 3.1a starts the two images together, which is what nothing did.
>
> **Results.**
> - The gate passes against freshly built images: `python tasks.py
>   images-transport` starts both on the network at the addresses `lib.sh`
>   gives them and reports "the console proxied /healthz to the API over
>   mTLS". That request is the one that answered 502 on a commissioned host.
>   It could not be made at all until RF-45, because the console image would
>   not build.
> - `tests/contract/test_deploy.py`: 122 pass, the network assertions among
>   them. `tests/contract/test_transport.py`: 16 pass against the edited
>   configuration, so the proxy still terminates TLS 1.3 only and still
>   presents its client certificate. The inventory reads the file unchanged:
>   `terminates_tls13_only` and `upstream_mtls` both true.
> - The coverage-gated stages: unit 1,737 at 90.20%, contract 535 at 89.15%,
>   integration 231 of 232 -- the one failure being the readiness probe's
>   timing, which RF-45's results describe and which fails the same way with
>   this work stashed.
> - `bash -n` passes on both edited scripts, and `lib.sh` answers `draupnir`,
>   `10.89.100.0/24`, `10.89.100.10` for the API, `10.89.100.20` for the
>   console and nothing for the worker.
> - The journeys and the a11y sweep were not re-run: they serve the console
>   directly rather than through this proxy, and nothing they exercise changed.

---

### RF-45 — P1 — The console image cannot be built: it does not carry the file its build reads

*Found while working RF-44.*

`web/apps/console/vite.config.ts` reads `scripts/proxied-prefixes.json` when the
configuration loads — the list of API prefixes both development servers and the
deployed nginx proxy, established by RF-36. `docker/web.Dockerfile` copies the
workspace directory by directory: `package.json`, the lockfile, the workspace
file, `packages/`, `apps/` and `tsconfig.base.json`. It does not copy
`web/scripts`.

So `pnpm run build` inside the image stops at `vite build` with
`ENOENT: no such file or directory, open '/src/scripts/proxied-prefixes.json'`,
and the console image cannot be built at all. Since `ad9f8ff`, which added the
file.

Nothing caught it:
- the console builds on a developer's machine from the checkout, where the file
  is beside the configuration that reads it;
- stage 3.1 builds the image, and a build failure is a red pipeline rather than
  a test that says what broke;
- stage 3.1a started the API image only, so nothing ever started the console's.

A file read by a configuration rather than imported by a module appears in no
dependency graph, which is why directory-by-directory copying missed it and why
the check below reads what the configuration opens.

**Prompt**

> Carry into the image every file the console's build reads. Hold the
> Dockerfile to it with a test that reads what `vite.config.ts` opens at build
> time, rather than a list somebody remembers to extend.

**Acceptance criteria**

- The console image builds.
- A test fails if the configuration reads a workspace path the image does not
  copy.
- Gated in stage 1, which runs the contract tests, and at stage 3.1, which
  builds the image.

> **Status — done.**
>
> `docker/web.Dockerfile` copies `web/scripts`, with the reason written beside
> it: a build input that nothing imports.
>
> `tests/contract/test_deploy.py` reads the `new URL('../../…')` references out
> of `vite.config.ts` and fails unless the Dockerfile copies the directory each
> one names. A list of files would have been a second place to remember; this
> reads the first.
>
> **Results.**
> - `docker buildx build --platform linux/arm64 -f docker/web.Dockerfile` now
>   completes. Before the fix it stopped at `vite build` with
>   `ENOENT: no such file or directory, open
>   '/src/scripts/proxied-prefixes.json'`, which is what stage 3.1 would have
>   done on every run since `ad9f8ff`.
> - The coverage-gated stages: unit 1,737 at 90.20%, contract 535 at 89.15%
>   (the new test among them), integration 231 of 232.
> - The one integration failure is not this change:
>   `test_degraded_modes.py::test_the_api_reports_degraded_readiness_when_the_database_is_gone`
>   fails the same way with the working tree stashed. It allows `/readyz` ten
>   seconds, and against a database at a port nothing answers on it took 10.6s
>   and 11.8s in two measurements here. The answer itself is the one SAD 11.2
>   row 5 asks for --
>   `{"status":"degraded","checks":{"database":false,"object_store":true}}`.
>   Where the time goes is *not* the database check: it fails in milliseconds
>   (`WinError 1225`, the connection refused, logged as
>   `readiness.check.failed`), and `readiness.CHECK_TIMEOUT_SECONDS` bounds
>   every check at two seconds. So something else in that request accounts for
>   the other eight, and this has not established what.
>
>   That is worth a finding of its own rather than a line here: an
>   orchestrator's readiness deadline is shorter than ten seconds, so the
>   degraded answer would not reach the operator it is for. Left open, with the
>   measurements above -- and since opened as RF-46 and closed there.

---

### RF-46 — P4 — The first readiness probe of a process does work its timeout cannot bound

*Found while working RF-45.*

`test_degraded_modes.py::test_the_api_reports_degraded_readiness_when_the_database_is_gone`
starts the API against a database at a port nothing answers on, and allows
`/readyz` ten seconds. It failed on this machine, at 10.6s and 11.8s in two
measurements, while giving the answer SAD 11.2 row 5 asks for: degraded, with
the database false.

The checks themselves were not the cause:
- `readiness.CHECK_TIMEOUT_SECONDS` bounds each at two seconds, and they run
  concurrently;
- a connection to a closed port on `127.0.0.1` is refused after 2.03s on
  Windows, measured raw and through asyncpg, which is inside that bound;
- in-process, and under a real `uvicorn` process, every `/readyz` answered in
  2.0 to 3.0s.

The slow answers came early in a fresh process, and only while Windows Smart
App Control was on and refusing a native module in this environment (recorded
under RF-30's re-run). With it off, the integration test passes in 4.9s.

What that path did on a first probe and never again is the finding. Once the
application has started, and before any probe, `minio` is not loaded: the
object-store check imported it and built a client inside the probe. The vault
check did the same with two HODD modules. An import is the one piece of work a
check's timeout cannot bound -- it can hold the interpreter while a scanner or a
cold disk makes it slow -- and the first probe is the one an orchestrator sends
while the process comes up, against its shortest patience. Whether that import
is what stalled could not be reproduced with Smart App Control off; that it is
first-use work on a path with a deadline does not depend on it.

**Prompt**

> Do every readiness check's setup at startup, so that no probe pays for an
> import or a client construction. Keep a failure to set up a degraded check
> rather than a process that will not start, and hold the rule with a test that
> reads the checks rather than trusting that nobody adds an import to one.

**Acceptance criteria**

- The object-store client is built when the lifespan makes the probe, and a
  probe builds none.
- A client that cannot be built leaves a probe that reports the object store
  unreachable, and the API still starts.
- No check contains an import statement.
- `test_degraded_modes.py`'s readiness test passes.
- Gated in stages 2.1 and 2.4.

> **Status — done.**
>
> **The object store.** `readiness.object_store_probe` imports `minio` and
> builds the client when the lifespan makes the probe, once. The probe only
> asks whether the bucket exists.
>
> A client that cannot be built -- an endpoint `Minio` refuses, say -- logs
> `readiness.object_store.unconfigurable` with the reason and leaves a probe
> that reports `false`. A misconfigured object store is then a degraded check,
> which is what SAD 11.2 asks for, rather than an API that refuses to start.
>
> **The vault and the database.** Their imports move to the module. HODD's
> reconciliation and stores are imported with readiness, not on a vault's
> first probe, and so is SQLAlchemy's `text`, which was already loaded but was
> still an import statement inside a check.
>
> **Held by a test that reads the source.**
> `tests/unit/test_readiness_setup.py` walks `readiness.py` and fails on any
> import inside a check, or inside what `object_store_probe` returns. Beside
> it: the client is built when the probe is made and never by a probe, a
> client that cannot be built degrades, and a configured vault means no bucket
> is probed.
>
> **Results.**
> - The coverage-gated stages: unit 1,743 at 90.25% (the four new tests among
>   them), contract 535 at 89.15%, integration 232 of 232 at 77.52%.
>   `test_degraded_modes.py`'s readiness test passes, and so do its other 19.
> - mypy is clean, the import contracts hold (7 kept) with `readiness`
>   importing HODD at module level, and the application imports.
> - The acceptance pack regenerates unchanged.
> - The diagnosis's measurements, for the record: a refused connection to
>   `127.0.0.1` took 2.03s raw and through asyncpg; in-process and under a real
>   `uvicorn` process every `/readyz` answered in 2.0 to 3.0s; before any probe
>   ran, `minio`, `draupnir.hodd.reconcile` and `draupnir.hodd.stores` were not
>   loaded.
> - A first run of `tests/contract/test_api_surface.py` alongside the
>   readiness tests failed seven authorisation tests, because that invocation
>   set `DRAUPNIR_DEV=1`, which the contract stage does not. Without it, 57 of
>   57 pass.

---

### RF-47 — P3 — The visual gate forgives more than a regression costs

RF-22 made stage 2.9 compare instead of recording into a container it threw
away. It compares, and it still passes the thing it exists to catch.

`web/playwright.config.ts:42` set `maxDiffPixelRatio: 0.01`. On a 1280x730
story that is 9,344 pixels of licence. A component one pixel taller than its
token — `.jg-gauge__track`, the capacity gauge's bar — was pushed to a branch
and opened as a pull request against `dev`: stage 2.9 **passed**, on all eight
shards, against the Linux baselines committed hours earlier. Reaching the
budget took a 40px change, which differed by 39,342 pixels at ratio 0.042.

So the gate's tolerance was wider than the defects it was placed there to
catch, which is RF-22's own shape in a third costume: something occupying the
place where a person would otherwise notice.

**Prompt**

> Take the pixel budget out of the visual comparison, and hold it out.
>
> `maxDiffPixelRatio` and `maxDiffPixels` both go to zero. Leave `threshold`
> at Playwright's default, which compares pixels perceptually rather than
> byte for byte, so anti-aliasing inside a pixel is still absorbed and
> anything that moves the layout is not.
>
> Then satisfy RF-22's third acceptance criterion with the budget gone: a
> deliberate one-pixel change, watched failing stage 2.9 on CI, recorded.

**Acceptance criteria**

- The visual comparison grants no pixel budget, proportional or absolute.
- A test asserts that, reading the configuration rather than its source text.
- Stage 2.9 passes on unchanged code with the budget gone, on the runner.
- A deliberate one-pixel change fails stage 2.9 on CI, watched and recorded.

> **Status: done.**
>
> **What the budget was hiding, measured.** With the budget removed, the 220
> committed `-win32.png` baselines no longer match what this machine renders:
> every one of the eight shards fails, the first story in each differing by
> between 1,488 and 7,536 pixels — all of it under the old 9,344-pixel licence
> and therefore invisible for as long as that licence existed. Whether the
> same is true of the runner's own baselines, recorded hours earlier on the
> architecture that diffs them, is the question the next pipeline run answers,
> and it is the honest order: assert the property, then find out.
>
> **The number is asserted, not commented.** `web/tests/visual-tolerance.test.ts`
> imports the configuration object and reads the value, because a regular
> expression over the source would pass just as happily on a commented-out
> line. `maxDiffPixels` is asserted alongside the ratio: it is the same licence
> counted absolutely, and either one alone would let the other back in. A
> budget is exactly the kind of thing that returns — one line, it makes a red
> run green, and the reason it was zero lives in a commit message nobody reads
> at the moment they are tempted.
>
> **Watched failing, on CI, with the budget gone.** The same one-pixel branch
> that had passed was merged up to the strict configuration and pushed to the
> pull request. Stage 2.9:
>
> ```
> 2 failed
>   Storybook baselines, shard 6 of 8
>     1006 pixels (ratio 0.01 of all image pixels) are different.
>     Expected: composites-capacity-gauge--read-only-visual-linux.png
>   Storybook baselines, shard 7 of 8
>     1006 pixels (ratio 0.01 of all image pixels) are different.
>     Expected: composites-capacity-gauge--ready-visual-linux.png
> 6 passed
> ```
>
> Two properties, from the one run. The gate fails on a component one pixel
> taller than its token — RF-22's third acceptance criterion, and this one's.
> And the six shards that passed carry 218 stories that matched their Linux
> baselines *exactly*, with no budget at all, so zero tolerance is a gate
> rather than a source of noise on the platform that does the diffing. That
> second property is the one that could not be assumed, and it is why the
> budget was asserted before it was proved rather than after.
>
> The pull request was closed unmerged and its branch deleted; the run is its
> record.
>
> **What this leaves open: the win32 baselines are stale.** With the budget
> gone, every one of the eight shards fails on this machine against the
> committed `-win32.png` files, the first story in each differing by 1,488 to
> 7,536 pixels. All of it sat under the old 9,344-pixel licence, so it accrued
> unseen. The runner's own baselines match to the pixel, which says the drift
> is the developer platform's and not the components'. Re-recording them is one
> `make test-visual` on a win32 machine with the files moved aside, and it is
> the same rule as the Linux half: a person looks at what changed and commits
> it on purpose. Until somebody does, stage 2.9 is a CI gate that a developer
> cannot run locally and have pass.

---

### RF-48 — P1 — The API image starts and cannot create a database engine

Stage 3.1a, the check RF-E23 added because nothing ever started a built image,
had never run: the job always stopped earlier. Once stage 2.9 passed for the
first time the pipeline reached it, and it failed:

```
ImportError: no pq wrapper available.
- couldn't import psycopg 'c' implementation: No module named 'psycopg_c'
- couldn't import psycopg 'binary' implementation: libz.so.1: cannot open
  shared object file: No such file or directory
- couldn't import psycopg 'python' implementation: libpq library not found
```

`psycopg[binary]`'s wheel bundles libpq, OpenSSL, krb5, ldap, SASL and the
rest; manylinux leaves zlib to the system. `gcr.io/distroless/cc-debian12`
does not carry it and has no package manager to add it. Inspected in the
builder stage, `libz.so.1` is the wheel's only external need beyond glibc.

Every import in the image succeeds. SQLAlchemy loads the DBAPI when an engine
is *created*, and both engines are created in the lifespan — so the deployed
API would have started, reported itself up, and failed on the first thing that
touched the database. This is precisely the failure RF-E23's check was written
for, one release later than it should have been caught.

**Prompt**

> Carry the library into the runtime image and hold it there.
>
> Stage the resolved `libz.so.1` in the builder, where there is a shell, and
> copy it into a loader default directory in the runtime stage, which has
> neither shell nor `ldconfig`. Then a contract test, because the copy is one
> line whose absence is invisible until something creates an engine.

**Acceptance criteria**

- The built image creates both engines, async and sync.
- A contract test fails if the runtime stage stops carrying the library.
- Stage 3.1a passes on CI.

> **Status: done.**
>
> **The library is staged, not guessed at.** The builder resolves the symlink
> with `cp -L` into `/extra`, and the runtime copies it to `/usr/lib`, which is
> one of the loader's own default directories: no `ld.so.cache` to rebuild, no
> `ldconfig` to run it, and no architecture in the path — the Debian multiarch
> directory is named after the architecture and the runtime stage has no shell
> to work out which.
>
> **Verified by running the pipeline's own check.** The image was rebuilt and
> the exact script from stage 3.1a run against it: `create_app`, both engines,
> and the worker module all load. This machine builds aarch64 natively, which
> is the architecture the runner uses, so that is the same check rather than an
> emulated approximation of it.
>
> **The contract test asserts the runtime stage, not the file.** It reads what
> follows `AS runtime` in `docker/api.Dockerfile`, so a copy that lands only in
> the builder — where it changes nothing — does not satisfy it. Stage 3.1a
> remains the real gate, since it starts the image; the test exists so that the
> next person to change the base image learns why the line is there.
>
> **What this says about stage 3.1a.** The step was added, was correct, and had
> never executed, because the job stops at the first failure and the stages
> before it were red for unrelated reasons. A check that has never run is a
> check nobody knows works — which is RF-22's lesson arriving from the
> opposite direction, and the reason the whole of stage 3 is now worth
> watching rather than assuming.

---

## 5  Summary

| ID | Severity | Finding |
|---|---|---|
| RF-01 | P1 | No authentication layer; the API 401s everything in production — **done** |
| RF-02 | P1 | No signature verifier wired; zero plug-ins load in production — **done** |
| RF-03 | P1 | No reverse proxy, no TLS; `/auth/login` does not exist — **done** |
| RF-04 | P1 | No image is published; rollback passes a version string as a revision — **done**; image signing outstanding |
| RF-05 | P2 | `publishRelease` enforces none of AC-S8, AC-F9 or AC-S13 — **done** |
| RF-06 | P2 | Approval signatures unverified; approver-role fact hard-coded — **done** |
| RF-07 | P2 | Nothing anchors the chain; the anchor control never blocks — **done** |
| RF-08 | P2 | Artefacts never reach HODD or MinIO; the S3 seal is in-process — **done** |
| RF-09 | P2 | Secrets broker, egress allow-list and sandbox profile uncalled — **done** |
| RF-10 | P3 | The worker dispatches a stand-in; evaluation is synthetic — **done** |
| RF-11 | P3 | `submitRun` validates nothing; the tier table is unconsulted — **done** |
| RF-12 | P3 | Corpus ingest and curation are accepted and never performed — **done** |
| RF-13 | P3 | The 56-element array is not built; `getArray` fabricates it — **done**; the requeue refuses over slurmrestd |
| RF-14 | P4 | The idempotency store is process-local — **done** |
| RF-15 | P4 | The event stream is process-local, one-kind, and not a stream — **done** |
| RF-16 | P4 | `listApprovals` and `listModels` ignore their cursor — **done**; a malformed cursor was silently ignored too |
| RF-17 | P4 | `readyz` checks one dependency and builds an engine per probe — **done** |
| RF-18 | P4 | `/metrics` exposes no DRAUPNIR metric; traces go nowhere — **done**; two worker-side metrics await a scrape surface on the worker |
| RF-19 | P5 | Eleven coverage targets collect nothing — **done**; floors raised to 91/87/81 |
| RF-20 | P5 | HODD and GLEIPNIR are under no coverage floor — **done**; every shipped module is measured, floors 90/87/77 |
| RF-21 | P5 | The breaking-change gate has no baseline — **done**; a parameter's schema was never compared either |
| RF-22 | P5 | The visual regression gate never gates in CI — **done**; 220 `-linux` baselines recorded on the runner and committed by hand, stage 2.9 compares for the first time, and a one-pixel regression was watched failing it on CI once RF-47 took the pixel budget away |
| RF-23 | P5 | `clients-check` fails on any CRLF checkout — **done**; it could not run at all since RF-01, and both clients had drifted by three operations |
| RF-24 | P5 | `tasks.py ci` is not the pipeline — **done**; the pipeline never generated the cryptographic inventory it uploads |
| RF-25 | P5 | The secret scan cannot run on the documented Windows path — **done**; it diagnoses the mount failure and never passes without scanning |
| RF-26 | P5 | Two frontend advisories sit below the audit threshold — **done**; there were three, the third high, and all are fixed rather than accepted |
| RF-27 | P6 | Nine screens have a primary action with no operation behind it — **done**; four built, five read only by proposal, and S06 and S15 were fabricating what they showed |
| RF-28 | P6 | CON-B reports neither thermal nor fabric bandwidth — **done**; thermal was RF-E15's, and the fabric reading had no export and its baseline was read under the wrong name |
| RF-29 | P6 | The open keyboard finding K-1 is still open — **done**; unavailable controls stay in the tab ring, and activating one is shown to do nothing |
| RF-30 | P6 | Three documents state counts and controls the code does not have — **done**; figures and reachability are now derived and tested, and transport security is marked NOT BUILT (RF-37) |
| RF-31 | P6 | Committed acceptance evidence is non-deterministic — **done**; run records are not committed, the pipeline writes and uploads both, and stage 2.8 fails if one shows up as a change |
| RF-32 | P2 | A conditional write is conditional on nothing; the console's four conditional actions cannot succeed — **done**; each tag is over the state the write changes, the reads return it, and the console sends it |
| RF-33 | P3 | The release package is read from a table only the seed writes — **done**; artefacts, approvals and releases are projected from the chain, and a decision records its artefact |
| RF-34 | P3 | A release does not record the licence policy it was judged under — **done**; the decision's version is carried to the release, and the copyright policy is rendered under it or refused |
| RF-35 | P4 | The console's default specification is refused by its own API — **done**; the default takes its tier, base and site from a table generated from the API's and from `/healthz`, and J2 uses it; an over-budget checkpoint interval is a 422 rather than a 500, and the board reads a run it has not seen rather than labelling it by identifier |
| RF-36 | P5 | The journey stack does not route `/auth`, so sign-in cannot pass — **done**; both development servers read one list of proxied prefixes, `/auth` among them, and a test holds it to nginx |
| RF-37 | P2 | SAD 9.5's transport security is not built: nothing terminates TLS and nothing uses mTLS — **done**; the console proxy terminates TLS 1.3 only, the API and MEGINGJORD require the certificates the proxy and GULLINBURSTI present, and the inventory row reads the proxy's configuration |
| RF-38 | P5 | The Storybook axe sweep races Storybook's own axe run — **done**; the addon's automatic run is off on the sweep's pages, and the shard fails if it ever starts again |
| RF-39 | P4 | `draupnirctl` cannot perform a conditional write: it never sends `If-Match` — **done**; all six conditional commands take `--if-match`, or read the tag from the read the OpenAPI document declares for them |
| RF-40 | P3 | The console's approval can never be accepted: no `decidedAt`, and a placeholder signature — **done**; the approver's local signing agent signs the payload the API verifies, S13 says before the dialog when no key is reachable, and J3 approves end to end |
| RF-41 | P3 | Gate results are read from a table only the seed writes, so an estate's approval queue shows no evidence — **done**; `gate_result` is projected from the outcomes the chain records, the seed inserts no row, and S13 offers no decision on an empty evidence table |
| RF-42 | P3 | The licence register is read from a table only the seed writes, so a registered source appears nowhere — **done**; `source` is projected from the registrations and the corpus transitions the chain records, the seed inserts no row, and `registerSource` records its residency constraint |
| RF-43 | P1 | Nothing but the demonstration procedure takes a run's licence decision, so a submitted run stays at DRAFT — **done**; the worker registers a submitted run's corpus and takes GLEIPNIR's licence decision through the installed `draupnir.policy` driver, sharing one implementation with the procedure; base licences are declared, and Tier A's Gemma terms are refused by the policy in force |
| RF-44 | P1 | The console proxy cannot reach the API on a commissioned host: its upstream 127.0.0.1 is its own container's loopback — **done**; the units share a container network with fixed addresses, the proxy passes to the API's, and stage 3.1a starts both images and proxies a request through to it over mTLS |
| RF-45 | P1 | The console image cannot be built: `web.Dockerfile` does not carry the file `vite.config.ts` reads — **done**; the image copies `web/scripts`, and a contract test holds it to what the console build reads |
| RF-46 | P4 | The first readiness probe of a process does work its timeout cannot bound — **done**; every check's setup happens at startup, a failed setup degrades, and a test reads the checks for imports |
| RF-47 | P3 | The visual gate forgives more than a regression costs: 9,344 pixels of licence per story — **done**; the budget is zero, a one-pixel change was watched failing stage 2.9 on CI while 218 other stories matched exactly, and a test reads the configuration so the budget cannot return; the win32 baselines are stale and need re-recording by hand |
| RF-48 | P1 | The API image starts and cannot create a database engine: distroless carries no `libz.so.1` — **done**; the builder stages the library and the runtime carries it, a contract test holds the runtime stage to it, and the pipeline's own check passes against the rebuilt image |

---

## 6  Sequencing

**First, because nothing else can be verified end to end without them.**
RF-01, RF-02, RF-03, RF-04. Until these land there is no configuration in which
the platform serves an authenticated request, loads a driver, is reachable from
its own console, or can be deployed and rolled back. Everything below is
currently unobservable in a real deployment.

**Second, the controls that are claimed and absent.** RF-05, RF-06, RF-07,
RF-08, RF-09. These are the findings with the widest gap between what the
documentation asserts and what happens, and an assurance system whose assurance
is unenforced is the failure with the worst consequences.

**Third, the run pipeline.** RF-10, RF-11, RF-12, RF-13. The platform's purpose
sentence is to "turn one shared substrate into fifty six derived models"; these
four are what stands between the current system and doing that once.

**Fourth, the edge contracts.** RF-14 through RF-18. Each becomes a live defect
the moment more than one process runs, which is the deployment SAD 5.1
specifies.

**Fifth, the gates.** RF-19 through RF-26. These should arguably be first —
several would have caught findings above — but they are cheap and independent,
and doing them first would only turn the build red while the work above is in
flight. Do them alongside.

**Found on the way, and not to wait for the end.** RF-32 to RF-36 surfaced while
RF-27 ran the journeys for the first time since RF-11. RF-32 belongs with the
second group — a control claimed and unenforced, and four console actions that
cannot succeed. RF-33 and RF-34 belong with the run pipeline, because a release
nobody can read back is the last step of it. RF-35 and RF-36 are small, and
until they land two journeys stay red for reasons that have nothing to do with
whatever change is being tested. RF-37 surfaced while RF-30 re-marked the
reconciliation, and belongs with the second group: a transport the
specification requires, which the installer insists on a certificate for and
nothing serves. RF-38 surfaced during RF-31 and is done. RF-39 and RF-40
surfaced while RF-32 made the console's conditional writes succeed. RF-39 belongs
with the edge contracts: the CLI's four conditional commands still cannot
succeed. RF-40 belongs with the second group: the approver's primary action in
the console still cannot succeed. RF-41 surfaced while RF-33 projected the
release tables, and belongs with the run pipeline beside RF-33: an approver on
an estate is shown no gate evidence. RF-42 and RF-43 surfaced while RF-34
carried the licence decision to the release. RF-42 belongs with RF-41, one more
table only the seed writes. RF-43 belongs with the first group: until it lands,
a run submitted through the console never reaches the pipeline the rest of this
register repairs. RF-44 surfaced while RF-37 started the proxy for the first
time, and belonged with the first group: until it landed, a commissioned
console answered every request it proxied with a 502. RF-45 surfaced while
RF-44 started the console image for the first time, and belonged with it: the
image could not be built. RF-46 surfaced while RF-45's stages ran, and belongs
with the edge contracts: it is the probe an orchestrator acts on.

**Throughout, the documentation.** RF-27 through RF-31 should be amended as each
finding is closed, not batched at the end. When this was written the
reconciliation could not say which modules a running deployment reaches. RF-30
gave it the mark, *reachable*, derived by `scripts/reachability.py` and held to
the document by a test. It was re-run once RF-45 had closed, and RF-27 to
RF-31 were amended with it: see the amendment at the end of each.

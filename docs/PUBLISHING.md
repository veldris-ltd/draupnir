# Publishing

DRAUPNIR is internal software. **The control plane is never published to a
public index.** One thing goes to PyPI and one thing only: a name reservation
that contains no code.

---

## 1. Why there is a reservation at all

The name `draupnir` on PyPI belongs to
[an unrelated project](https://pypi.org/project/draupnir/) — an ancestral
sequence reconstruction tool, published first and still maintained. It cannot
be claimed, and asking for it under PEP 541 would mean asking a researcher to
give up a name they were using before us.

So the control plane is distributed as **`veldris-draupnir`**. The import name
is unchanged: `import draupnir` and the `draupnirctl` console script work
exactly as before. Only the distribution name carries the prefix.

That leaves one hole. `veldris-draupnir` is free today, and a name that is free
today can be taken tomorrow by anyone — at which point a misconfigured runner,
a fresh virtual environment or a mistyped `pip install` fetches their code
under our name. The reservation closes it.

The rule this establishes is worth stating plainly:

> Every first party distribution is named `veldris-…`. A distribution without
> that prefix is not ours, whatever its import name suggests.

`tests/unit/test_distribution.py` enforces it, and fails the build if a
distribution named `draupnir` is ever installed.

## 2. What the reservation contains

`packaging/pypi-reservation/` builds a distribution with **no importable
module at all**. That is deliberate. Anyone who installs it from a public index
gets metadata and a README; `import draupnir` afterwards fails with
`ModuleNotFoundError`, which is the loudest safe outcome. A placeholder that
shipped a stub `draupnir` package could shadow the real one and half-work,
which is worse than failing.

```
veldris_draupnir-0.0.0.dist-info/METADATA
veldris_draupnir-0.0.0.dist-info/RECORD
veldris_draupnir-0.0.0.dist-info/WHEEL
```

It is version `0.0.0` and classified `Development Status :: 7 - Inactive`, so
nothing resolving a version range picks it up by accident.

## 3. Publishing it

> **Credentials never enter this repository, a terminal transcript, or a chat.**
> The trusted publishing route below uses no token at all, which is why it is
> the recommended one.

### Route A — trusted publishing, no token (recommended)

PyPI mints a short-lived credential for a specific workflow in a specific
repository. There is nothing to store, nothing to rotate and nothing to leak.

1. Build the distribution locally so you know what you are about to publish:

   ```bash
   uv build packaging/pypi-reservation --out-dir dist/reservation
   ```

2. On PyPI, go to **Your projects → Publishing → Add a pending publisher** and
   register:

   | Field | Value |
   |---|---|
   | PyPI project name | `veldris-draupnir` |
   | Owner | your GitHub organisation |
   | Repository name | this repository |
   | Workflow name | `publish-reservation.yaml` |
   | Environment name | `pypi` |

   A *pending* publisher is the right kind: the project does not exist yet, and
   registering one is what lets the first upload create it.

3. In the repository settings, create an environment named `pypi` and add
   yourself as a required reviewer. The workflow then cannot publish without a
   human approving that specific run.

4. Run the workflow from the Actions tab: **publish reservation → Run
   workflow**. It is `workflow_dispatch` only and will not fire on a push.

### Route B — manual upload with a token

Use this only if trusted publishing is unavailable.

```bash
uv build packaging/pypi-reservation --out-dir dist/reservation
uv publish dist/reservation/*
```

`uv publish` reads `UV_PUBLISH_TOKEN` from the environment. Set it in your
shell for the one command and unset it afterwards; do not put it in `.env`,
which this repository ignores but does not encrypt.

Use a **project-scoped** token once the project exists. The first upload needs
an account-scoped token because the project does not exist yet — replace it
with a scoped one immediately afterwards and revoke the broad one.

### Rehearse on TestPyPI first

TestPyPI is a separate index with separate accounts. Uploads there are
disposable, so this is where to discover a metadata mistake:

```bash
uv publish --index testpypi dist/reservation/*
```

Add the index to `~/.config/uv/uv.toml` rather than to this repository:

```toml
[[index]]
name = "testpypi"
url = "https://test.pypi.org/simple/"
publish-url = "https://test.pypi.org/legacy/"
```

## 4. Afterwards

Verify from a machine that has never seen this repository:

```bash
uv run --isolated --with veldris-draupnir python -c "import draupnir"
```

That should fail with `ModuleNotFoundError`. If it succeeds, something other
than the reservation was published and it needs pulling.

A published version cannot be replaced, only yanked. Nothing about the
reservation should ever need a second version; if one is published by mistake,
yank it and publish `0.0.1` with the same empty contents.

## 5. How the real thing is distributed

Not through PyPI. The control plane reaches ALVISS as the aarch64 container
images built by the pipeline (`make images`, SAD 11H stage 3), and reaches a
developer's machine from the repository:

```bash
uv sync --all-groups
```

If an internal index is ever stood up, pin it so first party names never
resolve publicly:

```toml
[[tool.uv.index]]
name = "veldris"
url = "https://packages.veldris.internal/simple/"
explicit = true

[tool.uv.sources]
veldris-draupnir = { index = "veldris" }
```

`explicit = true` matters: without it, uv may consult the index for any
package, and the point here is the opposite — first party names come from one
place and nowhere else.

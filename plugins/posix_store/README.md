# veldris-draupnir-posix-store

The reference `StoreDriver`: `hodd://` URIs resolved to paths under one root.

AC-D2 asks that every plug-in interface has a reference implementation and a
worked example. `draupnir.store` had neither, because HODD addresses its own
vault directly and never needed the extension point to do it — which is the
reason to write one. An extension point nobody has extended is an extension
point whose Protocol has never been read by anyone who was not writing it.

## What it is not

It is not HODD. `draupnir.hodd.stores` is the vault: sealing, quotas,
manifests, a site registry, retention. An import contract forbids a driver
reaching into it, so this is written against `draupnir.interfaces` alone —
which is exactly what a third party has to work from.

## What is worth copying

- **The root is checked before every operation.** `put` creates the artefact's
  parent directories, which is right inside a mounted filesystem and wrong
  outside one: with the mount gone, creating parents recreates the mount point
  on local disk and the artefact lands where nobody backs it up. That was found
  by unmounting a vault in the degraded-mode tests, not by review.
- **`resolve` touches no disk.** It answers when the store is unreachable, so
  an error can name the path it meant to use.
- **A path that escapes the root is refused.** A store that returned
  `hodd://sindri/../../etc/passwd` would be a file read primitive with a URI
  syntax.
- **`put` refuses to overwrite.** Two different sets of bytes at one URI make
  every hash recorded against that URI ambiguous.

## Conformance

```bash
uv run pytest tests/contract/test_reference_drivers.py -q
```

"""Sign the build artefacts with the internal Veldris PKI key.

SAD 11H stage 3.4, and Decision S9: internal PKI with a self hosted Rekor
transparency log rather than public Sigstore, so that the same tamper evidence
is obtained without publishing release metadata externally.

This produces two files next to the artefacts:

    manifest.json      one SHA-256 per artefact, plus the build identity
    manifest.json.sig  a detached Ed25519 signature over the manifest bytes

Submission of the signature to the transparency log is the responsibility of
the release stage in SKIDBLADNIR; this script stops at producing the evidence.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The environment variable holding a base64 PKCS#8 Ed25519 private key. It is
#: read from the environment and never written anywhere, including logs.
KEY_VARIABLE = "DRAUPNIR_SIGNING_KEY"


def digest(path: Path) -> str:
    """Return the SHA-256 of a file, read in chunks."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def build_manifest(paths: list[Path], *, revision: str, version: str) -> dict[str, Any]:
    """Return the manifest that will be signed."""
    return {
        "schema": "draupnir/signing-manifest/v1",
        "version": version,
        "revision": revision,
        "artefacts": [
            {
                "path": path.relative_to(REPO_ROOT).as_posix(),
                "sha256": digest(path),
                "size": path.stat().st_size,
            }
            for path in sorted(paths)
        ],
    }


def sign(payload: bytes, key_material: str) -> bytes:
    """Return a detached Ed25519 signature over `payload`."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    raw = base64.b64decode(key_material)
    private_key = load_pem_private_key(raw, password=None)
    if not hasattr(private_key, "sign"):
        msg = "the signing key does not support signing"
        raise SystemExit(msg)
    return private_key.sign(payload)  # type: ignore[call-arg]


def main(argv: list[str] | None = None) -> int:
    """Write and sign the artefact manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sbom-dir", type=Path, default=REPO_ROOT / "sbom")
    parser.add_argument("--version", default=os.environ.get("DRAUPNIR_VERSION", "0.1.0"))
    parser.add_argument("--revision", default=os.environ.get("GITHUB_SHA", "unknown"))
    args = parser.parse_args(argv)

    artefacts = sorted(args.sbom_dir.glob("*.cdx.json"))
    if not artefacts:
        print(f"no artefacts found under {args.sbom_dir}", file=sys.stderr)
        return 1

    manifest = build_manifest(artefacts, revision=args.revision, version=args.version)
    payload = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")

    manifest_path = args.sbom_dir / "manifest.json"
    manifest_path.write_bytes(payload)
    print(f"wrote {manifest_path} covering {len(artefacts)} artefact(s)")

    key_material = os.environ.get(KEY_VARIABLE)
    if not key_material:
        print(
            f"{KEY_VARIABLE} is not set; the manifest is unsigned. A release "
            "requires a signature (SAD 11H stage 3.4).",
            file=sys.stderr,
        )
        return 1

    signature = sign(payload, key_material)
    signature_path = args.sbom_dir / "manifest.json.sig"
    signature_path.write_bytes(base64.b64encode(signature))
    print(f"wrote {signature_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

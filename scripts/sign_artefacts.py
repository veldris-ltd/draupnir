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
from datetime import UTC, datetime
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


def sign_distributions(
    names: list[str], *, key_material: str, key_id: str, signed_at: datetime | None = None
) -> dict[str, Any]:
    """Sign the installed plug-in distributions into the manifest PkiVerifier reads.

    RF-02: this script signed the SBOM directory and nothing signed a plug-in,
    so `PkiVerifier` had an empty signature record and refused everything. The
    two production call sites took `UnverifiedVerifier` instead, and the estate
    had two states — refuse everything, or load everything unsigned.

    **The digest is computed the same way the verifier computes it**, by
    calling the same function. A signer and a verifier that each hashed a
    distribution their own way would agree until the day they did not, and the
    disagreement would present as tampering.
    """
    from draupnir.svalinn.pki import digest_of

    when = signed_at or datetime.now(UTC)
    signatures: list[dict[str, Any]] = []
    for name in sorted(names):
        sha256, unreadable = digest_of(name)
        if unreadable:
            msg = f"{name} could not be hashed: {unreadable}"
            raise SystemExit(msg)
        signatures.append(
            {
                "distribution": name,
                "version": _version_of(name),
                "keyId": key_id,
                "signature": sign(bytes.fromhex(sha256), key_material).hex(),
                "signedAt": when.isoformat(),
                "sha256": sha256,
            }
        )
    return {"schema": "draupnir/plugin-signatures/v1", "signatures": signatures}


def _version_of(name: str) -> str:
    """The installed version of one distribution."""
    from importlib.metadata import version

    return version(name)


def installed_plugins() -> list[str]:
    """Every distribution that publishes a DRAUPNIR entry point group."""
    from importlib.metadata import distributions

    from draupnir.interfaces.types import GROUPS

    wanted = set(GROUPS)
    found: set[str] = set()
    for distribution in distributions():
        name = distribution.metadata["Name"]
        if not name:
            continue
        if any(point.group in wanted for point in distribution.entry_points):
            found.add(name)
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    """Write and sign the artefact manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sbom-dir", type=Path, default=REPO_ROOT / "sbom")
    parser.add_argument("--version", default=os.environ.get("DRAUPNIR_VERSION", "0.1.0"))
    parser.add_argument("--revision", default=os.environ.get("GITHUB_SHA", "unknown"))
    parser.add_argument(
        "--distributions",
        action="store_true",
        help=(
            "sign the installed plug-in distributions into the manifest PkiVerifier "
            "reads, instead of the SBOM"
        ),
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--key-id", default=os.environ.get("DRAUPNIR_SIGNING_KEY_ID", "forge-1"))
    args = parser.parse_args(argv)

    if args.distributions:
        key_material = os.environ.get(KEY_VARIABLE)
        if not key_material:
            print(f"{KEY_VARIABLE} is not set; nothing was signed.", file=sys.stderr)
            return 1
        names = installed_plugins()
        if not names:
            print("no installed distribution publishes a DRAUPNIR entry point", file=sys.stderr)
            return 1
        manifest = sign_distributions(names, key_material=key_material, key_id=args.key_id)
        target = args.out or (args.sbom_dir / "plugin-signatures.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        # LF on every platform (RF-23). This one matters more than most:
        # the manifest is signed, and a signature is over bytes. A manifest
        # written on Windows and verified on Linux would be a signature
        # failure with no explanation anywhere.
        target.write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )
        print(f"wrote {target} covering {len(names)} distribution(s)")
        return 0

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

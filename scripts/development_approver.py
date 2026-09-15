"""A development approver key, for the console stack's signing agent. RF-40.

The console signs an approval through `draupnir.gleipnir.signing_agent`, and the
API verifies the signature against the approver's registered public key. The
journeys confirm an approval end to end, so the stack they start needs both: a
private key for the development principal, which the agent holds, and its
public half where the API reads approver keys.

Generated once and kept. A stack left running between runs goes on verifying
the same key, and a second run does not register a key the running agent does
not hold.

    python scripts/development_approver.py .dev
"""

from __future__ import annotations

import argparse
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

#: The subject the development principal presents (`draupnir.api.development`),
#: which is the name the API looks its key up by.
SUBJECT = "dev@veldris.internal"


def ensure(directory: Path) -> tuple[Path, Path]:
    """The private key and the approver key store, created where absent."""
    private = directory / "approver.key"
    store = directory / "approvers"
    store.mkdir(parents=True, exist_ok=True)

    if not private.is_file():
        generated = Ed25519PrivateKey.generate()
        private.write_bytes(
            generated.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )

    key = serialization.load_pem_private_key(private.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        msg = f"{private} is not an Ed25519 key; delete it and run this again"
        raise SystemExit(msg)
    (store / f"{SUBJECT}.pem").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    return private, store


def main(argv: list[str] | None = None) -> int:
    """Create the key and the store, and print where they are."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    arguments = parser.parse_args(argv)

    private, store = ensure(arguments.directory)
    print(f"development approver {SUBJECT}: key {private}, registered in {store}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

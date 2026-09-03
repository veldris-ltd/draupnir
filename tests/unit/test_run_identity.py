"""Run identity, AC-F1.

"A run specification file is submitted through `draupnirctl` and through the
web console, and both produce an identical run identity, being the hash of the
specification plus its input artefact hashes."

The console and the CLI agree by construction -- both post to `POST /v1/runs`
and the identity is computed at the edge rather than by either client -- so
what is left to establish here is that the function itself is a function: the
same specification and inputs always give the same digest, and anything that
would make two different pieces of work look like one is refused.
"""

from __future__ import annotations

import pytest

from draupnir.core.domain.identity import (
    InputHashError,
    normalise_digest,
    run_identity,
)

SPEC = "a" * 64
BASE = "b" * 64
CORPUS = "c" * 64


def test_the_same_specification_and_inputs_give_the_same_identity() -> None:
    assert run_identity(SPEC, [BASE, CORPUS]) == run_identity(SPEC, [BASE, CORPUS])


def test_the_order_the_inputs_are_listed_in_does_not_change_the_identity() -> None:
    """A client that lists its inputs in a different order submits the same work.

    Without this the identity would depend on the shape of the client's loop,
    which is exactly the sort of accident that makes two identical runs look
    different a year later.
    """
    assert run_identity(SPEC, [BASE, CORPUS]).digest == run_identity(SPEC, [CORPUS, BASE]).digest


def test_the_spelling_of_a_digest_does_not_change_the_identity() -> None:
    """A specification writes `sha256:…` and an artefact URI carries bare hex."""
    prefixed = run_identity(f"sha256:{SPEC}", [f"sha256:{BASE.upper()}"])
    bare = run_identity(SPEC, [BASE])
    assert prefixed.digest == bare.digest


def test_a_repeated_input_is_counted_once() -> None:
    """Naming the same artefact twice is naming it once."""
    assert run_identity(SPEC, [BASE, BASE]).digest == run_identity(SPEC, [BASE]).digest


def test_a_different_corpus_is_different_work() -> None:
    """The reason the inputs are in the identity at all.

    Two runs over the same base model and different corpora produce different
    models, and an identity that ignored the dataset would call them the same
    run and make the comparison meaningless.
    """
    assert run_identity(SPEC, [BASE, CORPUS]).digest != run_identity(SPEC, [BASE]).digest


def test_a_different_specification_is_different_work() -> None:
    assert run_identity(SPEC, [BASE]).digest != run_identity("d" * 64, [BASE]).digest


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-digest",
        "sha256:",
        "a" * 63,
        "a" * 65,
        "sha256:" + "g" * 64,
        "hodd://models/core/MIDGARD-CORE",
    ],
)
def test_an_unresolved_input_is_refused_rather_than_hashed(value: str) -> None:
    """An identity over an unresolved reference looks reproducible and is not.

    `hodd://models/core/MIDGARD-CORE` is the realistic case: a specification
    names an artefact, and if the digest it resolved to at submission time is
    not recorded then the name is a moving target and the identity is a
    statement about nothing.
    """
    with pytest.raises(InputHashError):
        run_identity(SPEC, [value])


def test_an_unresolved_specification_hash_is_refused() -> None:
    with pytest.raises(InputHashError):
        run_identity("not-a-hash", [BASE])


def test_the_identity_carries_what_it_was_computed_over() -> None:
    """The ledger payload has to be enough to recompute the digest.

    An identity recorded without its inputs cannot be checked from the record,
    and the record is the only place anyone will ever check it from.
    """
    identity = run_identity(SPEC, [CORPUS, BASE])
    payload = identity.as_payload()

    assert payload["spec_hash"] == SPEC
    assert payload["input_artefact_sha256"] == [BASE, CORPUS]
    assert payload["run_identity"] == identity.digest
    assert (
        run_identity(str(payload["spec_hash"]), list(identity.input_artefact_sha256)).digest
        == identity.digest
    )


def test_normalise_digest_accepts_both_spellings_and_nothing_else() -> None:
    assert normalise_digest(f"  SHA256:{BASE.upper()}  ") == BASE
    assert normalise_digest(BASE) == BASE
    with pytest.raises(InputHashError):
        normalise_digest("sha512:" + "a" * 128)


def test_no_inputs_is_a_valid_identity() -> None:
    """A specification that consumes nothing resolved still has an identity.

    It is a weaker one -- nothing pins what it read -- and the API refuses such
    a submission separately. The function does not, because "this specification
    with no resolved inputs" is a well defined thing to hash and conflating the
    two rules would put the policy in the wrong place.
    """
    identity = run_identity(SPEC, [])
    assert identity.input_artefact_sha256 == ()
    assert identity.digest != run_identity(SPEC, [BASE]).digest

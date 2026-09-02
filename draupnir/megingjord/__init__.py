"""MEGINGJORD, the belt of strength: the federation registry.

Site registry, anchor store and countersigning, policy distribution, OIDC
issuer, signing trust root, self-hosted transparency log. SAD 11A.1.

Owns: The Matrix tier. Cross-forge ledger anchors, policy and gate
distribution, the RBAC source of truth, the plug-in signature trust root.
Must not: Hold a corpus or a weight. Ever.

That prohibition is threat T13's whole mitigation, and it is enforced by
construction rather than by review: every payload is built through
`core.domain.federation.sealed`, which walks the finished structure and refuses
anything that is not a hash, a name, a timestamp or a number. There is no path
that serialises an unchecked payload.

What it can verify is correspondingly narrow. MEGINGJORD holds no entries, so
it cannot check that entry 1,000 follows from entry 999; what it checks is that
a site is not contradicting itself, which is detectable from hashes alone. That
narrowness is what lets the federation hold hashes alone.

Deployed separately from the forge (SAD 11E.1), on a VPS in the United Kingdom.
Its custody concentration at Site 0 is accepted under section 16A, and what
makes that acceptable is that the concentrated component cannot hold the thing
whose disclosure would matter.
"""

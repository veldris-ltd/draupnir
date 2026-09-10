"""SVALINN, the shield before the sun: the cross-cutting security layer.

Identity, authorisation, secrets, signing, egress and the executor sandbox.
SAD 5.2.

One caveat, here rather than buried: the executor sandbox is a specification,
not a control in force. Sindri runs training as a process on a shared
appliance under Slurm, so the layer `sandbox` describes does not exist there.
`containment` states what does, and names the difference. Every other module
in this package is enforced on every call.

Owns: OIDC authentication, RBAC enforcement, secrets brokering, plug-in
signature verification, artefact signing, egress brokering, sandbox profiles.
Must not: Make policy about what a run computes.

| Module | What it refuses |
|---|---|
| `roles` | A permission no role holds, and any role holding both submit and publish |
| `identity` | A principal with no role, and a weak authenticator on a release |
| `authz` | A call with no declared requirement, and one with no principal |
| `secrets` | A lease that outlives its job, and a secret in a rendered plan |
| `scanning` | Registration of an artefact carrying a credential |
| `egress` | An outbound call to a destination nobody declared |
| `sandbox` | An executor with a network -- **where jobs are containers** |
| `containment` | Nothing; it describes what is in force, and the shortfall |
| `integrity` | A load whose bytes are not what the specification expects |
| `envelope` | An envelope that verifies under no accepted algorithm |
| `pki` | An unsigned plug-in, and an unknown signer |
| `signing` | An unsigned chain head |
| `inventory` | A build whose cryptography is not documented |

Everything here fails closed. The pattern is uniform and deliberate: in the
absence of information these modules refuse rather than permit, because the
alternative -- a safe default -- is still a default, and a default is a
decision nobody made.

The HTTP wiring lives in `draupnir.api.guards`, which is where the edge is.
This package holds no framework import, so the authorisation decision can be
tested without a request and cannot come to depend on one.
"""

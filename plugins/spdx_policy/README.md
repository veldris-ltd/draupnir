# veldris-draupnir-spdx-policy

The reference `PolicyDriver`: a licence decision from an SPDX identifier and a
personal data determination.

AC-D2 asks that every plug-in interface has a reference implementation and a
worked example. `draupnir.policy` had neither, because GLEIPNIR decides the
licence regime in force and never needed the extension point to do it. SAD 10.2
names the case that does: a jurisdiction whose regime differs arrives as a
policy driver rather than as a change to the core.

## What it is not

It is not GLEIPNIR. `draupnir.gleipnir.policy` is the regime in force, with its
rule table, its copyright policy rendering and its Article 53 obligations. An
import contract forbids a driver reaching into it.

## The worked example

Three rules and a default, first match wins:

| Rule | Applies to | Verdict |
|---|---|---|
| `personal-data-requires-approval` | any licence, `personalData: true` | REQUIRES_APPROVAL |
| `permissive-permitted` | Apache-2.0, MIT, BSD-3-Clause, CC-BY-4.0, CC0-1.0, OGL-UK-3.0 | PERMIT |
| `share-alike-refused` | CC-BY-SA-4.0, GPL-3.0-only, AGPL-3.0-only | REFUSE |
| default | anything else | REFUSE |

## What is worth copying

- **Deny by default.** A licence no rule matches is refused. Permitting it
  would make the policy's silence a permission, and a corpus whose licence
  nobody wrote a rule for is a corpus nobody assessed.
- **The decision carries its version.** A decision taken in March stays
  explicable in November when the policy has moved (SAD 9A.2).
- **Rules are data.** Article 53 requires the copyright policy to be machine
  readable and published; a rule expressed as a function is a rule nobody
  outside engineering can read. `as_mapping()` renders the whole policy.
- **It answers and never acts.** Decision S4: GLEIPNIR judges, never executes.

## Conformance

```bash
uv run pytest tests/contract/test_reference_drivers.py -q
```

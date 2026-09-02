"""SKIDBLADNIR, the ship that folds into a pouch: quantisation and release.

Format conversion, SBOM, model card, lineage. SAD 5.2.

Owns: Format conversion, SBOM generation, model card rendering, lineage
attestation, registry publish.
Must not: Publish without a GLEIPNIR release approval.

| Module | What it produces |
|---|---|
| `formats` | The export formats, and the MLX-against-NVFP4 cross-platform check |
| `facts` | A recorded fact, and the difference between "no" and "not recorded" |
| `modelcard` | The card, rendered from facts and stating what it does not know |
| `sbom` | The CycloneDX bill of materials |
| `lineage` | The chain to base model licences and corpus hashes, and its gaps |
| `article53` | The training content summary and the downstream annex |
| `publish` | Five refusals, applied in an order that matters |

Article 53 artefacts are generated from the record and never authored
(Decision S11). Article 50 attaches to the system that generates content, which
is the Midgard Suite and not the forge (SAD 9A.1) -- there is a test that fails
if anything here starts to implement it.

Nothing in this package imports HODD, GLEIPNIR or RAUN. The records arrive as
mappings, which is the same seam a policy driver consumes and is required by
the module independence contract in any case.
"""

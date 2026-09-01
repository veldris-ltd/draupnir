"""HODD, the hoard: corpus and artefact store.

Immutable ingest, hashing, licence register, retention. SAD 5.2.

Owns: Ingest, hashing, immutability, licence register, retention, quota, object and file layout.
Must not: Interpret licence terms. It records them; GLEIPNIR judges them.
"""

# draupnir-targz-export

A reference `ExportDriver` that packages an artefact directory as a
`.tar.gz`, and the demonstration of AC-N9: a new export format, working, in
under two hundred lines, with no file under `draupnir/core/` modified.

It is deliberately the least interesting export format there is. The point is
not what it produces but what adding it cost: one distribution, one entry
point, no change to the control plane.

```bash
uv pip install -e plugins/targz_export
```

`tests/contract/test_export_extension.py` counts its lines, loads it through
the plug-in registry and runs the published conformance suite against it.

# vendor

`veldris_shared_protocol-1.2.0-py3-none-any.whl` is the shared protocol
package: the wire format GULLINBURSTI speaks to MEGINGJORD (VLD-SAD-MEGINGJORD-001
section 33). It is published on no package index, so it is kept here and
`pyproject.toml` installs it from this path. Pipelines need no second
repository and no token to build.

It is built from `veldris-ltd/shared-protocol` at its 1.2.0 commit and is
reproducible: building it again gives the same bytes.

    uv build --wheel --out-dir vendor/ ../shared-protocol
    shasum -a 256 vendor/*.whl
    # da092b11f9096ef8c182d5ca76424ca59242d78ec1df64993425b8bee6cfb96b

To take a new version: build it the same way, replace the wheel, change the
path in `pyproject.toml`, `uv lock`, and run `shared-protocol conformance`
(`make test-protocol`) against it.

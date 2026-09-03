# syntax=docker/dockerfile:1.9
#
# draupnir-api and draupnir-worker. AC-Q7: builds for aarch64 from a distroless
# base and runs rootless.
#
#     docker buildx build --platform linux/arm64 -f docker/api.Dockerfile .
#
# The runtime is `gcr.io/distroless/cc-debian12`: no shell, no package manager,
# a non-root default user. Distroless does not publish a Python 3.12 image, so
# the interpreter is uv's own managed build, which is relocatable and links its
# OpenSSL and SQLite statically. Copying that tree into the distroless stage
# gives exactly the Python the lockfile was resolved against, rather than
# whichever version a base image happens to ship.

ARG UV_VERSION=0.12.8
ARG PYTHON_VERSION=3.12

FROM ghcr.io/astral-sh/uv:${UV_VERSION}-debian-slim AS builder

ARG PYTHON_VERSION

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_NO_CACHE=1

WORKDIR /app

RUN uv python install "${PYTHON_VERSION}"

# Dependencies first, so a source change does not re-resolve the world.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the application itself.
COPY draupnir ./draupnir
COPY draupnirctl ./draupnirctl
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

# --------------------------------------------------------------------------
# Runtime. Distroless: no shell, no package manager, nothing to pivot from.
# --------------------------------------------------------------------------
FROM gcr.io/distroless/cc-debian12:nonroot AS runtime

ARG VERSION=0.0.0
ARG REVISION=unknown

LABEL org.opencontainers.image.title="draupnir-api" \
      org.opencontainers.image.description="DRAUPNIR control plane API" \
      org.opencontainers.image.vendor="Veldris" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="https://veldris.internal/draupnir"

# The interpreter must land on the same path it had in the builder: the
# virtual environment refers to it by absolute path.
COPY --from=builder /python /python
COPY --from=builder /app /app

WORKDIR /app

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Rootless. 65532 is the distroless `nonroot` user, stated numerically so that
# a runAsNonRoot admission check can verify it without resolving a name.
USER 65532:65532

EXPOSE 8000

# The interpreter is the entry point and the module is the command, so the one
# image serves both deployable units: the default runs the API, and
#
#     docker run draupnir-api -m draupnir.worker
#
# runs the worker of SAD 5.1 ("two to four processes, poll the ledger for
# actionable transitions"). One image rather than two, because they are one
# codebase and a second Dockerfile is a second thing to keep in step.
ENTRYPOINT ["/app/.venv/bin/python"]
CMD ["-m", "uvicorn", "draupnir.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

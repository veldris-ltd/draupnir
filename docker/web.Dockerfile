# syntax=docker/dockerfile:1.9
#
# draupnir-web. Static assets built once and served by a distroless nginx.
# AC-Q7: aarch64, distroless, rootless.
#
#     docker buildx build --platform linux/arm64 -f docker/web.Dockerfile .

ARG NODE_IMAGE=cgr.dev/chainguard/node
ARG NGINX_IMAGE=cgr.dev/chainguard/nginx

FROM ${NODE_IMAGE}:latest-dev AS builder

WORKDIR /src

# The Chainguard node image runs as an unprivileged user, so npm installs into
# a writable prefix under the user's home rather than /usr/local. pnpm is
# installed at the version the workspace pins, not whichever one the base image
# happens to carry: pnpm 9 and pnpm 11 read `pnpm.overrides` from different
# files, and the overrides there are security fixes.
ENV CI=true     NPM_CONFIG_PREFIX=/home/node/.local     PATH=/home/node/.local/bin:$PATH

RUN npm install --global pnpm@9.12.0

COPY --chown=node:node web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
COPY --chown=node:node web/packages ./packages
COPY --chown=node:node web/apps ./apps
COPY --chown=node:node web/tsconfig.base.json ./
# The console's `vite.config.ts` reads `scripts/proxied-prefixes.json` when the
# configuration loads, so it is a build input even though nothing imports it
# (RF-45). Without it `vite build` stops before it starts, and this image --
# the one the pipeline ships -- could not be built at all.
COPY --chown=node:node web/scripts ./scripts

RUN pnpm install --frozen-lockfile --ignore-scripts
RUN pnpm run build

FROM ${NGINX_IMAGE}:latest AS runtime

ARG VERSION=0.0.0
ARG REVISION=unknown

LABEL org.opencontainers.image.title="draupnir-web" \
      org.opencontainers.image.description="DRAUPNIR console" \
      org.opencontainers.image.vendor="Veldris" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY --from=builder /src/apps/console/dist /usr/share/nginx/html

# Rootless. The Chainguard nginx image already runs as 65532. It listens on
# 8443 in TLS 1.3 only, with the certificates draupnir-run.sh mounts at
# /etc/draupnir/tls; there is no plain HTTP port (SAD 9.5, RF-37).
USER 65532:65532
EXPOSE 8443

ENTRYPOINT ["/usr/sbin/nginx"]
CMD ["-g", "daemon off;"]

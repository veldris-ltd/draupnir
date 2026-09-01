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

# Rootless. The Chainguard nginx image already runs as 65532 and binds 8080.
USER 65532:65532
EXPOSE 8080

ENTRYPOINT ["/usr/sbin/nginx"]
CMD ["-g", "daemon off;"]

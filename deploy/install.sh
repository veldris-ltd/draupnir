#!/usr/bin/env bash
# Commission the DRAUPNIR control plane on ALVISS at a site. SAD 11.1.
#
# **What this installs and what it does not.** SAD 468 places DRAUPNIR Core and
# modules on "ALVISS at Sindri", and Decision S3 keeps the control plane off
# the appliances. So this installs the three control plane units --
# `draupnir-api`, `draupnir-worker`, `draupnir-web` -- as rootless containers
# under the user systemd instance (SAD 11.1 step 1, AC-Q7), and nothing else.
#
# It does not install PostgreSQL or MinIO. SAD 7.2 puts both on ANDVARI as
# existing instances, so this checks that they answer and stops if they do not.
# An installer that stood up its own database would give the site a second one.
#
# It does not build the estate. The appliances, the fabric and the Slurm
# controller are VLD-INF-SINDRI-001 and out of scope (SAD 1.3).
#
# **Idempotent.** Every step is written to be safe to repeat: re-running after a
# partial failure is the supported way to finish, and re-running after success
# changes nothing. Commissioning is the one operation nobody gets to rehearse.
#
# **What it never writes.** Secrets are brokered by SVALINN and are never in
# configuration (SAD 11.1 step 5). This writes `draupnir.env` and refuses to
# put a credential in it; `secrets.env` is the operator's file and is created
# here only as an empty 0600 placeholder if absent.
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults. Every one is overridable, and every one is a non-secret.
# ---------------------------------------------------------------------------
SITE_ID="${DRAUPNIR_SITE_ID:-sindri}"
REGISTRY="${DRAUPNIR_REGISTRY:-registry.veldris.internal}"
REVISION=""
PODMAN="${DRAUPNIR_PODMAN:-podman}"

CONFIG_DIR="${DRAUPNIR_CONFIG_DIR:-${XDG_CONFIG_HOME:-${HOME}/.config}/draupnir}"
STATE_DIR="${DRAUPNIR_STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/draupnir}"
UNIT_DIR="${DRAUPNIR_UNIT_DIR:-${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user}"
LIBEXEC_DIR="${DRAUPNIR_LIBEXEC_DIR:-${HOME}/.local/libexec/draupnir}"

# The dependencies this checks and never installs, as host:port.
POSTGRES_HOST="${DRAUPNIR_POSTGRES_HOST:-andvari.veldris.internal}"
POSTGRES_PORT="${DRAUPNIR_POSTGRES_PORT:-5432}"
OBJECT_STORE_HOST="${DRAUPNIR_OBJECT_STORE_HOST:-andvari.veldris.internal}"
OBJECT_STORE_PORT="${DRAUPNIR_OBJECT_STORE_PORT:-9000}"

# The units and the image each runs, shared with rollout.sh and rollback.sh
# so that the three cannot disagree about what is deployed.
# shellcheck source=deploy/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
UNITS=("${DRAUPNIR_UNITS[@]}")

DRY_RUN=0
MODE="install"
SKIP_DEPENDENCY_CHECK=0

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Output. Plain, prefixed, and on the right stream: an installer's log is read
# once, in an incident, by someone who did not write it.
# ---------------------------------------------------------------------------
say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    warning: %s\n' "$*" >&2; }
fail() { printf '\nerror: %s\n' "$*" >&2; exit 1; }
run() {
  if ((DRY_RUN)); then
    printf '    [dry-run] %s\n' "$*"
  else
    "$@"
  fi
}

usage() {
  cat <<'USAGE'
usage: install.sh [options]

  --revision REV      Image revision to start from. Defaults to the newest tag
                      already present locally, and is required on a host that
                      has never pulled one.
  --registry HOST     Image registry. Default: registry.veldris.internal
  --site SITE         Site identifier written to the configuration.
                      Default: sindri
  --check             Run the preflight and dependency checks and stop. Changes
                      nothing. Use this before a maintenance window.
  --uninstall         Stop and remove the units, the wrapper and the generated
                      configuration. Leaves secrets.env and the state directory,
                      which this did not create the contents of.
  --dry-run           Print every action without performing it.
  --skip-dependency-check
                      Do not check PostgreSQL and MinIO. For a host being
                      commissioned before ANDVARI is reachable.
  -h, --help          This text.

Environment: DRAUPNIR_POSTGRES_HOST, DRAUPNIR_OBJECT_STORE_HOST and the
DRAUPNIR_*_DIR paths override the defaults. Secrets are never read here.
USAGE
}

while (($#)); do
  case "$1" in
    --revision) REVISION="${2:?--revision needs a value}"; shift 2 ;;
    --registry) REGISTRY="${2:?--registry needs a value}"; shift 2 ;;
    --site) SITE_ID="${2:?--site needs a value}"; shift 2 ;;
    --check) MODE="check"; shift ;;
    --uninstall) MODE="uninstall"; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-dependency-check) SKIP_DEPENDENCY_CHECK=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "unknown argument: $1" ;;
  esac
done

# ---------------------------------------------------------------------------
# Preflight. Everything checked before anything is written, so a host that
# cannot run this is told so before it is half-installed.
# ---------------------------------------------------------------------------
preflight() {
  say "preflight"

  command -v "${PODMAN}" >/dev/null 2>&1 \
    || fail "${PODMAN} not found. The control plane runs as rootless containers (AC-Q7); install podman first."
  info "podman: $("${PODMAN}" --version 2>/dev/null || echo unknown)"

  [[ "$(id -u)" -ne 0 ]] \
    || fail "run this as the service account, not as root. The units are rootless by requirement (AC-Q7), and installing them into root's systemd instance is not the same system."

  command -v systemctl >/dev/null 2>&1 || fail "systemctl not found."
  systemctl --user show-environment >/dev/null 2>&1 \
    || fail "no user systemd instance for $(id -un). Log in as the service account, or enable lingering: loginctl enable-linger $(id -un)"

  # Lingering is what makes the difference between a control plane that
  # survives the operator logging out and one that does not. It is a warning
  # rather than a failure because a commissioning session is often exactly the
  # session that is still logged in.
  if command -v loginctl >/dev/null 2>&1; then
    if [[ "$(loginctl show-user "$(id -un)" --property=Linger --value 2>/dev/null || echo no)" != "yes" ]]; then
      warn "lingering is off for $(id -un): the units stop when this session ends."
      warn "  enable it with: loginctl enable-linger $(id -un)"
    else
      info "lingering: enabled"
    fi
  fi
}

# Reachability only. Credentials are SVALINN's and are not read here, so this
# proves the port answers rather than that the schema is right -- the pipeline's
# migration stage is what proves the schema.
check_dependencies() {
  say "dependencies on ANDVARI"
  if ((SKIP_DEPENDENCY_CHECK)); then
    warn "skipped by --skip-dependency-check"
    return 0
  fi

  local failures=0
  local probe
  for probe in "PostgreSQL:${POSTGRES_HOST}:${POSTGRES_PORT}" "MinIO:${OBJECT_STORE_HOST}:${OBJECT_STORE_PORT}"; do
    local name="${probe%%:*}" rest="${probe#*:}"
    local host="${rest%%:*}" port="${rest##*:}"
    if timeout 5 bash -c "exec 3<>/dev/tcp/${host}/${port}" 2>/dev/null; then
      info "${name} at ${host}:${port}: answering"
    else
      warn "${name} at ${host}:${port}: no answer"
      failures=$((failures + 1))
    fi
  done

  if ((failures)); then
    fail "$(printf '%d of 2 dependencies did not answer. SAD 7.2 puts both on ANDVARI as existing instances: this installer does not stand them up. Fix the dependency, or pass --skip-dependency-check to commission ahead of it.' "${failures}")"
  fi
}

# ---------------------------------------------------------------------------
# Configuration. File based and version controllable (SAD 11.1 step 5). The
# database URL here names the host and the role and carries no password: the
# password is SVALINN's and belongs in secrets.env.
# ---------------------------------------------------------------------------
write_configuration() {
  say "configuration"
  run mkdir -p "${CONFIG_DIR}" "${STATE_DIR}"

  local target="${CONFIG_DIR}/draupnir.env"
  local rendered
  rendered="$(cat <<EOF
# DRAUPNIR control plane configuration. Generated by deploy/install.sh.
# Non-secret settings only: secrets live in secrets.env and are brokered by
# SVALINN (SAD 11.1 step 5). Every name carries the DRAUPNIR_ prefix so the
# container inherits nothing by accident.
DRAUPNIR_ENV=production
DRAUPNIR_SITE_ID=${SITE_ID}
DRAUPNIR_API_HOST=0.0.0.0
DRAUPNIR_API_PORT=8000
DRAUPNIR_LOG_LEVEL=info
DRAUPNIR_OBJECT_STORE_ENDPOINT=${OBJECT_STORE_HOST}:${OBJECT_STORE_PORT}
DRAUPNIR_OBJECT_STORE_BUCKET=draupnir
DRAUPNIR_OBJECT_STORE_SECURE=true
EOF
)"

  if ((DRY_RUN)); then
    printf '    [dry-run] write %s:\n' "${target}"
    printf '%s\n' "${rendered}" | sed 's/^/        /'
  else
    printf '%s\n' "${rendered}" >"${target}"
    chmod 0644 "${target}"
    info "wrote ${target}"
  fi

  # The secrets file is the operator's, not this script's. Created empty and
  # unreadable by anyone else if it is absent, never overwritten if present.
  local secrets="${CONFIG_DIR}/secrets.env"
  if [[ -e "${secrets}" ]]; then
    info "kept ${secrets} (not overwritten)"
  else
    run touch "${secrets}"
    run chmod 0600 "${secrets}"
    info "created empty ${secrets} (0600)"
    info "  it needs DRAUPNIR_DATABASE_URL, DRAUPNIR_DATABASE_URL_SYNC,"
    info "  DRAUPNIR_OBJECT_STORE_ACCESS_KEY and DRAUPNIR_OBJECT_STORE_SECRET_KEY"
  fi
}

# ---------------------------------------------------------------------------
# Units. Rendered from the templates beside this script so that what runs on
# ALVISS is what is in the repository, reviewed, rather than a heredoc.
# ---------------------------------------------------------------------------
install_units() {
  say "units"
  run mkdir -p "${UNIT_DIR}" "${LIBEXEC_DIR}"
  run install -m 0755 "${HERE}/units/draupnir-run.sh" "${LIBEXEC_DIR}/draupnir-run.sh"
  info "installed ${LIBEXEC_DIR}/draupnir-run.sh"

  local podman_path
  podman_path="$(command -v "${PODMAN}")"

  local unit template target
  for unit in "${UNITS[@]}"; do
    template="${HERE}/units/${unit}.service.in"
    target="${UNIT_DIR}/${unit}.service"
    [[ -r "${template}" ]] || fail "missing unit template: ${template}"
    if ((DRY_RUN)); then
      printf '    [dry-run] render %s -> %s\n' "${template}" "${target}"
    else
      sed -e "s|@LIBEXEC@|${LIBEXEC_DIR}|g" \
          -e "s|@PODMAN@|${podman_path}|g" \
          -e "s|@SITE_ID@|${SITE_ID}|g" \
          "${template}" >"${target}"
      chmod 0644 "${target}"
      info "wrote ${target}"
    fi
  done

  run systemctl --user daemon-reload
}

# The revision is seeded through the same mechanism rollout.sh uses, so that a
# first install and every later rollout publish a revision the same way.
seed_revision() {
  say "revision"
  if [[ -z "${REVISION}" ]]; then
    local seeded=0 unit
    for unit in "${UNITS[@]}"; do
      [[ -r "${STATE_DIR}/image-${unit}" ]] && seeded=$((seeded + 1))
    done
    if ((seeded == ${#UNITS[@]})); then
      info "no --revision given; keeping the references already in ${STATE_DIR}"
      return 0
    fi
    fail "no --revision given and no image reference on this host. Pass --revision <sha>, which is the revision the pipeline built and signed."
  fi

  local unit image
  for unit in "${UNITS[@]}"; do
    image="$(draupnir_image_for "${unit}" "${REVISION}" "${REGISTRY}")"
    info "${unit} <- ${image}"
    run "${PODMAN}" pull "${image}"
    run systemctl --user set-environment "$(draupnir_image_var "${unit}")=${image}"
    run bash -c "printf '%s\n' '${image}' >'${STATE_DIR}/image-${unit}'"
  done
}

enable_units() {
  say "enable and start"
  local unit
  for unit in "${UNITS[@]}"; do
    run systemctl --user enable "${unit}.service"
  done
  for unit in "${UNITS[@]}"; do
    run systemctl --user restart "${unit}.service"
  done
}

verify() {
  say "verify"
  ((DRY_RUN)) && { info "[dry-run] skipped"; return 0; }

  local unit failures=0
  for unit in "${UNITS[@]}"; do
    if systemctl --user is-active --quiet "${unit}.service"; then
      info "${unit}.service: active"
    else
      warn "${unit}.service: not active"
      warn "  journalctl --user -u ${unit}.service -n 50"
      failures=$((failures + 1))
    fi
  done
  ((failures)) && fail "${failures} unit(s) did not start."

  # /readyz reports each dependency separately, so it is the one worth waiting
  # for: /healthz answers as soon as the process is up and says nothing about
  # whether the control plane can reach anything. AC-N6 expects service within
  # 30 seconds of the process starting.
  local url="http://127.0.0.1:${DRAUPNIR_API_PORT:-8000}"
  if command -v curl >/dev/null 2>&1; then
    local waited=0
    until curl -fsS --max-time 3 "${url}/healthz" >/dev/null 2>&1; do
      ((waited >= 30)) && { warn "no answer from ${url}/healthz after 30s"; break; }
      sleep 2
      waited=$((waited + 2))
    done
    if curl -fsS --max-time 3 "${url}/healthz" >/dev/null 2>&1; then
      info "healthz: answering after ${waited}s"
      if curl -fsS --max-time 5 "${url}/readyz" >/dev/null 2>&1; then
        info "readyz: every dependency reports true"
      else
        warn "readyz: a dependency reports false. The control plane is up and cannot reach something."
        warn "  curl -s ${url}/readyz | jq"
      fi
    fi
  else
    warn "curl not found; skipped the health check"
  fi
}

uninstall() {
  say "uninstall"
  local unit
  for unit in "${UNITS[@]}"; do
    run systemctl --user disable --now "${unit}.service" 2>/dev/null || true
    run rm -f "${UNIT_DIR}/${unit}.service"
    run "${PODMAN}" rm --ignore --force "${unit}" >/dev/null 2>&1 || true
    run systemctl --user unset-environment "$(draupnir_image_var "${unit}")" 2>/dev/null || true
  done
  run systemctl --user daemon-reload
  run rm -f "${LIBEXEC_DIR}/draupnir-run.sh"
  run rm -f "${CONFIG_DIR}/draupnir.env"
  info "removed the units, the wrapper and draupnir.env"
  info "kept ${CONFIG_DIR}/secrets.env and ${STATE_DIR}: this did not create their contents"
}

main() {
  case "${MODE}" in
    check)
      preflight
      check_dependencies
      say "check complete"
      info "nothing was changed."
      ;;
    uninstall)
      preflight
      uninstall
      say "uninstalled"
      ;;
    install)
      preflight
      check_dependencies
      write_configuration
      install_units
      seed_revision
      enable_units
      verify
      say "commissioned"
      info "site: ${SITE_ID}    registry: ${REGISTRY}"
      info "next: put the credentials in ${CONFIG_DIR}/secrets.env and restart,"
      info "      then run the migrations from the pipeline (stage 4.1)."
      ;;
  esac
}

main "$@"

#!/usr/bin/env bash
# Commission the DRAUPNIR control plane on ALVISS at a site. SAD 11.1.
#
# **What this installs and what it does not.** SAD 468 places DRAUPNIR Core and
# modules on "ALVISS at Sindri", and Decision S3 keeps the control plane off
# the appliances. So this installs the three control plane units --
# `draupnir-api`, `draupnir-worker`, `draupnir-web` -- as rootless containers
# under the host's user service manager (SAD 11.1 step 1, AC-Q7), and nothing
# else.
#
# **Two service managers.** systemd user units on Linux, launchd user agents on
# macOS. VLD-INF-SINDRI-001 Rev 3.3 section 6 makes ALVISS a Mac mini M4 Pro,
# so the second is the one the specified host actually has. The container is
# identical on both -- same image, same rootless invocation, same read-only
# root -- and the difference lives in `lib.sh`, which this calls through five
# verbs rather than naming a manager itself.
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
REVISION=""
PODMAN="${DRAUPNIR_PODMAN:-podman}"

# Empty means "derive it from the site", which cannot happen until the
# arguments have been read: `--site` changes the DNS zone and therefore every
# hostname below it. An environment override wins over the derivation, and a
# command line argument wins over both.
SITE_DOMAIN="${DRAUPNIR_SITE_DOMAIN:-}"
REGISTRY="${DRAUPNIR_REGISTRY:-}"

CONFIG_DIR="${DRAUPNIR_CONFIG_DIR:-${XDG_CONFIG_HOME:-${HOME}/.config}/draupnir}"
STATE_DIR="${DRAUPNIR_STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/draupnir}"
LIBEXEC_DIR="${DRAUPNIR_LIBEXEC_DIR:-${HOME}/.local/libexec/draupnir}"
# Resolved after lib.sh is sourced: where the units go depends on which service
# manager this host has, and that is lib.sh's answer to give.
UNIT_DIR=""
LOG_DIR=""

# The dependencies this checks and never installs, as host:port. SAD 7.2 puts
# both on ANDVARI; the name it answers to is the site's business, not this
# file's.
POSTGRES_HOST="${DRAUPNIR_POSTGRES_HOST:-}"
POSTGRES_PORT="${DRAUPNIR_POSTGRES_PORT:-5432}"
OBJECT_STORE_HOST="${DRAUPNIR_OBJECT_STORE_HOST:-}"
OBJECT_STORE_PORT="${DRAUPNIR_OBJECT_STORE_PORT:-9000}"

# Where ANDVARI's vault is mounted on this host. VLD-INF-SINDRI-001 Procedure
# S9 uses /forge/vault on the appliances, and the control plane needs it too:
# SAD 11.3 gives the worker a capacity alarm at 85 per cent, and a signal whose
# source is not mounted is a signal that never fires. Empty means this forge has
# no vault, which is a legitimate configuration and is reported as such rather
# than skipped.
VAULT_ROOT="${DRAUPNIR_VAULT_ROOT-/forge/vault}"

# Where slurmrestd answers. VLD-INF-SINDRI-001 puts the Slurm controller on
# REGIN; the control plane reaches it over HTTP rather than with `sbatch`,
# because ALVISS has no Slurm client tools and the container has no shell to
# run them from. Derived after the arguments, like every other name.
SCHEDULER_URL="${DRAUPNIR_SCHEDULER_URL-}"

# Where the site's Prometheus answers. VLD-INF-SINDRI-001 section 34 step 7
# puts it on REGIN, scraping the DCGM exporter on each appliance. The control
# plane only ever reads from it: the exporter is the collector, and a second
# path to the same GPUs would be a second thing to keep in step with a driver
# update. Derived after the arguments, like every other name.
PROMETHEUS_URL="${DRAUPNIR_PROMETHEUS_URL-}"

# The certificate and key the console proxy serves browsers with. From the
# Veldris internal CA (docs/runbook.md, Certificates).
#
# `--check` refuses to commission without them. SAD 9.5 is "TLS 1.3 only", and
# before RF-03 nothing in deploy/ mentioned TLS at all. RF-37 built the
# transport, and `--check` now asks the proxy to load these rather than asking
# whether the files exist: a file that exists and is not the certificate for
# its key is the failure a readability check cannot see.
TLS_CERTIFICATE="${DRAUPNIR_TLS_CERTIFICATE-}"
TLS_PRIVATE_KEY="${DRAUPNIR_TLS_PRIVATE_KEY-}"

# The internal signing CA of Decision S9, and the certificates the two mutually
# authenticated hops present (SAD 9.5, RF-37): the proxy's client certificate
# to the API, the API's own server certificate, and GULLINBURSTI's site
# certificate to MEGINGJORD. The last pair is required only where a registry is
# configured.
INTERNAL_CA="${DRAUPNIR_INTERNAL_CA-}"
PROXY_CLIENT_CERTIFICATE="${DRAUPNIR_PROXY_CLIENT_CERTIFICATE-}"
PROXY_CLIENT_PRIVATE_KEY="${DRAUPNIR_PROXY_CLIENT_PRIVATE_KEY-}"
API_TLS_CERTIFICATE="${DRAUPNIR_API_TLS_CERTIFICATE-}"
API_TLS_PRIVATE_KEY="${DRAUPNIR_API_TLS_PRIVATE_KEY-}"
FEDERATION_CLIENT_CERTIFICATE="${DRAUPNIR_FEDERATION_CLIENT_CERTIFICATE-}"
FEDERATION_CLIENT_PRIVATE_KEY="${DRAUPNIR_FEDERATION_CLIENT_PRIVATE_KEY-}"

# Who issues the tokens this control plane trusts, and who they are addressed
# to. MEGINGJORD is the identity provider for the Forge Matrix (SAD 9.3).
#
# The API refuses to start with neither these nor DRAUPNIR_DEV, because a
# control plane that starts with no way to authenticate anybody answers 401 to
# everything -- which reads as a broken deployment rather than as a missing
# setting, and gets debugged instead of configured (RF-01).
#
# The issuer defaults into the federation zone rather than the site zone:
# MEGINGJORD is one registry for the whole Forge Matrix and is not at a forge.
OIDC_ISSUER="${DRAUPNIR_OIDC_ISSUER-https://megingjord.veldris.internal}"
OIDC_AUDIENCE="${DRAUPNIR_OIDC_AUDIENCE-draupnir-control-plane}"

# Empty derives it from the issuer by the discovery convention. Set it where
# the provider publishes its keys somewhere else.
OIDC_JWKS_URL="${DRAUPNIR_OIDC_JWKS_URL-}"

# The appliances cabled into the ring, comma separated. Empty means all of
# them, which is the ordinary case. `-` rather than `:-` so a site can set it
# explicitly empty and mean it.
#
# Named rather than counted: ring membership is which machines have a DAC cable
# between them, and a count cannot say which two. VLD-WIR-SINDRI-001 section
# 7.4 recables the two survivors of an appliance failure as a direct pair, and
# gap G6 holds zero spare QSFP56 cables -- so that is the configuration this
# estate ends up in, and it must be declared rather than guessed. The worker
# records the declaration to the ledger, because a substrate run across two
# ranks is not comparable to one across three.
RING_MEMBERS="${DRAUPNIR_RING_MEMBERS-}"

# Where the supply daemon writes its status block. Empty by default and empty
# at Sindri today: the UPS is gap G1, on order. `-` rather than `:-` so a site
# can set it explicitly empty and mean it.
#
# The worker reads this file and the file only; DRAUPNIR opens no USB device.
# `draupnir-run.sh` bind-mounts it read-only into the worker container when it
# exists, so configuring a path before the daemon runs starts the worker
# unchanged and says why.
SUPPLY_STATUS="${DRAUPNIR_WORKER_SUPPLY_STATUS-}"

# Where MEGINGJORD answers, and the key this forge signs its chain head with.
# Both empty at Sindri today, and truthfully so: VLD-INF-SINDRI-001 gap G-E21
# has the WireGuard link to Veldris_NXT unbuilt, so the name does not resolve.
#
# Both or neither. A registry with no key submits unsigned heads, which
# MEGINGJORD refuses in as many words -- "an unsigned head is not a claim about
# a chain, it is a packet" -- and a stream of rejections reads like a broken
# tunnel rather than a missing setting. With neither, the anchor duty alarms
# saying the chain is anchored nowhere, which is what is true (SAD 11A.3).
#
# The key names itself: the identifier MEGINGJORD knows it by is derived from
# the public half, so there is no second setting to keep in step with it.
REGISTRY_URL="${DRAUPNIR_REGISTRY_URL-}"
SITE_SIGNING_KEY="${DRAUPNIR_SITE_SIGNING_KEY-}"

# Where the secrets this estate brokers to jobs are held: a JSON object of name
# to value, readable only by the worker's user. Empty at Sindri, which brokers
# none to a training job today.
#
# A file rather than the process environment, because the environment of a
# long-lived process is readable from /proc by anything running as the same
# user and is inherited by every child it spawns -- which is the opposite of
# what a lease is for. The worker never writes a value into a job: it issues a
# short-lived lease and the job environment carries the reference (threat T6).
SECRET_STORE="${DRAUPNIR_SECRET_STORE-}"

# Where a curator drops a jurisdiction's retrieved sources, one directory per
# ISO 3166-1 alpha-3 code, and where the evaluation sets live.
#
# A directory rather than a fetch: retrieving a corpus is outbound traffic to a
# host no allow-list entry covers, and threat T11 makes that the broker's
# decision rather than a background job's. On this estate the curator copies
# the files onto the mount, which is what VLD-INF-SINDRI-001 describes.
#
# The evaluation sets are not optional in the way an empty setting usually is.
# Curation refuses without them, and that is deliberate: SAD 6.1 makes
# `decontamination_confirmed` a condition of reaching CURATED, and a corpus
# curated without the check is one whose evaluation scores measure the overlap
# with the test set rather than the model.
INCOMING_ROOT="${DRAUPNIR_INCOMING_ROOT-${VAULT_ROOT:+${VAULT_ROOT}/incoming}}"
EVALUATION_SETS="${DRAUPNIR_EVALUATION_SETS-${VAULT_ROOT:+${VAULT_ROOT}/evaluation}}"

# DRAUPNIR_WORKER_STAND_IN is deliberately not written here.
#
# It makes the worker run a development executor instead of the driver each
# specification names, and derive gate measurements from an artefact's digest
# instead of measuring anything. That is for `make procedure` on a machine with
# no GPU. An estate that set it would record checkpoints nobody trained and
# gates nobody measured, and the chain would carry both for as long as it
# exists -- so it is absent from the unit environment rather than present and
# empty, and an operator who wants it exports it for one command themselves.

# The generic resource type Slurm knows this estate's accelerator by.
# VLD-INF-SINDRI-001 section 34 declares `Name=gpu Type=gb10` in gres.conf and
# every batch script in Part 5 asks for `gpu:gb10:1`. Empty at a forge whose
# scheduler declares no type, where a job asks for an untyped count instead.
ACCELERATOR="${DRAUPNIR_ACCELERATOR-gb10}"

# The fabric probe of SAD 11.3, which is the estate's in every part. The binary
# is built on the appliances by VLD-INF-SINDRI-001 Procedure S5 and is on no
# PATH; the interface and RoCE device names are from the ring configuration,
# and section 48.2 warns that a driver update can rename them. The baseline is
# the figure acceptance test A3 measures at commissioning: without it the 80
# per cent alarm has nothing to compare against and cannot be raised.
FABRIC_PROBE_BINARY="${DRAUPNIR_FABRIC_PROBE_BINARY-/forge/tools/nccl-tests/build/all_reduce_perf}"
FABRIC_INTERFACE="${DRAUPNIR_FABRIC_INTERFACE-enp1s0f0np0}"
FABRIC_HCA="${DRAUPNIR_FABRIC_HCA-rocep1s0f0,rocep1s0f1,roceP2p1s0f0,roceP2p1s0f1}"
FABRIC_BASELINE_GBPS="${DRAUPNIR_FABRIC_BASELINE_GBPS-0}"

# The units and the image each runs, shared with rollout.sh and rollback.sh
# so that the three cannot disagree about what is deployed.
# shellcheck source=deploy/lib.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
UNITS=("${DRAUPNIR_UNITS[@]}")

# systemd on Linux, launchd on macOS. VLD-INF-SINDRI-001 Rev 3.3 section 6
# makes ALVISS a Mac mini M4 Pro, so the second one is not hypothetical: it is
# the manager the host this installer is written for actually has.
PLATFORM="$(draupnir_platform)"
case "${PLATFORM}" in
  linux)
    UNIT_DIR="${DRAUPNIR_UNIT_DIR:-${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user}"
    ;;
  darwin)
    UNIT_DIR="${DRAUPNIR_UNIT_DIR:-${HOME}/Library/LaunchAgents}"
    # launchd has no journal, so the agents need somewhere to write. Named
    # here and rendered into the plists, so the runbook and the units cannot
    # disagree about where an operator looks.
    LOG_DIR="${DRAUPNIR_LOG_DIR:-${HOME}/Library/Logs/draupnir}"
    ;;
esac

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
  --registry HOST     Image registry. Default: registry.<site domain>
  --site SITE         Forge identifier written to the configuration, and the
                      first label of the DNS zone every default name sits in.
                      Default: sindri, giving sindri.veldris.internal
  --check             Run the preflight and dependency checks and stop. Changes
                      nothing on this host; the deep checks may build the
                      checkout's virtual environment. Use this before a
                      maintenance window, and again after the credentials go
                      into secrets.env -- that is when the deep checks can
                      first run.
  --uninstall         Stop and remove the units, the wrapper and the generated
                      configuration. Leaves secrets.env and the state directory,
                      which this did not create the contents of.
  --dry-run           Print every action without performing it.
  --skip-dependency-check
                      Do not check PostgreSQL and MinIO. For a host being
                      commissioned before ANDVARI is reachable.
  -h, --help          This text.

Every default hostname is derived from the site's DNS zone, which is
`<site>.veldris.internal` unless DRAUPNIR_SITE_DOMAIN says otherwise. At Sindri
that is sindri.veldris.internal, which is the zone REGIN's dnsmasq actually
serves (VLD-INF-SINDRI-001 section 10.4). A name one label short of it does not
resolve.

Environment: DRAUPNIR_SITE_DOMAIN, DRAUPNIR_POSTGRES_HOST,
DRAUPNIR_OBJECT_STORE_HOST, DRAUPNIR_VAULT_ROOT, DRAUPNIR_SCHEDULER_URL,
DRAUPNIR_PROMETHEUS_URL, DRAUPNIR_WORKER_SUPPLY_STATUS, DRAUPNIR_RING_MEMBERS,
DRAUPNIR_REGISTRY_URL, DRAUPNIR_SITE_SIGNING_KEY, DRAUPNIR_SECRET_STORE,
DRAUPNIR_INCOMING_ROOT, DRAUPNIR_EVALUATION_SETS,
DRAUPNIR_OIDC_ISSUER, DRAUPNIR_OIDC_AUDIENCE,
DRAUPNIR_TLS_CERTIFICATE, DRAUPNIR_TLS_PRIVATE_KEY, DRAUPNIR_INTERNAL_CA,
DRAUPNIR_PROXY_CLIENT_CERTIFICATE, DRAUPNIR_PROXY_CLIENT_PRIVATE_KEY,
DRAUPNIR_API_TLS_CERTIFICATE, DRAUPNIR_API_TLS_PRIVATE_KEY,
DRAUPNIR_FEDERATION_CLIENT_CERTIFICATE, DRAUPNIR_FEDERATION_CLIENT_PRIVATE_KEY,
DRAUPNIR_ACCELERATOR, the DRAUPNIR_FABRIC_* probe settings and the
DRAUPNIR_*_DIR paths override the defaults. DRAUPNIR_FABRIC_BASELINE_GBPS is
the figure acceptance test A3 measured; leave it at 0 until there is one, and
the probe reports a reading without an alarm rather than alarming against a
number nobody took. Set DRAUPNIR_VAULT_ROOT to the empty string for a forge
with no vault; the checks then report it as unconfigured rather than broken.
Secrets are never read here.

The service manager is systemd on Linux and launchd on macOS, chosen from the
platform. On macOS the units are agents in ~/Library/LaunchAgents and their
output goes to ~/Library/Logs/draupnir, because launchd has no journal.
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

# Derived here rather than above, because `--site` decides all of them and is
# only known once the arguments have been read. `:=` fills in what neither the
# environment nor an argument supplied, so precedence reads argument, then
# environment, then the site.
: "${SITE_DOMAIN:=$(draupnir_site_domain "${SITE_ID}")}"
export DRAUPNIR_SITE_DOMAIN="${SITE_DOMAIN}"
: "${REGISTRY:=$(draupnir_host_for registry "${SITE_ID}")}"
: "${POSTGRES_HOST:=$(draupnir_host_for andvari "${SITE_ID}")}"
: "${OBJECT_STORE_HOST:=$(draupnir_host_for andvari "${SITE_ID}")}"
: "${SCHEDULER_URL:=http://$(draupnir_host_for regin "${SITE_ID}"):6820}"
: "${PROMETHEUS_URL:=http://$(draupnir_host_for regin "${SITE_ID}"):9090}"

# ---------------------------------------------------------------------------
# Preflight. Everything checked before anything is written, so a host that
# cannot run this is told so before it is half-installed.
# ---------------------------------------------------------------------------
preflight() {
  say "preflight"
  info "platform: ${PLATFORM}"
  info "site: ${SITE_ID} (${SITE_DOMAIN})"

  command -v "${PODMAN}" >/dev/null 2>&1 \
    || fail "${PODMAN} not found. The control plane runs as rootless containers (AC-Q7); install podman first."
  info "podman: $("${PODMAN}" --version 2>/dev/null || echo unknown)"

  [[ "$(id -u)" -ne 0 ]] \
    || fail "run this as the service account, not as root. The units are rootless by requirement (AC-Q7), and installing them into root's service manager is not the same system."

  case "${PLATFORM}" in
    linux) preflight_systemd ;;
    darwin) preflight_launchd ;;
  esac
}

preflight_systemd() {
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

preflight_launchd() {
  command -v launchctl >/dev/null 2>&1 \
    || fail "launchctl not found. This looks like macOS and has no launchd; nothing here can start a unit."

  # The podman client on macOS talks to a Linux virtual machine. Without one
  # every podman command fails in a way that reads like a broken install, so
  # this asks the question directly and answers it with the command to run.
  if "${PODMAN}" info >/dev/null 2>&1; then
    info "podman machine: running"
  else
    fail "$(printf 'the podman machine is not running, so no container can start.\n  start it:   %s machine start\n  or make it: %s machine init --cpus 2 --memory 4096 --now' "${PODMAN}" "${PODMAN}")"
  fi

  # The agents run in the `gui` domain, which exists only while the service
  # account is logged in. This is the macOS counterpart of systemd lingering
  # and it has no counterpart of `enable-linger`: the remedies are a logged-in
  # session or automatic login, and both are the site's decision rather than
  # this script's.
  launchctl print "gui/$(id -u)" >/dev/null 2>&1 \
    || fail "no launchd gui domain for uid $(id -u). Run this from a logged-in session as the service account, not over a bare ssh with no console session."
  info "launchd gui domain: available"

  # FileVault is enabled on ALVISS by VLD-INF-SINDRI-001 Procedure S10. It is
  # worth stating because it decides what happens after a power cut, and the
  # answer is not the one the systemd path gives.
  if command -v fdesetup >/dev/null 2>&1 \
    && [[ "$(fdesetup status 2>/dev/null || true)" == *"FileVault is On"* ]]; then
    warn "FileVault is on: this host does not return unattended after a power cut."
    warn "  the disk must be unlocked at the console before any agent runs."
    warn "  for a planned reboot use: sudo fdesetup authrestart"
  fi

  # Everything the container reads from disk has to be inside a path the
  # machine shares. podman machine shares the user's home directory and little
  # else, so a configuration directory outside it resolves on the client and
  # not in the VM.
  case "${CONFIG_DIR}" in
    "${HOME}"/*) : ;;
    *) warn "${CONFIG_DIR} is outside ${HOME}, which the podman machine does not share by default." ;;
  esac
}

# Reachability only. Credentials are SVALINN's and are not read here, so this
# proves the port answers rather than that the schema is right -- the pipeline's
# migration stage is what proves the schema.
# Whether a name resolves at all. 0 yes, 1 no, 2 could not tell.
#
# Kept separate from the connection test because the two failures have nothing
# in common. A name that does not resolve is almost always the wrong DNS zone,
# which is a one word fix here; a name that resolves and does not answer is a
# machine or a service that is down, which is somebody else's job and a
# different phone call. Reporting both as "no answer" sent an operator to the
# infrastructure team for a typo in a domain.
#
# Three resolvers because there is no portable one: `getent` is glibc and not
# on macOS, `dscacheutil` is macOS only, and `host` is neither guaranteed. When
# none is present this says so rather than guessing, and the connection test
# still runs.
resolves() {
  local name="${1:?resolves needs a name}"

  # A literal address needs no resolver, and asking one about it is how a
  # host configured by address gets reported as broken.
  [[ "${name}" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && return 0

  if command -v getent >/dev/null 2>&1; then
    getent hosts "${name}" >/dev/null 2>&1 && return 0
    return 1
  fi
  if command -v dscacheutil >/dev/null 2>&1; then
    dscacheutil -q host -a name "${name}" 2>/dev/null | grep -q "ip_address" && return 0
    return 1
  fi
  if command -v host >/dev/null 2>&1; then
    host "${name}" >/dev/null 2>&1 && return 0
    return 1
  fi
  return 2
}

check_dependencies() {
  say "dependencies on ANDVARI"
  info "site domain: ${SITE_DOMAIN}"
  if ((SKIP_DEPENDENCY_CHECK)); then
    warn "skipped by --skip-dependency-check"
    return 0
  fi

  local failures=0 unresolved=0
  local probe
  for probe in "PostgreSQL:${POSTGRES_HOST}:${POSTGRES_PORT}" "MinIO:${OBJECT_STORE_HOST}:${OBJECT_STORE_PORT}"; do
    local name="${probe%%:*}" rest="${probe#*:}"
    local host="${rest%%:*}" port="${rest##*:}"

    local verdict=0
    resolves "${host}" || verdict=$?
    if ((verdict == 1)); then
      warn "${name} at ${host}: does not resolve"
      warn "  the site domain is ${SITE_DOMAIN}; ${host} is not a name in it,"
      warn "  or REGIN's dnsmasq has no record for it (VLD-INF-SINDRI-001 section 10.4)."
      failures=$((failures + 1))
      unresolved=$((unresolved + 1))
      continue
    fi
    if ((verdict == 2)); then
      warn "${name} at ${host}: cannot check name resolution on this host"
      warn "  no getent, dscacheutil or host command. Trying the connection anyway."
    fi

    if timeout 5 bash -c "exec 3<>/dev/tcp/${host}/${port}" 2>/dev/null; then
      info "${name} at ${host}:${port}: answering"
    else
      warn "${name} at ${host}:${port}: resolves, no answer"
      failures=$((failures + 1))
    fi
  done

  if ((failures)); then
    if ((unresolved)); then
      fail "$(printf '%d of 2 dependencies did not resolve. That is a naming problem rather than an outage: check --site and DRAUPNIR_SITE_DOMAIN, which currently give %s. Nothing was changed.' "${unresolved}" "${SITE_DOMAIN}")"
    fi
    fail "$(printf '%d of 2 dependencies did not answer. SAD 7.2 puts both on ANDVARI as existing instances: this installer does not stand them up. Fix the dependency, or pass --skip-dependency-check to commission ahead of it.' "${failures}")"
  fi

  check_dependencies_accept_us
  check_tls
}

# TLS, and a refusal rather than a warning. SAD 9.5 is "TLS 1.3 only", with
# mTLS between control plane components, and the console sends a session
# cookie marked `Secure` -- which a browser will not send over plain HTTP at
# all, so a deployment with no certificate is one where signing in appears to
# work and every subsequent request is anonymous.
#
# RF-37. This used to ask whether the files were readable, which passed for a
# certificate that was not the one for its key and for a file that was not a
# certificate at all. It now asks the things that load them:
# - the console image runs `nginx -t` with the material mounted where
#   docker/nginx.conf reads it, which loads the configuration, both
#   certificates, both keys and the CA;
# - `openssl` holds the API's certificate, and GULLINBURSTI's where a registry
#   is configured, to the internal CA and to its own key (check_certificate).
#
# Skipped where DRAUPNIR_DEV is set, which is the same escape the plug-in
# loader and the authentication layer use and should look the same.
check_tls() {
  case "${DRAUPNIR_DEV:-}" in
    1 | true | TRUE | yes)
      info "tls: not required, DRAUPNIR_DEV is set"
      return 0
      ;;
  esac

  local missing=()
  [[ -z "${TLS_CERTIFICATE}" ]] && missing+=("DRAUPNIR_TLS_CERTIFICATE")
  [[ -z "${TLS_PRIVATE_KEY}" ]] && missing+=("DRAUPNIR_TLS_PRIVATE_KEY")
  [[ -z "${INTERNAL_CA}" ]] && missing+=("DRAUPNIR_INTERNAL_CA")
  [[ -z "${PROXY_CLIENT_CERTIFICATE}" ]] && missing+=("DRAUPNIR_PROXY_CLIENT_CERTIFICATE")
  [[ -z "${PROXY_CLIENT_PRIVATE_KEY}" ]] && missing+=("DRAUPNIR_PROXY_CLIENT_PRIVATE_KEY")
  [[ -z "${API_TLS_CERTIFICATE}" ]] && missing+=("DRAUPNIR_API_TLS_CERTIFICATE")
  [[ -z "${API_TLS_PRIVATE_KEY}" ]] && missing+=("DRAUPNIR_API_TLS_PRIVATE_KEY")
  if [[ -n "${REGISTRY_URL}" ]]; then
    [[ -z "${FEDERATION_CLIENT_CERTIFICATE}" ]] && missing+=("DRAUPNIR_FEDERATION_CLIENT_CERTIFICATE")
    [[ -z "${FEDERATION_CLIENT_PRIVATE_KEY}" ]] && missing+=("DRAUPNIR_FEDERATION_CLIENT_PRIVATE_KEY")
  fi
  if ((${#missing[@]})); then
    fail "$(printf 'the TLS material is not configured: %s is not set. SAD 9.5 is TLS 1.3 only, with mTLS between control plane components, and the session cookie is marked Secure -- over plain HTTP a browser will not send it, so signing in appears to work and every request after it is anonymous. Obtain the certificates from the Veldris internal CA (docs/runbook.md, Certificates), or set DRAUPNIR_DEV=1 on a machine with no real data.' "$(IFS=', '; echo "${missing[*]}")")"
  fi

  # Each end loads its material in the image it will run in: the revision
  # being installed, or the reference already recorded. --check pulls nothing,
  # so with neither there is nothing to load the files, and that is said rather
  # than something weaker checked instead.
  local web_image api_image
  web_image="$(known_image draupnir-web)"
  api_image="$(known_image draupnir-api)"
  if [[ -z "${web_image}" || -z "${api_image}" ]]; then
    fail "the TLS material cannot be checked: the console and API images are not both known on this host, so nothing can load it. Pass --revision <sha> and pull those images first with ${PODMAN} pull; --check pulls nothing."
  fi

  local tested
  if ! tested="$("${PODMAN}" run --rm --pull=never --read-only \
    --tmpfs /tmp:rw,size=16m \
    --entrypoint /usr/sbin/nginx \
    --volume "${TLS_CERTIFICATE}:/etc/draupnir/tls/server.pem:ro" \
    --volume "${TLS_PRIVATE_KEY}:/etc/draupnir/tls/server.key:ro" \
    --volume "${PROXY_CLIENT_CERTIFICATE}:/etc/draupnir/tls/proxy-client.pem:ro" \
    --volume "${PROXY_CLIENT_PRIVATE_KEY}:/etc/draupnir/tls/proxy-client.key:ro" \
    --volume "${INTERNAL_CA}:/etc/draupnir/tls/internal-ca.pem:ro" \
    "${web_image}" -t 2>&1)"; then
    fail "$(printf 'the console proxy does not load its TLS material:\n%s\nEach file is mounted where docker/nginx.conf reads it and nginx refused one. A certificate that is not the one for its key, or a file the container user cannot read (docs/runbook.md, Certificates), is the usual cause.' "${tested}")"
  fi
  info "tls: the console proxy loads its certificate, its client certificate and the CA"

  check_python_end "the API" server "${API_TLS_CERTIFICATE}" "${API_TLS_PRIVATE_KEY}" "${api_image}"
  if [[ -n "${REGISTRY_URL}" ]]; then
    check_python_end "GULLINBURSTI" client \
      "${FEDERATION_CLIENT_CERTIFICATE}" "${FEDERATION_CLIENT_PRIVATE_KEY}" "${api_image}"
  fi
}

# The image a unit will run: the revision being installed, or the reference
# already recorded on this host. Empty when neither is known.
known_image() {
  local unit="$1"
  if [[ -n "${REVISION}" ]]; then
    draupnir_image_for "${unit}" "${REVISION}" "${REGISTRY}"
  elif [[ -r "${STATE_DIR}/image-${unit}" ]]; then
    cat "${STATE_DIR}/image-${unit}"
  fi
  return 0
}

# One certificate a Python end presents, loaded the way that end loads it: in
# the API image, as the user the unit runs as, through
# `draupnir.svalinn.transport`, which also checks the internal CA issued it. A
# host-side openssl would read the files as the service account, and under
# rootless podman that is not who reads them in deployment.
check_python_end() {
  local who="$1" role="$2" certificate="$3" private_key="$4" image="$5" checked
  if ! checked="$("${PODMAN}" run --rm --pull=never --read-only \
    --entrypoint /app/.venv/bin/python \
    --volume "${certificate}:/etc/draupnir/tls/check.pem:ro" \
    --volume "${private_key}:/etc/draupnir/tls/check.key:ro" \
    --volume "${INTERNAL_CA}:/etc/draupnir/tls/internal-ca.pem:ro" \
    "${image}" -m draupnir.svalinn.transport "${role}" \
    /etc/draupnir/tls/check.pem /etc/draupnir/tls/check.key /etc/draupnir/tls/internal-ca.pem 2>&1)"; then
    fail "$(printf '%s does not load its TLS material:\n%s' "${who}" "${checked}")"
  fi
  info "tls: ${who} loads its certificate, which the internal CA issued"
}

# A socket answering proves the machine is on. It does not prove there is a
# `draupnir` database, a `draupnir` bucket, or a credential that works, and at
# Sindri none of those exists until somebody makes them: VLD-INF-SINDRI-001
# Procedure S8 creates the `mlflow` database and nothing else, and starts MinIO
# with no bucket. All three pass the check above and none lets the control
# plane start.
#
# So this asks the harder question, with the drivers the application itself
# uses. It is a separate step rather than part of the loop above because it
# needs things the socket test does not -- a checkout, uv, and credentials --
# and each of those being absent is a different sentence rather than a failure.
check_dependencies_accept_us() {
  say "will they accept us"

  local root="${HERE}/.."
  local script="${root}/scripts/preflight.py"

  if [[ ! -r "${script}" ]]; then
    warn "no ${script}: the deep checks were skipped."
    warn "  they are in the repository, and this is running from somewhere else."
    return 0
  fi
  if ! command -v uv >/dev/null 2>&1; then
    warn "uv is not installed, so the deep checks were skipped."
    warn "  they ask whether the database and the bucket will accept this application,"
    warn "  which a socket test cannot. VLD-INF-SINDRI-001 Procedure S10 installs uv."
    return 0
  fi

  # The configuration the container will be given, read the same way podman
  # reads it. Sourced in a subshell so nothing here inherits a credential: this
  # script never holds one, and that stays true of the process as well as of
  # the files it writes.
  local output=""
  output="$(
    set -a
    # Seeded before the file is sourced so that the very first --check, run
    # before anything is installed, still reports whether the vault is mounted.
    # A draupnir.env written by a previous run overrides it, which is the right
    # precedence: what is configured beats what would be.
    DRAUPNIR_VAULT_ROOT="${VAULT_ROOT}"
    DRAUPNIR_SCHEDULER_URL="${SCHEDULER_URL}"
    # shellcheck disable=SC1091
    [[ -r "${CONFIG_DIR}/draupnir.env" ]] && source "${CONFIG_DIR}/draupnir.env"
    # shellcheck disable=SC1091
    [[ -r "${CONFIG_DIR}/secrets.env" ]] && source "${CONFIG_DIR}/secrets.env"
    set +a
    cd "${root}" && uv run --frozen python scripts/preflight.py 2>&1
  )" || true

  local reported=0 refusals=0
  local dependency verdict detail remedy
  while IFS='|' read -r dependency verdict detail remedy; do
    reported=$((reported + 1))
    case "${verdict}" in
      ok)
        info "${dependency}: ${detail}"
        ;;
      unverified)
        warn "${dependency}: not verified. ${detail}"
        [[ -n "${remedy}" ]] && warn "  ${remedy}"
        ;;
      *)
        warn "${dependency}: ${verdict}. ${detail}"
        [[ -n "${remedy}" ]] && warn "  ${remedy}"
        refusals=$((refusals + 1))
        ;;
    esac
  done < <(printf '%s\n' "${output}" \
    | grep -E '^[a-z-]+\|(ok|unreachable|auth-refused|missing|unverified)\|' || true)

  if ((reported == 0)); then
    warn "the deep checks produced nothing. What they printed was:"
    printf '%s\n' "${output}" | sed 's/^/        /' >&2
    warn "  this is a fault in the check rather than in the estate; the socket tests above passed."
    return 0
  fi

  if ((refusals)); then
    fail "$(printf '%d dependency check(s) refused. Each line above names what to do; none of them is fixed by re-running this. Nothing on this host was changed.' "${refusals}")"
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
DRAUPNIR_VAULT_ROOT=${VAULT_ROOT}
DRAUPNIR_SCHEDULER_URL=${SCHEDULER_URL}
DRAUPNIR_PROMETHEUS_URL=${PROMETHEUS_URL}
DRAUPNIR_WORKER_SUPPLY_STATUS=${SUPPLY_STATUS}
DRAUPNIR_RING_MEMBERS=${RING_MEMBERS}
DRAUPNIR_REGISTRY_URL=${REGISTRY_URL}
DRAUPNIR_SITE_SIGNING_KEY=${SITE_SIGNING_KEY}
DRAUPNIR_SECRET_STORE=${SECRET_STORE}
DRAUPNIR_INCOMING_ROOT=${INCOMING_ROOT}
DRAUPNIR_EVALUATION_SETS=${EVALUATION_SETS}
DRAUPNIR_OIDC_ISSUER=${OIDC_ISSUER}
DRAUPNIR_TLS_CERTIFICATE=${TLS_CERTIFICATE}
DRAUPNIR_TLS_PRIVATE_KEY=${TLS_PRIVATE_KEY}
DRAUPNIR_INTERNAL_CA=${INTERNAL_CA}
DRAUPNIR_PROXY_CLIENT_CERTIFICATE=${PROXY_CLIENT_CERTIFICATE}
DRAUPNIR_PROXY_CLIENT_PRIVATE_KEY=${PROXY_CLIENT_PRIVATE_KEY}
DRAUPNIR_API_TLS_CERTIFICATE=${API_TLS_CERTIFICATE}
DRAUPNIR_API_TLS_PRIVATE_KEY=${API_TLS_PRIVATE_KEY}
DRAUPNIR_FEDERATION_CLIENT_CERTIFICATE=${FEDERATION_CLIENT_CERTIFICATE}
DRAUPNIR_FEDERATION_CLIENT_PRIVATE_KEY=${FEDERATION_CLIENT_PRIVATE_KEY}
DRAUPNIR_OIDC_AUDIENCE=${OIDC_AUDIENCE}
DRAUPNIR_OIDC_JWKS_URL=${OIDC_JWKS_URL}
DRAUPNIR_ACCELERATOR=${ACCELERATOR}
DRAUPNIR_FABRIC_PROBE_BINARY=${FABRIC_PROBE_BINARY}
DRAUPNIR_FABRIC_INTERFACE=${FABRIC_INTERFACE}
DRAUPNIR_FABRIC_HCA=${FABRIC_HCA}
DRAUPNIR_FABRIC_BASELINE_GBPS=${FABRIC_BASELINE_GBPS}
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
    info "  DRAUPNIR_OBJECT_STORE_ACCESS_KEY and DRAUPNIR_OBJECT_STORE_SECRET_KEY."
    info "  DRAUPNIR_SCHEDULER_TOKEN and DRAUPNIR_SCHEDULER_USER are optional:"
    info "  without them --check reports the scheduler as reachable and does not"
    info "  check that it has the partitions runs are placed on."
  fi
}

# ---------------------------------------------------------------------------
# Units. Rendered from the templates beside this script so that what runs on
# ALVISS is what is in the repository, reviewed, rather than a heredoc.
# ---------------------------------------------------------------------------
install_units() {
  say "units"
  run mkdir -p "${UNIT_DIR}" "${LIBEXEC_DIR}"
  [[ -n "${LOG_DIR}" ]] && run mkdir -p "${LOG_DIR}"
  run install -m 0755 "${HERE}/units/draupnir-run.sh" "${LIBEXEC_DIR}/draupnir-run.sh"
  # The wrapper sources this at every start, for the estate's names and the
  # platform test. Installed together so the two cannot be different versions.
  run install -m 0644 "${HERE}/lib.sh" "${LIBEXEC_DIR}/lib.sh"
  info "installed ${LIBEXEC_DIR}/draupnir-run.sh and lib.sh"

  # An absolute path, because neither manager gives a unit a useful PATH and
  # launchd's is the narrower of the two: a Homebrew podman is not on it.
  local podman_path
  podman_path="$(command -v "${PODMAN}")"

  local suffix
  case "${PLATFORM}" in
    linux) suffix="service" ;;
    darwin) suffix="plist" ;;
  esac

  local unit template target
  for unit in "${UNITS[@]}"; do
    template="${HERE}/units/${unit}.${suffix}.in"
    target="${UNIT_DIR}/$(draupnir_unit_filename "${unit}")"
    [[ -r "${template}" ]] || fail "missing unit template: ${template}"
    if ((DRY_RUN)); then
      printf '    [dry-run] render %s -> %s\n' "${template}" "${target}"
    else
      sed -e "s|@LIBEXEC@|${LIBEXEC_DIR}|g" \
          -e "s|@PODMAN@|${podman_path}|g" \
          -e "s|@SITE_ID@|${SITE_ID}|g" \
          -e "s|@LABEL@|$(draupnir_launchd_label "${unit}")|g" \
          -e "s|@LOGDIR@|${LOG_DIR}|g" \
          "${template}" >"${target}"
      chmod 0644 "${target}"
      info "wrote ${target}"
    fi
  done

  run draupnir_service_reload
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
    run draupnir_service_set_image "${unit}" "${image}"
    run bash -c "printf '%s\n' '${image}' >'${STATE_DIR}/image-${unit}'"
  done
}

enable_units() {
  say "enable and start"
  local unit
  for unit in "${UNITS[@]}"; do
    run draupnir_service_enable "${unit}" "${UNIT_DIR}/$(draupnir_unit_filename "${unit}")"
  done
  for unit in "${UNITS[@]}"; do
    run draupnir_service_restart "${unit}"
  done
}

verify() {
  say "verify"
  ((DRY_RUN)) && { info "[dry-run] skipped"; return 0; }

  local unit failures=0
  for unit in "${UNITS[@]}"; do
    if draupnir_service_is_active "${unit}"; then
      info "${unit}: active"
    else
      warn "${unit}: not active"
      warn "  $(draupnir_service_logs_hint "${unit}")"
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
    run draupnir_service_disable "${unit}"
    run rm -f "${UNIT_DIR}/$(draupnir_unit_filename "${unit}")"
    run "${PODMAN}" rm --ignore --force "${unit}" >/dev/null 2>&1 || true
    run draupnir_service_unset_image "${unit}" 2>/dev/null || true
  done
  run draupnir_service_reload
  run rm -f "${LIBEXEC_DIR}/draupnir-run.sh" "${LIBEXEC_DIR}/lib.sh"
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
      info "nothing on this host was changed."
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

# Run when executed, define when sourced. A test that wants to ask `resolves`
# what it makes of a name should not have to commission a control plane to find
# out, and reading the function with a regular expression tests the reading
# rather than the function.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi

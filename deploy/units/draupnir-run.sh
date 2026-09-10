#!/usr/bin/env bash
# Start one DRAUPNIR control plane container. SAD 11.1 step 1, AC-Q7.
#
# The units call this rather than spelling `podman run` out three times,
# because the three differ in four details and agree in nine, and three copies
# of the nine is how they drift. There are now six units -- a systemd unit and
# a launchd agent for each -- which makes the same argument twice as strongly.
#
# **On resolving the image.** `rollout.sh` publishes a revision by setting
# `DRAUPNIR_IMAGE_<unit>` in the user manager's environment and restarting the
# unit, so that is the first place this looks. The manager's environment does
# not survive a reboot, so each successful start also writes the reference it
# used to the state directory and falls back to that file when the variable is
# absent. The file is written after resolution rather than before, so a rollout
# that names an image which cannot be pulled leaves the last good reference in
# place.
#
# That fallback earns its place across an *attended* restart, which on ALVISS
# is the only kind there is. It used to be justified here by "a control plane
# that comes back from a power cut with no image reference is a control plane
# that does not come back", which was reasoning about an unattended return this
# host cannot perform (RF-E22). Three separate things stop it, and removing any
# one of them changes nothing:
#
#   1. FileVault. Procedure S10 enables it, so the machine boots to an unlock
#      screen and nothing runs until somebody types a password at the console.
#   2. The `gui` launchd domain. These agents exist only while the service
#      account is logged in, and macOS has no counterpart of systemd's
#      `loginctl enable-linger`.
#   3. `podman machine`. On macOS it is a per-user virtual machine tied to that
#      user's session, so even a system LaunchDaemon would have nothing to talk
#      to.
#
# The fallback is still right. The reason is a person restarting the host, or
# `launchctl kickstart` after a rollout -- not an outage nobody attends.
set -euo pipefail

UNIT="${1:?usage: draupnir-run.sh <draupnir-api|draupnir-worker|draupnir-web>}"

# The estate's names and the platform test, from the one file that holds them:
# this script used to carry its own copy of both, and the registry copy was
# wrong. install.sh puts lib.sh beside this script in libexec; in the
# repository it is one directory up. Both are tried so the wrapper runs from
# either, and neither is guessed at.
_here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -r "${_here}/lib.sh" ]]; then
  # shellcheck source=deploy/lib.sh
  source "${_here}/lib.sh"
elif [[ -r "${_here}/../lib.sh" ]]; then
  # shellcheck source=deploy/lib.sh
  source "${_here}/../lib.sh"
else
  echo "draupnir-run.sh: no lib.sh beside or above ${_here}." >&2
  echo "  install.sh installs the two together; running one without the other" >&2
  echo "  is a half-finished install rather than a configuration to guess at." >&2
  exit 1
fi

STATE_DIR="${DRAUPNIR_STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/draupnir}"
CONFIG_DIR="${DRAUPNIR_CONFIG_DIR:-${XDG_CONFIG_HOME:-${HOME}/.config}/draupnir}"
REGISTRY="${DRAUPNIR_REGISTRY:-$(draupnir_host_for registry)}"
PODMAN="${DRAUPNIR_PODMAN:-podman}"

mkdir -p "${STATE_DIR}"

# The variable name rollout.sh sets: the unit name with hyphens as underscores.
image_var="DRAUPNIR_IMAGE_${UNIT//-/_}"
image="${!image_var:-}"
image_file="${STATE_DIR}/image-${UNIT}"

if [[ -z "${image}" && -r "${image_file}" ]]; then
  image="$(cat "${image_file}")"
fi

if [[ -z "${image}" ]]; then
  echo "${UNIT}: no image reference." >&2
  echo "  ${image_var} is unset and ${image_file} does not exist." >&2
  # The command differs by service manager, and an operator reading this at
  # 3 a.m. should be given the one that works on the host they are on.
  if [[ "$(draupnir_platform)" == "darwin" ]]; then
    echo "  Set one with: launchctl setenv ${image_var} ${REGISTRY}/${UNIT}:<revision>" >&2
  else
    echo "  Set one with: systemctl --user set-environment ${image_var}=${REGISTRY}/${UNIT}:<revision>" >&2
  fi
  exit 1
fi

# Per-unit differences, in one place. The worker runs the API image with a
# different command: the pipeline builds `api` and `web` and there is no third
# image, because the worker is the same application with a different entry
# point. Its own DRAUPNIR_IMAGE_ variable still wins if one is set, so a future
# separate worker image needs no change here.
case "${UNIT}" in
  draupnir-api)
    publish=("--publish" "${DRAUPNIR_API_BIND:-127.0.0.1}:${DRAUPNIR_API_PORT:-8000}:8000")
    command=()
    ;;
  draupnir-worker)
    publish=()
    command=("-m" "draupnir.worker")
    ;;
  draupnir-web)
    publish=("--publish" "${DRAUPNIR_WEB_BIND:-127.0.0.1}:${DRAUPNIR_WEB_PORT:-8080}:8080")
    command=()
    ;;
  *)
    echo "unknown unit: ${UNIT}" >&2
    exit 64
    ;;
esac

# The vault, for the unit that reads it. Only the worker does: SAD 11.3's
# capacity alarm is a periodic duty, and the API and the console have no
# business holding a file handle on ANDVARI's export.
#
# Read-only into the container even though the host mount is read-write. The
# worker measures the vault and writes nothing to it; `vault_admin.py
# reconcile --apply` is the thing that writes, and it runs on the host rather
# than in here. A mount that is writable by something that never writes is a
# mount that can be written by accident.
#
# Mounted at the same path inside as outside, so a message naming a path means
# the same thing wherever it is read. Absent when the host has no vault, which
# is why a development machine starts unchanged.
mounts=()
if [[ "${UNIT}" == "draupnir-worker" ]]; then
  # The vault root is in draupnir.env, which podman reads for the container and
  # this script does not. Read the one key rather than sourcing the file: the
  # installer guarantees draupnir.env holds no credential, but a wrapper that
  # executes a configuration file is a wrapper that would run whatever a
  # configuration file came to contain.
  vault_root="${DRAUPNIR_VAULT_ROOT:-}"
  if [[ -z "${vault_root}" && -r "${CONFIG_DIR}/draupnir.env" ]]; then
    vault_root="$(sed -n 's/^DRAUPNIR_VAULT_ROOT=//p' "${CONFIG_DIR}/draupnir.env" | tail -n 1)"
  fi

  if [[ -n "${vault_root}" && -d "${vault_root}" ]]; then
    mounts+=("--volume" "${vault_root}:${vault_root}:ro")
  elif [[ -n "${vault_root}" ]]; then
    # Not fatal: SAD 11.2 row 4 makes a missing vault a degraded mode rather
    # than a stop, and the worker reports it as a finding every tick. Said
    # once at start so the reason is in the log above the findings.
    echo "${UNIT}: ${vault_root} is not mounted; starting without it." >&2
    echo "  the vault capacity duty will report it every tick (runbook section 4)." >&2
  fi

  # The supply status file, on the same terms and for the same unit. SAD 11.2's
  # last row makes the worker the thing that acts on a mains transfer, and it
  # cannot read a file on the host from inside a container.
  #
  # Read-only, and here that is not a precaution but the whole relationship:
  # the daemon writes this file and DRAUPNIR only ever reads it. A writable
  # mount would let the control plane edit the evidence it acts on.
  #
  # Guarded on the file existing, so an estate with no supply -- which is every
  # estate today, gap G1 -- starts unchanged and reports nothing.
  supply_status="${DRAUPNIR_WORKER_SUPPLY_STATUS:-}"
  if [[ -z "${supply_status}" && -r "${CONFIG_DIR}/draupnir.env" ]]; then
    supply_status="$(sed -n 's/^DRAUPNIR_WORKER_SUPPLY_STATUS=//p' "${CONFIG_DIR}/draupnir.env" | tail -n 1)"
  fi

  if [[ -n "${supply_status}" && -f "${supply_status}" ]]; then
    mounts+=("--volume" "${supply_status}:${supply_status}:ro")
  elif [[ -n "${supply_status}" ]]; then
    # Not fatal, and not silent. A configured path that is not there means the
    # daemon is not running, and the estate then has no supply signal at all --
    # which is the state it is in with no UPS fitted, and worth saying out loud
    # to somebody who believes one is.
    echo "${UNIT}: ${supply_status} does not exist; starting with no supply signal." >&2
    echo "  a mains transfer will not force a checkpoint (VLD-WIR-SINDRI-001 7.4)." >&2
  fi
fi

# Configuration is file based (SAD 11.1 step 5). Secrets are not in it: the
# secrets file is optional, is never written by the installer, and is the seam
# SVALINN fills. `--env-file` is read by podman at start, so a rotated secret
# takes effect on restart without the unit changing.
env_files=()
[[ -r "${CONFIG_DIR}/draupnir.env" ]] && env_files+=("--env-file" "${CONFIG_DIR}/draupnir.env")
[[ -r "${CONFIG_DIR}/secrets.env" ]] && env_files+=("--env-file" "${CONFIG_DIR}/secrets.env")

printf '%s\n' "${image}" >"${image_file}"

# On macOS the podman client talks to a Linux virtual machine, and a Mac that
# has just been restarted has no machine running. Somebody is at the console --
# see the note at the top of this file -- but they have logged in and expect
# the control plane to come up, not to have to start a VM by hand. So this
# starts it once and then says plainly what to do if it still cannot be
# reached. `podman info` is the check rather than `machine list` because it
# asks the question that matters -- can this client reach a runtime -- rather
# than parsing a table.
if [[ "$(draupnir_platform)" == "darwin" ]]; then
  if ! "${PODMAN}" info >/dev/null 2>&1; then
    echo "${UNIT}: podman is not reachable; starting the machine" >&2
    "${PODMAN}" machine start >/dev/null 2>&1 || true
  fi
  if ! "${PODMAN}" info >/dev/null 2>&1; then
    echo "${UNIT}: the podman machine is not running and could not be started." >&2
    echo "  start it:   ${PODMAN} machine start" >&2
    echo "  or make it: ${PODMAN} machine init --cpus 2 --memory 4096 --now" >&2
    exit 1
  fi
fi

# Clear a stale container name before starting. A unit killed hard leaves the
# name taken, and the next start then fails on a name clash rather than on
# whatever caused the kill. This lived in the systemd units as ExecStartPre;
# it is here because launchd has no pre-start hook, and because one copy of it
# is better than four.
"${PODMAN}" rm --ignore --force "${UNIT}" >/dev/null 2>&1 || true

# `--rm` because the unit is the lifecycle: a stopped container that the
# service manager will recreate on the next start is a container that can
# disagree with the unit about which image it holds. `--pull=never` because
# rollout.sh pulls deliberately and a start that silently pulls is a start that
# can change the running revision without a rollout.
exec "${PODMAN}" run \
  --rm \
  --name "${UNIT}" \
  --pull=never \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  "${env_files[@]}" \
  "${mounts[@]}" \
  "${publish[@]}" \
  "${image}" \
  "${command[@]}"

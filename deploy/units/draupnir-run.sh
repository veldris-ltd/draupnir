#!/usr/bin/env bash
# Start one DRAUPNIR control plane container. SAD 11.1 step 1, AC-Q7.
#
# The systemd units call this rather than spelling `podman run` out three
# times, because the three differ in four details and agree in nine, and three
# copies of the nine is how they drift.
#
# **On resolving the image.** `rollout.sh` publishes a revision by setting
# `DRAUPNIR_IMAGE_<unit>` in the user manager's environment and restarting the
# unit, so that is the first place this looks. The manager's environment does
# not survive a reboot, though, and a control plane that comes back from a
# power cut with no image reference is a control plane that does not come back.
# So each successful start writes the reference it used to the state directory
# and falls back to that file when the variable is absent. The file is written
# after resolution rather than before, so a rollout that names an image which
# cannot be pulled leaves the last good reference in place.
set -euo pipefail

UNIT="${1:?usage: draupnir-run.sh <draupnir-api|draupnir-worker|draupnir-web>}"

STATE_DIR="${DRAUPNIR_STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/draupnir}"
CONFIG_DIR="${DRAUPNIR_CONFIG_DIR:-${XDG_CONFIG_HOME:-${HOME}/.config}/draupnir}"
REGISTRY="${DRAUPNIR_REGISTRY:-registry.veldris.internal}"
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
  echo "  Set one with: systemctl --user set-environment ${image_var}=${REGISTRY}/${UNIT}:<revision>" >&2
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

# Configuration is file based (SAD 11.1 step 5). Secrets are not in it: the
# secrets file is optional, is never written by the installer, and is the seam
# SVALINN fills. `--env-file` is read by podman at start, so a rotated secret
# takes effect on restart without the unit changing.
env_files=()
[[ -r "${CONFIG_DIR}/draupnir.env" ]] && env_files+=("--env-file" "${CONFIG_DIR}/draupnir.env")
[[ -r "${CONFIG_DIR}/secrets.env" ]] && env_files+=("--env-file" "${CONFIG_DIR}/secrets.env")

printf '%s\n' "${image}" >"${image_file}"

# `--rm` because the unit is the lifecycle: a stopped container that systemd
# will recreate on the next start is a container that can disagree with the
# unit about which image it holds. `--pull=never` because rollout.sh pulls
# deliberately and a start that silently pulls is a start that can change the
# running revision without a rollout.
exec "${PODMAN}" run \
  --rm \
  --name "${UNIT}" \
  --pull=never \
  --read-only \
  --cap-drop=ALL \
  --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  "${env_files[@]}" \
  "${publish[@]}" \
  "${image}" \
  "${command[@]}"

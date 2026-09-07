# shellcheck shell=bash
# Shared facts about the control plane deployment. Sourced by install.sh,
# rollout.sh and rollback.sh.
#
# This file exists because three scripts had independently written down which
# units there are and which image each one runs, and two of them had it wrong:
# they resolved `draupnir-worker` to an image of that name, and the pipeline
# builds `api` and `web` only. A rollout would have failed on the pull.
#
# The worker is the API image with a different entry point -- the same
# application, a different process -- so there is no third image to build.

# The units of the control plane, in the order a human would name them.
DRAUPNIR_UNITS=("draupnir-api" "draupnir-worker" "draupnir-web")

# Print the image reference for one unit at one revision.
#
#   draupnir_image_for <unit> <revision> [registry]
draupnir_image_for() {
  local unit="${1:?draupnir_image_for needs a unit}"
  local revision="${2:?draupnir_image_for needs a revision}"
  local registry="${3:-${DRAUPNIR_REGISTRY:-registry.veldris.internal}}"

  local component
  case "${unit}" in
    draupnir-worker) component="draupnir-api" ;;
    draupnir-api|draupnir-web) component="${unit}" ;;
    *) echo "draupnir_image_for: unknown unit: ${unit}" >&2; return 64 ;;
  esac

  printf '%s/%s:%s\n' "${registry}" "${component}" "${revision}"
}

# The manager environment variable rollout.sh sets and draupnir-run.sh reads.
#
#   draupnir_image_var <unit>
draupnir_image_var() {
  local unit="${1:?draupnir_image_var needs a unit}"
  printf 'DRAUPNIR_IMAGE_%s\n' "${unit//-/_}"
}

#!/usr/bin/env bash
# Roll a revision out to ALVISS. SAD 11H stage 4.2.
#
# The control plane runs as rootless containers on ALVISS behind systemd. This
# script pulls the signed image for the revision, swaps the unit's image
# reference, restarts and waits for the unit to settle. It does not wait for
# the application to be healthy: that is the smoke stage's job, and conflating
# the two hides which of the deployment and the application failed.
set -euo pipefail

REVISION="${1:?usage: rollout.sh <revision>}"
REGISTRY="${DRAUPNIR_REGISTRY:-registry.veldris.internal}"

# The units and the image each one runs, in one place: this script used to
# resolve `draupnir-worker` to an image of that name, which the pipeline does
# not build, so the pull failed before anything was rolled anywhere.
# shellcheck source=deploy/lib.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
UNITS=("${DRAUPNIR_UNITS[@]}")

echo "==> rolling out ${REVISION}"

for unit in "${UNITS[@]}"; do
  image="$(draupnir_image_for "${unit}" "${REVISION}" "${REGISTRY}")"
  echo "    ${unit} <- ${image}"
  podman pull "${image}"
  systemctl --user set-environment "$(draupnir_image_var "${unit}")=${image}"
done

for unit in "${UNITS[@]}"; do
  systemctl --user restart "${unit}.service"
done

for unit in "${UNITS[@]}"; do
  systemctl --user is-active --quiet "${unit}.service" \
    || { echo "::error::${unit}.service did not start"; exit 1; }
done

echo "==> rollout complete"

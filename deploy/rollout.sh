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
UNITS=("draupnir-api" "draupnir-worker" "draupnir-web")

echo "==> rolling out ${REVISION}"

for unit in "${UNITS[@]}"; do
  image="${REGISTRY}/${unit}:${REVISION}"
  echo "    ${unit} <- ${image}"
  podman pull "${image}"
  systemctl --user set-environment "DRAUPNIR_IMAGE_${unit//-/_}=${image}"
done

for unit in "${UNITS[@]}"; do
  systemctl --user restart "${unit}.service"
done

for unit in "${UNITS[@]}"; do
  systemctl --user is-active --quiet "${unit}.service" \
    || { echo "::error::${unit}.service did not start"; exit 1; }
done

echo "==> rollout complete"

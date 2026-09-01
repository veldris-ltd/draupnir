#!/usr/bin/env bash
# Return ALVISS to the previous revision. SAD 11H stage 4.4.
#
# The schema is not rolled back. Migrations are forward only (AC-Q6): every
# migration is additive within a version, so the previous release runs against
# the newer schema. A schema fault is recovered by a restore plus a new forward
# migration, not by a downgrade path that has never been exercised.
set -euo pipefail

PREVIOUS="${1:?usage: rollback.sh <revision>}"
REGISTRY="${DRAUPNIR_REGISTRY:-registry.veldris.internal}"
UNITS=("draupnir-api" "draupnir-worker" "draupnir-web")

echo "==> rolling back to ${PREVIOUS}"

for unit in "${UNITS[@]}"; do
  image="${REGISTRY}/${unit}:${PREVIOUS}"
  systemctl --user set-environment "DRAUPNIR_IMAGE_${unit//-/_}=${image}"
done

for unit in "${UNITS[@]}"; do
  systemctl --user restart "${unit}.service"
done

echo "==> rollback complete; the schema was deliberately left forward"

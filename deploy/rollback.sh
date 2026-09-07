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

# shellcheck source=deploy/lib.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
UNITS=("${DRAUPNIR_UNITS[@]}")

echo "==> rolling back to ${PREVIOUS}"

for unit in "${UNITS[@]}"; do
  image="$(draupnir_image_for "${unit}" "${PREVIOUS}" "${REGISTRY}")"
  systemctl --user set-environment "$(draupnir_image_var "${unit}")=${image}"
done

for unit in "${UNITS[@]}"; do
  systemctl --user restart "${unit}.service"
done

echo "==> rollback complete; the schema was deliberately left forward"

#!/usr/bin/env bash
# Return ALVISS to the previous revision. SAD 11H stage 4.4.
#
# The schema is not rolled back. Migrations are forward only (AC-Q6): every
# migration is additive within a version, so the previous release runs against
# the newer schema. A schema fault is recovered by a restore plus a new forward
# migration, not by a downgrade path that has never been exercised.
set -euo pipefail

PREVIOUS="${1:?usage: rollback.sh <revision>}"
REGISTRY="${DRAUPNIR_REGISTRY:-}"

# shellcheck source=deploy/lib.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
UNITS=("${DRAUPNIR_UNITS[@]}")
: "${REGISTRY:=$(draupnir_host_for registry)}"

# Before anything is changed. A rollback runs when a deployment has already
# gone wrong, so the argument is checked at the top rather than discovered at
# the pull -- and the message names what arrived, because the thing that
# arrived is usually the output of a command somebody meant to be a revision.
draupnir_require_tag "${PREVIOUS}" "rollback revision" || exit $?

echo "==> rolling back to ${PREVIOUS}"

for unit in "${UNITS[@]}"; do
  image="$(draupnir_image_for "${unit}" "${PREVIOUS}" "${REGISTRY}")"
  draupnir_service_set_image "${unit}" "${image}"
done

for unit in "${UNITS[@]}"; do
  draupnir_service_restart "${unit}"
done

echo "==> rollback complete; the schema was deliberately left forward"

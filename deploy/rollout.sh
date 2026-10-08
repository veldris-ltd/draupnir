#!/usr/bin/env bash
# Roll a revision out to ALVISS. SAD 11H stage 4.2.
#
# The control plane runs as rootless containers on ALVISS behind systemd. This
# script pulls the image for the revision, swaps the unit's image reference,
# restarts and waits for the unit to settle. It does not wait for the
# application to be healthy: that is the smoke stage's job, and conflating the
# two hides which of the deployment and the application failed.
#
# **It used to say "the signed image", and nothing signed one.** RF-02 signs
# plug-in distributions and stage 3.4 signs the SBOM; no step signs a container
# image and nothing verifies one at pull. The word is removed rather than the
# claim left standing: a script that describes a control it does not perform is
# worse than one that performs no control, because the first is read as
# evidence. Image signing is recorded as outstanding in the register.
set -euo pipefail

REVISION="${1:?usage: rollout.sh <revision>}"
REGISTRY="${DRAUPNIR_REGISTRY:-}"
PODMAN="${DRAUPNIR_PODMAN:-podman}"

# The units and the image each one runs, in one place: this script used to
# resolve `draupnir-worker` to an image of that name, which the pipeline does
# not build, so the pull failed before anything was rolled anywhere.
# shellcheck source=deploy/lib.sh
source "$(dirname -- "${BASH_SOURCE[0]}")/lib.sh"
UNITS=("${DRAUPNIR_UNITS[@]}")

# Site scoped, and derived after the source because that is where the site
# domain is known. `registry.veldris.internal` was one label short of the zone
# REGIN actually serves, so the pull resolved nowhere.
: "${REGISTRY:=$(draupnir_host_for registry)}"

# The same check rollback.sh makes, for the same reason: a revision that is not
# a tag fails at the pull with a registry error rather than here with a
# sentence naming what arrived.
draupnir_require_tag "${REVISION}" "revision" || exit $?

echo "==> rolling out ${REVISION}"

for unit in "${UNITS[@]}"; do
  image="$(draupnir_image_for "${unit}" "${REVISION}" "${REGISTRY}")"
  echo "    ${unit} <- ${image}"
  "${PODMAN}" pull "${image}"
  draupnir_service_set_image "${unit}" "${image}"
done

for unit in "${UNITS[@]}"; do
  draupnir_service_restart "${unit}"
done

for unit in "${UNITS[@]}"; do
  draupnir_service_is_active "${unit}" \
    || { echo "::error::${unit} did not start. $(draupnir_service_logs_hint "${unit}")"; exit 1; }
done

echo "==> rollout complete"

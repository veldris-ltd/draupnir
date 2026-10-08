#!/usr/bin/env bash
# What revision is this host actually running? RF-04.
#
# `deploy.yaml` captured the rollback revision with `draupnirctl version`, which
# prints `draupnirctl 0.1.0 (OpenAPI 1.0.0)` -- a sentence, not a tag. That
# whole string was handed to `rollback.sh`, which built the image reference
# `registry.<site>.veldris.internal/draupnir-api:draupnirctl 0.1.0 (OpenAPI
# 1.0.0)`. Rollback, the step that runs when a deployment has already gone
# wrong, could not work.
#
# It was also the wrong question. `draupnirctl` is a client on the runner; its
# version is the version of the thing asking, not of the thing running on
# ALVISS. The revision this host is serving is the one `draupnir-run.sh` wrote
# to the state directory when it last started a unit -- written *after* the
# image resolved, so a rollout that named an image which could not be pulled
# leaves the last good reference in place.
#
# Prints the tag alone, and nothing else, so a caller can use it without
# parsing.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/lib.sh
source "${HERE}/lib.sh"

UNIT="${1:-draupnir-api}"
STATE_DIR="${DRAUPNIR_STATE_DIR:-${HOME}/.local/state/draupnir}"
image_file="${STATE_DIR}/image-${UNIT}"

if [[ ! -r "${image_file}" ]]; then
  # Nothing on stdout. A caller capturing this wants a tag or nothing, and a
  # message on stdout would be captured as though it were one -- which is the
  # class of fault this script exists to fix.
  echo "no recorded image for ${UNIT}: ${image_file} does not exist." >&2
  echo "  this host has not started that unit, so there is no revision to roll back to." >&2
  exit 1
fi

image="$(cat "${image_file}")"

# The tag is everything after the last colon, and the colon has to come after
# the last slash: a registry may carry a port, and `host:5000/draupnir-api:sha`
# would otherwise yield `5000/draupnir-api:sha`.
name="${image##*/}"
tag="${name##*:}"

if [[ "${tag}" == "${name}" || -z "${tag}" ]]; then
  echo "the recorded image ${image} carries no tag." >&2
  exit 1
fi

printf '%s\n' "${tag}"

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
#
# **Two service managers.** SAD Decision S3 puts the control plane on ALVISS,
# and VLD-INF-SINDRI-001 Rev 3.3 section 6 makes ALVISS a Mac mini M4 Pro. So
# the units are systemd user units on Linux and launchd user agents on macOS,
# and the difference is confined to this file: rollout.sh and rollback.sh name
# no manager at all, and install.sh branches only where it renders a template.
#
# The container is identical on both. Same distroless aarch64 image, same
# rootless invocation, same read-only root -- AC-Q7 is a property of the
# container, not of what starts it.

# The units of the control plane, in the order a human would name them.
DRAUPNIR_UNITS=("draupnir-api" "draupnir-worker" "draupnir-web")

# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

# The DNS zone one forge serves.
#
# VLD-INF-SINDRI-001 Rev 3.3 section 10.4: dnsmasq on REGIN declares
# `local=/sindri.veldris.internal/`, which makes it authoritative for that zone
# and for nothing else. A name one label short of it -- `andvari.veldris
# .internal` rather than `andvari.sindri.veldris.internal` -- is forwarded to
# the site router and denied by the section 10.5 egress policy, so it does not
# resolve at all. Every default in these scripts used to be one label short.
#
# The zone is per forge because the Forge Matrix has more than one and they are
# not the same site. `DRAUPNIR_SITE_DOMAIN` overrides it for an estate whose
# naming differs; `DRAUPNIR_SITE_ID` chooses the forge.
#
#   draupnir_site_domain [site]
draupnir_site_domain() {
  local site="${1:-}"
  [[ -z "${site}" ]] && site="${DRAUPNIR_SITE_ID:-sindri}"
  printf '%s\n' "${DRAUPNIR_SITE_DOMAIN:-${site}.veldris.internal}"
}

# The fully qualified name of one host at one forge. The single place a
# hostname is spelled: five scripts had their own copy and all five were wrong
# in the same way.
#
#   draupnir_host_for <role> [site]
draupnir_host_for() {
  local role="${1:?draupnir_host_for needs a role}"
  printf '%s.%s\n' "${role}" "$(draupnir_site_domain "${2:-}")"
}

# Print the image reference for one unit at one revision.
#
# The registry is site scoped like everything else. A forge that is partitioned
# from the rest of the estate still has to be able to roll out (Decision S8),
# and it cannot do that against a registry on the other side of the partition.
#
#   draupnir_image_for <unit> <revision> [registry]
draupnir_image_for() {
  local unit="${1:?draupnir_image_for needs a unit}"
  local revision="${2:?draupnir_image_for needs a revision}"
  local registry="${3:-${DRAUPNIR_REGISTRY:-$(draupnir_host_for registry)}}"

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

# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------

# `linux` or `darwin`. Anything else is refused rather than guessed at: the
# two managers below are the two that exist, and a third platform silently
# taking the Linux path would install units nothing reads.
#
# `DRAUPNIR_PLATFORM` overrides it, which is how a test exercises the manager
# it is not running on.
draupnir_platform() {
  local found="${DRAUPNIR_PLATFORM:-}"
  if [[ -z "${found}" ]]; then
    case "$(uname -s 2>/dev/null || echo unknown)" in
      Linux) found="linux" ;;
      Darwin) found="darwin" ;;
      *) echo "draupnir_platform: unsupported platform: $(uname -s 2>/dev/null)" >&2; return 64 ;;
    esac
  fi
  printf '%s\n' "${found}"
}

# The file name of the unit template and of the installed unit, for one unit.
# Separate from the label because the two differ on macOS: the file is named
# after the unit so that an operator finds it, and the label is reverse-DNS
# because launchd's domain syntax needs it to be.
#
#   draupnir_unit_filename <unit>
draupnir_unit_filename() {
  local unit="${1:?draupnir_unit_filename needs a unit}"
  case "$(draupnir_platform)" in
    linux) printf '%s.service\n' "${unit}" ;;
    darwin) printf '%s.plist\n' "$(draupnir_launchd_label "${unit}")" ;;
  esac
}

# The launchd label for one unit. Derived, not tabulated, so it cannot drift
# from the unit name: `draupnir-api` is `com.veldris.draupnir.api`.
#
#   draupnir_launchd_label <unit>
draupnir_launchd_label() {
  local unit="${1:?draupnir_launchd_label needs a unit}"
  printf 'com.veldris.%s\n' "${unit//-/.}"
}

# The launchd service target: `gui/<uid>/<label>`. The `gui` domain rather
# than `system` because the units are rootless by requirement (AC-Q7), and a
# LaunchDaemon in /Library/LaunchDaemons is bootstrapped by root.
#
#   draupnir_launchd_target <unit>
draupnir_launchd_target() {
  local unit="${1:?draupnir_launchd_target needs a unit}"
  printf 'gui/%s/%s\n' "$(id -u)" "$(draupnir_launchd_label "${unit}")"
}

# ---------------------------------------------------------------------------
# Service manager. Five verbs, two implementations, and every caller above
# speaks only these -- which is what keeps rollout.sh identical on both.
# ---------------------------------------------------------------------------

# Re-read whatever the unit directory now holds. A no-op on launchd, which
# reads a plist at bootstrap rather than keeping a cached view of one.
draupnir_service_reload() {
  case "$(draupnir_platform)" in
    linux) systemctl --user daemon-reload ;;
    darwin) : ;;
  esac
}

# Publish the image reference for one unit into the manager's environment, so
# that draupnir-run.sh finds it on the next start.
#
#   draupnir_service_set_image <unit> <image>
draupnir_service_set_image() {
  local unit="${1:?draupnir_service_set_image needs a unit}"
  local image="${2:?draupnir_service_set_image needs an image}"
  local variable
  variable="$(draupnir_image_var "${unit}")"

  case "$(draupnir_platform)" in
    linux) systemctl --user set-environment "${variable}=${image}" ;;
    darwin) launchctl setenv "${variable}" "${image}" ;;
  esac
}

#   draupnir_service_unset_image <unit>
draupnir_service_unset_image() {
  local unit="${1:?draupnir_service_unset_image needs a unit}"
  local variable
  variable="$(draupnir_image_var "${unit}")"

  case "$(draupnir_platform)" in
    linux) systemctl --user unset-environment "${variable}" ;;
    darwin) launchctl unsetenv "${variable}" ;;
  esac
}

# Register the unit with the manager and make it start at login or boot.
# Idempotent on both: repeating it is the supported way to finish a partial
# install, which is the one operation nobody gets to rehearse.
#
#   draupnir_service_enable <unit> <installed unit file>
draupnir_service_enable() {
  local unit="${1:?draupnir_service_enable needs a unit}"
  local unit_file="${2:?draupnir_service_enable needs the installed unit file}"

  case "$(draupnir_platform)" in
    linux)
      systemctl --user enable "${unit}.service"
      ;;
    darwin)
      # bootout first, ignoring its failure: bootstrap refuses a label that is
      # already bootstrapped, and "already installed" must not be an error
      # when re-running is the documented recovery.
      launchctl bootout "$(draupnir_launchd_target "${unit}")" >/dev/null 2>&1 || true
      launchctl bootstrap "gui/$(id -u)" "${unit_file}"
      ;;
  esac
}

#   draupnir_service_restart <unit>
draupnir_service_restart() {
  local unit="${1:?draupnir_service_restart needs a unit}"
  case "$(draupnir_platform)" in
    linux) systemctl --user restart "${unit}.service" ;;
    darwin) launchctl kickstart -k "$(draupnir_launchd_target "${unit}")" ;;
  esac
}

# True when the unit is running. Quiet: the caller decides what to print.
#
#   draupnir_service_is_active <unit>
draupnir_service_is_active() {
  local unit="${1:?draupnir_service_is_active needs a unit}"
  case "$(draupnir_platform)" in
    linux)
      systemctl --user is-active --quiet "${unit}.service"
      ;;
    darwin)
      # `launchctl print` exits zero for a bootstrapped service whether or not
      # it is running, so the state line is the answer rather than the exit
      # code. A service that keeps crashing is bootstrapped and not running.
      launchctl print "$(draupnir_launchd_target "${unit}")" 2>/dev/null \
        | grep -qE '^[[:space:]]*state = running'
      ;;
  esac
}

# Stop the unit and deregister it. Never fails on a unit that is not there.
#
#   draupnir_service_disable <unit>
draupnir_service_disable() {
  local unit="${1:?draupnir_service_disable needs a unit}"
  case "$(draupnir_platform)" in
    linux) systemctl --user disable --now "${unit}.service" >/dev/null 2>&1 || true ;;
    darwin) launchctl bootout "$(draupnir_launchd_target "${unit}")" >/dev/null 2>&1 || true ;;
  esac
}

# Where an operator reads this unit's output. Named here because the two
# managers answer it very differently, and the runbook has to say one of them.
#
#   draupnir_service_logs_hint <unit>
draupnir_service_logs_hint() {
  local unit="${1:?draupnir_service_logs_hint needs a unit}"
  case "$(draupnir_platform)" in
    linux) printf 'journalctl --user -u %s.service -n 50\n' "${unit}" ;;
    darwin) printf 'tail -n 50 %s/Library/Logs/draupnir/%s.log\n' "${HOME}" "${unit}" ;;
  esac
}

# Is this a usable image tag? RF-04.
#
# OCI tags are up to 128 characters of [A-Za-z0-9_][A-Za-z0-9_.-]*. The reason
# this exists is narrower than the grammar: `deploy.yaml` captured the rollback
# revision with `draupnirctl version`, which prints a sentence, and the whole
# sentence became the tag -- so the image reference was
# `.../draupnir-api:draupnirctl 0.1.0 (OpenAPI 1.0.0)`.
#
# Checked here rather than left to the registry, because the failure surfaces
# at `podman pull` during a rollback, which is the one moment when a confusing
# error is most expensive.
#
#   draupnir_is_tag <candidate>
draupnir_is_tag() {
  local candidate="${1-}"
  [[ -n "${candidate}" ]] || return 1
  ((${#candidate} <= 128)) || return 1
  [[ "${candidate}" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]*$ ]]
}

# Refuse a revision that is not a tag, naming what arrived.
#
#   draupnir_require_tag <candidate> <what it is for>
draupnir_require_tag() {
  local candidate="${1-}" purpose="${2:-revision}"
  if ! draupnir_is_tag "${candidate}"; then
    printf 'the %s %q is not an image tag.\n' "${purpose}" "${candidate}" >&2
    printf '  A tag is up to 128 characters of letters, digits, underscore, dot and\n' >&2
    printf '  hyphen. Whitespace and parentheses usually mean a command that prints a\n' >&2
    printf '  sentence was captured instead of one that prints a revision: use\n' >&2
    printf '  deploy/current-revision.sh, which prints the tag this host is running.\n' >&2
    return 64
  fi
}


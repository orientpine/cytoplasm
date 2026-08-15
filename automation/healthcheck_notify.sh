#!/usr/bin/env bash
# automation/healthcheck_notify.sh — hand this sweep's failing check names to the
# owner-notice aggregator. Invoked once by healthcheck.sh at the end of a failing sweep.
#
# WHY it is its own file: healthcheck.sh sits AT the 250 pure-LOC gate, so the wiring
# there must be exactly one line. Same reason checkout_mirror_probe.sh and
# skill_mount_probe.sh exist.
#
# WHY it loads credentials itself: healthcheck runs from the **ops crontab**, not from a
# systemd unit, so the `EnvironmentFile=` that gives the reconcile timer its token never
# applies here. A no-agent cron component must self-load its secrets — the repo convention
# that exists precisely because a watcher silently missing a credential looks identical to
# a healthy one. Same file the repair watcher and the reconcile timer already use
# (`root:ops 0640`), so this adds no new secret, file or token.
#
# WHY it must not fail the sweep: this runs AFTER the checks and the repair tickets. If a
# Discord outage could take healthcheck's exit code with it, adding an alarm would make
# the monitor itself less reliable than before. Hence no `set -e`, and a swallowed rc.
set -uo pipefail

readonly CREDENTIAL_FILE="${HEALTHCHECK_NOTIFY_ENV:-/etc/autophagy/repair-approval.env}"

# shellcheck source=automation/runtime_root.sh
source "$(dirname "${BASH_SOURCE[0]}")/runtime_root.sh"
RUNTIME_ROOT="$(autophagy_runtime_root)"
readonly RUNTIME_ROOT

if [[ -r "$CREDENTIAL_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090 - path is a fixed constant, resolved above
  source "$CREDENTIAL_FILE"
  set +a
fi

PYTHONPATH="$RUNTIME_ROOT" python3 "$RUNTIME_ROOT/automation/healthcheck_notify.py" "$@" \
  || printf '[healthcheck-notify] delivery step failed; the sweep verdict is unaffected\n' >&2
exit 0

#!/usr/bin/env bash
# automation/provision-supply-chain-watch.sh — install and start the ⑦ supply-chain
# approval watcher, but only after proving it can actually see an approval (B-5).
#
# WHY it is one script rather than a handful of root commands in a handoff message:
# every step is `sudo` on prod, and a hand-typed root command is its own outage. It is
# idempotent, so re-running after a change is the normal way to use it.
#
# WHY it refuses to enable a timer it cannot prove works: this watcher's failure mode is
# WORSE than silence — it is a SUCCESSFUL silence. Two ordinary misconfigurations make
# every tick return zero records and exit 0:
#
#   * `ProtectHome=yes` replaces /home with an empty directory. All three things this
#     watcher reads live under the record owner's $HOME (pending records, the interop
#     config carrying the owner id, the bot token). Enumeration then finds nothing,
#     forever, and the unit reports success while the owner's ✅ is never read.
#   * A missing gate directory produces the identical picture: zero records, exit 0.
#
# A timer in that state looks healthy on `systemctl` and never converges. So the
# preconditions are checked BEFORE the timer starts, and any one of them missing is a
# hard stop rather than a warning — the whole point of ⑦ is that no ✅ is missed.
#
# The account, the secrets path, the runtime and the entry point are all read from the
# unit itself. A second copy of any of them is the copy that rots when the unit changes;
# that exact gap shipped once on the neighbouring reconcile timer (2026-08-01: the
# service ran as `ops` while the only grant named `deploy-runner`).
#
# Env (test seams; default to the real node paths):
#   UNIT_DIR                          default /etc/systemd/system
#   UNIT_SRC_DIR                      default <repo>/automation/systemd
#   SERVICE_HOME                      default the service account's passwd home
#   RUNTIME_ROOT                      default the unit's own WorkingDirectory
#   SUPPLY_CHAIN_WATCH_ASSUME_ROOT    override the root check for hermetic tests
#   SUPPLY_CHAIN_WATCH_NO_ENABLE=1    install everything but leave the timer stopped
#   HELPER_PATH                       default /usr/local/libexec/autophagy-resume-deploy
#   SUDOERS_PATH                      default /etc/sudoers.d/autophagy-supply-chain-resume
set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
eval "$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env)"
readonly UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
readonly UNIT_SRC="${UNIT_SRC_DIR:-$REPO_ROOT/automation/systemd}"
readonly SERVICE="autophagy-supply-chain-watch.service"
readonly TIMER="autophagy-supply-chain-watch.timer"
readonly HELPER_NAME="autophagy-resume-deploy"
readonly HELPER_PATH="${HELPER_PATH:-$NODE_LIBEXEC_DIR/$HELPER_NAME}"
readonly HELPER_SRC="$REPO_ROOT/automation/libexec/$HELPER_NAME"
readonly SUDOERS_PATH="${SUDOERS_PATH:-/etc/sudoers.d/autophagy-supply-chain-resume}"
readonly SUDOERS_SRC="$REPO_ROOT/automation/sudoers.d/autophagy-supply-chain-resume"
readonly RENDER_DIR="$(mktemp -d)"
trap 'rm -rf -- "$RENDER_DIR"' EXIT
python3 "$REPO_ROOT/automation/node_asset_renderer.py" "$UNIT_SRC/$SERVICE" "$RENDER_DIR/service"
python3 "$REPO_ROOT/automation/node_asset_renderer.py" "$SUDOERS_SRC" "$RENDER_DIR/sudoers"
python3 "$REPO_ROOT/automation/node_asset_renderer.py" "$HELPER_SRC" "$RENDER_DIR/helper"

log() { printf '[provision-supply-chain-watch] %s\n' "$*"; }
die() { log "ERROR: $1" >&2; exit 1; }

is_root() {
  if [[ -n "${SUPPLY_CHAIN_WATCH_ASSUME_ROOT:-}" ]]; then
    [[ "$SUPPLY_CHAIN_WATCH_ASSUME_ROOT" == "1" ]]
  else
    [[ "$EUID" == 0 ]]
  fi
}
is_root || die "run as root: sudo bash automation/provision-supply-chain-watch.sh"

for command_name in install systemctl sudo id visudo; do
  command -v "$command_name" >/dev/null || die "required command missing: $command_name"
done

readonly SERVICE_SRC="$RENDER_DIR/service"
for src in "$SERVICE_SRC" "$UNIT_SRC/$TIMER" "$HELPER_SRC" "$SUDOERS_SRC"; do
  [[ -f "$src" ]] || die "tracked source missing: $src"
done

# One directive, one answer — the unit is the single source of truth for all of these.
unit_directive() { sed -n "s/^$1=\(.*\)$/\1/p" "$SERVICE_SRC" | head -1; }

# The silent killer. Checked on the SOURCE, before anything is installed, so a unit that
# someone 'hardened' never reaches the node at all.
if grep -q '^ProtectHome=yes' "$SERVICE_SRC"; then
  die "ProtectHome=yes in $SERVICE — /home would be empty and every tick would find zero records while exiting 0"
fi
grep -q '^ProtectHome=no' "$SERVICE_SRC" \
  || die "ProtectHome is not stated in $SERVICE — state it explicitly so nobody 'hardens' it later"

SERVICE_USER="$(unit_directive User)"
[[ -n "$SERVICE_USER" ]] || die "$SERVICE does not name the account it runs as"
id -u "$SERVICE_USER" >/dev/null 2>&1 || die "service account does not exist: $SERVICE_USER"

SERVICE_HOME="${SERVICE_HOME:-$(getent passwd "$SERVICE_USER" | cut -d: -f6)}"
[[ -n "$SERVICE_HOME" && -d "$SERVICE_HOME" ]] \
  || die "cannot resolve \$HOME for $SERVICE_USER — the records, config and token all live there"

# The unit hard-codes an absolute secrets path. If the account's real home ever moves,
# systemd fails the unit every tick; catch the divergence here instead.
ENV_FILE="$(unit_directive EnvironmentFile)"
[[ -n "$ENV_FILE" ]] || die "$SERVICE declares no EnvironmentFile — the bot token would be absent"
[[ "$(dirname "$ENV_FILE")" == "$SERVICE_HOME" ]] \
  || die "EnvironmentFile ($ENV_FILE) is not in ${SERVICE_USER}'s home ($SERVICE_HOME)"
[[ -s "$ENV_FILE" ]] || die "missing or empty .env.secrets: $ENV_FILE"
grep -q 'DISCORD_BOT_TOKEN' "$ENV_FILE" \
  || die "no DISCORD_BOT_TOKEN in $ENV_FILE — the watcher cannot read a single reaction"

# Both of these produce zero records with exit 0, which is why they are checked at all.
readonly GATE_DIR="$SERVICE_HOME/.hermes/skill-gate"
[[ -d "$GATE_DIR" ]] \
  || die "no skill-gate records directory: $GATE_DIR — every tick would enumerate zero approvals and succeed"
readonly INTEROP_CONFIG="$SERVICE_HOME/.hermes/interop/config.json"
[[ -s "$INTEROP_CONFIG" ]] \
  || die "no interop config: $INTEROP_CONFIG — the owner-id lookup exits 2 on every tick"

WORKDIR="$(unit_directive WorkingDirectory)"
[[ -n "$WORKDIR" ]] || die "$SERVICE declares no WorkingDirectory"
EXEC_SCRIPT="$(unit_directive ExecStart | awk '{print $NF}')"
[[ -n "$EXEC_SCRIPT" ]] || die "$SERVICE declares no ExecStart"
RUNTIME="${RUNTIME_ROOT:-$WORKDIR}"
readonly CLI_REL="${EXEC_SCRIPT#"$WORKDIR"/}"
[[ -d "$RUNTIME" ]] || die "runtime release is absent: $RUNTIME"
[[ -f "$RUNTIME/$CLI_REL" ]] || die "watcher entry point is absent: $RUNTIME/$CLI_REL"

# The strongest precondition: can THIS account actually load the code, from THIS runtime?
# Import only — main() would talk to Discord and could resume a deploy, which is exactly
# what the owner has not authorized yet at provisioning time.
sudo -n -u "$SERVICE_USER" env "PYTHONPATH=$RUNTIME" python3 -c \
  "import automation.supply_chain_watch_cli" >/dev/null 2>&1 \
  || die "$SERVICE_USER cannot import the watcher from $RUNTIME — refusing to start a timer that cannot run"

# The watcher can read an approval but not finish one: it runs as an account with no
# sudo, while the pipeline escalates per step. Install the ONE command it may escalate.
install -d -m 0755 -o root -g root "$(dirname "$HELPER_PATH")"
install -m 0755 -o root -g root "$RENDER_DIR/helper" "$HELPER_PATH"
install -d -m 0755 -o root -g root "$(dirname "$SUDOERS_PATH")"
install -m 0440 -o root -g root "$RENDER_DIR/sudoers" "$SUDOERS_PATH"
visudo -cf "$SUDOERS_PATH" >/dev/null || die "sudoers file is invalid: $SUDOERS_PATH"

# Syntactically valid is not the same as effective. Ask sudo what the account may
# actually run — the neighbouring reconcile timer shipped with a grant naming the wrong
# account, and no unit test looked at both files together (2026-08-01).
sudo -n -l -U "$SERVICE_USER" 2>/dev/null | grep -Fq "$HELPER_PATH" \
  || die "$SERVICE_USER still may not run $HELPER_PATH — refusing to start a timer that cannot finish a deploy"

install -d -m 0755 -o root -g root "$UNIT_DIR"
install -m 0644 -o root -g root "$SERVICE_SRC" "$UNIT_DIR/$SERVICE"
install -m 0644 -o root -g root "$UNIT_SRC/$TIMER" "$UNIT_DIR/"
systemctl daemon-reload

if [[ "${SUPPLY_CHAIN_WATCH_NO_ENABLE:-}" == "1" ]]; then
  log "READY (timer left stopped) user=$SERVICE_USER gate=$GATE_DIR runtime=$RUNTIME"
  exit 0
fi
systemctl enable --now "$TIMER"
log "READY user=$SERVICE_USER gate=$GATE_DIR runtime=$RUNTIME timer=$TIMER"

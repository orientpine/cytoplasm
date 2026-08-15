#!/usr/bin/env bash
#=============================================================================
# automation/bootstrap-accounts.sh — plan task W0-4 (autophagy-agents)
#
# Provisions the service-account structure on the DGX Spark nodes:
#
#   production: primary agent + independent peer + infrastructure accounts
#   rag: infrastructure account only
#
# WHAT IT DOES (per role):
#   [all roles]
#     - useradd -m -s /bin/bash for each account (idempotent: skips existing)
#     - loginctl enable-linger + verify Linger=yes
#     - ~<user>/.env.secrets created EMPTY, mode 600, owned by that user
#       (real secrets are added by later tasks W0-6/W1-1 — NEVER by this script)
#     - home directory forced to mode 700. This is the actual isolation
#       mechanism ("3계정 상호 시크릿 읽기 불가", plan constraint 5): with the
#       home itself at 700, other accounts cannot even traverse into it, so
#       the 600 on .env.secrets is defense-in-depth, not the only barrier.
#       Set explicitly because useradd's default home mode varies by distro.
#   [production only]
#     - group 'autophagy' + members agent, peer (read audience for checkout)
#     - ~agent/{wiki,notes,patent-drafts,outputs,mail} skeleton, each 700
#       (plan constraint 8 — populated later by W2-2/W5-1/W5-3/4/5/W4-2)
#     - /srv/autophagy-private/{runtime-logs,repair-logs}  ops:ops 700
#     - /srv/autophagy-agents                              ops:autophagy 2750
#     - /srv/autophagy-repair-work                         ops:ops 700 (git clone)
#     - gitleaks 8.30.1 (linux_arm64) system-wide at /usr/local/bin/gitleaks
#     - per-account git config: safe.directory + placeholder identity
#     - ops deploy key (~ops/.ssh/id_ed25519) + clone/pull of the repo
#     - commit-refusal pre-commit hook in /srv/autophagy-agents/.git/hooks
#
# USAGE (run ON the target node, as root — cha runs this personally, W0-4 is
# a [USER] task because sudo on both nodes requires an interactive password):
#
#   production node: sudo bash bootstrap-accounts.sh production
#   RAG node:        sudo bash bootstrap-accounts.sh rag
#
# Getting the script onto a node for the first run (the /srv checkout that
# would normally carry it does not exist yet — chicken and egg):
#
#   Copy bootstrap-accounts.sh, node_config.py, and node_config_sh.py to the node.
#   sudo bash /tmp/bootstrap-accounts.sh production
#
# TWO-PHASE FLOW (production only; rag completes in a single run):
#   Run 1: provisions everything up to the deploy key, then PRINTS the public
#          key and EXITS 0. A human must register it as a READ-ONLY Deploy Key
#          at https://github.com/orientpine/autophagy-agents/settings/keys
#          (this script cannot do that: 'ops' has no authenticated gh, and
#          deploy-key registration needs repo admin — deliberately human).
#   Run 2: detects working key auth, clones the repo to /srv/autophagy-agents
#          (or pulls if present), installs the commit-refusal hook. Done.
#
# The script is FULLY IDEMPOTENT — safe to re-run any number of times:
# every resource is check-before-create; .env.secrets is never truncated;
# git identity is only set if unset (cha's later customization survives).
#
# Runbook + verification commands: docs/guide/w0-4-account-setup.md
#=============================================================================
set -Eeuo pipefail
readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
eval "$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env)"

readonly GITLEAKS_VERSION="8.30.1"
readonly REPO_SSH_URL="$NODE_ORIGIN_URL"
readonly REPO_KEYS_URL="https://github.com/orientpine/autophagy-agents/settings/keys"
readonly DEPLOY_DIR="$NODE_DEPLOY_CHECKOUT"
readonly REPAIR_WORK_DIR="$NODE_REPAIR_WORK"
readonly PRIVATE_DIR="$NODE_PRIVATE_ROOT"
readonly QUEUE_DIR="$NODE_REPAIR_REPORT_QUEUE"
readonly ACK_DIR="$NODE_REPAIR_REPORT_ACK"
readonly CAPABILITY_DIR="$NODE_REPAIR_CAPABILITY"
readonly GROUP_NAME="$NODE_SERVICE_GROUP"
readonly DEPLOY_KEY_COMMENT="$NODE_OPS_ACCOUNT@$NODE_PRIMARY_NODE_NAME-autophagy-deploy"
# accept-new = trust-on-first-use for github.com's host key (no interactive
# prompt mid-script). Paranoid option: pre-pin fingerprints from
# https://api.github.com/meta into ~ops/.ssh/known_hosts before run 2.
readonly GIT_SSH_CMD="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)

log()  { echo "[bootstrap] $*"; }
warn() { echo "[bootstrap] WARN: $*" >&2; }
die()  { echo "[bootstrap] ERROR: $*" >&2; exit 1; }
banner() { echo; echo "===== $* ====="; }
trap 'echo "[bootstrap] FAILED at line $LINENO: $BASH_COMMAND" >&2' ERR

usage() {
  cat >&2 <<'EOF'
Usage: sudo bash bootstrap-accounts.sh <production|rag>

  production  configured agent/peer/ops accounts + group/dirs/deploy checkout
  rag         configured ops account only (linger + .env.secrets + home 700)
EOF
  exit 2
}

require_cmds() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || die "required command not found: $c"
  done
}

#-----------------------------------------------------------------------------
# Accounts: create + linger + home 700 + ~/.env.secrets 600
#-----------------------------------------------------------------------------
ensure_account() {
  local u="$1" desc="$2" home pg sec linger

  if id "$u" >/dev/null 2>&1; then
    log "account '$u': already exists — skipping useradd"
  else
    # Regular login accounts (NOT --system): later tasks run Hermes/OpenClaw
    # instances under them (W1-2/W1-3/W3-4/W6-2), which want normal shells,
    # home dirs and systemd user managers.
    useradd -m -s /bin/bash -c "$desc" "$u"
    log "account '$u': created"
  fi

  home="$(getent passwd "$u" | cut -d: -f6)"
  [[ -d "$home" ]] || die "home directory for '$u' not found: $home"
  pg="$(id -gn "$u")"

  # Isolation: home itself must be 700 (do NOT rely on distro default 755/750).
  chown "$u:$pg" "$home"
  chmod 700 "$home"
  log "account '$u': home $home enforced 700 ($u:$pg)"

  # Linger: user services may run without an active login session.
  loginctl enable-linger "$u"
  linger="$(loginctl show-user "$u" --property=Linger --value 2>/dev/null || true)"
  if [[ "$linger" == "yes" || -e "/var/lib/systemd/linger/$u" ]]; then
    log "account '$u': Linger=yes"
  else
    die "linger verification failed for '$u' (got: '${linger:-<none>}')"
  fi

  # Empty secrets placeholder. NEVER truncate an existing file — later tasks
  # (W0-6, W1-1, ...) write real values into it; re-runs only enforce perms.
  sec="$home/.env.secrets"
  if [[ ! -f "$sec" ]]; then
    install -m 600 -o "$u" -g "$pg" /dev/null "$sec"
    log "account '$u': created empty $sec (600)"
  else
    log "account '$u': $sec exists — enforcing 600/ownership only"
  fi
  chown "$u:$pg" "$sec"
  chmod 600 "$sec"
}

#-----------------------------------------------------------------------------
# Production: group, agent home skeleton, /srv dirs
#-----------------------------------------------------------------------------
provision_group() {
  local u
  groupadd -f "$GROUP_NAME"
  log "group '$GROUP_NAME': present"
  # agent + peer read the shared checkout via this group. ops is not added:
  # it OWNS the checkout, owner bits already grant full access.
  for u in "$NODE_AGENT_ACCOUNT" "$NODE_PEER_ACCOUNT"; do
    if id -nG "$u" | tr ' ' '\n' | grep -qx "$GROUP_NAME"; then
      log "group '$GROUP_NAME': '$u' already a member"
    else
      usermod -aG "$GROUP_NAME" "$u"
      log "group '$GROUP_NAME': added '$u'"
    fi
  done
}

provision_agent_skeleton() {
  local home d
  home="$(getent passwd "$NODE_AGENT_ACCOUNT" | cut -d: -f6)"
  # Personal sensitive material lives in agent's 700 home (plan constraint 8).
  # Empty skeleton only; populated by W2-2 (wiki), W5-1 (notes),
  # W5-3/4/5 (patent-drafts, outputs), W4-2 (mail).
  for d in wiki notes patent-drafts outputs mail; do
    install -d -m 700 -o "$NODE_AGENT_ACCOUNT" -g "$(id -gn "$NODE_AGENT_ACCOUNT")" "$home/$d"
  done
  log "agent skeleton: $home/{wiki,notes,patent-drafts,outputs,mail} (700)"
}

provision_srv_dirs() {
  # Protected private paths: ops-only, nothing for group/others.
  install -d -m 700 -o "$NODE_OPS_ACCOUNT" -g "$(id -gn "$NODE_OPS_ACCOUNT")" \
    "$PRIVATE_DIR" "$PRIVATE_DIR/runtime-logs" "$PRIVATE_DIR/repair-logs"
  log "private dirs: $PRIVATE_DIR/{runtime-logs,repair-logs} (ops, 700)"

  # Shared deploy checkout root. Mode 2750, reasoning:
  #   - owner ops rwx           : ops clones/pulls/commits (full read-write)
  #   - group autophagy r-x     : agent/peer can list + read + traverse, but
  #                               CANNOT create/delete/modify (no group write)
  #   - others ---              : outside the group nothing is even visible;
  #                               this outer gate is why inner file modes
  #                               (644 from ops's umask) don't leak anything
  #   - setgid (the leading 2)  : files/dirs git creates inside inherit the
  #                               'autophagy' group automatically, so group
  #                               readability survives pulls without needing
  #                               recursive chgrp fixups on every update
  install -d -m 2750 -o "$NODE_OPS_ACCOUNT" -g "$GROUP_NAME" "$DEPLOY_DIR"
  log "deploy dir: $DEPLOY_DIR (ops:$GROUP_NAME, 2750 setgid)"

  install -d -m 2750 -o "$NODE_OPS_ACCOUNT" -g "$GROUP_NAME" "$QUEUE_DIR"
  install -d -m 2750 -o "$NODE_AGENT_ACCOUNT" -g "$NODE_OPS_ACCOUNT" "$ACK_DIR" "$CAPABILITY_DIR"
  # Producers and consumers may hold this inode open; never replace it on re-run.
  test -e "$QUEUE_DIR/queue.lock" \
    || install -m 640 -o "$NODE_OPS_ACCOUNT" -g "$GROUP_NAME" /dev/null "$QUEUE_DIR/queue.lock"
  log "repair report dirs: queue ops:$GROUP_NAME; ack/capability agent:ops (2750 setgid)"
  log "repair report lock: $QUEUE_DIR/queue.lock (create-if-absent, ops:$GROUP_NAME 640)"
}

#-----------------------------------------------------------------------------
# Production: gitleaks binary (aarch64) — same method proven in W0-1,
# tarball swapped to linux_arm64 (see docs/troubleshooting/gitleaks-setup.md
# and .omo/notepads/autophagy-agents/learnings.md).
#-----------------------------------------------------------------------------
install_gitleaks() {
  local current arch tmpd url
  if [[ -x /usr/local/bin/gitleaks ]]; then
    current="$(/usr/local/bin/gitleaks version 2>/dev/null || echo unknown)"
    if [[ "$current" == "$GITLEAKS_VERSION" ]]; then
      log "gitleaks: $GITLEAKS_VERSION already at /usr/local/bin/gitleaks — skipping"
      return 0
    fi
    warn "gitleaks: found version '$current', reinstalling pinned $GITLEAKS_VERSION"
  fi

  case "$(uname -m)" in
    aarch64|arm64) arch="arm64" ;;  # DGX Spark nodes
    x86_64)        arch="x64"   ;;  # defensive: correct tarball elsewhere too
    *) die "unsupported architecture: $(uname -m)" ;;
  esac

  url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_${arch}.tar.gz"
  tmpd="$(mktemp -d)"
  log "gitleaks: downloading $url"
  curl -sL -o "$tmpd/gitleaks.tar.gz" "$url"
  tar -xzf "$tmpd/gitleaks.tar.gz" -C "$tmpd"
  # /usr/local/bin: root-writable, on every account's default PATH.
  install -m 755 "$tmpd/gitleaks" /usr/local/bin/gitleaks
  rm -rf "$tmpd"
  log "gitleaks: installed $(/usr/local/bin/gitleaks version) at /usr/local/bin/gitleaks"
}

#-----------------------------------------------------------------------------
# Production: per-account git config (safe.directory + identity)
#-----------------------------------------------------------------------------
configure_git_account() {
  local u="$1"
  # git refuses to touch a repo owned by another user unless safe.directory
  # is set — required for agent/peer on the ops-owned checkout (and harmless
  # for ops itself). --add is guarded to avoid duplicate entries on re-runs.
  if ! sudo -u "$u" -H git config --global --get-all safe.directory 2>/dev/null \
      | grep -qx "$DEPLOY_DIR"; then
    sudo -u "$u" -H git config --global --add safe.directory "$DEPLOY_DIR"
    log "git ($u): safe.directory += $DEPLOY_DIR"
  else
    log "git ($u): safe.directory already set"
  fi

  # Placeholder identity — cha may want to customize these later; only set
  # when unset so re-runs never clobber a customized value.
  if ! sudo -u "$u" -H git config --global user.name >/dev/null 2>&1; then
    sudo -u "$u" -H git config --global user.name "$u (autophagy-agents)"
    log "git ($u): user.name set to placeholder"
  fi
  if ! sudo -u "$u" -H git config --global user.email >/dev/null 2>&1; then
    sudo -u "$u" -H git config --global user.email "$u@autophagy.local"
    log "git ($u): user.email set to placeholder"
  fi
}

#-----------------------------------------------------------------------------
# Production: deploy key, auth gate, checkout, commit-refusal hook
#-----------------------------------------------------------------------------
ensure_deploy_key() {
  # Runs entirely as ops (-H => HOME=~ops, so ~ resolves to ops's home).
  # -N "" : no passphrase — key is confined to ops's 700 home and is meant
  #         for unattended pulls; GitHub side is registered READ-ONLY.
  sudo -u "$NODE_OPS_ACCOUNT" -H env DEPLOY_KEY_COMMENT="$DEPLOY_KEY_COMMENT" bash -c '
    set -euo pipefail
    umask 077
    mkdir -p ~/.ssh
    chmod 700 ~/.ssh
    if [[ ! -f ~/.ssh/id_ed25519 ]]; then
      ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519 -C "$DEPLOY_KEY_COMMENT"
      echo "[bootstrap]   deploy key generated: ~/.ssh/id_ed25519"
    else
      echo "[bootstrap]   deploy key already exists: ~/.ssh/id_ed25519"
    fi
  '
}

github_auth_ok() {
  # `ssh -T git@github.com` exits 1 even on success; the success marker is
  # the "successfully authenticated" greeting, so parse output instead.
  local out
  out="$(sudo -u "$NODE_OPS_ACCOUNT" -H ssh "${SSH_OPTS[@]}" -T git@github.com 2>&1 || true)"
  [[ "$out" == *"successfully authenticated"* ]]
}

print_key_registration_banner() {
  local ops_home pub
  ops_home="$(getent passwd "$NODE_OPS_ACCOUNT" | cut -d: -f6)"
  pub="$(cat "$ops_home/.ssh/id_ed25519.pub")"
  cat <<EOF

==============================================================================
 PHASE 1 COMPLETE — HUMAN ACTION REQUIRED (deploy key not registered yet)
==============================================================================
 ops deploy PUBLIC key:

   $pub

 1) Open:  $REPO_KEYS_URL
 2) "Add deploy key"
      Title: $DEPLOY_KEY_COMMENT
      Key:   paste the single line above
      [ ] Allow write access   <-- LEAVE UNCHECKED (read-only Deploy Key)
 3) Re-run this script on this node to complete the checkout:
      sudo bash bootstrap-accounts.sh production

 (This registration needs a human with repo admin access — the script
  intentionally does not attempt it: 'ops' has no authenticated gh CLI.)
==============================================================================
EOF
}

do_checkout() {
  if [[ -d "$DEPLOY_DIR/.git" ]]; then
    log "checkout: repo present — pulling as ops (--ff-only: deploy checkout must never merge)"
    sudo -u "$NODE_OPS_ACCOUNT" -H env GIT_SSH_COMMAND="$GIT_SSH_CMD" \
      git -C "$DEPLOY_DIR" pull --ff-only
  else
    if [[ -n "$(ls -A "$DEPLOY_DIR" 2>/dev/null)" ]]; then
      die "$DEPLOY_DIR is non-empty but not a git repo — inspect manually before re-running"
    fi
    log "checkout: cloning $REPO_SSH_URL -> $DEPLOY_DIR as ops"
    sudo -u "$NODE_OPS_ACCOUNT" -H env GIT_SSH_COMMAND="$GIT_SSH_CMD" \
      git clone "$REPO_SSH_URL" "$DEPLOY_DIR"
  fi
  # Re-assert the outer gate after git activity (belt and braces).
  chown "$NODE_OPS_ACCOUNT:$GROUP_NAME" "$DEPLOY_DIR"
  chmod 2750 "$DEPLOY_DIR"
  log "checkout: up to date; $DEPLOY_DIR perms re-asserted (ops:$GROUP_NAME 2750)"
}

provision_repair_work_clone() {
  local origin
  if [[ -e "$REPAIR_WORK_DIR/.git" ]]; then
    log "repair work clone: repo present — skipping bootstrap clone"
    return 0
  fi

  install -d -m 700 -o "$NODE_OPS_ACCOUNT" -g "$(id -gn "$NODE_OPS_ACCOUNT")" "$REPAIR_WORK_DIR"
  if [[ -n "$(ls -A "$REPAIR_WORK_DIR" 2>/dev/null)" ]]; then
    die "$REPAIR_WORK_DIR is non-empty but not a git repo — inspect manually before re-running"
  fi
  origin="$(sudo -u "$NODE_OPS_ACCOUNT" git -C "$DEPLOY_DIR" remote get-url origin)"
  [[ -n "$origin" ]] || die "$DEPLOY_DIR has no origin remote"
  log "repair work clone: cloning deploy checkout origin -> $REPAIR_WORK_DIR as ops"
  sudo -u "$NODE_OPS_ACCOUNT" -H env GIT_SSH_COMMAND="$GIT_SSH_CMD" \
    git clone "$origin" "$REPAIR_WORK_DIR"

  # Re-assert the outer gate after git activity (belt and braces).
  chown -R "$NODE_OPS_ACCOUNT:$NODE_OPS_ACCOUNT" "$REPAIR_WORK_DIR"
  chmod 700 "$REPAIR_WORK_DIR"
  log "repair work clone: created; $REPAIR_WORK_DIR perms re-asserted (ops:ops 700)"
}

install_commit_refusal_hook() {
  # The deploy checkout is a ONE-WAY MIRROR of origin/main (root AGENTS.md,
  # "ops 체크아웃 단방향 규칙"): the only writes allowed inside it are git fetch and
  # git pull --ff-only. A commit made here runs in production but never reaches
  # git — the next deploy from a clean checkout silently reverts it — and until
  # someone untangles it the divergence blocks every session's ff-pull. So this
  # hook does not scan and does not judge: it refuses, unconditionally.
  #
  # It supersedes the gitleaks pre-commit hook that used to live here. That is
  # not a weakening: a commit that cannot happen cannot leak a secret, and
  # gitleaks still guards the workstation checkouts, where commits belong.
  #
  # Idempotent by construction — install(1) replaces the one target path, so a
  # re-run leaves exactly one hook and no backup files.
  #
  # git fetch and git pull --ff-only are untouched: neither creates a commit, so
  # neither runs pre-commit.
  local deploy_dir="${1:?deploy checkout path required}"
  local hook="$deploy_dir/.git/hooks/pre-commit"
  local source="$REPO_ROOT/automation/hooks/deploy-checkout-pre-commit"
  [[ -f "$source" ]] || die "tracked commit-refusal hook missing: $source"
  # owner ops with 755: ops is the account git runs as in this checkout, and
  # group-readability is harmless — the group has no write access to .git.
  install -m 755 -o "$NODE_OPS_ACCOUNT" -g "$GROUP_NAME" "$source" "$hook"
  log "hook: commit-refusal pre-commit installed at $hook (ops, 755)"
}

#-----------------------------------------------------------------------------
# Summary — prints the evidence cha needs for docs/qa/W0-4/
#-----------------------------------------------------------------------------
print_summary() {
  local u home
  banner "SUMMARY (role=$ROLE, host=$(hostname))"
  getent passwd "${ACCOUNTS[@]}"
  for u in "${ACCOUNTS[@]}"; do
    home="$(getent passwd "$u" | cut -d: -f6)"
    echo "--- $u"
    loginctl show-user "$u" --property=Linger 2>/dev/null \
      || echo "Linger=file:$(test -e "/var/lib/systemd/linger/$u" && echo yes || echo no)"
    ls -ld "$home"
    ls -l "$home/.env.secrets"
  done
  if [[ "$ROLE" == "production" ]]; then
    echo "--- /srv"
    ls -ld "$PRIVATE_DIR" "$PRIVATE_DIR/runtime-logs" "$PRIVATE_DIR/repair-logs" "$DEPLOY_DIR" \
      "$REPAIR_WORK_DIR" "$QUEUE_DIR" "$ACK_DIR" "$CAPABILITY_DIR"
    ls -l "$QUEUE_DIR/queue.lock"
    echo "--- gitleaks"
    /usr/local/bin/gitleaks version 2>/dev/null || echo "gitleaks: NOT INSTALLED"
  fi
  echo
  log "verification runbook: docs/guide/w0-4-account-setup.md (in the repo)"
}

#-----------------------------------------------------------------------------
# Main
#-----------------------------------------------------------------------------
ROLE="${1:-}"
case "$ROLE" in
  production) ACCOUNTS=("$NODE_AGENT_ACCOUNT" "$NODE_PEER_ACCOUNT" "$NODE_OPS_ACCOUNT") ;;
  rag)        ACCOUNTS=("$NODE_OPS_ACCOUNT") ;;
  *) usage ;;
esac

[[ "$(id -u)" -eq 0 ]] || die "must run as root: sudo bash bootstrap-accounts.sh $ROLE"

require_cmds useradd groupadd usermod loginctl getent install id
if [[ "$ROLE" == "production" ]]; then
  require_cmds git curl tar ssh ssh-keygen sudo
fi

banner "W0-4 bootstrap — role=$ROLE host=$(hostname) arch=$(uname -m)"

banner "[1/8] accounts + linger + home 700 + .env.secrets 600"
case "$ROLE" in
  production)
    ensure_account "$NODE_AGENT_ACCOUNT" "autophagy primary agent"
    ensure_account "$NODE_PEER_ACCOUNT" "autophagy independent peer agent"
    ensure_account "$NODE_OPS_ACCOUNT" "autophagy infra/repair/hub operations"
    ;;
  rag)
    ensure_account "$NODE_OPS_ACCOUNT" "autophagy infra/repair/hub operations"
    ;;
esac

if [[ "$ROLE" == "rag" ]]; then
  log "rag role: phases 2-8 are production-only — done."
  print_summary
  log "DONE (role=rag)"
  exit 0
fi

banner "[2/8] group '$GROUP_NAME' + membership (agent, peer)"
provision_group

banner "[3/8] agent home skeleton (constraint 8)"
provision_agent_skeleton

banner "[4/8] protected dirs $PRIVATE_DIR (ops 700)"
banner "[5/8] deploy dir $DEPLOY_DIR (ops:$GROUP_NAME 2750)"
provision_srv_dirs

banner "[6/8] gitleaks $GITLEAKS_VERSION (system-wide)"
install_gitleaks

banner "[7/8] per-account git config (safe.directory + identity)"
for u in "${ACCOUNTS[@]}"; do
  configure_git_account "$u"
done

banner "[8/8] deploy key -> GitHub auth gate -> checkout -> commit-refusal hook -> repair work clone"
ensure_deploy_key
if github_auth_ok; then
  log "GitHub deploy-key auth OK (ops)"
  do_checkout
  install_commit_refusal_hook "$DEPLOY_DIR"
  provision_repair_work_clone
else
  print_key_registration_banner
  print_summary
  log "PHASE 1 done — re-run after registering the deploy key (exit 0, by design)"
  exit 0
fi

print_summary
log "DONE (role=production) — all phases complete"

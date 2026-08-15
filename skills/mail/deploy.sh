#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
host="${DEPLOY_SSH_HOST:-<primary-node>}"

run_agent() {
  local script="$1"
  ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "$script")"
}

push_file() {
  local source="$1" destination="$2"
  run_agent "umask 077; mkdir -p \"\$HOME/$(dirname "$destination")\"; cat > \"\$HOME/$destination\"; chmod 600 \"\$HOME/$destination\"" < "$source"
}

# Ship the vendored mailon tree (source of truth after the emailAutomation
# cutover) to a checkout-external staging dir, then build+activate a versioned
# runtime release LOCALLY on the node. mailon_runtime_release.sh keeps data/
# logs/.venv outside the checkout so runtime writes never dirty it (Oracle S3).
deploy_vendor_mailon() {
  local staging='.hermes/mailon-vendor-staging'
  run_agent "rm -rf \"\$HOME/$staging\"; mkdir -p \"\$HOME/$staging\""
  # Stream the vendor tree (mailon/ + requirements.txt) as a tar to the node.
  deploy_archive_stream "$repo_root" "$repo_root/skills/mail/vendor" mailon requirements.txt \
    | ssh "$host" "sudo -n -u agent -H bash -lc $(printf '%q' "umask 077; tar -C \"\$HOME/$staging\" -xf -")"
  push_file "$repo_root/skills/mail/scripts/mailon_runtime_release.sh" \
    "$staging/mailon_runtime_release.sh"
  run_agent "chmod 700 \"\$HOME/$staging/mailon_runtime_release.sh\"; \"\$HOME/$staging/mailon_runtime_release.sh\" \"\$HOME/$staging\""
}

# Deploy guard: refuse to push code that origin/main does not have (see the header of
# automation/deploy_provenance.sh for why a silent revert is otherwise inevitable).
source "$repo_root/automation/deploy_provenance.sh"
deploy_provenance_check "$repo_root" \
  "$repo_root/skills/mail/scripts/mail_digest_watch.py" \
  "$repo_root/skills/mail/scripts/mailon_runtime_release.sh" \
  "$repo_root/skills/mail/vendor/mailon" \
  "$repo_root/skills/mail/vendor/requirements.txt" || exit 4

push_file "$repo_root/skills/mail/scripts/mail_digest_watch.py" \
  '.hermes/scripts/mail_digest_watch.py'
# Register (or converge) the daily digest cron. --deliver discord routes the
# no-agent script's stdout to the owner DM, so a failure marker line + exit 1
# actually reaches cha (the 2026-07-31 incident vanished under --deliver local,
# which has 0 delivery targets). An already-registered job is converged in
# place with `edit` (preserving its job id/history); otherwise it is created.
run_agent 'PATH="$HOME/.local/bin:$PATH"; job_id=$(hermes cron list | awk "/^  [0-9a-f]+ \[/{id=\$1} /Name:[[:space:]]+mail-daily-digest\$/{print id; exit}"); if [ -n "$job_id" ]; then hermes cron edit "$job_id" --deliver discord --no-agent --script mail_digest_watch.py; else hermes cron create "0 8 * * *" --name mail-daily-digest --no-agent --script mail_digest_watch.py --deliver discord; fi'
run_agent 'PATH="$HOME/.local/bin:$PATH"; hermes cron list'

# Build + activate the vendored mailon runtime release on the node.
deploy_vendor_mailon

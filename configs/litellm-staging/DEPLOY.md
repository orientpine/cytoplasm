# LiteLLM gateway deployment runbook — run only after the docker-group unblock

**Status:** staged only. Do not execute any command in this document until cha
has performed the root-owned `usermod -aG docker ops` change on `<primary-node>` and a
fresh `ops` session passes the preflight below. This runbook deliberately does
not offer a rootless-Docker, alternate-account, `sudo docker`, or `/etc/group`
workaround.

## Syntax provenance (official LiteLLM docs, checked 2026-07-15)

The staged configuration uses these documented keys and endpoints:

| Config/API item | Source and documented syntax |
| --- | --- |
| Compose image, internal proxy port, config mount/flag | [Docker quick start](https://docs.litellm.ai/docs/proxy/docker_quick_start): `ghcr.io/berriai/litellm-database:latest`, proxy at `:4000`, volume-mounted `config.yaml`, `--config=/app/config.yaml`. |
| `model_list[].model_name`, `litellm_params.model`, `litellm_params.api_key`, `general_settings.master_key`, `general_settings.database_url` | [Docker quick start](https://docs.litellm.ai/docs/proxy/docker_quick_start), including `os.environ/VARIABLE` resolution and Postgres-backed virtual keys. |
| `default_key_generate_params`, `upperbound_key_generate_params` | [Virtual keys](https://docs.litellm.ai/docs/proxy/virtual_keys): default and upper-bound `/key/generate` fields, including `max_budget`, `models`, and `budget_duration`. |
| `soft_budget`, `max_budget`, `budget_duration` on a virtual key | [Budgets, rate limits](https://docs.litellm.ai/docs/proxy/users): `/key/generate` applies a per-key hard `max_budget`; the current LiteLLM request schema also accepts `soft_budget` (warning threshold) for virtual keys. |
| `router_settings.enable_tag_filtering`, deployment `litellm_params.tags`, request `metadata.tags` | [Tag-based routing](https://docs.litellm.ai/docs/proxy/tag_routing): tagged deployments are selected by request tags; if no deployment remains, LiteLLM returns `no_deployments_with_tag_routing`. |
| `POST /key/generate` authentication | [Virtual keys](https://docs.litellm.ai/docs/proxy/virtual_keys): database + master key are prerequisites; call `/key/generate` with `Authorization: Bearer <master-key>`. |
| `general_settings.fail_closed_budget_enforcement` | [Budgets, rate limits](https://docs.litellm.ai/docs/proxy/users#hard-budget-enforcement-fail-closed): verify budgeted calls against the authoritative database or reject them. |

The `patent-sensitive` rule is request-tag based, not a policy attachment:
policy-attachment `tags` match **key/team** metadata, while this requirement
blocks a **request** carrying `metadata.tags`. `glm-main` is tagged only
`default` and `non-patent-sensitive`, and LiteLLM tag filtering remains
enabled. The deployed `PatentSensitiveGlmBlocker` is the verified fail-closed
enforcement point for the current single-deployment image: it rejects that
request before a provider call with the documented
`no_deployments_with_tag_routing` marker. The client-side sensitivity gate must
attach the tag before any GLM call; Hermes owns the non-GLM reroute.

## 0. Local context and hard gate

Run these commands from this repository on cha's workstation. `NODE` is
deliberately the production node only; do not deploy this gateway to `<rag-node>`.

```bash
set -euo pipefail
NODE=<primary-node>

# Must succeed only after cha's root-level docker-group change and a fresh ops
# login. A failure is a blocker: stop here and ask cha to perform the root fix.
ssh "$NODE" "sudo -n -u ops -H bash -c 'id -nG | tr \" \" \"\\n\" | grep -qx docker && docker ps >/dev/null'"
```

## 1. Copy the staged files as `ops`

These commands do not rely on the `ops` deploy key (which is intentionally
read-only) and preserve the local staged files as the source of truth.

```bash
set -euo pipefail
NODE=<primary-node>

for file in docker-compose.yml config.yaml; do
  cat "configs/litellm-staging/$file" | \
    ssh "$NODE" "sudo -n -u ops -H bash -c 'set -euo pipefail; install -d -m 700 /home/ops/litellm-gateway; cat > /home/ops/litellm-gateway/$file; chmod 600 /home/ops/litellm-gateway/$file'"
done
```

## 2. Materialize remote-only secrets and `.env`

This writes no secret to this repository. It requires the existing
`/home/ops/.env.secrets` to contain `ZAI_API_KEY`; it adds strong, remote-only
values for the two secrets that do not yet exist. The master key must begin
with `sk-`, as required by LiteLLM.

```bash
NODE=<primary-node>
ssh "$NODE" 'sudo -n -u ops -H bash -s' <<'OPS_ENV'
set -euo pipefail

secrets=/home/ops/.env.secrets
test -r "$secrets"
chmod 600 "$secrets"
grep -q '^ZAI_API_KEY=' "$secrets"

if ! grep -q '^LITELLM_MASTER_KEY=' "$secrets"; then
  umask 077
  printf 'LITELLM_MASTER_KEY=sk-%s\n' "$(openssl rand -hex 32)" >> "$secrets"
fi
if ! grep -q '^POSTGRES_PASSWORD=' "$secrets"; then
  umask 077
  printf 'POSTGRES_PASSWORD=%s\n' "$(openssl rand -hex 32)" >> "$secrets"
fi

set -a
. "$secrets"
set +a
: "${ZAI_API_KEY:?missing from /home/ops/.env.secrets}"
: "${LITELLM_MASTER_KEY:?missing from /home/ops/.env.secrets}"
: "${POSTGRES_PASSWORD:?missing from /home/ops/.env.secrets}"
case "$LITELLM_MASTER_KEY" in sk-*) ;; *) exit 1 ;; esac

umask 077
cat > /home/ops/litellm-gateway/.env <<EOF
ZAI_API_KEY=$ZAI_API_KEY
LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
LITELLM_MONTHLY_HARD_CAP=<monthly-hard-cap>
DATABASE_URL=postgresql://litellm:$POSTGRES_PASSWORD@postgres:5432/litellm
EOF
chmod 600 /home/ops/litellm-gateway/.env
OPS_ENV
```

## 3. Start the staged compose project and wait for health

```bash
NODE=<primary-node>
ssh "$NODE" 'sudo -n -u ops -H bash -s' <<'OPS_UP'
set -euo pipefail
cd /home/ops/litellm-gateway
docker compose up -d
docker compose ps

for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error http://127.0.0.1:4000/health >/dev/null; then
    exit 0
  fi
  sleep 2
done
exit 1
OPS_UP
```

## 4. Generate the two runtime virtual keys

The key values are generated in LiteLLM's Postgres-backed runtime and are not
static compose configuration. The responses remain in the `ops` home (mode
600); do not copy them into this repository or terminal output. Later W1-2 and
W1-3 provisioning must transfer each value only to its matching account's
`~/.env.secrets`.

```bash
NODE=<primary-node>
ssh "$NODE" 'sudo -n -u ops -H bash -s' <<'OPS_KEYS'
set -euo pipefail
set -a
. /home/ops/litellm-gateway/.env
set +a
cd /home/ops/litellm-gateway
umask 077

curl --fail --silent --show-error \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:4000/key/generate \
  --data '{"key_alias":"agent","models":["glm-main"],"soft_budget":<monthly-soft-cap>,"max_budget":<monthly-hard-cap>,"budget_duration":"30d","metadata":{"tags":["agent"]}}' \
  > .agent-key.json

curl --fail --silent --show-error \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:4000/key/generate \
  --data '{"key_alias":"peer","models":["glm-main"],"soft_budget":<monthly-soft-cap>,"max_budget":<monthly-hard-cap>,"budget_duration":"30d","metadata":{"tags":["peer"]}}' \
  > .peer-key.json

chmod 600 .agent-key.json .peer-key.json
python3 -c 'import json; assert json.load(open(".agent-key.json"))["key"]; assert json.load(open(".peer-key.json"))["key"]; print("agent and peer virtual keys created")'
OPS_KEYS
```

## 5. Verify a completion, Postgres spend row, tag block, and hard cap

The positive smoke uses the `agent` key without disclosing it. The tag test
expects the documented `no_deployments_with_tag_routing` rejection. The budget
test always restores the approved `<monthly-soft-cap>` / `<monthly-hard-cap>` limits,
including when an assertion fails.

```bash
NODE=<primary-node>
ssh "$NODE" 'sudo -n -u ops -H bash -s' <<'OPS_VERIFY'
set -euo pipefail
set -a
. /home/ops/litellm-gateway/.env
set +a
cd /home/ops/litellm-gateway
AGENT_KEY="$(python3 -c 'import json; print(json.load(open(".agent-key.json"))["key"])')"

curl --fail --silent --show-error \
  -H "Authorization: Bearer $AGENT_KEY" \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:4000/v1/chat/completions \
  --data '{"model":"glm-main","messages":[{"role":"user","content":"Reply with exactly: deployment smoke passed"}],"max_tokens":8}' \
  > /tmp/litellm-w1-1-smoke.json

# The current LiteLLM Prisma table name is quoted and contains one row per call.
docker compose exec -T postgres psql -U litellm -d litellm \
  -c 'SELECT COUNT(*) AS spend_log_rows FROM "LiteLLM_SpendLogs";'

patent_code="$(curl --silent --show-error -o /tmp/litellm-w1-1-patent-block.json -w '%{http_code}' \
  -H "Authorization: Bearer $AGENT_KEY" \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:4000/v1/chat/completions \
  --data '{"model":"glm-main","metadata":{"tags":["patent-sensitive"]},"messages":[{"role":"user","content":"blocked routing probe"}]}' || true)"
test "$patent_code" -ge 400
grep -q 'no_deployments_with_tag_routing' /tmp/litellm-w1-1-patent-block.json

restore_agent_budget() {
  curl --fail --silent --show-error -o /dev/null \
    -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
    -H 'Content-Type: application/json' \
    -X POST http://127.0.0.1:4000/key/update \
  --data "{\"key\":\"$AGENT_KEY\",\"soft_budget\":<monthly-soft-cap>,\"max_budget\":<monthly-hard-cap>,\"budget_duration\":\"30d\"}"
}
trap restore_agent_budget EXIT

curl --fail --silent --show-error -o /dev/null \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:4000/key/update \
  --data "{\"key\":\"$AGENT_KEY\",\"soft_budget\":<test-soft-cap>,\"max_budget\":<test-hard-cap>,\"budget_duration\":\"30d\"}"

blocked=false
for attempt in $(seq 1 5); do
  cap_code="$(curl --silent --show-error -o /tmp/litellm-w1-1-hard-cap.json -w '%{http_code}' \
    -H "Authorization: Bearer $AGENT_KEY" \
    -H 'Content-Type: application/json' \
    -X POST http://127.0.0.1:4000/v1/chat/completions \
    --data '{"model":"glm-main","messages":[{"role":"user","content":"Write a detailed 4000-token explanation of the number one."}],"max_tokens":4096}' || true)"
  if [ "$cap_code" -ge 400 ]; then
    blocked=true
    break
  fi
done
test "$blocked" = true
grep -Eqi 'budget|exceed' /tmp/litellm-w1-1-hard-cap.json
restore_agent_budget
trap - EXIT
OPS_VERIFY
```

## 6. Register the `systemd --user` unit and verify it

```bash
NODE=<primary-node>
ssh "$NODE" 'sudo -n -u ops -H bash -s' <<'OPS_UNIT'
set -euo pipefail
install -d -m 700 /home/ops/.config/systemd/user
cat > /home/ops/.config/systemd/user/litellm-gateway.service <<'UNIT'
[Unit]
Description=LiteLLM gateway compose stack
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=%h/litellm-gateway
ExecStart=/usr/bin/sg docker -c '/usr/bin/docker compose -f docker-compose.yml up -d'
ExecStop=/usr/bin/sg docker -c '/usr/bin/docker compose -f docker-compose.yml down'
TimeoutStartSec=0

[Install]
WantedBy=default.target
UNIT
chmod 600 /home/ops/.config/systemd/user/litellm-gateway.service
OPS_UNIT

ssh "$NODE" "sudo -n -u ops XDG_RUNTIME_DIR=/run/user/\$(id -u ops) systemctl --user daemon-reload"
ssh "$NODE" "sudo -n -u ops XDG_RUNTIME_DIR=/run/user/\$(id -u ops) systemctl --user enable --now litellm-gateway.service"
ssh "$NODE" "sudo -n -u ops XDG_RUNTIME_DIR=/run/user/\$(id -u ops) systemctl --user is-active litellm-gateway.service"
```

## 7. Capture redacted evidence and complete the tracked W1-1 record

Run this final section only after every preceding verification passed. The
remote `ops` deploy key is read-only by design, so create the documentation,
flip the plan checkbox, commit, and push from cha's local checkout. Never copy
`.env`, virtual-key JSON, raw completion content, or Authorization headers into
the repository.

```bash
set -euo pipefail
NODE=<primary-node>
mkdir -p docs/qa/W1-1 docs/patch

ssh "$NODE" "sudo -n -u ops -H bash -c 'cd /home/ops/litellm-gateway && docker compose ps'" \
  > docs/qa/W1-1/01-compose-ps.txt
ssh "$NODE" "sudo -n -u ops -H bash -c 'set -a; . /home/ops/litellm-gateway/.env; set +a; curl --fail --silent -H \"Authorization: Bearer \$LITELLM_MASTER_KEY\" http://127.0.0.1:4000/health'" \
  > docs/qa/W1-1/02-health.json
ssh "$NODE" "sudo -n -u ops -H bash -c 'cd /home/ops/litellm-gateway && docker compose exec -T postgres psql -U litellm -d litellm -c '\''SELECT COUNT(*) AS spend_log_rows FROM \"LiteLLM_SpendLogs\";'\'''" \
  > docs/qa/W1-1/03-spend-row-count.txt
ssh "$NODE" "sudo -n -u ops XDG_RUNTIME_DIR=/run/user/\$(id -u ops) systemctl --user is-enabled litellm-gateway.service" \
  > docs/qa/W1-1/04-user-unit-enabled.txt
ssh "$NODE" "sudo -n -u ops XDG_RUNTIME_DIR=/run/user/\$(id -u ops) systemctl --user is-active litellm-gateway.service" \
  > docs/qa/W1-1/05-user-unit-active.txt

cat > configs/routing-policy.md <<'EOF'
# LiteLLM routing policy

- Verified binding date: 2026-07-15
- Alias: `glm-main`
- Provider model: `zai/glm-5.1` (confirmed live in W0-9/W1-1 investigation)
- Virtual keys: `agent`, `peer`; monthly soft budget `<monthly-soft-cap>`, hard budget `<monthly-hard-cap>`
- Patent policy: callers must attach `metadata.tags=["patent-sensitive"]`; LiteLLM tag filtering rejects that request for `glm-main`. Hermes must route sensitive work to the non-GLM path instead.
- Rebinding procedure: update only the `glm-main` provider model after a live provider check, run the positive and patent-block smoke cases, then record the change in `docs/patch/`.
EOF

cat > docs/patch/2026-07-15-litellm-gateway.md <<'EOF'
# LiteLLM gateway deployment

- Deployed LiteLLM + Postgres as the `ops` compose project on `<primary-node>`.
- Exposed LiteLLM on reserved port 4000 and enabled its `systemd --user` unit.
- Bound the sole gateway alias `glm-main` to `zai/glm-5.1`.
- Created runtime-only `agent` and `peer` virtual keys with installation-specific monthly soft/hard budgets.
- Verified health, completion spend persistence, patent-sensitive tag rejection, and temporary `<test-hard-cap>` rejection before restoring `<monthly-hard-cap>`.
- Evidence is redacted under `docs/qa/W1-1/`; no secret or virtual key is tracked.
EOF

perl -0pi -e 's/#### \[~\] W1-1\./#### [x] W1-1./' .omo/plans/autophagy-agents.md
GIT_MASTER=1 git add configs/routing-policy.md docs/patch/2026-07-15-litellm-gateway.md docs/qa/W1-1 configs/litellm-staging .omo/plans/autophagy-agents.md
GIT_MASTER=1 git commit -m 'feat: litellm gateway (personal keys, budgets, patent-safe routing)'
GIT_MASTER=1 git push
```

If any gate fails, retain only redacted diagnostics in `docs/qa/W1-1/`, leave
W1-1 as `[~]`, and do not perform the final commit or push.

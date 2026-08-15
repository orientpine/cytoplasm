#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
eval "$(python3 "$ROOT/automation/node_config_sh.py" --print-env)"
EVIDENCE_DIR="$ROOT/docs/qa/F4"
SUMMARY="$EVIDENCE_DIR/summary.txt"
failed=0

mkdir -p "$EVIDENCE_DIR"
umask 077
: >"$SUMMARY"
printf 'F4 scope-fidelity audit\nrun_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$SUMMARY"

record() {
  local name="$1"
  shift
  local output="$EVIDENCE_DIR/$name.txt"
  "$@" >"$output" 2>&1
  local status=$?
  printf 'exit=%d\n' "$status" >>"$output"
  printf '%s: exit=%d\n' "$name" "$status" >>"$SUMMARY"
  if (( status != 0 )); then
    failed=1
  fi
}

check_approvals() {
  ssh "$NODE_PRIMARY_NODE_NAME" "sudo -n -u $NODE_AGENT_ACCOUNT -H python3 - $NODE_DEPLOY_CHECKOUT/logs/approvals.jsonl $NODE_AGENT_HOME" <<'PY'
import json
import sys
from pathlib import Path

approvals_path = Path(sys.argv[1])
home = Path(sys.argv[2])
interop_config = json.loads((home / ".hermes/interop/config.json").read_text(encoding="utf-8"))
owner_id = interop_config.get("owner_id")
if not isinstance(owner_id, str) or not owner_id:
    raise SystemExit("owner_id missing from interop config")
owner_approved = set()
send_logged = set()
for raw in approvals_path.read_text(encoding="utf-8").splitlines():
    row = json.loads(raw)
    approval = row.get("approval", {})
    result = row.get("result", {})
    if (
        row.get("action") == "external_effect.approval"
        and result.get("status") == "approved"
        and approval.get("owner_id") == owner_id
    ):
        owner_approved.add((approval.get("message_id"), approval.get("method")))
    if (
        row.get("action", "").endswith(("reply_send", "request_mail"))
        and result.get("status") == "sent"
    ):
        send_logged.add((approval.get("ref"), approval.get("method")))

sent = 0
injected = 0
unmatched = []
for path in home.rglob("send-log.jsonl"):
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(raw)
        if row.get("status") != "sent":
            continue
        if row.get("method") == "signed_injection_e2e" and str(row.get("ref", "")).startswith("injected:"):
            injected += 1
            continue
        sent += 1
        key = (row.get("ref"), row.get("method"))
        if key not in owner_approved or key not in send_logged:
            unmatched.append((key[0], key[1], row.get("sha256")))

print(f"owner_approved_records={len(owner_approved)}")
print(f"send_logged_records={len(send_logged)}")
print(f"sent_records={sent}")
print(f"injected_test_records={injected}")
print(f"unmatched_sends={len(unmatched)}")
for ref, method, digest in unmatched:
    print(f"unmatched ref={ref!r} method={method!r} sha256_prefix={str(digest)[:12]}")
raise SystemExit(1 if unmatched else 0)
PY
}

check_patent_glm() {
  ssh "$NODE_PRIMARY_NODE_NAME" "sudo -n -u $NODE_OPS_ACCOUNT -H bash -s" <<'SH'
set -euo pipefail
cd /home/ops/litellm-gateway
count="$(sg docker -c 'docker compose exec -T postgres psql -U litellm -d litellm -Atc "SELECT COUNT(*) AS patent_tag_glm_rows FROM \"LiteLLM_SpendLogs\" WHERE \"model_group\" LIKE '\''glm%'\'' AND COALESCE(\"metadata\"::text, '\'''\'') ILIKE '\''%patent-sensitive%'\'';"')"
printf '%s\n' "$count"
test "$(tr -d "[:space:]" <<<"$count")" = 0
SH
}

check_glm_coding_key() {
  local account
  for account in "$NODE_AGENT_ACCOUNT" "$NODE_PEER_ACCOUNT" "$NODE_OPS_ACCOUNT"; do
    ssh "$NODE_PRIMARY_NODE_NAME" "sudo -n -u $account -H bash -s" <<'SH'
set -euo pipefail
names="$(sed -nE 's/^([A-Za-z_][A-Za-z0-9_]*)=.*/\1/p' "$HOME/.env.secrets" | grep -Ei 'glm.*(coding|plan)|(coding|plan).*glm' || true)"
if [[ -n "$names" ]]; then
  printf 'account=%s prohibited_key_names=%s\n' "$(id -un)" "$(tr '\n' ',' <<<"$names")"
  exit 1
fi
printf 'account=%s prohibited_key_names=0\n' "$(id -un)"
SH
  done
}

check_legacy() {
  ssh "$NODE_PRIMARY_NODE_NAME" "bash -s -- '$NODE_OPERATOR_ACCOUNT'" <<'SH'
set -euo pipefail
operator_account="$1"
for port in 8000 11435 18789 18791 18792 8081; do
  if ss -H -ltn "sport = :$port" | grep -q .; then
    printf 'legacy_port=%s present\n' "$port"
    exit 1
  fi
  printf 'legacy_port=%s absent\n' "$port"
done
names="$(docker ps -a --format '{{.Names}}')"
if grep -Eqi 'vllm-qwen36|sse_repack_proxy|openclaw|telegram-gateway|cloudflared' <<<"$names"; then
  printf 'legacy_container_or_service=present\n'
  exit 1
fi
if crontab -l 2>/dev/null | grep -Eqi 'morning-report|watchdog|sse|cost_monitor|wiki_indexer'; then
  printf 'legacy_cron=present\n'
  exit 1
fi
printf 'primary_node_legacy_containers_and_cron=absent\n'
operator_wiki="/home/$operator_account/cha_wiki"
stat -c 'preserve=%n inode=%i owner=%U:%G type=%F' "$operator_wiki"
test "$(stat -c '%i:%U:%G:%F' "$operator_wiki")" = "56098890:$operator_account:$operator_account:directory"
SH
  ssh "$NODE_RAG_NODE_NAME" "bash -s" <<'SH'
set -euo pipefail
for port in 4000 9000; do
  if ss -H -ltn "sport = :$port" | grep -q .; then
    printf 'legacy_port=%s present\n' "$port"
    exit 1
  fi
  printf 'legacy_port=%s absent\n' "$port"
done
names="$(docker ps -a --format '{{.Names}}')"
if grep -Eqi 'whisper-stt|qdrant-db|vllm-embeddings|postgres-litellm|litellm-gateway' <<<"$names"; then
  printf 'legacy_container=present\n'
  exit 1
fi
if crontab -l 2>/dev/null | grep -Eqi 'morning-report|watchdog'; then
  printf 'legacy_cron=present\n'
  exit 1
fi
printf 'rag_node_legacy_containers_and_cron=absent\n'
printf 'current_rag_ports='
ss -H -ltn '( sport = :8001 or sport = :6333 or sport = :6334 )' | wc -l
stat -c 'preserve=/srv/cha_wiki inode=%i owner=%U:%G type=%F' /srv/cha_wiki
test "$(stat -c '%i:%U:%G:%F' /srv/cha_wiki)" = '2131548:cha-wiki:cha-wiki:directory'
SH
}

check_rag_access() {
  ssh "$NODE_RAG_NODE_NAME" "sudo -n -u $NODE_OPS_ACCOUNT -H bash -s" <<'SH'
set -euo pipefail
payload='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"f4","version":"1"}}}'
invalid_credential='f4-invalid'
no_key="$(curl -sS -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8765/mcp/ -H 'content-type: application/json' --data "$payload")"
wrong_key_http="$(curl -sS -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8765/mcp/ -H 'content-type: application/json' -H "authorization: Bearer ${invalid_credential}" --data "$payload")"
printf 'no_key_http=%s\nwrong_key_http=%s\n' "$no_key" "$wrong_key_http"
test "$no_key" = 401
test "$wrong_key_http" = 403
SH
}

check_embedding() {
  local endpoint_file="$EVIDENCE_DIR/embedding-runtime.txt"
  ssh "$NODE_PRIMARY_NODE_NAME" "sudo -n -u $NODE_AGENT_ACCOUNT -H python3 -" >"$endpoint_file" <<'PY'
import json
from pathlib import Path
from urllib.parse import urlparse

config = json.loads(Path.home().joinpath('.hermes/rag-ingest/config.json').read_text(encoding='utf-8'))
endpoint = config['mcp_base_url']
parsed = urlparse(endpoint)
print(f'mcp_scheme={parsed.scheme}')
print(f'mcp_host={parsed.hostname}')
print(f'mcp_port={parsed.port}')
raise SystemExit(0 if parsed.port == 8765 and parsed.scheme == 'http' else 1)
PY
  local status=$?
  if (( status != 0 )); then
    printf 'exit=%d\n' "$status" >>"$endpoint_file"
    return "$status"
  fi
  if GIT_MASTER=1 git -C "$ROOT" grep -nE 'https?://(api\.openai\.com|api\.z\.ai|api\.cohere\.ai|api\.voyageai\.com)' -- automation/rag_ingest configs/rag; then
    printf 'external_embedding_source_reference=present\n' >>"$endpoint_file"
    return 1
  fi
  grep -Fq '임베딩 경로의 외부 임베딩 API 호출 == 0' "$ROOT/docs/qa/W2-4/02-initial-ingest-network0.txt"
  printf 'external_embedding_source_reference=absent\nprior_strace_external_embedding_calls=0\n' >>"$endpoint_file"
}

record approvals-send-log check_approvals
record patent-glm-spend check_patent_glm
record glm-coding-plan-key check_glm_coding_key
record legacy-teardown check_legacy
record rag-access-control check_rag_access
record external-embedding-zero check_embedding

if (( failed )); then
  printf 'F4 RESULT: FAIL\n' >>"$SUMMARY"
  exit 1
fi
printf 'F4 RESULT: PASS\n' >>"$SUMMARY"

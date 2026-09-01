#!/usr/bin/env bash
# Compare release packages with copies deployed outside the release tree.
# Sourced by healthcheck.sh. Read-only, recursive, and fail-closed: unreadable input
# is UNKNOWN. Four-column rows retain the primary/Python behavior from PR #233;
# optional node/profile columns add cross-node full-tree checks without weakening it.

runtime_package_log() { printf '[runtime-package] %s\n' "$*" >&2; }

runtime_package_deploy_script() { # <release-subpath>
  local source="$1" rest
  if [[ "$source" == configs/rag ]]; then
    printf 'automation/rag_stack/deploy.sh'
    return
  fi
  rest="${source#*/}"
  printf '%s/%s/deploy.sh' "${source%%/*}" "${rest%%/*}"
}

runtime_package_is_cache_path() {
  [[ "/$1/" == */__pycache__/* || "/$1/" == */.venv/* \
    || "/$1/" == */.ruff_cache/* || "/$1/" == */.pytest_cache/* ]]
}

# cron/ is not part of a runtime package: the deployers exclude it and the watcher
# wrapper lands in ~/.hermes/scripts, where the watcher manifest observes it. Leaving
# it in the release snapshot made the probe report ABSENT cron/<pkg>_watch.py on every
# tick (2026-08-22, first live run after the allowlist was regenerated). The release
# side alone is filtered on purpose — the remote command text is pinned by the
# allowlist wrapper, and the drift digest does not cover that text. An optional
# manifest file list narrows only the release snapshot: the runtime remains complete
# so stale extra files still fail the comparison.
runtime_package_selected() { # <profile> <relative-path> [deployed-python-files]
  local profile="$1" relative="$2" files="${3:-}"
  runtime_package_is_cache_path "$relative" && return 1
  case "$profile" in
    python) [[ "$relative" == *.py && "$relative" != cron/* ]] ;;
    tree) [[ "$relative" != .env.secrets ]] ;;
    *) return 2 ;;
  esac || return 1
  [[ -z "$files" || ",$files," == *",$relative,"* ]]
}

# Snapshot format is sha256|package-relative-path. The tree profile intentionally
# hashes every managed file, not only Python: Dockerfiles, compose, pyproject, lock
# files, service/env examples, and dockerignore files can all alter what is deployed.
runtime_package_local_snapshot() ( # <root> [python|tree] [deployed-python-files]
  local root="$1" profile="${2:-python}" files="${3:-}" file relative hash
  cd / || return 1
  [[ -d "$root" && -r "$root" && -x "$root" ]] || return 1
  [[ "$profile" == python || "$profile" == tree ]] || return 1
  find "$root" -type d \( -name __pycache__ -o -name .venv -o -name .ruff_cache -o -name .pytest_cache \) -prune -o -type f -print >/dev/null 2>&1 || return 1
  printf 'SNAPSHOT-V1\n'
  while IFS= read -r -d '' file; do
    relative="${file#"$root"/}"
    runtime_package_selected "$profile" "$relative" "$files" || continue
    [[ -r "$file" ]] || return 1
    hash="$(sha256sum -- "$file" | cut -d' ' -f1)" || return 1
    printf '%s|%s\n' "$hash" "$relative"
  done < <(find "$root" -type d \( -name __pycache__ -o -name .venv -o -name .ruff_cache -o -name .pytest_cache \) -prune -o -type f -print0 | sort -z)
)

runtime_package_remote_snapshot() { # <node> <account> <home-relative-runtime> <profile>
  local node="$1" account="$2" runtime="$3" profile="${4:-python}"
  capture_on_node "$node" \
    "sudo -n -u ${account} -H bash -o pipefail -c 'cd \"\$HOME\" || exit 44; root=\"\$HOME/${runtime}\"; profile=\"${profile}\"; [[ -d \"\$root\" && -r \"\$root\" && -x \"\$root\" ]] || exit 44; printf \"SNAPSHOT-V1\\n\"; find \"\$root\" -type d \\( -name __pycache__ -o -name .venv -o -name .ruff_cache -o -name .pytest_cache \\) -prune -o -type f -print0 | sort -z | while IFS= read -r -d \"\" file; do relative=\"\${file#\"\$root\"/}\"; if [[ \"\$profile\" == python && \"\$relative\" != *.py ]] || [[ \"\$relative\" == .env.secrets ]]; then continue; fi; [[ -r \"\$file\" ]] || exit 44; hash=\$(sha256sum -- \"\$file\" | cut -d\" \" -f1) || exit; printf \"%s|%s\\n\" \"\$hash\" \"\$relative\"; done'"
}

runtime_package_compare() { # <label> <deployer> <release-snapshot> <runtime-snapshot>
  local label="$1" deployer="$2" release_data="$3" runtime_data="$4"
  local line sha path failed=0 compared=0
  declare -A release_files=() runtime_files=()
  [[ "${release_data%%$'\n'*}" == SNAPSHOT-V1 && "${runtime_data%%$'\n'*}" == SNAPSHOT-V1 ]] || {
    runtime_package_log "RUNTIME-PACKAGE-UNKNOWN: malformed snapshot for $label — run $deployer"; return 1;
  }
  while IFS= read -r line; do
    [[ "$line" == SNAPSHOT-V1 || -z "$line" ]] && continue
    IFS='|' read -r sha path <<< "$line"
    [[ "$sha" =~ ^[0-9a-f]{64}$ && -n "$path" ]] || return 1
    release_files["$path"]="$sha"
  done <<< "$release_data"
  while IFS= read -r line; do
    [[ "$line" == SNAPSHOT-V1 || -z "$line" ]] && continue
    IFS='|' read -r sha path <<< "$line"
    [[ "$sha" =~ ^[0-9a-f]{64}$ && -n "$path" ]] || return 1
    runtime_files["$path"]="$sha"
  done <<< "$runtime_data"
  for path in "${!release_files[@]}"; do
    if [[ ! -v "runtime_files[$path]" ]]; then
      runtime_package_log "RUNTIME-PACKAGE ABSENT $path ($label) — run $deployer"; failed=1
    elif [[ "${runtime_files[$path]}" != "${release_files[$path]}" ]]; then
      runtime_package_log "RUNTIME-PACKAGE DIFF $path ($label) — run $deployer"; failed=1
    else compared=$((compared + 1)); fi
  done
  for path in "${!runtime_files[@]}"; do
    if [[ ! -v "release_files[$path]" ]]; then
      runtime_package_log "RUNTIME-PACKAGE EXTRA $path ($label) — run $deployer"; failed=1
    fi
  done
  (( failed == 0 )) || return 1
  runtime_package_log "RUNTIME-PACKAGE-PASS: $label ($compared file(s))"
}

# Host source convergence alone would miss this incident's next variant: an old MCP
# image still running after a source-only deploy. Read two shipped modules from the
# running compose-labelled container; no env file, rebuild, restart, or write occurs.
runtime_package_verify_rag_image() { # <node> <account> <release-rag-root> <deployer>
  local node="$1" account="$2" root="$3" deployer="$4" data relative want got failed=0
  if ! data="$(capture_on_node "$node" "sudo -n -u ${account} -H bash -o pipefail -c 'ids=\$(docker ps --filter label=com.docker.compose.service=mcp --format \"{{.ID}}\"); [[ \$(wc -w <<< \"\$ids\") == 1 ]] || exit 45; project=\$(docker inspect --format \"{{index .Config.Labels \\\"com.docker.compose.project\\\"}}\" \"\$ids\"); [[ \"\$project\" == personal-rag || \"\$project\" == personal_rag ]] || exit 45; docker exec \"\$ids\" sha256sum /app/src/rag_mcp/app.py /app/src/rag_mcp/store.py'")"; then
    runtime_package_log "RUNTIME-PACKAGE-UNKNOWN: cannot inspect running personal-rag MCP image — run $deployer then activate MCP"
    return 1
  fi
  for relative in app.py store.py; do
    want="$(sha256sum -- "$root/mcp/src/rag_mcp/$relative" | cut -d' ' -f1)" || return 1
    got="$(awk -v suffix="/rag_mcp/$relative" '$2 ~ suffix "$" {print $1}' <<< "$data")"
    if [[ "$got" != "$want" ]]; then
      runtime_package_log "RUNTIME-PACKAGE IMAGE-DIFF mcp/src/rag_mcp/$relative — run $deployer then activate MCP"; failed=1
    fi
  done
  (( failed == 0 ))
}

probe_runtime_packages_current() {
  local node="$1" _account="$2" manifest="$3"
  local source_root="${HEALTHCHECK_RELEASE_SOURCE_ROOT:-/srv/autophagy-agent-current}"
  local runtime_root="${HEALTHCHECK_RUNTIME_PACKAGE_ROOT:-}"
  local selector_filter="${HEALTHCHECK_RUNTIME_PACKAGE_SELECTOR:-all}"
  local failed=0 selected=0 row account source runtime policy selector profile files extra row_node deployer release_data runtime_data
  [[ "$selector_filter" =~ ^(all|default|rag)$ ]] || { runtime_package_log "RUNTIME-PACKAGE-UNKNOWN: invalid selector filter"; return 1; }
  [[ -r "$manifest" ]] || { runtime_package_log "RUNTIME-PACKAGE-UNKNOWN: unreadable manifest=$manifest"; return 1; }
  local -a rows=(); mapfile -t rows < "$manifest"
  for row in "${rows[@]}"; do
    [[ -n "${row//[[:space:]]/}" ]] || continue
    [[ "${row#"${row%%[![:space:]]*}"}" == \#* ]] && continue
    IFS='|' read -r account source runtime policy selector profile files extra <<< "$row"
    selector="${selector:-default}"; profile="${profile:-python}"
    [[ "$selector_filter" == all || "$selector" == "$selector_filter" ]] || continue
    selected=$((selected + 1))
    if ! [[ "$account" =~ ^[a-z][a-z0-9_-]*$ && "$source" =~ ^[A-Za-z0-9_./-]+$ && "$runtime" =~ ^[A-Za-z0-9_./-]+$ ]] \
      || [[ "$policy" != required || -n "$extra" || ! "$selector" =~ ^(default|rag)$ || ! "$profile" =~ ^(python|tree)$ ]] \
      || [[ -n "$files" && ( "$profile" != python || ! "$files" =~ ^([A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.py(,([A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.py)*$ ) ]]; then
      runtime_package_log "RUNTIME-PACKAGE-UNKNOWN: invalid manifest row: ${row:0:80}"; failed=1; continue
    fi
    row_node="$node"
    if [[ "$selector" == rag ]]; then
      row_node="${NODE_RAG_NODE_NAME:-}"
      [[ -n "$row_node" ]] || { runtime_package_log "RUNTIME-PACKAGE-UNKNOWN: RAG node is not configured"; failed=1; continue; }
    fi
    deployer="$(runtime_package_deploy_script "$source")"
    if ! release_data="$(runtime_package_local_snapshot "$source_root/$source" "$profile" "$files")"; then
      runtime_package_log "RUNTIME-PACKAGE-UNKNOWN: unreadable release source $source — run $deployer"; failed=1; continue
    fi
    if [[ -n "$runtime_root" ]]; then
      if ! runtime_data="$(runtime_package_local_snapshot "$runtime_root/$runtime" "$profile")"; then
        runtime_package_log "RUNTIME-PACKAGE-UNKNOWN: cannot read ${account}:${runtime} — run $deployer"; failed=1; continue
      fi
    elif ! runtime_data="$(runtime_package_remote_snapshot "$row_node" "$account" "$runtime" "$profile")"; then
      runtime_package_log "RUNTIME-PACKAGE-UNKNOWN: cannot read ${account}@${row_node}:${runtime} — node unreachable or command not allowlisted; run $deployer"; failed=1; continue
    fi
    runtime_package_compare "${account}@${row_node}:${runtime}" "$deployer" "$release_data" "$runtime_data" || failed=1
    if [[ -z "$runtime_root" && "$source" == configs/rag && "$profile" == tree ]]; then
      runtime_package_verify_rag_image "$row_node" "$account" "$source_root/$source" "$deployer" || failed=1
    fi
  done
  if (( selected == 0 )); then
    runtime_package_log "RUNTIME-PACKAGE-UNKNOWN: manifest has no rows for selector=$selector_filter"
    return 1
  fi
  (( failed == 0 ))
}

probe_primary_runtime_packages_current() {
  HEALTHCHECK_RUNTIME_PACKAGE_SELECTOR=default probe_runtime_packages_current "$@"
}

probe_rag_stack_current() {
  HEALTHCHECK_RUNTIME_PACKAGE_SELECTOR=rag probe_runtime_packages_current "$@"
}

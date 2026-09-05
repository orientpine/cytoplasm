#!/usr/bin/env bash
# automation/provision-skill-roots.sh — Hermes 스킬 루트 토폴로지를 **반전**한다.
# 은퇴한 read-only bind 부트스트랩을 대체한다. 멱등, root 실행.
#
# WHY (2026-08-15, SS-1): 예전 부트스트랩은 agent 계정의 `<agent_home>/.hermes/skills` 를
# `<skill_store>/live` 의 read-only bind 로 덮었다. 그래서 Hermes 가 자기 1차
# 루트에 써야 하는 것들(`.usage.json`·`.curator_state`·`.archive`)을 agent 계정에서
# 아예 쓸 수 없었고, Hermes v0.18.2 는 새 스킬을 `HERMES_HOME/skills` 에만 만든다.
# `skills.external_dirs` 는 1차 루트 뒤에 스캔되는 읽기 전용 발견 목록이므로
# (agent/skill_utils.py:503-511) 방향이 반대여야 한다:
#   1차 루트 = agent 소유 쓰기 가능(0700) / live = external_dirs 로 등록.
# 소유자 정책: 자작 스킬은 승인 없이 착지하고 사후 감사한다(guard_agent_created).
#
# 유지되는 것(구 스크립트에서 그대로): store/releases/live 생성, 특권 헬퍼 설치,
# sudoers 설치 + visudo 검증, 리포 스킬의 릴리스 설치 루프. 사라지는 것: bind 마운트.
#
# Env (테스트 seam; 기본값은 `automation/node_config_sh.py --print-env` 가 내는 노드 config):
#   AGENT_ACCOUNT / PEER_ACCOUNT     default $NODE_AGENT_ACCOUNT / $NODE_PEER_ACCOUNT
#   AGENT_HOME / PEER_HOME           default $NODE_AGENT_HOME / $NODE_PEER_HOME
#   STORE_ROOT                       default $NODE_SKILL_STORE
#   FSTAB_PATH                       default /etc/fstab
#   HELPER_PATH / SUDOERS_PATH       특권 헬퍼·sudoers 설치 경로
#   VERIFY_SKILL                     외부 발견 검증에 쓰는 governed 스킬 (default wiki)
#   SKILL_ROOTS_ASSUME_ROOT          root 검사 우회 (헤르메틱 테스트 전용)
#   SKILL_ROOTS_SKIP_STORE           특권 store 설치 arm 생략 (헤르메틱 테스트 전용)
set -euo pipefail

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
eval "$(python3 -B "$REPO_ROOT/automation/node_config_sh.py" --print-env)"
readonly AGENT_ACCOUNT="${AGENT_ACCOUNT:-$NODE_AGENT_ACCOUNT}"
readonly PEER_ACCOUNT="${PEER_ACCOUNT:-$NODE_PEER_ACCOUNT}"
readonly AGENT_HOME="${AGENT_HOME:-$NODE_AGENT_HOME}"
readonly PEER_HOME="${PEER_HOME:-$NODE_PEER_HOME}"
readonly STORE_ROOT="${STORE_ROOT:-$NODE_SKILL_STORE}"
readonly LIVE_ROOT="$STORE_ROOT/live"
readonly AGENT_SKILLS_ROOT="$AGENT_HOME/.hermes/skills"
readonly PEER_SKILLS_ROOT="$PEER_HOME/.hermes/skills"
readonly AGENT_CONFIG="$AGENT_HOME/.hermes/config.yaml"
readonly PEER_CONFIG="$PEER_HOME/.hermes/config.yaml"
readonly HUB_STATE="$AGENT_HOME/.hermes/skill-hub-state"
readonly HUB_TARGET="$AGENT_SKILLS_ROOT/.hub"
readonly FSTAB_PATH="${FSTAB_PATH:-/etc/fstab}"
readonly HELPER_PATH="${HELPER_PATH:-$NODE_LIBEXEC_DIR/autophagy-install-skill}"
readonly SUDOERS_PATH="${SUDOERS_PATH:-/etc/sudoers.d/autophagy-skill-store}"
readonly VERIFY_SKILL="${VERIFY_SKILL:-wiki}"

# 은퇴한 부트스트랩이 남긴 정확한 두 줄. 문자열 구성은 구 스크립트와 동일하다.
readonly LEGACY_BIND_ENTRY="$LIVE_ROOT $AGENT_SKILLS_ROOT none bind,ro,nosuid,nodev 0 0"
readonly LEGACY_HUB_ENTRY="$HUB_STATE $HUB_TARGET none bind,rw,nosuid,nodev,noexec 0 0"

# peer 잔여 사본 허용목록 — 이 이름들만, 그리고 검증을 통과할 때만 제거한다.
readonly PEER_RESIDUE_ALLOWLIST=(coordination prompt wiki)
# peer 자작 스킬 — curator 가 자동 전이시키지 못하게 pin 한다.
# `skill-deploy-review` 는 여기 넣지 않는다. E7(docs/patch/2026-07-17-e7-peer-attestation.md)이
# "an agent must not read a Discord request and be instructed to run a reviewer as part of
# deployment" 로 그 리뷰어를 은퇴시켰고, 결정론적 대체제는 ops 체크아웃에서 도는
# automation/peer_attest.py 다(자가 스킬 루트를 참조하지 않는다). 이름을 되돌려 넣으면
# 프로비저닝마다 부활해 peer 가 `[release]` 승인 카드까지 즉석 심사한다 — 그 심사는 HEAD 를
# 관측 미러에서 찾는데 미러는 sync_mirror 규칙상 릴리스 수렴 뒤에야 전진하므로 새 릴리스
# HEAD 는 언제나 "unpushed tip" 으로 읽혀 거짓 ⛔ 가 상시 발생한다(v1.1.2 실측).
readonly PEER_PINNED_SKILLS=(autophagy-interop)

log() { printf '[provision-skill-roots] %s\n' "$*"; }
die() { log "ERROR: $1" >&2; exit 1; }
timestamp() { date -u +%Y%m%dT%H%M%SZ; }

is_root() {
  if [[ -n "${SKILL_ROOTS_ASSUME_ROOT:-}" ]]; then
    [[ "$SKILL_ROOTS_ASSUME_ROOT" == "1" ]]
  else
    [[ "$EUID" == 0 ]]
  fi
}

run_as() { # run_as <account> <home> <command...>
  local account="$1" home="$2"
  shift 2
  sudo -n -u "$account" -H env "HOME=$home" "PATH=$home/.local/bin:$PATH" "$@"
}

# 인자를 받지 않는다 — 두 arm 은 모두 멱등이라 항상 함께 돌려도 안전하다.
(( $# == 0 )) || die "unexpected argument: $1 (this provisioner takes none)"

# ---------- preflight ----------
is_root || die "run as root: sudo bash automation/provision-skill-roots.sh"
for command_name in install python3 tar sha256sum mountpoint umount visudo systemctl sudo stat; do
  command -v "$command_name" >/dev/null || die "required command missing: $command_name"
done
id "$AGENT_ACCOUNT" >/dev/null 2>&1 || die "agent account missing: $AGENT_ACCOUNT"
[[ -d "$REPO_ROOT/skills" ]] || die "canonical skills directory missing: $REPO_ROOT/skills"

# ---------- store arm (구 스크립트에서 그대로 유지) ----------
install_store() {
  install -d -m 0755 -o root -g root \
    "$(dirname "$HELPER_PATH")" "$STORE_ROOT" "$STORE_ROOT/releases" "$LIVE_ROOT"
  install -d -m 0755 -o root -g root "$LIVE_ROOT/.hub"
  install -m 0755 -o root -g root "$REPO_ROOT/automation/skill_store.py" "$HELPER_PATH"
  # sudoers 시드는 `$NODE_*` 플레이스홀더를 담고 있다 — 렌더러를 거치지 않고
  # 그대로 설치하면 root 소유 규칙이 존재하지 않는 경로를 가리킨다.
  python3 -B "$REPO_ROOT/automation/node_asset_renderer.py" \
    "$REPO_ROOT/automation/sudoers.d/autophagy-skill-store" "$SUDOERS_PATH.tmp"
  install -m 0440 -o root -g root "$SUDOERS_PATH.tmp" "$SUDOERS_PATH"
  rm -f "$SUDOERS_PATH.tmp"
  visudo -cf "$SUDOERS_PATH" >/dev/null

  local source skill digest
  for source in "$REPO_ROOT"/skills/*; do
    [[ -f "$source/SKILL.md" ]] || continue
    skill="$(basename "$source")"
    digest="$(PYTHONPATH="$REPO_ROOT" python3 -B -c 'from pathlib import Path; from automation.skill_review import skill_digest; import sys; print(skill_digest(Path(sys.argv[1])))' "$source")"
    tar -C "$(dirname "$source")" --exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo' -czf - "$skill" \
      | "$HELPER_PATH" install --skill "$skill" --hash "$digest"
  done
  log "store: helper=$HELPER_PATH sudoers=$SUDOERS_PATH live=$LIVE_ROOT"
}

# ---------- 레거시 bind 해체 ----------
unmount_legacy_binds() {
  local target
  # .hub 가 스킬 루트 **안**에 중첩돼 있으므로 반드시 먼저 푼다.
  for target in "$HUB_TARGET" "$AGENT_SKILLS_ROOT"; do
    if mountpoint -q "$target"; then
      umount "$target" || die "could not unmount legacy bind: $target"
      log "legacy bind unmounted: $target"
    else
      log "not a mountpoint — skipping unmount: $target"
    fi
  done
}

purge_legacy_fstab() {
  if ! grep -Fqx -- "$LEGACY_BIND_ENTRY" "$FSTAB_PATH" \
    && ! grep -Fqx -- "$LEGACY_HUB_ENTRY" "$FSTAB_PATH"; then
    log "fstab: no legacy bind entries — skipping"
    return 0
  fi
  local backup="${FSTAB_PATH}.bak-selfskill-$(timestamp)"
  cp -p -- "$FSTAB_PATH" "$backup"
  local staged="${FSTAB_PATH}.selfskill.tmp"
  grep -Fvx -- "$LEGACY_BIND_ENTRY" "$FSTAB_PATH" \
    | grep -Fvx -- "$LEGACY_HUB_ENTRY" > "$staged" || true
  cat -- "$staged" > "$FSTAB_PATH" # 인플레이스로 써서 inode·권한을 보존한다
  rm -f -- "$staged"
  log "legacy fstab bind entries purged (backup: $backup)"
  systemctl daemon-reload
}

# ---------- agent 1차 루트 ----------
ensure_agent_root() {
  if [[ -d "$AGENT_SKILLS_ROOT" && "$(stat -c '%U' "$AGENT_SKILLS_ROOT")" != "$AGENT_ACCOUNT" ]]; then
    local backup="${AGENT_SKILLS_ROOT}.root-owned.$(timestamp)"
    mv -- "$AGENT_SKILLS_ROOT" "$backup" # 지우지 않는다 — 되돌릴 수 있게 비켜둘 뿐
    log "leftover non-agent skills root moved aside: $backup"
  fi
  install -d -m 0700 -o "$AGENT_ACCOUNT" -g "$AGENT_ACCOUNT" "$AGENT_SKILLS_ROOT"
  log "agent primary skills root ready (0700 $AGENT_ACCOUNT): $AGENT_SKILLS_ROOT"
}

migrate_hub_state() {
  if [[ ! -d "$HUB_STATE" ]]; then
    log "hub state absent — nothing to migrate: $HUB_STATE"
    return 0
  fi
  install -d -m 0700 -o "$AGENT_ACCOUNT" -g "$AGENT_ACCOUNT" "$HUB_TARGET"
  local expected="" entry name
  # readback 은 **이번에 실제로 옮긴** taps.json 에만 건다 — 재실행 시 목적지의
  # 최신본을 남은 근원과 비교해 거짓 불일치를 내면 안 된다.
  shopt -s dotglob nullglob
  for entry in "$HUB_STATE"/*; do
    name="$(basename "$entry")"
    if [[ -e "$HUB_TARGET/$name" ]]; then
      log "hub migrate: $name already present in the primary root — leaving the source alone"
      continue
    fi
    if [[ "$name" == "taps.json" ]]; then
      expected="$(sha256sum < "$entry" | cut -d' ' -f1)"
    fi
    mv -- "$entry" "$HUB_TARGET/$name"
  done
  shopt -u dotglob nullglob
  if [[ -n "$expected" ]]; then
    [[ -f "$HUB_TARGET/taps.json" ]] || die "hub migrate: taps.json vanished during migration"
    local observed
    observed="$(sha256sum < "$HUB_TARGET/taps.json" | cut -d' ' -f1)"
    [[ "$observed" == "$expected" ]] || die "hub migrate: taps.json sha256 mismatch"
    log "hub migrate: taps.json readback OK sha256=${observed:0:16}"
  fi
  log "hub state migrated into the primary root: $HUB_TARGET"
}

# ---------- config 패치 (stdlib/grep 전용 — PyYAML 없음) ----------
skills_block() {
  awk '
    /^skills:[[:space:]]*$/ { inside = 1; next }
    inside && /^[^[:space:]#]/ { inside = 0 }
    inside { print }
  ' "$1"
}

config_missing_lines() { # <config> <wants_external> -> 부족한 줄들(들여쓰기 포함)
  local trimmed
  trimmed="$(skills_block "$1" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  grep -Fxq 'guard_agent_created: true' <<<"$trimmed" \
    || printf '  guard_agent_created: true\n'
  [[ "$2" == 1 ]] || return 0
  if ! grep -Fxq -- "- $LIVE_ROOT" <<<"$trimmed"; then
    grep -Fxq 'external_dirs:' <<<"$trimmed" || printf '  external_dirs:\n'
    printf '    - %s\n' "$LIVE_ROOT"
  fi
}

patch_config() { # <label> <config> <account> <wants_external>
  local label="$1" config="$2" account="$3" wants_external="$4" missing backup
  [[ -f "$config" ]] || die "$label config missing: $config"
  if grep -Eq '^skills:[[:space:]]*$' "$config"; then
    missing="$(config_missing_lines "$config" "$wants_external")"
    if [[ -z "$missing" ]]; then
      log "$label config: skills block already satisfies the contract — skipping"
      return 0
    fi
    # 기존 블록은 넘겨짚어 고치지 않는다 — 소유자가 직접 채우고 재실행하면 수렴한다.
    log "SKILLS-BLOCK-BLOCK: $label config already has a 'skills:' block" >&2
    log "add these lines under 'skills:' in $config, then re-run:" >&2
    printf '%s\n' "$missing" >&2
    die "refusing to edit an existing skills block blindly: $config"
  fi
  backup="${config}.bak-selfskill-$(timestamp)"
  install -m 0600 -o "$account" -g "$account" "$config" "$backup"
  {
    printf '\nskills:\n'
    [[ "$wants_external" == 1 ]] && printf '  external_dirs:\n    - %s\n' "$LIVE_ROOT"
    printf '  guard_agent_created: true\n'
  } >> "$config"
  log "$label config: canonical skills block appended (backup: $backup)"
}

verify_agent() {
  ! mountpoint -q "$AGENT_SKILLS_ROOT" || die "agent skills root is still a mountpoint"
  ! mountpoint -q "$HUB_TARGET" || die "hub target is still a mountpoint"
  ! grep -Fqx -- "$LEGACY_BIND_ENTRY" "$FSTAB_PATH" || die "legacy bind entry still in fstab"
  ! grep -Fqx -- "$LEGACY_HUB_ENTRY" "$FSTAB_PATH" || die "legacy hub entry still in fstab"
  [[ "$(stat -c '%U' "$AGENT_SKILLS_ROOT")" == "$AGENT_ACCOUNT" ]] \
    || die "agent skills root is not owned by $AGENT_ACCOUNT"
  [[ "$(stat -c '%a' "$AGENT_SKILLS_ROOT")" == "700" ]] \
    || die "agent skills root is not 0700"
  [[ -z "$(config_missing_lines "$AGENT_CONFIG" 1)" ]] || die "agent config contract unmet"
  # governed 스킬이 여전히 보이면 external_dirs 발견이 실제로 동작한다는 증거다.
  run_as "$AGENT_ACCOUNT" "$AGENT_HOME" hermes skills list | grep -Fq -- "$VERIFY_SKILL" \
    || die "external discovery broken: '$VERIFY_SKILL' is not listed for $AGENT_ACCOUNT"
  log "VERIFIED agent root=$AGENT_SKILLS_ROOT (0700 $AGENT_ACCOUNT) external=$LIVE_ROOT"
}

# ---------- peer arm ----------
remove_peer_residue() {
  local name directory
  for name in "${PEER_RESIDUE_ALLOWLIST[@]}"; do
    directory="$PEER_SKILLS_ROOT/$name"
    [[ -d "$directory" ]] || continue
    if [[ ! -f "$directory/SKILL.md" ]]; then
      log "PEER-RESIDUE-SKIP: $name has no SKILL.md — leaving it alone"
      continue
    fi
    if ! grep -Fqx 'author: autophagy-agents' "$directory/SKILL.md"; then
      log "PEER-RESIDUE-SKIP: $name is not repo-authored (no 'author: autophagy-agents')"
      continue
    fi
    if [[ ! -d "$REPO_ROOT/skills/$name" ]]; then
      log "PEER-RESIDUE-SKIP: $name is not in the repo skills tree"
      continue
    fi
    rm -rf -- "$directory"
    log "peer residue removed: $directory"
  done
}

peer_is_pinned() {
  run_as "$PEER_ACCOUNT" "$PEER_HOME" hermes curator status 2>/dev/null \
    | grep -E '^pinned \([0-9]+\):' | grep -Eq "(^|[ ,])$1([ ,]|$)"
}

pin_peer_skills() {
  local name
  for name in "${PEER_PINNED_SKILLS[@]}"; do
    if [[ ! -d "$PEER_SKILLS_ROOT/$name" ]]; then
      log "PEER-PIN-SKIP: $name is not present in $PEER_SKILLS_ROOT"
      continue
    fi
    if peer_is_pinned "$name"; then
      log "peer pin: $name pinned (readback OK)"
      continue
    fi
    run_as "$PEER_ACCOUNT" "$PEER_HOME" hermes curator pin "$name" >/dev/null
    peer_is_pinned "$name" || die "peer pin readback failed: $name"
    log "peer pin: $name pinned (readback OK)"
  done
}

# ---------- run ----------
if [[ -n "${SKILL_ROOTS_SKIP_STORE:-}" ]]; then
  log "store install skipped (SKILL_ROOTS_SKIP_STORE)"
else
  install_store
fi
unmount_legacy_binds
purge_legacy_fstab
ensure_agent_root
migrate_hub_state
patch_config agent "$AGENT_CONFIG" "$AGENT_ACCOUNT" 1
verify_agent

# peer 계정이 없는 노드도 있다 — 그때는 굳힐 것이 없으므로 큰 소리로 건너뛴다.
if id "$PEER_ACCOUNT" >/dev/null 2>&1; then
  patch_config peer "$PEER_CONFIG" "$PEER_ACCOUNT" 0
  remove_peer_residue
  pin_peer_skills
else
  log "PEER-ARM-SKIP: account missing: $PEER_ACCOUNT"
fi

log "READY primary=$AGENT_SKILLS_ROOT external=$LIVE_ROOT"

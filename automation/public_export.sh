#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly UPDATE_TRUST_PRINCIPAL="update-trust@autophagy"
readonly MANIFEST_PATH="configs/public-export-manifest.txt"
readonly CANARY_DIR=".public-export-scanner-canary"
readonly RELEASE_TAG_PATTERN='^v[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z][0-9A-Za-z.-]*)?$'

log() { printf '[public-export] %s\n' "$*" >&2; }
block() { log "PUBLIC-EXPORT-BLOCK: $*"; exit 1; }

cleanup_non_venv_pycache() {
  local scan_root="$1" cache_dir
  [[ -d "$scan_root" ]] || block "scan root is not a directory: $scan_root"
  scan_root="$(realpath -- "$scan_root")" || block "cannot resolve scan root: $scan_root"
  while IFS= read -r -d '' cache_dir; do
    log "removing non-venv Python bytecode cache: $cache_dir"
    rm -rf -- "$cache_dir"
  done < <(
    find "$scan_root" \
      \( -type d \( -name .venv -o -name venv -o -exec test -f '{}/pyvenv.cfg' \; \) -prune \) -o \
      \( -type d -name __pycache__ -print0 \)
  )
}

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then return 0; fi

usage() {
  cat <<'EOF'
Usage: automation/public_export.sh \
  --target-dir DIR --remote URL_OR_PATH [--version vX.Y.Z] \
  --signing-key PATH --repository-name OWNER/REPO --visibility public \
  [--source-repo DIR] [--source-ref REF]

Build a public-history snapshot, test and scan it, sign its release tag with the
update-trust SSH key, then atomically push main and the tag. The destination
remote must be distinct from the private source repository's origin.
When --version is omitted, the semantic release tag already attached to the
source commit is reused. An explicit --version always wins.
EOF
}

source_repo=""
source_ref="origin/main"
target_dir=""
remote=""
version=""
signing_key="${UPDATE_TRUST_SIGNING_KEY:-}"
repository_name=""
visibility=""

while (($# > 0)); do
  case "$1" in
    --source-repo) [[ $# -ge 2 ]] || block "$1 requires a value"; source_repo="$2"; shift 2 ;;
    --source-ref) [[ $# -ge 2 ]] || block "$1 requires a value"; source_ref="$2"; shift 2 ;;
    --target-dir) [[ $# -ge 2 ]] || block "$1 requires a value"; target_dir="$2"; shift 2 ;;
    --remote) [[ $# -ge 2 ]] || block "$1 requires a value"; remote="$2"; shift 2 ;;
    --version) [[ $# -ge 2 ]] || block "$1 requires a value"; version="$2"; shift 2 ;;
    --signing-key) [[ $# -ge 2 ]] || block "$1 requires a value"; signing_key="$2"; shift 2 ;;
    --repository-name) [[ $# -ge 2 ]] || block "$1 requires a value"; repository_name="$2"; shift 2 ;;
    --visibility) [[ $# -ge 2 ]] || block "$1 requires a value"; visibility="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) block "unknown argument: $1" ;;
  esac
done

[[ -n "$target_dir" ]] || block "--target-dir is required"
[[ -n "$remote" ]] || block "--remote is required"
[[ -n "$signing_key" ]] || block "--signing-key or UPDATE_TRUST_SIGNING_KEY is required"
[[ -n "$repository_name" ]] || block "--repository-name is required"
[[ "$visibility" == "public" ]] || block "--visibility must be exactly public"
[[ "$repository_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
  || block "--repository-name must be OWNER/REPO"

# C2: --remote reaches git argument positions. It must not be readable as an
# option, must not carry control characters that would break ls-remote parsing,
# and must not select the command-executing ext:: transport.
reject_control_characters() {
  [[ "$2" == "${2//[[:cntrl:]]/}" ]] || block "$1 must not contain control characters"
}
reject_control_characters "--remote" "$remote"
[[ "$remote" != -* ]] || block "--remote must not begin with a dash"
[[ "${remote,,}" != ext::* ]] \
  || block "--remote must not use the command-executing ext:: transport"

# C3: --signing-key is interpolated into `git -c "user.signingkey=$signing_key"`,
# where a newline would append a second, attacker-chosen git config line.
reject_control_characters "--signing-key" "$signing_key"

for tool in find git gitleaks grep python3 pytest ssh-keygen tar cp mktemp realpath; do
  command -v "$tool" >/dev/null 2>&1 || block "required tool is unavailable: $tool"
done

readonly script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
source_repo="${source_repo:-$(dirname -- "$script_dir")}"
source_root="$(git -C "$source_repo" rev-parse --show-toplevel 2>/dev/null)" \
  || block "--source-repo is not a git working tree"
source_root="$(realpath -- "$source_root")"
target_dir="$(realpath -m -- "$target_dir")"
[[ ! -e "$target_dir" && ! -L "$target_dir" ]] || block "target already exists: $target_dir"
[[ -d "$(dirname -- "$target_dir")" ]] || block "target parent does not exist"
[[ "$target_dir" != "$source_root" && "$target_dir" != "$source_root/"* ]] \
  || block "target must be outside the private source working tree"
[[ "$remote" != "origin" ]] || block "the private source origin cannot be the public destination"

origin_url="$(git -C "$source_root" remote get-url origin 2>/dev/null || true)"
destination_url="$(git -C "$source_root" remote get-url -- "$remote" 2>/dev/null || printf '%s' "$remote")"
[[ -z "$origin_url" || "$destination_url" != "$origin_url" ]] \
  || block "destination resolves to the private source origin"

status="$(git -C "$source_root" status --porcelain=v1 --untracked-files=all)" \
  || block "cannot inspect source worktree status"
[[ -z "$status" ]] || block "source worktree is dirty; commit or remove every change first"
[[ "$(git -C "$source_root" rev-parse --is-shallow-repository)" == "false" ]] \
  || block "source is shallow; full-history secret scanning is impossible"
source_commit="$(git -C "$source_root" rev-parse --verify "$source_ref^{commit}" 2>/dev/null)" \
  || block "source ref is not a commit: $source_ref"
if [[ -z "$version" ]]; then
  tag_listing="$(git -C "$source_root" tag --points-at "$source_commit" --list 'v*')" \
    || block "cannot inspect release tags on source commit"
  declare -a release_tags=()
  while IFS= read -r candidate; do
    [[ "$candidate" =~ $RELEASE_TAG_PATTERN ]] && release_tags+=("$candidate")
  done <<<"$tag_listing"
  ((${#release_tags[@]} > 0)) \
    || block "source commit has no semantic release tag; pass --version explicitly"
  ((${#release_tags[@]} == 1)) \
    || block "source commit has multiple semantic release tags; pass --version explicitly"
  version="${release_tags[0]}"
fi
[[ "$version" =~ $RELEASE_TAG_PATTERN ]] \
  || block "--version must be a v-prefixed semantic version"
source_date="$(git -C "$source_root" show -s --format=%cI "$source_commit")" \
  || block "cannot read source commit timestamp"

[[ -f "$signing_key" && ! -L "$signing_key" ]] \
  || block "update-trust signing key must be a regular non-symlink file"
public_key="$(ssh-keygen -y -f "$signing_key" </dev/null 2>/dev/null)" \
  || block "update-trust signing key is unreadable or unusable"

if gitleaks git --help >/dev/null 2>&1 && gitleaks dir --help >/dev/null 2>&1; then
  gitleaks_mode="commands"
elif gitleaks detect --help >/dev/null 2>&1; then
  gitleaks_mode="legacy"
else
  block "gitleaks provides neither git/dir commands nor the legacy detect command"
fi

scan_git_history() {
  local scan_root="$1"
  case "$gitleaks_mode" in
    commands) (cd -- "$scan_root" && gitleaks git --no-banner --redact --log-opts="--all --full-history" .) ;;
    legacy) (cd -- "$scan_root" && gitleaks detect --no-banner --redact --source . --log-opts="--all --full-history") ;;
    *) block "internal gitleaks mode is invalid" ;;
  esac
}

scan_directory() {
  local scan_root="$1"
  case "$gitleaks_mode" in
    commands) (cd -- "$scan_root" && gitleaks dir --no-banner --redact .) ;;
    legacy) (cd -- "$scan_root" && gitleaks detect --no-banner --redact --no-git --source .) ;;
    *) block "internal gitleaks mode is invalid" ;;
  esac
}

scan_directory_report() {
  local scan_root="$1" report="$2"
  case "$gitleaks_mode" in
    commands) (cd -- "$scan_root" && gitleaks dir --no-banner --redact --report-format json --report-path "$report" .) ;;
    legacy) (cd -- "$scan_root" && gitleaks detect --no-banner --redact --no-git --report-format json --report-path "$report" --source .) ;;
    *) block "internal gitleaks mode is invalid" ;;
  esac
}

canary_root=""
remove_canary() {
  if [[ -n "$canary_root" ]]; then rm -rf -- "$canary_root"; canary_root=""; fi
}

scratch="$(mktemp -d)" || block "cannot create private scratch directory"
target_created=0
cleanup() {
  local rc=$?
  trap - EXIT
  remove_canary
  rm -rf -- "$scratch"
  if ((rc != 0 && target_created == 1)); then rm -rf -- "$target_dir"; fi
  exit "$rc"
}
trap cleanup EXIT

# C5: a zero-finding scan only means something if the scanner actually read the
# tree. Plant two detectable canaries under the scan root - one plain, one that
# the canary directory's own .gitignore excludes - and require gitleaks to
# report BOTH. A build that honours ignore files would skip part of the tree
# while still exiting 0, which is precisely the false assurance this blocks.
# The canary literal is assembled at runtime so this file carries no
# secret-shaped string of its own, and the canaries never outlive the probe.
verify_scanner_reaches() {
  local scan_root="$1"
  local report="$scratch/canary-report.json"
  local root="$scan_root/$CANARY_DIR"
  local probe="AKI""A""QYLPMN5HHHFPZAAA"
  canary_root="$root"
  mkdir -p -- "$root" || block "cannot plant the scanner canary under $scan_root"
  printf 'aws_access_key_id = "%s"\n' "$probe" >"$root/plain.txt"
  printf 'ignored.txt\n' >"$root/.gitignore"
  printf 'aws_access_key_id = "%s"\n' "$probe" >"$root/ignored.txt"
  rm -f -- "$report"
  scan_directory_report "$scan_root" "$report" || true
  remove_canary
  [[ -s "$report" ]] || block "gitleaks wrote no report while probing $scan_root"
  grep -F -q -- "$CANARY_DIR/plain.txt" "$report" \
    || block "gitleaks missed a planted canary in $scan_root; a zero-finding scan proves nothing"
  grep -F -q -- "$CANARY_DIR/ignored.txt" "$report" \
    || block "gitleaks skipped an ignore-listed canary in $scan_root; scan coverage is narrower than assumed"
  rm -f -- "$report"
}

manifest="$scratch/manifest"
git -C "$source_root" show "$source_commit:$MANIFEST_PATH" >"$manifest" 2>/dev/null \
  || block "$MANIFEST_PATH is missing or unreadable at $source_ref"

declare -a exclusions=()
declare -A seen=()
has_omo=0
has_qa=0
line_number=0
while IFS= read -r line || [[ -n "$line" ]]; do
  ((line_number += 1))
  [[ "$line" != *$'\r'* ]] || block "manifest line $line_number contains a carriage return"
  [[ -n "$line" ]] || continue
  [[ "$line" != \#* ]] || continue
  [[ ! "$line" =~ ^[[:space:]] && ! "$line" =~ [[:space:]]$ ]] \
    || block "manifest line $line_number has surrounding whitespace"
  [[ "$line" != /* && "$line" != ./* && "$line" != *\\* && "$line" != *//* ]] \
    || block "manifest line $line_number is not a canonical relative path"
  [[ "$line" != *"*"* && "$line" != *"?"* && "$line" != *"["* && "$line" != *"]"* ]] \
    || block "manifest line $line_number contains a glob pattern"
  path="${line%/}"
  [[ -n "$path" && "/$path/" != *"/../"* && "/$path/" != *"/./"* ]] \
    || block "manifest line $line_number contains traversal"
  [[ "$path" != ".git" && "$path" != .git/* ]] \
    || block "manifest must describe snapshot content, not git metadata"
  [[ -z "${seen[$path]+present}" ]] || block "manifest path is duplicated: $path"
  seen["$path"]=1
  object_type="$(git -C "$source_root" cat-file -t "$source_commit:$path" 2>/dev/null)" \
    || block "manifest path is absent at $source_ref: $path"
  if [[ "$line" == */ ]]; then
    [[ "$object_type" == "tree" ]] || block "manifest directory is not a tree: $line"
  else
    [[ "$object_type" == "blob" ]] || block "manifest file is not a blob: $line"
  fi
  exclusions+=("$line")
  [[ "$line" != ".omo/" ]] || has_omo=1
  [[ "$line" != "docs/qa/" ]] || has_qa=1
done <"$manifest"
((has_omo == 1 && has_qa == 1)) \
  || block "manifest must explicitly exclude both .omo/ and docs/qa/"

cleanup_non_venv_pycache "$source_root"
log "scanning private working tree and all private refs"
verify_scanner_reaches "$source_root"
scan_directory "$source_root" || block "working-tree secret scan failed"
scan_git_history "$source_root" || block "full-history secret scan failed"

remote_refs="$(GIT_TERMINAL_PROMPT=0 git ls-remote -- "$remote")" \
  || block "destination remote is unavailable"
[[ -z "$(GIT_TERMINAL_PROMPT=0 git ls-remote -- "$remote" "refs/tags/$version")" ]] \
  || block "destination tag already exists: $version"

snapshot="$scratch/snapshot"
mkdir "$snapshot"
git -C "$source_root" archive "$source_commit" | tar -x -C "$snapshot" \
  || block "cannot materialize source snapshot"
for exclusion in "${exclusions[@]}"; do rm -rf -- "$snapshot/${exclusion%/}"; done
[[ -f "$snapshot/automation/public_export_redaction.py" ]] \
  || block "snapshot redaction helper is absent: automation/public_export_redaction.py"
python3 "$snapshot/automation/public_export_redaction.py" "$snapshot" \
  || block "vendored public-snapshot de-identification failed"

public_main=""
while IFS=$'\t' read -r oid ref; do
  if [[ "$ref" == "refs/heads/main" ]]; then public_main="$oid"; break; fi
done <<<"$remote_refs"
if [[ -n "$public_main" ]]; then
  GIT_TERMINAL_PROMPT=0 git clone --quiet --branch main --single-branch --no-tags -- "$remote" "$target_dir" \
    || block "cannot clone destination main"
else
  GIT_TERMINAL_PROMPT=0 git clone --quiet --no-checkout --no-tags -- "$remote" "$target_dir" \
    || block "cannot initialize destination clone"
  git -C "$target_dir" symbolic-ref HEAD refs/heads/main \
    || block "cannot initialize destination main branch"
fi
target_created=1
git -C "$target_dir" rm -r --quiet --ignore-unmatch . \
  || block "cannot clear the prior public snapshot"
cp -a -- "$snapshot/." "$target_dir/" || block "cannot populate public snapshot"
git -C "$target_dir" add -A || block "cannot stage public snapshot"

log "scanning and testing exported tree"
verify_scanner_reaches "$target_dir"
scan_directory "$target_dir" || block "exported-tree secret scan failed"
(cd -- "$target_dir" && python3 -m pytest tests/unit) || block "exported-tree unit tests failed"

commit_message="Public export $version ($repository_name, $visibility)"
GIT_AUTHOR_DATE="$source_date" GIT_COMMITTER_DATE="$source_date" \
  git -C "$target_dir" -c user.name="Autophagy Update Trust" \
    -c user.email="$UPDATE_TRUST_PRINCIPAL" -c commit.gpgsign=false \
    commit --quiet -m "$commit_message" -m "source-sha:$source_commit" \
    || block "public snapshot has no committable change"
export_commit="$(git -C "$target_dir" rev-parse HEAD)"

allowed_signers="$scratch/update-allowed-signers"
printf '%s namespaces="git" %s\n' "$UPDATE_TRUST_PRINCIPAL" "$public_key" >"$allowed_signers"
tag_message="Autophagy public release $version
repository:$repository_name
visibility:$visibility
source-sha:$source_commit"

# D8: this is the UPDATE TRUST key for public release tags, not the separate
# managed-skill group key. Git's native SSH-tag namespace is always `git`.
git -C "$target_dir" -c gpg.format=ssh -c "user.signingkey=$signing_key" \
  -c user.name="Autophagy Update Trust" -c "user.email=$UPDATE_TRUST_PRINCIPAL" \
  tag -s "$version" -m "$tag_message" \
  || block "signed update-trust tag creation failed"
[[ "$(git -C "$target_dir" cat-file -t "refs/tags/$version")" == "tag" ]] \
  || block "release tag is not an annotated signed tag"
git -C "$target_dir" -c gpg.format=ssh \
  -c "gpg.ssh.allowedSignersFile=$allowed_signers" verify-tag "$version" \
  || block "release tag does not verify as $UPDATE_TRUST_PRINCIPAL in namespace git"
scan_git_history "$target_dir" || block "public-history secret scan failed"

GIT_TERMINAL_PROMPT=0 git -C "$target_dir" push --atomic -- "$remote" \
  HEAD:refs/heads/main "refs/tags/$version:refs/tags/$version" \
  || block "atomic public commit-and-tag push failed"

remote_main="$(GIT_TERMINAL_PROMPT=0 git ls-remote -- "$remote" refs/heads/main)" \
  || block "cannot read back destination main"
remote_tag="$(GIT_TERMINAL_PROMPT=0 git ls-remote -- "$remote" "refs/tags/$version")" \
  || block "cannot read back destination tag"
[[ "${remote_main%%$'\t'*}" == "$export_commit" ]] \
  || block "destination main does not match the exported commit"
[[ "${remote_tag%%$'\t'*}" == "$(git -C "$target_dir" rev-parse "refs/tags/$version")" ]] \
  || block "destination tag does not match the signed local tag"

log "PUBLIC-EXPORT-OK repository=$repository_name visibility=$visibility source_sha=$source_commit commit=$export_commit tag=$version target=$target_dir"

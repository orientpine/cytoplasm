#!/usr/bin/env bash
# automation/release-note.sh — 이미 잘린 릴리스 태그에 GitHub Release 노트를 게시한다.
#
# 정상 경로에서는 release.sh 가 태그와 같은 단계에서 노트를 게시하므로 이 명령이 필요 없다.
# 이것은 노트 없이 남은 태그를 보충하는 자리다 — release.sh 는 직전 릴리스에 노트가 없으면
# 새 릴리스를 열지 않고(RELEASE-NOTE-MISSING) 이 명령을 안내한다. 구현은 release_tag_lib.sh 의
# ensure_release_note 하나이며 여기서 사본을 만들지 않는다. 이미 노트·자산이 있으면 무동작이다.
#
# 사용: automation/release-note.sh <vX.Y.Z> --notes-file <note.md>
#   note.md 첫 줄은 `# <한 줄 요약>`(Release 제목이 된다), 그 아래 한국어 본문.
#   PR 목록과 업데이트 신뢰키 지문·update-trust.pub 자산은 자동으로 붙는다.
# Env: UPDATE_TRUST_SIGNING_KEY(공개키 또는 개인키 경로), RELEASE_GH, RELEASE_NOTE_REPO
# Exit: 0 게시됨(또는 이미 있음) · 2 usage · 4 태그 없음 · 1 게시 실패(RELEASE-NOTE-FAIL)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${RELEASE_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
# shellcheck source=automation/release_tag_lib.sh
source "${RELEASE_TAG_LIB:-$SCRIPT_DIR/release_tag_lib.sh}"
release_tag_log() { printf '[release-note] %s\n' "$*" >&2; }

usage="usage: release-note.sh <vX.Y.Z> --notes-file <note.md>"
tag="${1:-}"
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ && "${2:-}" == "--notes-file" && -n "${3:-}" && $# -eq 3 ]] \
  || { echo "$usage" >&2; exit 2; }

git -C "$REPO_ROOT" fetch --quiet origin --tags || { release_tag_log "could not fetch origin"; exit 4; }
sha="$(git -C "$REPO_ROOT" ls-remote --tags origin "refs/tags/$tag^{}" | awk '{ print $1 }')"
[[ -n "$sha" ]] || { release_tag_log "$tag is not a signed release tag on origin"; exit 4; }
ensure_release_note "$REPO_ROOT" "$tag" "$sha" "$3"

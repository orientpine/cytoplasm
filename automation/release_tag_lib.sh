#!/usr/bin/env bash
# 서명된 릴리스 태그를 자르는 단일 구현.
#
# 2분 리컨실러(`converge_origin_main.sh`)는 **`origin/main` HEAD 자체가 annotated 서명
# 태그의 peel 대상일 때만** 수렴한다. 인자를 받지 않는 것이 그 헬퍼의 계약이라(MD-1),
# 자동 트리거는 설치될 sha 를 고를 수 없고 서명만이 그것을 정한다. 그래서 태그가 없으면
# 프로덕션은 전진하지 않는다 — 조용히. 매 틱 `UPDATE-TRUST-BLOCK` 으로 서지만 rc 0 이다.
#
# 이 구현은 원래 `land.sh` 안에만 있었고, 그래서 **`land.sh` 로 들어온 커밋에만** 태그가
# 붙었다. 브랜치 작업은 land 가 아니라 PR 머지로 main 에 도달하므로(land.sh 헤더의 명시),
# PR 로 들어온 커밋에는 아무도 태그를 붙이지 않았다 — 2026-08-20 실측으로 PR 6건이 그렇게
# 들어갔고 리컨실러가 132회 연속 실패하며 프로덕션이 2커밋 뒤에 얼어 있었다.
#
# 호출자는 `release_tag_log` 를 덮어써 자기 접두사로 로그할 수 있다(land.sh 가 그렇게 한다).

release_tag_log() { printf '[release-tag] %s\n' "$*" >&2; }

#: 서명키는 **로컬에만** 둔다. CI 로 옮기면 "머지 = 프로덕션 임의 코드 실행"이 되어
#: MD-1 이 막으려던 그 escalation 이 그대로 되살아난다(AGENTS.md 「공개 릴리스 규칙」).
: "${UPDATE_TRUST_SIGNING_KEY:=$HOME/.ssh/autophagy_update_trust.pub}"

next_release_tag() { # next_release_tag <repo_root> [major|minor|patch]
  local latest major minor patch bump="${2:-patch}"
  case "$bump" in major|minor|patch) ;; *) return 1 ;; esac
  latest="$(git -C "$1" ls-remote --tags --refs origin 'refs/tags/v*' 2>/dev/null \
    | awk '{ sub("refs/tags/", "", $2); if ($2 ~ /^v[0-9]+\.[0-9]+\.[0-9]+$/) print $2 }' \
    | sort -V | tail -n 1)"
  [[ -n "$latest" ]] || { printf 'v1.0.0\n'; return 0; }
  IFS=. read -r major minor patch <<<"${latest#v}"
  case "$bump" in
    major) printf 'v%s.0.0\n' "$((major + 1))" ;;
    minor) printf 'v%s.%s.0\n' "$major" "$((minor + 1))" ;;
    patch) printf 'v%s.%s.%s\n' "$major" "$minor" "$((patch + 1))" ;;
  esac
}

latest_release_base() { # latest_release_base <repo_root> — the sha the newest release tag peels to
  git -C "$1" ls-remote --tags origin 'refs/tags/v*' 2>/dev/null \
    | awk '$2 ~ /\^\{\}$/ { sub("refs/tags/", "", $2); sub(/\^\{\}$/, "", $2); print $2 " " $1 }' \
    | sort -V | tail -n 1 | awk '{ print $2 }'
}

released_tag_at() { # released_tag_at <repo_root> <sha> — the tag already peeling to sha
  git -C "$1" ls-remote --tags origin 2>/dev/null \
    | awk -v sha="$2" '$1 == sha && $2 ~ /\^\{\}$/ { sub("refs/tags/", "", $2); sub(/\^\{\}$/, "", $2); print $2 }' \
    | head -n 1
}

release_version_for() { # release_version_for <repo_root> <sha> [major|minor|patch]
  #: HEAD 에 이미 릴리스 태그가 있으면 그것이 이 릴리스의 버전이다 — 완결기·재실행은 태그 컷
  #: 뒤의 deploy 를 재개하는 것이지 다음 버전을 여는 것이 아니다(2026-09-03: next 를 다시
  #: 계산해 v1.1.2 를 요청하자 ensure_signed_tag 의 이름 불일치 검사가 자기 태그 v1.1.1 을 거부했다).
  local existing
  existing="$(released_tag_at "$1" "$2")"
  if [[ -n "$existing" ]]; then printf '%s\n' "$existing"; return 0; fi
  next_release_tag "$1" "${3:-patch}"
}

ensure_signed_tag() { # ensure_signed_tag <repo_root> <sha> [requested_version]
  local existing tag="${3:-}"
  existing="$(released_tag_at "$1" "$2")"
  if [[ -n "$existing" ]]; then
    if [[ -n "$tag" && "$existing" != "$tag" ]]; then
      release_tag_log "tag at HEAD is $existing, not requested $tag"
      return 1
    fi
    release_tag_log "already released as $existing"
    return 0
  fi
  [[ -f "$UPDATE_TRUST_SIGNING_KEY" ]] \
    || { release_tag_log "no update-trust signing key at $UPDATE_TRUST_SIGNING_KEY"; return 1; }
  if [[ -z "$tag" ]]; then
    tag="$(next_release_tag "$1")" \
      || { release_tag_log "could not read the released tag series"; return 1; }
  fi
  git -C "$1" -c gpg.format=ssh -c "user.signingkey=$UPDATE_TRUST_SIGNING_KEY" \
      tag -s "$tag" -m "release: $tag" "$2" >/dev/null 2>&1 \
    || { release_tag_log "signing tag $tag failed"; return 1; }
  git -C "$1" push origin "$tag" >/dev/null 2>&1 \
    || { release_tag_log "pushing tag $tag failed"; return 1; }
  release_tag_log "signed release tag $tag -> $2"
}

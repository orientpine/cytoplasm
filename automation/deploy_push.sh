#!/usr/bin/env bash
# 배포 파일을 노드로 보내고 **착지를 확인한다.**
#
# 왜 확인이 필요한가 (2026-08-20 실측): `skills/wiki/deploy.sh` 가 rc=0 으로 끝났는데
# 노드의 파일은 7월 22일자 그대로였다. 그때 11개 deploy.sh 의 `push_file` 은 전부
# 바이트 동일했고 전부 이 한 줄로 끝났다:
#
#     run_agent "... cat > \"\$HOME/<dest>\" ..." < "$source"
#
# 원격 `cat` 은 stdin 이 비어 있어도 0을 돌려준다. 그래서 **아무것도 쓰지 않아도 성공**이다.
# 그 실행에서는 ssh 가 로컬 포워딩 실패를 경고했고(`Could not request local forwarding`),
# `bash -lc` 로그인 셸이 프로필을 읽으며 stdin 을 먼저 소비할 수 있는 구조였다 — 어느 쪽이든
# 보내는 쪽에서는 구별할 방법이 없다. `set -euo pipefail` 은 이미 다 붙어 있었으므로
# 종료코드 전파 문제가 아니라 **확인하지 않은 쓰기** 문제였다.
#
# 원격 read-back 해시 대조는 이 리포가 이미 쓰는 방식이다 — `obsidian_write` 는 push 뒤
# 원격에서 해시를 다시 읽고, 스킬 배포 판정은 `readlink live/<skill>` 이다
# (「커밋됨 ≠ 배포됨」). 배포 스크립트만 그 규율 밖에 있었다.
#
# 호출자는 `run_agent <script>` 를 정의해 둔 상태여야 한다(계정·호스트가 스크립트마다 다르다).
#
# 호출 규약: 성공은 0, 어떤 실패든 `DEPLOY-BLOCK` 줄을 stderr 에 남기고 5 를 돌려준다 —
# 호출자가 `set -euo pipefail` 이든 아니든 같다. 배포기는 errexit 아래에서 이 헬퍼를 부르므로,
# 함수 안에서만 errexit 를 끄고(`local -` 가 반환 때 호출자의 옵션을 되돌린다) 각 단계의
# 실패를 직접 처리한다. 끄지 않으면 쓰기 단계의 ssh 가 255 를 내는 순간 호출자의 셸이 끝나
# 진단도 이 코드도 없이 rc 255 로 끝난다(RCB todo 59, todo 45 의 트리 교체 헬퍼와 같은 결함).
# 쓰기 단계가 0 이 아니면 read-back 이 맞아도 실패다(fail-closed) — 그래도 read-back 은
# 돌려 진단 줄에 두 단계의 코드와 노드의 해시를 함께 싣는다.

#: read-back 은 **자기 stdin 을 /dev/null 로 막는다.** 막지 않으면 ssh 가 호출자의 stdin 을
#: 먹어, 바로 다음 push_file 이 빈 파일을 쓰고도 성공을 보고한다 — 고치려던 그 증상 그대로다.
push_file() { # push_file <source> <destination-relative-to-HOME>
  local -
  set +e
  local source="$1" destination="$2" want got quoted write_rc read_rc
  # 목적지는 원격 셸에 두 번 파싱된다 — 셸 문자가 든 경로는 보내기 전에 거부한다.
  if [[ ! "$destination" =~ ^[A-Za-z0-9._/-]+$ || "$destination" == /* || "/$destination/" == */./* || "/$destination/" == */../* ]]; then
    printf 'DEPLOY-BLOCK: unsafe destination %q — only a plain home-relative path is allowed.\n' "$destination" >&2
    return 5
  fi
  quoted="$(printf '%q' "$destination")"
  if ! want="$(sha256sum -- "$source" 2>/dev/null)" || [[ -z "$want" ]]; then
    printf 'DEPLOY-BLOCK: cannot hash source %q for %s — nothing was sent.\n' "$source" "$destination" >&2
    return 5
  fi
  want="${want%% *}"

  # 목적지에 바로 쓰지 않는다: 같은 디렉터리의 임시 파일에 받아 해시가 맞을 때만 rename 한다.
  # 반쯤 쓰인 파일은 읽는 쪽(cron)에게 절대 보이지 않고, 실패하면 기존 목적지가 그대로 남는다.
  run_agent 'umask 077
dest="$HOME"/'"$quoted"'
mkdir -p "$(dirname "$dest")" || exit 1
tmp="$dest.deploy-tmp.$$"
cat > "$tmp" || { rm -f "$tmp"; exit 1; }
chmod 600 "$tmp" || { rm -f "$tmp"; exit 1; }
got="$(sha256sum -- "$tmp" | cut -d" " -f1)"
if [ "$got" = "'"$want"'" ]; then
  mv -f "$tmp" "$dest" || { rm -f "$tmp"; exit 1; }
else
  rm -f "$tmp"
  exit 1
fi' < "$source"
  write_rc=$?

  got="$(run_agent "sha256sum \"\$HOME\"/$quoted 2>/dev/null | cut -d' ' -f1" < /dev/null)"
  read_rc=$?
  got="${got//[[:space:]]/}"

  if [[ "$write_rc" -ne 0 || "$read_rc" -ne 0 || "$got" != "$want" ]]; then
    printf 'DEPLOY-BLOCK: %s did not land on the node — a transfer step failed or the read-back does not match.\n' \
      "$destination" >&2
    printf '              want=%s got=%s write_rc=%s readback_rc=%s\n' \
      "${want:0:16}" "${got:0:16}" "$write_rc" "$read_rc" >&2
    printf '              re-run this deploy; if it repeats, check ssh forwarding warnings and the agent login shell.\n' >&2
    return 5
  fi
}

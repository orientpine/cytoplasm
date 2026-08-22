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

#: read-back 은 **자기 stdin 을 /dev/null 로 막는다.** 막지 않으면 ssh 가 호출자의 stdin 을
#: 먹어, 바로 다음 push_file 이 빈 파일을 쓰고도 성공을 보고한다 — 고치려던 그 증상 그대로다.
push_file() { # push_file <source> <destination-relative-to-HOME>
  local source="$1" destination="$2" want got
  want="$(sha256sum -- "$source" | cut -d' ' -f1)"

  run_agent "umask 077; mkdir -p \"\$HOME/$(dirname "$destination")\"; cat > \"\$HOME/$destination\"; chmod 600 \"\$HOME/$destination\"" < "$source"

  got="$(run_agent "sha256sum \"\$HOME/$destination\" 2>/dev/null | cut -d' ' -f1" < /dev/null)"
  got="${got//[[:space:]]/}"

  if [[ "$got" != "$want" ]]; then
    printf 'DEPLOY-BLOCK: %s did not land on the node — the remote command reported success but the file does not match.\n' \
      "$destination" >&2
    printf '              want=%s got=%s\n' "${want:0:16}" "${got:0:16}" >&2
    printf '              re-run this deploy; if it repeats, check ssh forwarding warnings and the agent login shell.\n' >&2
    return 5
  fi
}

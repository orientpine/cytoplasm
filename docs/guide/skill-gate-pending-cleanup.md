# skill-gate pending 정리 절차

skill-gate pending 레코드는 임의로 일괄 삭제하지 않는다. 레코드는 소유자의 승인·거부 판단과
검토된 아티팩트를 잇는 근거이므로, 정리는 각 레코드의 현재 바인딩을 확인한 뒤 기존 게이트의
CAS 경로로만 수행한다.

## 현재 스키마 레코드

consume-on-mount 이전에 남은 `doctype`, `hello-autophagy`, `managed-hello-autophagy`, `meeting`,
`procurement`, `prompt`, `proposal`, `recall`, `repair`, `report`, `topics` 레코드는 다음 두 경로 중
하나로 정리한다.

1. 같은 스킬을 다음에 배포하면 MOUNT 성공 직후 `consume`이 정확한 `(skill, hash, message_id)`를
   비교해 자동 회수한다.
2. 다시 배포할 효과가 없고 소유자가 정리를 결정하면 `skill_gate.py abandon --skill ... --hash ...
   --message-id ... --reason ...`을 실행한다. 세 필드가 모두 일치해야 하며 감사 로그를 먼저
   fsync한 뒤 레코드만 회수한다. Discord 메시지는 삭제하지 않는다.

## pre-schema 레코드

`action_hash`, `kind`, `channel_id`, `policy_version`, `surface`가 빠진 레거시는 자동 승인 근거로
승격하지 않는다. 소유자가 이미 실현된 효과와 레코드 바인딩을 확인한 경우에만 위 `abandon`에
`--legacy-only`를 추가한다. 이 플래그는 현재 스키마 레코드를 거부하므로 레거시 종결 명령이
현대 레코드를 잘못 회수하지 못한다.

이 문서는 실행 지시가 아니라 폐쇄 경로 정의다. 현재 운영 레코드의 실제 정리 여부와 시점은
소유자가 결정한다.

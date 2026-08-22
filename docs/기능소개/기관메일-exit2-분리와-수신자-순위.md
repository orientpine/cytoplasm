# 기관메일 exit 2 분리와 수신자 순위

## 무엇을

기관메일(mailon)의 종료코드 2를 **인증 실패**와 **브라우저 실패**로 갈라서, 서로 다른
`error_code`와 서로 다른 안내문을 낸다. 가릴 근거가 없으면 둘 중 하나로 단정하지 않는다.
그리고 이름→주소 해석(`resolve`)의 후보 순서를 결정론으로 두고, 주소가 여러 개면 그 사실을
`ambiguous`로 알린다.

## 왜

**5일짜리 오진이 이 한 줄에서 나왔다.** 소유자의 논문 메일이 막힌 동안 원인은 계속
"기관메일 인증 실패"로 보고됐다. 실제 원인은 로그인 완료 오판과 메일함 SPA 기동 경쟁이었고
자격증명은 멀쩡했다. mailon의 exit 2는 이름부터 `auth_or_browser_error` — 두 부류를 한
코드로 접은 값인데, 래퍼가 그것을 조건 없이 `auth_error`로 접고 첫 줄부터 인증 실패라고
**단정**하는 안내문을 붙였다. 그래서 수리 방향이 반대로 돌아 비밀번호 교체 권고까지 갔다.
가를 신호는 이미 있었다(`classify_stderr`가 stderr에서 뽑는 `failure_signature`). 쓰지
않았을 뿐이다.

**resolve는 한 사람에 대해 순위 없는 후보를 돌려줬다.** 실측에서 후보 3건 중 주소는 2종이었고
(`organization`·`contacts`가 같고 `history`가 달랐다), 어느 쪽이 현재 유효한지 판정할 신호가
응답에 없었다. 호출자가 임의로 고르면 **잘못된 수신자에게 발송**되고 그건 되돌릴 수 없다.

덤으로, 재인증 안내문 ③이 2026-07-30에 이 저장소로 흡수된 `~/emailAutomation`을 가리키고
있었다. 하필 장애 대응 중에만 읽히는 문구라 평소에는 낡은 채로 드러나지 않는다.

## 사용 시나리오

**브라우저 문제일 때.** compose가 기동 경쟁으로 죽으면 이제 `error_code`가 `browser_error`로
나오고, 안내문은 "자격증명 문제가 아니므로 재인증·비밀번호 교체로 가지 말 것"으로 시작해
잔여 chrome 정리 → agent-browser 확인 → selector 순으로 이끈다. 프로세스 종료코드는 여전히
2라서 이 코드로 분기하던 호출자는 아무 영향을 받지 않는다.

**진짜 인증 문제일 때.** stderr에 `LoginError`가 있으면 예전처럼 `auth_error` + 재인증
절차가 나온다. 다만 ③번 수동 검증 단계는 실재하는 런타임
`~/.hermes/mailon-runtime/current`를 가리킨다.

**가릴 수 없을 때.** 시그니처가 없으면 `auth_or_browser_error`로 두고 "둘 중 하나로
단정하지 말 것 — 단정이 수리 방향을 반대로 돌린 실측 선례가 있다"고 말한다. 모르는 것을
모른다고 말하는 것이 fail-closed다.

**이름으로 메일 보낼 때.** `resolve --name 김샘플`의 후보는 `organization` → `contacts` →
`history` 순으로 정렬돼 오고, 주소가 2종 이상이면 `ambiguous: true`와
`distinct_address_count`가 함께 온다. 에이전트는 후보 수가 아니라 **주소 수**로 판단한다 —
후보가 셋이어도 주소가 하나면 물어볼 것이 없고, 주소가 둘이면 자동 선택 없이 cha에게 묻는다.

**호스트를 안 준 채 배포할 때.** `skills/mail/deploy.sh`가 `DEPLOY_SSH_HOST` 없이 실행되면
`DEPLOY-BLOCK`으로 즉시 멈춘다(exit 3). 예전에는 해석되지 않는 이름으로 ssh를 시도해 DNS
오류가 났고, 그 메시지는 진짜 원인("변수를 안 줬다")을 가리키지 않았다.

## 관련

- 분기 판정: `skills/mail/scripts/mailon_failure.py` (`classify_exit_two`)
- 후보 순위·모호성: `skills/mail/scripts/mail_wrapper_read.py` (`rank_candidates`,
  `distinct_addresses`) · 우선순위 상수는 `mailon_interface.RESOLVE_GROUP_PRIORITY`
- 검사: `tests/unit/test_mailon_failure_split.py` · `tests/unit/test_deploy_host_fail_closed.py`
- 문서: `skills/mail/SKILL.md` 종료코드 표와 resolve 판정 규칙(v1.5.9)
- 외부효과: 없다. 발송은 변함없이 소유자 ✅ 게이트를 거치며, 이 변경은 그 게이트 앞의
  진단과 후보 제시 방식만 바꾼다.

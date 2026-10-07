# mailon 런타임 고정 탐지

## 무엇을

노드에서 도는 mailon 런타임이 그 노드의 기준 트리가 들고 있는 vendor 코드와 같은지 대조해, 다르면
소유자에게 **한 번** 알린다. 판정은 매일 아침 다이제스트 틱이 태우고, 고정이 해소되면 한 번 더
알린 뒤 다시 조용해진다. 기준 트리는 `AUTOPHAGY_REPO_ROOT` 가 있으면 그것, 없으면 릴리스 포인터
`/srv/autophagy-agent-current`(그 아래 `skills` 가 있을 때), 그것도 아니면 관측 미러 `/srv/autophagy-agents`
다 — `origin/main` 이 아니라 노드가 지금 실행하는 릴리스다.

## 왜

**19일이 조용히 지나갔다.** 2026-07-29에 커밋된 vendor 수정이 프로덕션에 도달한 것은 08-18이다.
그동안 발송은 8/14까지 멀쩡히 동작했으므로 이것은 「중단」이 아니라 **고정**이었고, 비정상이라는
신호가 어디에도 없었다. 스킬은 `readlink live/<skill>` 해시로 판정하고 코드는 리컨실러가
따라오지만, **vendor 런타임은 둘 다 아니다** — 사람이 `skills/mail/deploy.sh`를 돌려야만 갱신된다.

더 나쁜 것은 마침내 배포하는 순간이다. 19일치 미검증 변경이 한꺼번에 올라가고, 실제로 그날
결함 2건이 동시에 올라와 모든 발송이 즉시 실패했다. 2회 연속 실패가 mail-mode를 `no-go`로
강등시켰다.

## 만들다 나온 것: 다이제스트가 내용 주소가 아니었다

런타임 릴리스 디렉터리는 vendor 소스의 다이제스트로 **이름 지어진다**. 그런데 그 계산이
`sha256sum`의 `"<해시>  <경로>"` 출력을 그대로 다시 해싱하고 있어서 **절대 경로가 다이제스트에
섞여** 있었다. 같은 바이트를 다른 디렉터리에 풀면 다른 값이 나온다는 뜻이고, 저장소 트리와 배포
트리를 대조하는 프로브는 그 위에서 원리적으로 성립할 수 없다. 다이제스트를 공용 헬퍼 하나로
옮겨 경로 접두사를 제거했고, 생산자(릴리스 스크립트)와 탐지자(드리프트 프로브)가 같은 함수를
쓰는지 회귀로 고정했다 — 드리프트 탐지기가 드리프트하면 아무 의미가 없다.

## 사용 시나리오

**정상.** 런타임이 최신이면 아무 말도 없다. 다이제스트는 평소대로 조용히 끝난다.

**고정.** 노드 런타임이 옛 vendor에 묶여 있으면 다음 아침 다이제스트 DM에 한 줄이 붙는다 —
`mailon-runtime-drift failed 1 ticks in a row: DRIFT ... runtime=<런타임 digest> repo=<기준 트리 vendor digest>`.
그 뒤로는 며칠이 지나도 반복하지 않는다. `skills/mail/deploy.sh`를 소유자 승인 아래 돌려
수렴시키면 그 다음 틱에 `recovered` 한 줄이 오고 끝난다.

**판정 불가.** 런타임이 없거나 `current` 심링크가 끊겼거나 릴리스 트리에 vendor가 없으면
exit 2 `UNKNOWN`이다. 이때는 **아무 말도 하지 않는다** — 판정할 수 없는 것은 사건이 아니라
노드에서 메워야 할 공백이고, 0으로 접지도 않는다(부재는 PASS가 아니다).

## 판정이 더 말하는 것 (2026-10)

- **신원**: 런타임 빌드가 `runtime-manifest.json` 을 남겼으면 그 `src_digest`·`req_digest` 쌍이
  신원이다. `src_digest` 는 런타임 자신의 `mailon/` 으로 다시 계산해 대조하므로, manifest 만 맞고
  내용이 바뀐 경우를 `corrupt=1` 로 잡는다. `req_digest` 는 manifest 에 적힌 값을 기준 트리
  `requirements.txt` 의 sha256 앞 16자와 비교할 뿐이고, 설치된 패키지를 다시 계산하지는 않는다.
  vendor 가 같아도 그 값이 다르면 DRIFT 다. manifest 가 없는 옛 빌드는 예전처럼 디렉터리 이름이
  신원이다.
- **방향**: 릴리스 트리에는 `.git` 이 없으므로, git 이 답하는 첫 루트(기본: 릴리스 트리, 그다음
  관측 미러)에서 두 다이제스트를 도입한 커밋을 찾아 조상 관계를 본다. 런타임이 뒤처졌으면
  `skills/mail/deploy.sh` 를, 런타임이 앞섰으면(`direction=runtime-ahead`) `automation/release.sh`
  를 안내한다. 판정할 수 없으면 `direction=unknown` 으로 둘 다 안내한다.
- **수렴**: 이 프로브는 여전히 탐지만 한다. 수렴은 이제 릴리스가 한다 — mailon 런타임은
  `skills/mail/deploy-manifest.txt` 의 `v2:derived` 행으로 선언돼 있어, 릴리스 판정이 어긋남을 보면
  `skills/mail/deploy.sh` 를 돌린다.

## 왜 healthcheck가 아닌가

런타임은 `~agent/.hermes` 아래 0700 agent 소유라 ops 계정으로 도는 healthcheck가 읽을 수 없다.
우회하려면 중첩 sudo가 필요한데, 이 저장소에는 SSH allowlist와 중첩 sudo가 **둘 다 rc=126으로
프로브를 죽인** 실측 선례가 있다. 반면 다이제스트 워처는 이미 agent 계정으로 돌고 이미
`--deliver discord`로 소유자에게 닿는다 — 권한도 전달 경로도 이미 맞다. healthcheck 배선은
권한 설계 결정이 필요해 후속 과제로 남겼다.

## 관련

- 프로브: `skills/mail/scripts/mailon_runtime_drift.sh` · 공용 다이제스트
  `skills/mail/scripts/mailon_vendor_digest.sh`
- 태우는 곳: `skills/mail/scripts/mail_digest_watch.py` (`_report_runtime_drift`) · 1회 통지는
  `watch_failure_streak` 재사용
- 검사: `tests/unit/test_mailon_runtime_drift.py` · `tests/unit/test_vendor_fixture_provenance.py`
- 외부효과: 없다. 프로브는 읽기 전용이고 자격증명을 읽지 않는다. 수렴(재배포)은 릴리스 판정이
  소유 배포기를 돌려 한다(릴리스 승인 경로).
- `skills/mail/vendor/**`는 바이트 그대로다(변경 0줄).

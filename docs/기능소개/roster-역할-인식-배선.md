# Roster 역할 인식 배선 (W-F2-D)

## 무엇을

검증된 roster의 역할 정보를 실제 소비자에 연결했다. 선택적 `update_channel`은
리컨실러의 소프트웨어 원격을 바꾸고, `admin.publisher_principal`은 관리형 스킬
서명자를 정하며, healthcheck는 `group_id`와 roster 멤버 수를 정보로 보여준다.

외부효과 승인 의미는 바뀌지 않는다. roster 관리자나 멤버는 다른 설치의 승인을 대신할
수 없고, 각 설치는 계속 자기 `owner_id` 하나만 승인 주체로 인정한다.

## 왜

W-F2-A에서 roster schema를 만들었지만 소비자가 읽지 않으면 역할 정보는 문서에만
머문다. 반대로 roster를 승인 게이트에 연결하면 연합 관리자가 팀원 권한을 얻는 D1 위반이
된다. 이 기능은 업데이트·서명 신뢰·관측에만 roster를 사용하고 승인 경계는 의도적으로
roster-free로 남긴다.

## 사용 시나리오

### 기본 upstream

1. 관리자가 roster에서 `update_channel`을 생략한다.
2. 팀원 리컨실러는 이전과 똑같이 checkout의 `origin`에서 서명 릴리스를 검증한다.
3. healthcheck에는 `ROSTER-IDENTITY`와 group ID·멤버 수가 정보성으로 표시된다.

### 명시적 fork

1. 관리자가 별도 소프트웨어 채널을 운영하기로 합의하고 roster에 Git URL을 적는다.
2. 리컨실러는 그 URL의 서명 태그를 검증하고 같은 채널에서 SHA-pinned snapshot을 만든다.
3. URL을 checkout의 영구 `origin`으로 덮어쓰지 않으며, 필드를 다시 생략하면 upstream
   경로로 돌아간다.

### 실패·거부

- roster가 없거나 깨졌거나 신원 보고를 읽지 못하면 healthcheck는
  `ROSTER-UNAVAILABLE`을 정보로 남길 뿐 전체 healthcheck를 실패시키지 않는다.
- 관리형 스킬은 usable roster principal이 없으면 기존 W-F3-A 경계에서 fail-closed한다.
- roster에 어떤 역할이 있어도 외부효과 실행에는 로컬 owner의 기존 승인이 필요하다.

## 관련

- 계획: `.omo/plans/public-release.md` W-F2-D
- 리컨실러: `automation/deploy_reconcile_cli.py`, `automation/deploy_update_channel.py`,
  `automation/update_trust.py`, `automation/converge_origin_main.sh`
- 역할·관측: `automation/group_roster/`, `automation/healthcheck.sh`,
  `automation/healthcheck_roster_probe.sh`
- 기존 principal 신뢰: `automation/managed_sync/cli.py`, `automation/managed_sync/verify.py`
- 승인 경계 회귀 방지: `tests/unit/test_external_effect_roster_boundary.py`

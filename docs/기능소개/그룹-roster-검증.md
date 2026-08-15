# 그룹 roster 데이터 모델·검증 (W-F2-A)

## 무엇을

연구그룹 하나의 관리자·구성원·선택적 업데이트 채널을 `schema: 1` YAML로 표현하고,
`python3 -m automation.group_roster validate <path>`로 소비 전에 엄격히 검증한다.
W-F2-A는 순수 데이터 모델·파서·검증기를 제공했고, 후속 W-F2-C가 이 검증기를
[서명 roster 자동 배포](서명-roster-배포.md)의 설치 전 경계로 연결했다. healthcheck 역할 배선은 여전히 후속 wave다.

## 왜

연합형 그룹은 중앙 서버가 아니라 관리자가 소유한 스킬 저장소·서명키 한 쌍·roster 세 가지로만 존재한다.
따라서 roster가 모호하거나 일부 필드를 조용히 버리면 잘못된 발행자나 구성원을 신뢰하는 공급망 문제가 된다.
v1은 설치당 그룹을 정확히 하나만 허용하고, 모든 미지 필드·중복 ID·잘못된 principal·공개키를 fail-closed로 거부한다.

## 사용 시나리오

### 정상 경로

1. `configs/roster.example.yaml`을 `~/.hermes/roster.yaml`로 복사하고 합성 예시값을 실제 그룹 값으로 바꾼다.
2. 관리자 한 명과 멤버 목록(`active` 또는 `removed`)을 기록한다.
3. `python3 -m automation.group_roster validate ~/.hermes/roster.yaml`을 실행한다.
4. `ROSTER-VALID`와 exit 0이 나오면 후속 wave가 사용할 수 있는 typed roster다. `update_channel`을 생략하면 upstream을 직접 따른다.

### 실패·거부 경로

- `admin`이 없거나 목록으로 둘 이상을 넣으면 exit 2로 거부한다.
- 관리자와 멤버의 Discord ID가 겹치거나, 미지 status/필드가 있거나, principal이
  `publisher-<name>@autophagy` 형식이 아니면 거부한다.
- 서명 공개키가 완전한 OpenSSH `ssh-ed25519` 공개키가 아니거나 YAML 키가 중복돼도 거부한다.
  오류는 `ROSTER-INVALID: <구체적 원인>`으로 출력되며 traceback은 노출하지 않는다.

## 관련

- 모델·파서·검증: `automation/group_roster/schema.py`, `parser.py`, `validator.py`
- CLI: `automation/group_roster/cli.py`, `automation/group_roster/__main__.py`
- 추적 시드 / 런타임: `configs/roster.example.yaml` / `~/.hermes/roster.yaml`
- 테스트: `tests/unit/test_group_roster.py`, `tests/unit/test_group_roster_cli.py`
- 계획: `.omo/plans/public-release.md`의 W-F2-A
- 서명 배포 소비자: W-F2-C [소개](서명-roster-배포.md)
- 남은 healthcheck·역할 배선은 W-F2-D가 소유한다.

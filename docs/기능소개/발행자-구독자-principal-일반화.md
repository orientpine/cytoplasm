# 발행자·구독자 principal 일반화 (W-F3-A)

## 무엇을

관리형 스킬(`managed-*`) 발행·검증에 박혀 있던 `cha` / `publisher-cha@autophagy` 상수를 없애고,
**발행자는 자기 로컬 config**에서, **구독자는 그룹 roster**에서 principal을 해석하게 했다.
기본값은 어느 쪽에도 없다 — 설정이 없으면 발행도 검증도 거부된다(fail-closed).

## 왜

기존에는 `managed_sync/verify.py`가 `publisher-cha@autophagy`를 상수로 들고 서명 주체를 exact-match했고,
`publish_cli.py`는 매니페스트 `publisher`를 `"cha"`로, 서명 태그 이메일을 `publisher-cha@autophagy`로 박아
넣었다. 즉 **cha가 아닌 사람은 이 채널로 스킬을 발행할 수도, 자기 그룹 관리자의 릴리스를 검증할 수도 없었다.**
공개 배포에서 이 상수는 곧 "모든 설치가 cha를 신뢰한다"는 뜻이므로, 신뢰 근원을 설치별 설정으로 옮겼다.

## 사용 시나리오

### 그룹 관리자(발행자)

1. `configs/managed-publisher.default.json`을 `~/.hermes/managed-skills/publisher.json`으로 복사하고
   자기 값으로 채운다(`MANAGED_PUBLISHER_CONFIG`로 경로 override 가능).

   ```json
   { "publisher": "example-admin", "publisher_principal": "publisher-example-admin@autophagy" }
   ```

2. 평소대로 발행한다. 매니페스트 `publisher`와 SSH 서명 태그의 `user.email`이 모두 이 config에서 나온다.

   ```
   PUBLISHED skill=managed-hello-autophagy tag=managed-hello-autophagy/v1
   ```

3. **실패 경로**: config가 없거나 `publisher_principal`이 `publisher-<slug>@autophagy` 형식이 아니면
   서브프로세스를 하나도 띄우지 않고 멈춘다.

   ```
   PUBLISH-BLOCK: publisher config not found: /home/<user>/.hermes/managed-skills/publisher.json
   ```

### 팀원(구독자)

1. 관리자에게 **대역외로** 발행자 principal과 공개키 지문을 확인받아
   `/etc/autophagy/managed-skills-allowed-signers`에 등록하고, 같은 principal을
   `~/.hermes/roster.yaml`의 `admin.publisher_principal`에 적는다(`AUTOPHAGY_ROSTER`로 override 가능).
2. `python3 -m automation.managed_sync sync`가 그 principal로 서명된 릴리스만 격리소에 넣는다.
3. **실패 경로**: roster가 없거나 principal이 형식에 맞지 않으면 아무도 신뢰하지 않고 즉시 멈춘다.

   ```
   CONFIG-ERROR: cannot read roster file /home/<user>/.hermes/roster.yaml: ...
   ```

   다른 principal이 서명한 태그는 기존 분류 그대로 거부된다 — `SYNC-FAILED ... reason=WRONG-PRINCIPAL`.

## 관련

- 발행자: `automation/managed_skills/publisher_config.py`, `automation/managed_skills/publish_cli.py`,
  시드 `configs/managed-publisher.default.json`
- 구독자: `automation/managed_sync/cli.py`(roster 해석), `automation/managed_sync/verify.py`(주체 대조)
- 공유 형식: `automation/managed_skills/principal.py` — roster 스키마 v1(`automation/group_roster/validator.py`)과
  권한 있는 설치기(`automation/skill_store.py`)의 형식에 테스트로 고정된다.
- 승인·게이트는 변경 없음. 발행 승인(오너 ✅)·활성화 승인(구독자 ✅)은 그대로다.
- **운영 전제(config-before-code)**: 이 코드가 프로덕션에 반영되기 **전에** 발행 워크스테이션에
  `~/.hermes/managed-skills/publisher.json`을, 각 구독 노드에 `~/.hermes/roster.yaml`을 배치해야 한다.
  두 파일 모두 기존 코드가 읽지 않으므로 미리 두어도 안전하다.

# Peer attestation 파일 서명 모드 (W-F2.5-A)

## 무엇을

스킬 배포의 독립 peer 검증 결과를 Discord 두 번째 봇 답글 대신 peer 계정의 SSH 서명으로
증명할 수 있다. 신규 설치는 봇 하나만 운영하면서도 owner 승인과 peer 검증을 모두 통과해야
MOUNT되는 기존 4단계 게이트를 유지한다.

## 왜

기존 Discord 모드는 agent bot과 peer bot 두 개가 필요했다. 봇을 하나로 줄이면서 단순히 peer
답글 검사를 제거하면, 프롬프트 인젝션된 agent가 자기 리뷰를 스스로 통과시킬 수 있다. 파일
서명 모드는 agent가 읽을 수만 있는 root-owned 공개키와 peer만 읽는 private key로 발행 주체를
분리하고, 요청 nonce·스킬 digest·승인 채널·메시지·시각을 함께 서명해 같은 보안 속성을 보존한다.

## 사용 시나리오

### 정상 흐름

1. 신규 설치기가 peer 홈에 서명 keypair를 만들고 공개키만 `/etc/autophagy/`에 0644로 게시한다.
2. 스킬 배포가 peer 샌드박스 리뷰를 실행한다. peer는 stdout에 단일 signed attestation을 내보낸다.
3. gate가 고정 namespace·공개키·요청 바인딩·30분 TTL을 검증하고 승인 메시지의 peer verdict를
   fingerprint가 포함된 PASS로 갱신한다.
4. cha가 같은 메시지에 ✅를 누르면 MOUNT한다. signed blob은 파일이나 공유 큐에 남기지 않는다.

### 실패·거부 흐름

모드가 비어 있거나, 키·namespace·필드·nonce·digest가 다르거나, attestation이 요청보다 이르거나
TTL을 1초라도 넘기거나, 공개키 또는 부모가 agent-writable이면 gate가 fail-closed로 거부한다.
기존 `peer_attest_mode = "discord"` 설치는 종전 bot-id·reply-reference 검증을 그대로 사용한다.

## 관련

- 계약·검증: `automation/peer_attestation.py`, `automation/peer_signed_attestation.py`
- producer/runtime: `automation/peer_attest.py`, `automation/peer_attest_runtime.py`
- gate·courier: `automation/skill_gate.py`, `automation/skill_gate_refresh.py`,
  `automation/deploy-skill.sh`
- 설치기: `automation/install/plan.py`, `automation/install/peer_attest_key.py`,
  `automation/install/apply.py`, `automation/install/state.py`
- 설계/작업: `.omo/plans/public-release.md` §W-F2.5-A

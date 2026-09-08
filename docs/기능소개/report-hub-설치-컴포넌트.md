# report-hub 설치 컴포넌트

## 무엇을

보고 허브를 주 노드 설치기의 선택 컴포넌트로 제공한다. `report-hub`·`full` 프로필은
자동 선택하고, `core`·`rag`와 프로필 없는 기본 설치는 선택하지 않는다.
`--with-component report-hub`로 명시적으로 추가할 수 있으며 중복 지정은 한 번으로
합쳐진다. `managed-sync`의 기존 root system 유닛·타이머 동작은 바뀌지 않는다.

설치 대상은 ops 홈의 사용자 유닛 2개(`0644`), `report-hub/` 작업 디렉터리(`0750`),
`automation -> <release_current>/automation` 링크, 빈 주석 `hub.env`(`0600`)다.
모두 ops 소유다. 유닛은 ops 사용자 매니저에서 활성화하며, 다음 실행은 실제 파일·링크·
`systemctl --user is-enabled` 상태를 읽어 이미 수렴한 변경을 생략한다.
`hub.env`는 최초 한 번만 생성하고 운영자가 채운 값은 덮어쓰지 않는다.

## 왜

헬스체크가 요구하는 보고 허브 유닛을 설치하지 않던 프로필과 실제 배치 사이의 공백을
닫는다. 기존 사용자 유닛을 system 유닛으로 바꾸는 대신 사용자 범위를 명시해 수동
배포와 같은 원본을 쓴다. 유닛의 작업 경로는 유지하고 링크로 릴리스 코드에 연결하므로
별도 코드 사본의 갱신 규칙도 만들지 않는다.

RAG는 다른 경계다. `automation/rag_stack/deploy.sh`가 SSH로 **별도 `RAG_NODE`**에
배포한다. 주 노드만 설치하는 이 설치기에 RAG 컴포넌트는 없으며, `rag`·`full` 프로필의
RAG 항목은 원격 감시 선언이다.

## 사용 시나리오

### 정상 흐름

1. [설치 가이드](../guide/install.md#프로필과-헬스체크-선언)의 dry-run과 실제 설치에서
   같은 `--profile report-hub`를 쓴다. 다른 전제 실패는 기존처럼 첫 FAIL에서 멈춘다.
2. 계획에서 ops 사용자 유닛·작업 디렉터리·링크·`hub.env`·활성화 액션을 확인한다.
3. ops로 `hub.env`의 서버 ID, DB·격리 로그·비공개 peers 경로,
   `REPORT_HUB_DASHBOARD_USER`, `REPORT_HUB_DASHBOARD_PASSWORD_SHA256`을 채운다.
   콜렉터의 `DISCORD_BOT_TOKEN`은 ops `.env.secrets`에 둘 수 있다. 값은 로그나 저장소에
   남기지 않는다. 선택 설정의 주석을 유지하면 런타임 기본값을 쓴다.
4. 최초 릴리스 수렴과 비공개 설정 준비 후 두 사용자 유닛을 재시작한다. 헬스체크는
   두 유닛 active와 포트 8800의 무인증 HTTP 401을 요구한다. 재실행은 자격증명을 보존한다.

`--profile core --with-component report-hub`는 파일 설치만 추가하고 감시 선언은 `core`로
유지한다. 파일 설치와 감시를 함께 선택하려면 `report-hub` 프로필을 권한다.

### 거부·준비 미완료 흐름

- `--with-component report-hbu` 오타는 알려진 `managed-sync`, `report-hub`를 제시하고
  CLI rc=2로 거부한다. 조용히 컴포넌트를 빼고 진행하지 않는다.
- 인증 미설정 대시보드는 기동을 거부한다. 활성화된 유닛이라고 익명 서비스를 열지 않는다.
- 최초 수렴 전에는 릴리스 링크 대상 코드가 없어 재시작을 반복할 수 있다.
  `enabled`와 `active`는 다른 판정이며, 설치기의 최종 헬스체크 성공을 뜻하지 않는다.
- 기존 수동 배포의 실제 `automation/` 디렉터리를 링크로 교체하려 하면 재귀 삭제하지 않고
  거부한다. 운영자가 먼저 백업·이관하고 재실행한다.

## 관련

- 구현: `automation/install/components.py`, `component_assets.py`, `profiles.py`,
  `plan.py`, `state.py`, `apply.py`.
- 설정·기존 수동 배포: [보고 허브 운영 가이드](../guide/report-hub.md).
- 회귀: `tests/unit/test_install_report_hub_component.py`.
- 실측: [11-report-hub-component-container.txt](../qa/INSTALL-TUI/11-report-hub-component-container.txt).
  특권 systemd 컨테이너의 실제 ops 매니저에서 두 유닛 `enabled`, 소유권·모드·링크,
  실제 상태 기반 재실행 변경 0개와 오타 거부를 확인했다. 토큰·인증정보는 넣지 않았으며
  active·HTTP 401·Hermes/Discord 이후 전체 설치 완주는 검증 범위 밖이다.

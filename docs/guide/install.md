# 설치 가이드 (제3자 단일 노드)

이 문서는 **자기 노드에 이 시스템을 처음 설치하는 사람**을 위한 것이다. 위에서
아래로 순서대로 따라가면 끝난다. 설치 절차의 단일 진실은 이 문서이며, 다른 문서는
이 문서를 링크할 뿐 같은 명령을 다시 적지 않는다.

읽기 전에 알아둘 것 하나: **설치기는 멱등이다.** 중간에 막히면 원인을 고치고
같은 명령을 다시 실행하면 된다. 이미 원하는 상태인 항목은 계획에서 빠지고, 남은
것만 실행된다. 그래서 "한 번에 완주"를 목표로 삼을 필요가 없다.

---

## 0. 전제

전제는 이 문서가 소유하지 않는다. 먼저 읽고 준비한다:

**[docs/guide/third-party-runtime-prereqs.md](third-party-runtime-prereqs.md)**

거기서 준비하는 것은 5종이다 — Discord 앱·봇·서버·채널 3종, 모델 provider(LiteLLM
호환 `/v1`), Hermes 게이트웨이(검증된 버전 핀), 업데이트 신뢰키 번들, 시크릿.
그 문서 §6의 체크리스트 9항을 통과한 뒤에 이 문서로 돌아온다.

이 문서가 추가로 요구하는 것:

| 항목 | 왜 |
|---|---|
| Linux + **systemd** 호스트, root 권한 | 리컨실러·스모크·공급망 워처가 systemd 타이머다. 계정 linger도 systemd 기능이다 |
| `git`, `curl`, `ssh-keygen`, `useradd`/`groupadd`/`usermod`, `visudo`, `tar` | 설치기가 직접 호출한다. 대부분의 배포판 기본 이미지에 있지만 slim 이미지에는 없다 |
| origin 호스트의 SSH 호스트키가 ops 계정 `known_hosts`에 있을 것 | clone은 `StrictHostKeyChecking=yes`로 수행된다(§4 참조) |
| 각 서비스 계정의 Hermes 게이트웨이 | **설치기는 Hermes를 설치하지 않는다.** 외부 전제이며, 없으면 fail-closed로 멈추고 안내한다 |

컨테이너에서는 완주할 수 없다 — systemd가 없기 때문이다. 실측은
`docs/qa/P0-5/03-systemd-boundary.txt`에 있다.

---

## 1. 소스 받기

설치기는 체크아웃 안에서 실행된다(자기 위치 기준으로 저장소 루트를 찾는다).

```bash
git clone <공개 저장소 URL> ~/autophagy-agents
cd ~/autophagy-agents
```

릴리스 번들에는 **업데이트 신뢰키 공개키**가 동봉되어 있다. 그 경로를 기억해 둔다
(아래에서 `<bundle>/update-trust.pub`으로 부른다).

---

## 2. 신뢰키 지문을 먼저 대조한다

이 단계가 이 설치에서 보안적으로 가장 중요한 사람 개입이다. 키는 설치기와 함께
오므로, 그 키가 진짜 유지보수자의 것인지는 **설치기가 아닌 경로**로 확인해야 한다.

```bash
python3 automation/install/trust_key_bootstrap.py fingerprint --key <bundle>/update-trust.pub
```

출력은 `SHA256:...` 한 줄이다. 이 값을 **공개 저장소 README와 릴리스 노트에 공지된
지문**과 눈으로 대조한다. 다르면 여기서 멈추고 유지보수자에게 확인한다.

쓰기 없이 무엇이 설치될지 먼저 보고 싶다면:

```bash
python3 automation/install/trust_key_bootstrap.py install --key <bundle>/update-trust.pub --dry-run
```

공지 지문을 인자로 넘기면 대조를 기계가 한다. 불일치면 **아무것도 쓰지 않고** rc 1이다:

```bash
python3 automation/install/trust_key_bootstrap.py install \
    --key <bundle>/update-trust.pub --dry-run \
    --expect-fingerprint 'SHA256:0imCAjLaEFCB8oNX05/7mHFQAZsL722KIEZsVD5yvrA'
```

> 신뢰키는 두 개가 아니라 두 **종류**다. 여기서 다루는 것은 업스트림 코드 릴리스를
> 검증하는 **업데이트 신뢰키**이고, 그룹 관리형 스킬을 검증하는 **그룹 서명키**는
> 다른 파일(`/etc/autophagy/managed-skills-allowed-signers`)에 들어간다. 설치기는
> 한쪽을 다른 쪽 경로에 쓰는 것을 거부한다. 자세한 구분은 전제 문서 §4.2.

---

## 3. 노드 config 작성

```bash
cp configs/node.example.toml /tmp/node.toml
$EDITOR /tmp/node.toml
```

`configs/node.example.toml`은 **cha의 프로덕션 값을 그대로 적어 둔 worked example**이다.
복사한 뒤 최소한 다음을 자기 값으로 바꾼다:

| 필드 | 바꿔야 하는 이유 |
|---|---|
| `origin_url` | 자기가 업데이트를 받아올 공개 저장소 |
| `require_signed_updates` | **생략하거나 `true`.** 예제의 `false`는 cha 노드의 전환기 opt-out이다. `false`는 변조 가능한 `main`을 root 입력으로 신뢰한다는 뜻이다 |
| `peer_attest_mode` | 신규 설치는 `signed`. (설치기는 이 값을 `signed`로 강제한다) |
| `primary_node_name` / `rag_node_name` | 자기 호스트 이름 |
| `deploy_ssh_host` | 워크스테이션에서 원격 배포하지 않는다면 빈 문자열 |
| `operator_account` | 자기 로그인 계정 |

경로·계정 필드는 기본값 그대로 두는 것을 권한다. 단, `release_store`와
`release_current`의 basename은 바꿀 수 없다 — 다른 조합을 주면 설치기가
`InstallAssetError`로 거부한다(조용히 잘못 설치하지 않는다).

작성한 파일은 설치기가 각 계정 홈의 `~/.hermes/node.toml`(0600)로 배포한다.
agent·peer 계정에는 각 게이트웨이 user unit의
`~/.config/systemd/user/<gateway-unit>.d/30-command-sync.conf`(0600)도 배포한다. 이
drop-in은 `DISCORD_COMMAND_SYNC_POLICY=bulk`를 고정해 게이트웨이 재시동 때 Discord
slash command를 건별 mutation하지 않고 한 번에 동기화한다. 설치기가 명시적으로
소유하는 desired state이므로 내용·소유권·모드가 달라지면 다음 실행에서 수렴하고,
이미 같으면 계획에서 빠진다.

`--config`를 생략하면 예제와 동일한 기본값이 쓰이므로, 제3자 설치는 **항상
`--config`를 명시한다.**

---

## 4. 계획을 먼저 본다 (`--dry-run`)

```bash
python3 -m automation.install \
    --config /tmp/node.toml \
    --update-trust-key <bundle>/update-trust.pub \
    --expect-update-trust-fingerprint 'SHA256:0imCAjLaEFCB8oNX05/7mHFQAZsL722KIEZsVD5yvrA' \
    --dry-run
```

- root 권한이 필요 없다.
- 아무것도 쓰지 않는다.
- 실제 실행이 무엇을 할지 번호가 붙은 액션 목록으로 전부 보여준다.
- 성공하면 **rc 0**이다.

출력은 이렇게 생겼다(깨끗한 컨테이너 실측 전문:
`docs/qa/P0-5/01-dry-run-clean-container.txt`):

```
INSTALL PLAN
01. account agent home=/home/agent
...
09. directory /srv/autophagy-private/locks owner=ops:autophagy mode=2770
...
25. peer-attest-key private=/home/peer/.ssh/peer_attest_ed25519 public=/etc/autophagy/peer-attest-peer.pub owner=peer ... private-content=never-printed
26. check hermes-gateway
27. check discord-readiness
28. deploy-key /home/ops/.ssh/id_ed25519 comment=... private=never-printed
29. check deploy-key-registration
30. gitleaks version=8.30.1
31. repository /srv/autophagy-agents origin=<your-origin>
...
33. file /etc/autophagy/update-allowed-signers owner=root:root mode=0644 sha256=...
...
37. file /home/agent/.config/systemd/user/hermes-gateway.service.d/30-command-sync.conf owner=agent:agent mode=0600 sha256=...
38. file /home/peer/.config/systemd/user/hermes-gateway.service.d/30-command-sync.conf owner=peer:peer mode=0600 sha256=...
...
58. timer autophagy-deploy-reconcile.timer enabled
61. check update-trust
62. check healthcheck
```

읽는 법:

- **`account`/`group`/`directory`/`file`** — 만들어질 계정·디렉터리·파일과 그 소유·모드.
  파일은 내용 sha256으로 비교하므로, 이미 같은 내용이면 다음 실행의 계획에서 사라진다.
- **`deploy-key` / `peer-attest-key`** — 키를 **생성**한다. 개인키는 절대 출력되지 않는다.
- **`check`** — 쓰기가 아니라 판정이다. `--dry-run`에서는 실행되지 않고 자리만 보인다.
- **`timer`** — systemd 타이머 활성화. 여기가 자동 업데이트가 켜지는 지점이다.

지문이 공지값과 다르면 계획을 출력하기도 전에 멈춘다:

```
[FAIL] update-trust: bundled fingerprint SHA256:... does not match the published value
--- NOT-INSTALLED: 1건 중 실패 1 / 경고 0
```

---

## 5. Discord 전제를 먼저 확인한다 (선택이지만 권장)

설치기도 같은 검사를 `discord-readiness` 체크로 수행하지만, 미리 돌리면 설치를
시작하기 전에 고칠 수 있다. 이 스크립트는 **GET만** 한다 — 게시·수정·삭제 없음.

```bash
set -a; . ~/.env.secrets; set +a          # DISCORD_BOT_TOKEN을 환경에 올린다
python3 automation/install/discord_check.py --config ~/.hermes/interop/config.json
```

토큰은 환경변수에서만 읽고 출력에 절대 포함되지 않는다. 실패는 항목을 지목한다:

```
[FAIL] token: INVALID-TOKEN: 401 — 토큰이 거부됐다. …
[FAIL] intent: MESSAGE-CONTENT-INTENT-OFF: Developer Portal → Bot → … 
[FAIL] permissions[<guild>]: MISSING-PERMISSION: …
[FAIL] channel[approvals]: MISSING-CHANNEL-ID: …
```

토큰이 비어 있으면 rc 2로 사용법 오류를 낸다. 채널 3종의 의미는 전제 문서 §1.5에 있다.

### 5.1 승인 표면 필수 키: `agent_chat_channel_id`

정책 v7(첫 태그 v1.0.71)부터 소유자 승인은 `#agent-chat` 아래의 요청별 스레드에 게시된다. 이 채널 ID는 `~/.hermes/interop/config.json`의 `agent_chat_channel_id`에 둔다.

키가 없으면 승인은 fail-closed로 게시를 거부하며, 오류는 정확히 다음과 같다.

```
ApprovalSurfaceError: agent_chat_channel_id is not configured in the interop config
```

이 경우 승인 게시만 멈추고 다른 기능은 계속 동작한다. `agent_chat_channel_id`에 채널 ID를 설정하면 재시작할 필요 없이 다음 승인 게시부터 정상 동작한다.

---

## 6. 실제 설치

```bash
sudo python3 -m automation.install \
    --config /tmp/node.toml \
    --update-trust-key <bundle>/update-trust.pub \
    --expect-update-trust-fingerprint 'SHA256:0imCAjLaEFCB8oNX05/7mHFQAZsL722KIEZsVD5yvrA'
```

**`sudo`는 환경변수를 지운다.** §5에서 `DISCORD_BOT_TOKEN`을 올려두었더라도 위 명령에는
전달되지 않으므로, 설치기의 `discord-readiness` 체크가 `discord_check.py rc=2`로 실패한다
— 토큰이 틀린 것이 아니라 아예 도달하지 않은 것이다. 토큰을 넘기려면 `--preserve-env`를
쓴다(`quickstart.sh`가 하는 것과 같다):

```bash
sudo --preserve-env=DISCORD_BOT_TOKEN python3 -m automation.install \
    --config /tmp/node.toml \
    --update-trust-key <bundle>/update-trust.pub \
    --expect-update-trust-fingerprint 'SHA256:0imCAjLaEFCB8oNX05/7mHFQAZsL722KIEZsVD5yvrA'
```

`sudo`가 `--preserve-env`를 거부하는 배포판이면 §5를 미리 통과시켜 두고 이 체크의
실패를 감수해도 된다 — Discord 전제는 설치 자체의 전제가 아니다.

root가 아니면 계획만 출력하고 거부한다:

```
[FAIL] root: run the installer as root; dry-run needs no privilege
```

실행은 계획 순서대로 진행하며 **첫 FAIL에서 멈춘다.** 이건 의도된 동작이다 —
전제가 깨진 채로 뒤 단계를 밀어붙이지 않는다. 고치고 같은 명령을 다시 실행하면
이미 끝난 항목은 건너뛴다.

중간에 사람이 개입해야 하는 지점이 정확히 두 곳 있다.

### 6.1 Hermes 게이트웨이 (`check hermes-gateway`)

계정별로 `hermes --version`과 게이트웨이 유닛 활성 여부를 확인한다. 없으면:

```
[FAIL] hermes-gateway: Hermes is an external prerequisite for <account>; install it in
       <home>, install/start <unit>, then rerun. The installer never installs Hermes.
```

전제 문서 §3의 버전 핀에 맞춰 각 계정에 설치·기동한 뒤 다시 실행한다.

### 6.2 배포 키 등록 (`check deploy-key-registration`)

설치기가 ops 계정용 ed25519 키쌍을 만들고 **공개키를 출력한다**(개인키는 출력되지
않는다):

```
[PASS] deploy-key-registration: register this PUBLIC key read-only, then rerun if clone fails: ssh-ed25519 AAAA... ops@<node>-autophagy-deploy
```

이 공개키를 소스 저장소에 **read-only deploy key로** 등록한다. 등록 전에는 다음
액션인 `repository` clone이 인증 실패로 멈춘다 — 그때 등록하고 같은 명령을 다시
실행하면 된다. clone은 `StrictHostKeyChecking=yes`로 수행되므로, ops 계정의
`known_hosts`에 origin 호스트키가 먼저 있어야 한다:

```bash
sudo runuser -u ops -- ssh-keyscan github.com >> /home/ops/.ssh/known_hosts   # 지문을 반드시 대조할 것
```

이 단계를 빼면 설치기가 clone 전에 멈추며 `KNOWN-HOSTS-MISSING`으로 호스트명과 위
명령을 그대로 알려준다 — 예전처럼 ssh 내부에서 이유 없이 죽지 않는다. **설치기가
호스트키를 대신 넣어주지는 않는다** — 어떤 호스트키가 진짜인지는 지문을 대역외로
대조한 사람만 정할 수 있고, 그 판단을 설치기가 가로채면 이 설정 자체가 무의미해진다.

> 그룹에 가입하는 경우 이 공개키가 3단계 핸드셰이크의 ①단계 제출물이다. 가입 절차
> 자체는 이 문서가 아니라 그룹 관리자/팀원 매뉴얼이 소유한다.

---

## 7. 끝났는지 어떻게 아는가

설치기의 마지막 두 액션이 종료 게이트다.

### `check update-trust`

`/etc/autophagy/update-allowed-signers`의 **소유·모드·지문**을 다시 읽어 판정한다.

```
[PASS] trust-key.file: /etc/autophagy/update-allowed-signers root:root 0644 정규 파일
[PASS] trust-key.fingerprint: 공지 지문과 일치 — SHA256:...
```

`--expect-update-trust-fingerprint`를 주지 않았다면 두 번째 줄은 PASS가 아니라
**WARN**이 되고 설치된 지문을 출력한다. 그 값을 공지값과 직접 대조하는 것은 사람의 몫이다.

언제든 다시 확인할 수 있다:

```bash
python3 automation/install/trust_key_bootstrap.py verify --expect-fingerprint 'SHA256:0imCAjLaEFCB8oNX05/7mHFQAZsL722KIEZsVD5yvrA'
```

파일이 없으면 이렇게 나오고, 그 상태에서는 리컨실러가 **어떤 릴리스도 적용하지 않는다**:

```
[FAIL] trust-key.file: TRUST-KEY-MISSING: /etc/autophagy/update-allowed-signers가 없다. …
```

### `check healthcheck`

ops 계정으로 `automation/healthcheck.sh`를 실행한다. 이 스크립트는 **읽기 전용**이며,
정의된 프로브를 전부 돌고 마지막 줄에 판정을 남긴다.

- 전부 통과: 로그 마지막이 `ALL_HEALTHY`, rc 0 →
  `[PASS] healthcheck: healthcheck.sh ALL_HEALTHY`
- 하나라도 실패: 실패한 체크마다 `FAIL <이름>`을 남기고 마지막이 `HEALTHCHECK_FAILED`,
  rc 1 → `[FAIL] healthcheck: healthcheck.sh rc=1`
- 원격 프로브가 **전부** 실패하면 개별 티켓 대신 `INFRA_FAILURE` 한 건으로 묶는다.
  이 경우 서비스 N개가 아니라 공유 SSH 경로 하나를 의심한다.

직접 돌려볼 수 있다(쓰기 없음):

```bash
sudo runuser -u ops -- bash /srv/autophagy-agents/automation/healthcheck.sh
```

### 최종 판정 줄

설치기는 마지막에 한 줄 요약을 낸다.

```
--- INSTALLED: N건 중 실패 0 / 경고 0
```

`INSTALLED` + 실패 0이면 rc 0이다. 실패가 하나라도 있으면 `NOT-INSTALLED` + rc 1이다.
**경고(WARN)는 rc를 바꾸지 않는다** — 대표적인 것이 위의 "지문을 직접 대조하라"이며,
그건 기계가 대신 판단할 수 없는 항목이기 때문이다.

### 자동 업데이트가 켜졌는지

```bash
systemctl list-timers | grep autophagy
```

`autophagy-deploy-reconcile.timer`가 보이면 리컨실러가 살아 있다. 그 뒤로는 업스트림에
**서명된 릴리스 태그**가 올라올 때마다 노드가 스스로 수렴하고, 실패하면 스스로
되돌린다. 서명이 없는 head는 적용되지 않으며 그 사유가 healthcheck에 나타난다.

### 관리형 스킬 격리와 소유자 알림이 준비됐는지

`managed-sync`를 opt-in으로 설치했다면 설치 직후 다음 두 읽기 전용 검사를 추가로 한다.

```bash
sudo runuser -u agent -- bash -lc 'cd /srv/autophagy-agent-current && PYTHONPATH=. python3 -m automation.managed_sync status'
sudo runuser -u agent -- bash -lc 'set -a; source "$HOME/.env.secrets"; set +a; python3 -c '\''import os; required=("DISCORD_BOT_TOKEN","AUTOPHAGY_OWNER_ID"); missing=[name for name in required if not os.environ.get(name)]; print("OWNER-NOTICE-CONFIGURED" if not missing else "OWNER-NOTICE-MISSING:"+",".join(missing)); raise SystemExit(bool(missing))'\'''
```

첫 명령은 검증된 릴리스가 quarantine에 몇 건 남았는지 `pending=<N>`으로 보여준다. 둘째
명령은 값 자체를 출력하지 않고 소유자 DM 통지에 필요한 두 이름의 설정 여부만 판정한다.
실제 통지 경로는 `automation/managed_sync/notify.py`가 아니라
`automation/owner_notice.py`이며, `managed_sync_watch.py`가 격리 성공 뒤 이 공용 경로를
best-effort로 호출한다. `pending`이 1 이상인데 둘째 명령이 실패하면 격리는 정상이어도
소유자가 새 릴리스를 모를 수 있으므로 설치 완료로 판정하지 않고 자격증명을 보완한다.

---

## 8. 자주 막히는 곳

| 증상 | 원인 | 조치 |
|---|---|---|
| `INSTALL-BLOCK: <메시지>` | config 파싱·자산 렌더·키 파싱 실패 | 메시지가 지목한 파일을 고친다. 아무것도 쓰이지 않았다 |
| `[FAIL] root: …` | `sudo` 없이 실제 실행 | `sudo`를 붙이거나 `--dry-run`을 쓴다 |
| `[FAIL] EnsureAccount: … FileNotFoundError` | `loginctl`/`useradd` 부재 = systemd 없는 환경 | 실제 systemd 호스트에서 실행한다 |
| `[FAIL] hermes-gateway: …` | 계정별 Hermes 미설치·미기동 | 전제 문서 §3대로 설치 후 재실행 |
| `[FAIL] discord-readiness: discord_check.py rc=1` | 토큰·인텐트·권한·채널 중 하나 | §5를 단독 실행해 어느 항목인지 본다 |
| `repository` 액션에서 멈춤 | deploy key 미등록 또는 `known_hosts` 부재 | §6.2 |
| `TRUST-KEY-FINGERPRINT-MISMATCH` | 번들 키 ≠ 공지 지문 | **진행하지 않는다.** 유지보수자에게 확인 |

---

## 9. 이 문서가 실제로 검증된 범위

정직하게 적는다. 근거는 `docs/qa/P0-5/`에 있다.

- **검증됨**: `--dry-run` 전 구간이 깨끗한 컨테이너에서 rc 0. 전제 미충족 6종이 각각
  이름을 지목해 보고된다. 비-root 실행 거부. 신뢰키 지문 대조·불일치 거부.
- **아직 실호스트에서 검증되지 않음**: 실제 apply → 타이머 활성 → `healthcheck.sh`
  전부 PASS → 서명 릴리스 push 후 `current` 전진. 컨테이너에는 systemd가 없고,
  Hermes는 설치기가 설치하지 않는 외부 전제라 컨테이너에서는 구조적으로 도달할 수
  없다. 이 구간은 실제 Linux+systemd 호스트를 가진 운영자가 처음 완주할 때 닫힌다.

## 관련

- 전제: [third-party-runtime-prereqs.md](third-party-runtime-prereqs.md)
- 편의 래퍼(위 §4→§6→§7 순서를 대신 지켜준다. 절차는 이 문서가 그대로 소유한다):
  [quickstart-install.md](quickstart-install.md) · [`automation/install/quickstart.sh`](../../automation/install/quickstart.sh)
- 환경변수 템플릿: [`configs/env.example`](../../configs/env.example)
- 노드 config 예제: [`configs/node.example.toml`](../../configs/node.example.toml)
- 검증 증적: [`docs/qa/P0-5/summary.md`](../qa/P0-5/summary.md)

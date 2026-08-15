# 빠른 시작 — 받은 것과, 실행할 한 줄

이 문서는 **매뉴얼이 아니라 표지**다. 설치 절차의 단일 진실은
[install.md](install.md)이고, 설치 이후의 사용법은 [manual-member.md](manual-member.md)가
소유한다. 여기서는 같은 내용을 다시 적지 않는다 — 처음 받은 사람이 **어디부터 손대야
하는지**만 알려주고 넘긴다.

---

## 1. 받은 것

| 받은 것 | 무엇인가 |
|---|---|
| 저장소 체크아웃(또는 릴리스 번들) | 설치기와 스킬 소스. 설치기는 이 체크아웃 안에서 실행된다 |
| `<bundle>/update-trust.pub` | 업스트림 릴리스를 검증할 **업데이트 신뢰키** 공개키 |
| `SHA256:<공지된-지문>` | 위 키의 지문. **키와 같은 경로로 오면 안 되는 값**이다 (§3) |
| 이 문서 | 표지 |

## 2. 먼저 준비할 것

준비물의 정본은 [third-party-runtime-prereqs.md](third-party-runtime-prereqs.md)이고,
그 문서 §6의 체크리스트 9항을 통과한 뒤에 아래를 실행한다. 준비가 덜 됐어도 §4의
**계획 확인(dry-run)까지는** 안전하게 돌려볼 수 있으니, 무엇이 부족한지 먼저 보는 것을
권한다 — 부족한 항목은 하나씩 이름이 나온다.

호스트 조건 하나만 여기서 못박는다: **Linux + systemd + root 권한.** 리컨실러·워처가
systemd 타이머라서 컨테이너에서는 완주할 수 없다.

## 3. 지문부터 대조한다 (사람이 해야 하는 유일한 판단)

키는 설치기와 함께 온다. 그러니 그 키가 진짜인지는 **설치기가 아닌 경로**로 확인해야
한다. 절차와 근거는 [install.md §2](install.md#2-신뢰키-지문을-먼저-대조한다)에 있다.

## 4. 실행할 한 줄

노드 config를 먼저 만든다([install.md §3](install.md#3-노드-config-작성) — 복사해서
자기 값으로 고치는 것이 전부다). 그 다음:

```bash
cd ~/autophagy-agents
automation/install/quickstart.sh \
    --config /tmp/node.toml \
    --update-trust-key <bundle>/update-trust.pub \
    --expect-update-trust-fingerprint 'SHA256:<공지된-지문>'
```

이 스크립트는 설치를 **대신 정의하지 않는다.** `python3 -m automation.install`을
install.md와 똑같은 인자로 조립해 실행할 뿐이고, 하는 일은 순서를 지켜주는 것이다.
계획을 직접 보고 손으로 실행하고 싶으면 install.md의 명령을 그대로 써도 결과는 같다.

자주 쓰는 옵션:

| 옵션 | 언제 |
|---|---|
| `--dry-run-only` | 계획만 보고 끝낸다. `sudo`를 전혀 쓰지 않는다 |
| `--yes` | 확인 프롬프트를 건너뛴다. **스크립트 실행 전용** |
| `--with-component managed-sync` | 관리형 스킬 자동 수신을 함께 설치 (나중에 붙여도 된다) |
| `--group-roster` / `--expect-group-skill-fingerprint` | 그룹 가입 — 절차는 [manual-member.md §3](manual-member.md) |

## 5. 무엇을 보게 되는가

| 단계 | 무슨 일이 | 멈출 수 있는가 |
|---|---|---|
| ① 계획 확인 | `--dry-run`이 먼저 돈다. **root 불필요, 아무것도 쓰지 않는다** | 여기서 실패하면 부족한 전제의 **이름**이 나온다. 아무것도 쓰이지 않은 상태다 |
| ② 요약 | 계획을 종류별로 세어 보여준다(계정 N·디렉터리 N·파일 N·타이머 N·판정 N) | — |
| ③ 확인 | `yes`를 직접 입력해야 다음으로 간다 | 그 전까지 `sudo`는 실행되지 않는다. 엔터만 치면 취소다 |
| ④ 실제 설치 → 종료 게이트 | `sudo`로 같은 명령을 실행하고, 설치기가 마지막에 돌린 `healthcheck` 판정을 요약한다 | 첫 실패에서 멈춘다. **고치고 같은 명령을 다시 실행하면 된다**(멱등) |

끝났다는 판정은 세 줄이다 — `[PASS] healthcheck: … ALL_HEALTHY`,
`[PASS] trust-key.fingerprint: …`, `--- INSTALLED: N건 중 실패 0`. 자세한 읽는 법은
[install.md §7](install.md#7-끝났는지-어떻게-아는가).

중간에 사람이 개입해야 하는 지점은 정확히 두 곳(**Hermes 게이트웨이 설치**, **배포 키
등록**)이며 둘 다 설치기가 그 자리에서 무엇을 하라고 출력한다 —
[install.md §6.1·§6.2](install.md#6-실제-설치).

> 팁: `sudo`는 환경변수를 지운다. 실제 설치 전에 `set -a; . ~/.env.secrets; set +a`로
> `DISCORD_BOT_TOKEN`을 올려두면 스크립트가 그 변수 하나만 sudo 너머로 전달한다.
> 없으면 경고하고, sudoers가 전달을 거부하면 그것도 알려준다(조용히 넘어가지 않는다).

## 6. 막히면

- 설치기가 지목한 이름을 그대로 [install.md §8 자주 막히는 곳](install.md#8-자주-막히는-곳)에서 찾는다.
- 전제(Discord·모델·Hermes·신뢰키) 문제면 [third-party-runtime-prereqs.md](third-party-runtime-prereqs.md).
- 로그 전문 경로는 스크립트가 마지막 줄에 출력한다.

## 7. 설치 다음

설치가 끝나면 이 문서는 할 일이 없다. 이후는 **[manual-member.md](manual-member.md)**가
소유한다 — 그룹 가입 3단계 핸드셰이크(§3), 일상 승인 게이트 읽는 법(§4), 관리형 스킬이
자동으로 마운트되지 **않는** 이유(§5)가 거기 있다.

## 8. 이 스크립트가 검증된 범위

정직하게 적는다. 증적은 [`docs/qa/W-F2.5-E/`](../qa/W-F2.5-E/quickstart-wrapper.md)에 있다.

- **검증됨** — 깨끗한 `python:3.12-slim` 컨테이너에서 계획 확인 경로 전 구간 rc 0
  (액션 62개, `--with-component managed-sync`면 65개). 인자 누락·읽을 수 없는 키·지문
  불일치·터미널 없음·`sudo` 없음이 전부 **이름을 지목하고** 종료하며, 확인 전에는
  아무것도 쓰지 않는다.
- **아직 검증되지 않음** — 실제 Linux+systemd 호스트에서의 apply → 타이머 활성 →
  `healthcheck.sh` 전부 PASS. 컨테이너에는 systemd가 없고 Hermes는 설치기가 설치하지
  않는 외부 전제라 구조적으로 도달할 수 없다([P0-5도 같은 경계를 남겼다](../qa/P0-5/summary.md)).
  이 구간은 실제 호스트를 가진 첫 운영자가 완주할 때 닫힌다.

## 관련

- 설치 절차 단일 진실: [install.md](install.md)
- 전제: [third-party-runtime-prereqs.md](third-party-runtime-prereqs.md)
- 설치 이후 전부: [manual-member.md](manual-member.md)
- 스크립트: [`automation/install/quickstart.sh`](../../automation/install/quickstart.sh)

# 후속 과제

> 열린 잔여 부채·개선만 남긴다. 현황판은 [features.md](features.md), 완료 기능은 [done.md](done.md).

> 완료·배포된 기능에서 발견됐지만 이번 사이클에 처리하지 않은 잔여 부채·개선을 모은다. 기능 단위 묶음 항목으로, 불릿마다 "문제 → 조치" + 영향 범위·심각도를 적는다. 상세 규칙: 루트 `AGENTS.md`「후속 과제 기록 규칙」.

## RAG 런타임 배포·탐지 수리 중 발견한 후속 과제 (2026-08-22)

- **런타임 패키지 프로브가 네 패키지를 아직 보지 못한다** — `interop_runtime`·`regression_bank_runtime`·`reminder_poller_runtime`·`research_trends_runtime`은 이번에 실측한 `rag_ingest`·`memory_curator`와 내부 전개 레이아웃이 달라 같은 재귀 대조를 적용하면 오판한다 → 각 배포기의 실제 전개 경로를 확인해 `configs/runtime-package-manifest.txt`에 등록하거나, 등록할 수 없다면 패키지별 사유와 다른 판정 방법을 명시한다. **현재 동작과 무관 · 심각도: 중(같은 조용한 드리프트가 재발할 수 있음)**.
- **embedding 소스와 실행 이미지의 갱신 시점이 갈린다** — `automation/rag_stack/deploy.sh`는 `embedding/` 정본도 노드에 동기화하지만 의도적으로 컨테이너를 재빌드하지 않고, 후속 활성화 명령도 검색 중단 범위를 줄이려고 MCP만 빌드한다. 따라서 embedding 소스는 최신이어도 실행 이미지는 2026-07 빌드본으로 남는다 → 모델 캐시와 긴 빌드 시간을 고려해 embedding 재빌드 변경 창을 소유자와 정한다. MCP 이미지는 새 healthcheck 프로브가 실행 컨테이너 내부 핵심 모듈 해시로 검증한다. **현재 동작과 무관 · 심각도: 중**.
- **RAG 노드의 `personal-rag.service` ops user unit이 2026-08-08 21:53 UTC부터 failed다** — 부팅 때 `docker compose up -d --build`가 `ghcr.io/astral-sh/uv:0.9.18` 메타데이터 DNS 조회(`127.0.0.53`, `server misbehaving`)에 실패했다. 컨테이너는 12일째 healthy이고 현재 `ghcr.io` 도달도 정상(HEAD `/v2/` → 405)이라 현 서비스 영향은 없지만, 같은 실패가 재부팅 때 나면 RAG 스택 전체가 올라오지 않는다 → 원인 해소를 확인한 뒤 유닛을 복구·재시작하고, 빌드 시 레지스트리 실패의 재시도·캐시 전략을 검토한다. **재부팅 복구성 영향 · 심각도: 중**.

관련 기능: [런타임 패키지 배포와 드리프트 탐지](기능소개/런타임-패키지-드리프트-탐지.md). 실측일 2026-08-22, 릴리스 `v1.0.46` (`99b9d25e`).

## 태그 없는 머지로 프로덕션이 다시 얼었다 (2026-08-21)

「릴리스 태그 규칙」이 생긴 다음 날 같은 정지가 재발했다. PR #203~#210 여덟 건이 태그 없이 main에 들어가 `origin/main` HEAD(`8ce01da0`)가 어떤 서명 태그의 peel 대상도 아니게 됐고, 리컨실러는 2분마다 수렴을 시도하다 exit 4로 실패했다. 이번엔 조용하지 않았다 — 실패 3회에서 소유자에게 드리프트 통지가 갔고, `automation/release-tag.sh`로 v1.0.25를 컷하자 2분 안에 수렴했다(`current -> 8ce01da0`, floor `v1.0.24@9c30748a` → `v1.0.25@8ce01da0`, `incident_open` 해제·`consecutive_failures` 0 확인).

- **태그는 여전히 사람이 기억해야 한다 — 규칙 하나로는 하루를 못 버텼다** — 2026-08-20에 「릴리스 태그 규칙」을 세우고 `release-tag.sh`를 만들었는데 그 다음 날 PR 8건이 또 태그 없이 들어갔다. 스크립트가 있다고 실행되는 것은 아니고, PR 머지는 GitHub에서 일어나 로컬 스크립트를 부를 자리가 애초에 없다. 서명키를 CI에 두는 것은 MD-1이 막는 바로 그 escalation이라 금지이므로, 자동화할 수 있는 것은 **서명 주체가 아니라 알림**이다 → 태그 없는 `origin/main` HEAD가 N분 이상 지속되면 소유자에게 "태그를 자르라"고 알리는 경로를 넣는다(리컨실러 통지가 결국 알려주기는 하지만, 그때는 이미 프로덕션이 멈춘 뒤다). **재발 실측 · 심각도: 높음(재발할 때마다 프로덕션이 멈춘다)**.
- **[해소 2026-08-21, PR #214] 리컨실러의 두 검증 경로가 서로 다른 node config를 읽었고, 그중 하나는 ops가 쓸 수 있는 파일이었다** — ops 사전 게이트는 `/etc/autophagy/node.toml`(이 노드는 `require_signed_updates = false`)을 읽어 **미서명 HEAD를 통과**시키고, root 헬퍼는 `getent passwd ops` 홈의 `~ops/.hermes/node.toml`을 읽는데 그 파일이 없어 기본값(서명 요구)으로 조용히 폴백했다. 둘은 TOCTOU 창을 닫으려고 같은 것을 두 번 검증하도록 설계됐는데 실제로는 서로 다른 정책을 검증했다 — 그래서 문서화된 조용한 스킵이 죽은 코드가 되고 2분마다 sudo 수렴이 실패로 집계됐다. **더 심각한 쪽은 그 파일의 소유자였다** — ops는 바로 그 헬퍼에 NOPASSWD sudo 권한이 있어, 자기 홈에 `require_signed_updates = false`를 쓰고 헬퍼를 부르면 root가 미서명 `origin/main`을 설치한다(MD-1이 막으려던 escalation이 config 경로로 부활). 조치: 특권 경로가 config를 아예 읽지 않는 `resolve-signed`로 전환하고(floor·신뢰루트는 root 소유 고정 경로로 명시), 사전 게이트도 서명 전용으로 맞췄고, `load_node_config(명시 경로)`는 부재 시 실패하게 했다. 소개: [서명 전용 특권 수렴 경계](기능소개/서명-전용-특권-수렴-경계.md).
- **[해소 2026-08-21, PR #214] `deploy-skill.sh`의 by-value 수렴이 서명 태그 게이트를 건너뛰었다** — 프로덕션이 `2c065bdf`를 돌고 있었는데 릴리스 플로어는 `v1.0.24@9c30748a`였고 `2c065bdf`로 peel 되는 태그는 존재한 적이 없다. 출처는 23:02 UTC `meeting` 스킬 배포였다 — `converge-release-runtime.sh`(by-value)는 타겟 기본값이 "지금의 `origin/main`"이라 서명도 floor도 보지 않는다. 즉 스킬 하나를 승인하면 코드 런타임 전체가 미서명 HEAD로 함께 전진했고 그 사실이 어디에도 남지 않았다. 조치: ops가 이미 NOPASSWD 권한을 가진 인자 없는 특권 헬퍼로 수렴한다(같은 lock, 서명 자체 해석). 서명 릴리스가 없으면 배포가 거기서 멈추고 태그를 자르라고 안내한다.
- **설치기가 `/etc/autophagy/node.toml`을 설치하지 않는다** — `automation/install/assets.py`는 계정 홈 사본만 설치하는데, 리컨실러 유닛이 `ProtectHome=tmpfs`라 서비스에게 그 홈은 빈 tmpfs로 보인다. 그래서 신규 설치 노드는 토폴로지를 seed에서 읽게 되고, 그것이 정확히 2026-08-16에 플레이스홀더 호스트명으로 세 가지가 조용히 실패한 원인이었다(`e71e10cc`가 읽기 경로만 고쳐두고 쓰기 경로는 남겨둔 상태) → 설치기가 `root:root 0644`로 그 경로를 설치하게 하고, 계정별 사본은 호환을 위해 당분간 유지한다. **이 노드는 이미 그 파일이 있어 영향 없음 · 심각도: 중(새 설치가 조용히 seed 토폴로지로 돌아간다)**.
- **롤백 방지 floor와 update-channel 바인딩이 ops 소유라 downgrade 여지가 남는다** — 위 수정으로 임의의 미서명 코드 설치는 막혔지만, 악의의 ops가 `/srv/autophagy-private/deploy-reconcile/release-floor.json`을 지우고 update channel이나 미러 origin을 **과거의 진짜 서명 릴리스**로 되돌리면 이전 서명 버전으로의 downgrade를 유도할 수 있다(취약점이 있는 구버전으로 되돌리는 공격) → floor를 root 소유 디렉터리로 옮기고, 사전 게이트는 read-only 검사만 하며 root 헬퍼가 서명 재검증 뒤 단조 증가시키도록 분리한다. 기존 floor는 초기화하지 말고 정확히 마이그레이션해야 하며, 공개 채널 전환 시의 설치 단위 floor 규칙도 그대로 유지한다. **ops 침해를 전제로 하는 잔여 위험 · 심각도: 중**.
- **[해소 2026-08-21] healthcheck의 update-trust 프로브가 config 기반 `resolve`를 썼다** — 수렴은 서명 전용으로 바뀌었는데 `automation/update_trust_probe.sh`는 노드 정책을 따랐다. `require_signed_updates = false`인 설치에서는 **프로브가 PASS인데 수렴은 막힌** 상태가 될 수 있었다 — 방금 고친 그 분기의 작은 복사본이다. 조치: 프로브도 `resolve-signed`를 쓰고 롤백 방지 floor를 함께 넘긴다. floor는 `NODE_PRIVATE_ROOT`에서 **유도**하지 보간하지 않는다 — 루트가 비어도 문자열은 `/`로 시작해서, 단순 절대경로 검사는 앵커 없는 `/deploy-reconcile/release-floor.json`을 통과시켰을 것이다. 이로써 노드 `node.toml`을 손으로 고칠 필요도 없어졌다.

증적: 노드 `<primary-node>` — 리컨실러 저널 00:29~00:45 UTC, `release-floor.json`·`state.json` 전후 대조, `readlink /srv/autophagy-agent-current` = `8ce01da0`.

## mailon 런타임이 19일간 옥 릴리스에 고정돼 있었다 (2026-08-18)

소유자의 논문 메일을 머춰 상태에서 수리하다, 진짜 원인이 드러났다. `035e767d`(2026-07-29, exact-set 수신자 검증)가 결함 **2건**을 함께 들여왔는데, mailon 런타임이 07-30 릴리스(`96599b22b6e6fdda`)에 **19일간 고정**돼 있어 프로덕션은 그 코드를 한 번도 실행하지 않았다. 그래서 발송은 8/14까지 멀줦히 동작했다. 오늘 compose 기동 경쟁(PR #152)을 고치려 vendor 트리를 배포하자 **둘이 동시에** 올라와 모든 발송이 즉시 실패했고, 2회 연속 실패가 mail-mode를 `no-go`로 강등시켰다. 두 결함은 PR #157·#158로 고쳤고 발송도 확인했다(보낸편지함 재조회 증적).

- **배포되지 않은 벤더 수정이 쌓여 있어도 알 방법이 없다** — mailon 런타임은 `skills/mail/deploy.sh`를 사람이 돌려야만 갱신된다. 07-29에 커밋된 수정이 08-18까지 도달하지 못했고, 그 19일간 **비정상이라는 신호가 어디에도 없었다** — 스킬은 `readlink live/<skill>`로 판정하고 코드는 리컴실러가 따라오지만, **vendor 런타임은 둘 다 아니다**. 더 나쁜 것은 배포 순간 19일치 미검증 변경이 한꺼번에 들어간다는 점이다 — 오늘이 정확히 그랬다. 조치: `~/.hermes/mailon-runtime/current`의 vendor digest를 `origin/main`의 `skills/mail/vendor` 해시와 대조하는 프로브를 healthcheck에 넣어 지연을 보이게 한다. **잠복 위험 · 심각도: 높음(다음 배포가 또 대량의 미검증 변경을 한꺼번에 올린다)**.
- **[해소 2026-08-20] 같은 구멍이 래퍼 전체에 있었다 — 이젠 헬스체크가 본다** — 윈줄은 mailon vendor 런타임 하나를 가리켰지만, `~/.hermes/scripts/` 의 **no-agent cron 래퍼 13개 전부**가 같은 부류였다 — 릴리스가 아니라 계정 홈에 살고, 그 스킬/패키지의 `deploy.sh` 를 사람이 돌려야 갱신된다. 스킬 마운트는 `skill_mounts_current`, 릴리스 밖 root 자산은 `release_helper_drift` 가 보는데 래퍼만 어느 쪽에도 속하지 않았다. 조치: `configs/watcher-deploy-manifest.txt`(account|source|destination|policy) + `automation/watcher_drift_probe.sh` 신설해 `watcher_wrappers_current` 로 배선. **원격 프로브다** — 래퍼는 agent·peer 홈(0700)에 있고 cron 계정(ops)은 그 계정으로 sudo 할 수 없어 LOCAL_PROBES 에 넣으면 영원히 UNKNOWN 이다. 목적지 이름이 소스 이름과 다를 수 있어(calendar 는 `confirm_reaction_watch.py` 를 `calendar_confirm_reaction_watch.py` 로 배포) 이름 추론이 아니라 표가 진실이고, conformance 테스트가 모든 `deploy.sh` 와 대조해 신규 래퍼 누락을 먼저 깨뜨린다. 실패 시 재배포 명령을 함께 찍는다(소스 경로 앞 두 조각에서 유도). **mailon vendor 런타임은 이 표의 대상이 아니다** — `~/.hermes/mailon-runtime/` 은 단일 파일이 아니라 트리라 윈줄의 digest 대조가 그대로 남아 있다. 켜자마자 라이브 노드에서 **실제 드리프트 두 건**을 잡았다 — `memory_curator_watch.py`(배포 08-03)·`memory_relocate_watch.py`(배포 08-02)가 구버전으로 돌고 있었고, 각각 `automation/memory_curator/deploy.sh`·`automation/memory_relocate/deploy.sh` 재실행이 필요하다(root 불필요). 둘 다 승인 게이트 워처라, 구버전이 도는 동안 소유자 ✅ 처리가 낡은 코드로 이뤄진다. **알려진 한계 2가지**(둘 다 실측에서 드러났다): ① healthcheck 전용 ssh 키는 `autophagy-healthcheck-probe` 강제명령에 묶여 `SSH_ORIGINAL_COMMAND` 의 sha256 정확 일치만 허용하는데, 그 래퍼는 **리포에 설치기가 없는 손 유지보수 노드 자산**이라 매니페스트에 행을 더하면 사람이 해시를 손으로 넣어야 한다 — 넣기 전까지 프로덕션에서는 전 행이 exit 126 이다(2026-08-20 실제로 12행이 오탐으로 나왔고, 14개 해시를 추가해 해소). **두 한계 모두 같은 날 해소됐다** — `automation/healthcheck_probe_wrapper.sh` 가 허용목록을 릴리스에서 생성하고(`--install` 은 root 불필요), `healthcheck_wrapper_current` 프로브가 래퍼 헤더의 입력 지문으로 재생성 필요를 잡는다. 목록은 손으로 적지 않고 **관측한다** — 헬스체크를 불러 `capture_on_node` 를 기록용으로 갈아끼우고 모든 체크를 한 바퀴 돌린다(실제 ssh 는 나가지 않는다). 켜 보니 그 시점 명령 43개 중 **23개가 목록 밖이라 거부**되고 있었고 — 다른 세션이 추가한 mailon·워처 체크와 **실패한 체크가 수리 티켓을 내는 경로**가 거기 포함됐다 — 잔여 해시 14개는 허용 범위만 넓히고 있었다. 재생성 뒤 rc=126 은 0이 되고 티켓 경로가 살아났으며, 그러자 그동안 가려져 있던 워처 드리프트가 2건에서 **13건**으로 드러났다. 소개: [헬스체크 허용목록 생성](기능소개/헬스체크-허용목록-생성.md).
- **테스트 픽스처가 사이트가 내지 않는 값을 기본값으로 썬다** — `test_mail_compose_form_verify.py`의 `_form(method="send")`가 그것이다. 실측하면 이 웹메일은 compose 내내 `method=""`를 낸다(12초 폴링, 프로덕션 로그도 8/12·8/13·8/14 모두 `""`). 그래서 **발송이 전면 불가한 동안에도 스위트는 초록**이었다. 이번에 기본값을 실측값으로 바꿔 기존 검사 전부가 현실 값으로 돌게 했으나, **같은 부류가 다른 벤더 경로에도 있는지는 보지 않았다**. 조치: 벤더 경계의 픽스처 상수는 실측 근거(로그·프로브)를 주석으로 달거나, node 기반 실제-JS 검사(이번 추가한 2건처럼)로 대체한다. **탐지 공백 · 심각도: 중(초록이 안전을 보장하지 않았다)**.
- **[해소 2026-08-18, PR #161] `send.py:157`의 무방비 `_tbar.compose()`** — 재시도 헬퍼를 `send_trigger.py`(compose UI 조작을 이미 소유하는 stdlib-only leaf)로 옮겨 `resolve.py`와 `send.py`가 함께 쓰게 했다. 좀은 `ComposeOpenBrowser` Protocol 을 신설해 기존 `TriggerBrowser` 는 건드리지 않았고, `clock` 주입으로 테스트 결정성을 확보했다. 실서피스 검증: `send --dry-run` 이 **exit 0 / `OK: dry_run; POST=0`**(이전엔 상시 exit 2), `resolve` 동일 회귀 없음. `wait_ms(3000)` 은 의도적으로 유지했다 — 재시도가 증명하는 것은 compose 가 *호출 가능*해졌다는 것뿐이고(실측 ~1.9초), 폼·CSRF·에디터 iframe 렌더 완료 시점은 측정된 바 없다.

- **런타임 고정을 healthcheck 가 못 본다 — 다이제스트 틱이 대신 본다** — 이번에 넣은 드리프트 프로브는 agent 계정 경로에 두고 다이제스트 워처가 태운다. `automation/healthcheck.sh` 에 넣지 않은 이유는 그쪽이 ops 계정으로 도는데 런타임은 `~agent/.hermes`(0700)라 읽을 수 없고, 중첩 sudo 는 이 저장소에서 rc=126 으로 프로브를 죽인 실측 선례가 있기 때문이다 → healthcheck 에서도 보이게 하려면 권한 설계(전용 read-only 노출 또는 ops 가 읽을 수 있는 상태 파일)를 먼저 정한다. **동작 결함 아님 · 심각도: 낮음(탐지 경로가 하나뿐)**.

증적: PR #157(빈 method) · PR #158(body probe), 릴리스 `85aaf5ca488f37a6`, 발송 확인 `2026-08-18T08:55:40Z` 보낸편지함. 기능 소개: [mailon 런타임 고정 탐지](기능소개/mailon-런타임-고정-탐지.md).

## 배포 스크립트 자체를 신뢰할 수 없었다 (2026-08-18)

죽은 워처 5개를 복구하다가, 그것들을 실어 나르는 **배포 계층 자체의 결함**이 드러났다. 이번 사건에서 가장 비싼 교훈은 워처 코드가 아니라 **배포가 실패해도 조용했다**는 점이다.

- **[해소 2026-08-18, PR #163] `deploy.sh` 3개에 실행권한이 없었다** — `skills/calendar` · `skills/coordination` · `automation/research_trends` 가 `-rw-rw-r--` 였다. 그래서 `./deploy.sh` 가 **rc=126으로 죽고**, 오늘 coordination 1차 배포가 정확히 이것으로 조용히 실패했다(해시가 그대로인 것을 보고나서야 알았다). chmod +x 와 함께 "모든 deploy.sh 는 실행가능"을 회귀로 고정했다.
- **`wiki`와 `patent-prep`은 지금도 배포 경로가 없다** — budget 과 같은 병이다. 둘을 `_KNOWN_UNDEPLOYED`에 사유와 함께 등록했지만 **심각도는 같지 않다**: `wiki-confirm-watch` cron 은 노드에 **실재하므로** wiki 의 커밋된 수정은 노드에 도달할 수 없다(이번 PR #162 의 `wiki_binding.py` 수정도 해당된다). `patent-prep` 은 cron 자체가 없어 잠복이다. 조치: `skills/budget/deploy.sh`를 본떠 `skills/wiki/deploy.sh`를 먼저 신설하고, patent-prep 은 cron 을 쓸 때 함께 만든다. **wiki=프로덕션 정지(수정 미도달) · 심각도: 높음**.
- **같은 `parents[3]` 결함이 승인 어댑터 4개에 더 있다** — `skills/calendar/scripts/calendar_approval.py` · `skills/budget/scripts/budget_gate.py` · `skills/wiki/scripts/wiki_approval.py` · `skills/patent-prep/scripts/patent_export_binding.py`. 이번에 binding 3건만 고쳤다. 같은 부류가 아닌 것도 구분해 둔다 — `calendar_cli.py`·`prompt_cli.py`·`topics_cli.py`는 checkout 상대 앱 import, `topics_registry.py`는 config 탐색, `todo_preflight.py`는 존재 검사형 probe라 무관하다. 조치: 같은 probe 형태를 적용하고 mounted-release 파라미터화에 추가한다. **잠복(해당 승인 경로가 돌 때 드러난다) · 심각도: 중**.
- **[해소 2026-08-19, PR #170] `budget-watch` 가 설정을 보지 못했다** — 경로 수정 후 오류가 `budget skill is not mounted` → `GATE-REFUSED BUDGET_SHEET_ID가 없습니다` 로 바뀌어 소유자 설정 문제로 보였는데, 시트 ID 를 `~/.env.secrets` 에 넣은 뒤에도 **같은 오류로 계속 죽었다**. 원인은 값이 아니라 전달 경로였다 — 이 래퍼는 `todo_confirm_reaction_watch` 와 달리 `~/.env.secrets` 를 **자체 로드하지 않았고**, Hermes no-agent cron 은 시크릿을 `os.environ` 에 넣지 않는다. 분리 증거: 그냥 실행 rc=1 / `set -a; . ~/.env.secrets` 후 실행 rc=0. `_load_env_secrets()` 추가 + 자식 `env=` 명시 전파로 해소했고, `env -i` 빈 환경에서 rc=0 · cron 틱 `2026-08-19T21:48:31 ok` 로 확인했다. 회귀는 ｢래퍼가 .env.secrets 를 읽는가｣와 ｢모든 subprocess.run 이 env= 를 넘기는가｣를 함께 고정한다.
- **같은 self-load 누락이 다른 워처에도 있는지는 보지 않았다** — budget 은 시트 ID 라는 **눈에 보이는 설정**이 있어 드러났을 뿐이다. 자격증명이 필요한 자식을 spawn 하는 no-agent 워처는 전부 같은 계약을 지켜야 하는데(규약 b-2), 이번 회귀는 budget 한 파일만 검사한다. 조치: `~/.hermes/scripts/` 로 배포되는 전체 워처를 대상으로 ｢시크릿을 쓰는 자식을 띄우면 self-load + env= 가 있다｣를 인벤토리 검사로 넓힌다(선례: 승인 파사드 conformance 테스트). **잠복 · 심각도: 중(설정을 넣어도 조용히 무시되는 부류라 발견이 늦다)**.
- **릴리스 리컴실러가 드리프트를 보고도 수렴하지 않고 exit 0 으로 끝난다** — 실측 2026-08-18: origin/main 이 `14868a66` 일 때 릴리스는 `c6543ce5`(3커밋 뒤)에 멈춰 있었고, `autophagy-deploy-reconcile.timer` 는 **정상 활성**이며 2분마다 `[deploy-reconcile] mirror left-behind: prod has not reached origin/main yet` 를 남기고 **exit 0** 으로 종료했다. 즉 정지가 실패로 잡히지 않는다. 이것은 기존 묶음 ｢릴리스 수렴이 조용히 멈춰 있었다(2026-08-16)｣와 같은 계열이나, 이번에는 **새 모듈을 참조하는 워처를 배포하면 그 참조가 깨진다**는 구체적 피해로 드러났다(PR #167 에서 즉시 회귀 발생·수정). 조치: 그 묶음과 합쳤 다룬다 — 최소한 수렴 지연이 임계치를 넘으면 exit 0 으로 끝내지 않게 한다. **원인 규명 2026-08-19**: 기전은 `exit 0` 자체가 아니라 `rollback-pending` 지문 교착이었다 — 증상 관찰은 정확했으나 원인 지목이 빗나갔고, 실측·해소·잔여는 그 묶음에 기록했다. **배포 순서 위험 · 심각도: 높음(automation.* 를 새로 참조하는 모든 배포가 잠재적으로 깨진다)**.
- **[해소 2026-08-18, PR #167] 새 모듈을 런타임에 실지 않아 워처가 회귀했다** — `topics_import.py` 분리 후 `research_trends.py` 가 그것을 `automation.*` 로 import 했는데 `deploy.sh` 의 런타임 스트림 목록에 없어 배포 즉시 rc=0 → rc=1 로 깨졌다. `poll_reminders.py` 가 이미 쓰는 표준 형태(flat 배포 + `try/except ImportError` 폴백)로 해소하고, ｢런타임 self-import 는 전부 배포 스트림에 있고 flat 폴백이 있어야 한다｣ 를 ast 기반 검사로 고정했다(TYPE_CHECKING 본문은 제외 — 정규식은 그 둘을 구분 못해 오탐이 난다).

- **fail-closed 호스트 가드가 deploy.sh 둘에만 있다** — `DEPLOY_SSH_HOST` 미지정 시 리터럴 플레이스홀더로 ssh 를 시도해 DNS 오류가 진짜 원인을 가리는 문제를 mail·wiki 두 스크립트에서만 닫았다(이번 불릿의 범위가 mail 이었다). calendar·coordination·budget·todo 와 `automation/**/deploy.sh` 는 그대로다 → 같은 가드를 전 배포 스크립트로 넓히고 `tests/unit/test_deploy_host_fail_closed.py` 의 목록을 전수로 바꾼다. **안전 문제 아님 · 심각도: 낮음(배포 절차 마찰)**.

증적: PR #161·#162·#163, cron 틱 `calendar/coordination 21:28:58 ok`, 워처 직접 실행 `notes/research rc=0`.

## 워처들이 스킬 루트 반전 이후 조용히 죽어 있었다 (2026-08-18)

메일 발송을 막던 compose 결함을 고친 뒤, **소유자가 ✅를 눌렀을 때 실제로 발송할 주체가 살아있는가**를 확인하다 드러났다. `mail-triage-watch`가 **111회 연속** 실패 중이었고(승인 게이트는 멀줦는데 실행기가 죽은 상태 — ✅를 눌러도 아무 일도 안 난다), 같은 부류로 5개 워처가 더 죽어 있었다. 메일 경로만 이번에 복구했다(PR #154·#155, cron 틱 `ok` 실측). 원인은 모두 SS-1 스킬 루트 **반전**이다 — `~/.hermes/skills`는 이제 자가 스킬 루트고 배포본은 `/srv/autophagy-skills/live`에 있다. 기능 소개: [워처 자격증명 계약과 실패 임계치 통지](기능소개/워처-자격증명-계약과-실패-임계치-통지.md).

- **[해소 2026-08-18, PR #163] repo 소스가 반전 이전 경로를 쓰던 워처 3개** — `budget_watch.py` · `notes_organize.py` · `research_trends.py`를 live store 해석으로 바꾸고 배포했다. 직접 실행 실측: notes·research **rc=0**, budget 은 오류가 `not mounted` → `BUDGET_SHEET_ID가 없습니다`로 **바뀌었다**(경로는 해결, 설정은 별건 — 아래 신규 묶음 참조). budget 은 **배포 스크립트 자체가 없어서** 고쳐도 올릴 수가 없었고, `skills/budget/deploy.sh`를 신설해 해소했다. `research_trends` 는 단순 경로 교체가 아니었다 — 패키지명이 디렉터리 위치에서 파생돼 위치 기반 계산으로 바꾸고 import 전 `TOPICS_SCRIPTS` 설정을 관련 테스트 전부에 넣었다.
- **[해소 2026-08-18] 노드 배포본이 낡았던 워처 2개 — 각 222회 연속 실패** — repo 는 이미 `991f3e2d`로 고쳐져 있었고 **배포만 안 된 상태**였다(코드 변경 0). `skills/calendar/deploy.sh`·`skills/coordination/deploy.sh` 실행으로 해소 — 둘 다 `Last run 21:28:58 ok`로 streak 종료. **1차 배포가 조용히 실패했는데 원인은 `deploy.sh` 실행권한 부재(rc=126)**였다 — 아래 신규 묶음에 기록.
- **[해소 2026-08-18, PR #162] `parents[3]` repo root 추정 어댑터 3개** — `calendar_binding.py` · `coordination_binding.py` · `wiki_binding.py`에 mail 과 같은 probe 방식을 적용하고 mounted-release 파라미터화에 3건을 추가했다. 예측대로 **앞 배포 결함을 고치자 바로 이 층이 드러났다** — coordination 직접 실행이 `승인 라이프사이클 모듈 불가 (AUTOPHAGY_REPO_ROOT=/srv/autophagy-skills/releases)`로 죽었다. **스킬 본체라 마운트는 소유자 ✅ 대기 중**(calendar·coordination·wiki 승인 요청 게시됨).
- **`--deliver local` 워처는 실패해도 아무에게도 닿지 않는다** — `mail-triage-watch`가 111회, calendar·coordination이 222회씩 연속 실패했지만 소유자는 자기 요청이 안 되는 것으로서만 알 수 있었다. `mail-daily-digest`가 `--deliver discord`를 쓰는 이유가 정확히 이것이다(2026-07-31 사건 주석). 다만 `*/2` 워처를 그대로 discord로 바꾸면 지속 실패 시 하루 720건 DM이라 그것도 답이 아니다. 조치: 고빈도 워처용 **연속 실패 임계치 기반 1회 통지**(예: N회 연속이면 1건만, 복구 시 해제)를 정한다 — 리컴실러가 `FAILURE_NOTICE_THRESHOLD`로 이미 쓰는 패턴을 재사용한다. **탐지 공백 · 심각도: 중(장애 지속 시간이 소유자의 다음 요청 시점에 좌우된다)**.

증적: PR #154(워처 미배포) · PR #155(승인 repo root), cron 틱 `2026-08-18T14:18:46 ok`.

## 기관메일 compose 기동 경쟁이 인증 실패로 오진됐다 (2026-08-18)

소유자의 논문 메일 발송이 5일간 막혔고 그동안 원인은 ｢기관메일 인증 실패｣로 보고됐다. 실제 원인은 인증이 아니라 **두 층의 브라우저 결함**이었다 — ① 로그인 완료 판정이 `wait_url("**/mail**")` glob이라 호스트 `mailon.kr`에 오매칭해 거부된 자격증명도 "성공"으로 읽혔고(PR #148), ② 로그인이 진짜로 통과하게 되자 그 다음 층에서 메일함 SPA 기동 경쟁이 드러났다(PR #152). 로그인 자체는 재현 가능하게 성공하며, 세션 초기에 소유자에게 전달한 "서버가 자격증명을 거부한다 → 비밀번호 교체 필요"는 **오진이었고 철회했다**. 수리 결과는 `resolve --name <이름>` RC=0·후보 3건으로 검증했다(릴리스 `895b3b14a2a1a92e`). 기능 소개: [기관메일 exit2 분리와 수신자 순위](기능소개/기관메일-exit2-분리와-수신자-순위.md).

- **exit code 2가 인증 실패와 브라우저 실패를 한 코드로 접는다 — 이것이 5일 오진의 실제 원인이다** — `mailon_interface.py`의 `exit_codes[2]`는 `auth_or_browser_error`인데 `mail_wrapper.py:_mailon_failure`가 `rc == 2`를 조건 없이 `auth_error`로 접고 `REAUTH_GUIDANCE`를 붙인다. 그 안내문은 첫 줄부터 "기관메일 인증 실패"라고 **단정**한다. 그래서 순전한 기동 경쟁이 자격증명 문제로 보고됐고, 수리 방향이 반대로 돌아 비밀번호 교체까지 권고되는 데까지 갔다. 조치: 이미 존재하는 `classify_stderr`의 `failure_signature`로 rc 2를 인증/브라우저로 갈라 서로 다른 `error_code`·안내문을 내거나, vendor 쪽에서 두 부류에 다른 exit code를 부여한다. **동작 결함 아님(수리는 끝남) · 심각도: 높음(오진이 수리 방향을 반대로 돌린다 — 실측 5일)**.
- **`REAUTH_GUIDANCE` ③가 존재하지 않는 `~/emailAutomation`을 지목한다** — `orientpine/emailAutomation`은 2026-07-30에 이 저장소로 흡수돼 `skills/mail/vendor/mailon/`으로 vendoring됐고 런타임은 `~/.hermes/mailon-runtime/`이다. 즉 진짜 인증 실패가 났을 때 소유자는 없는 디렉터리에서 수동 검증을 시도하게 된다. 하필 장애 대응 중에만 읽히는 문구라 평소에는 낡은 채로 드러나지 않는다. 조치: 안내문 ③를 현재 런타임 경로(`~/.hermes/mailon-runtime/current` + 스코프 venv)로 갱신한다. **동작 영향 없음 · 심각도: 낮음(문서 지연)**.
- **`resolve`가 한 사람에 대해 복수 주소를 순위 없이 돌려준다** — 실측: `--name <이름>` 후보 3건 중 주소는 2종이었다(`organization`·`contacts`가 같고 `history`가 다름). 어느 쪽이 현재 유효한지 판정할 신호가 응답에 없어 호출자가 임의로 고르면 **잘못된 수신자에게 발송**된다 — 되돌릴 수 없는 외부효과다. 지금은 발송 전 소유자 승인 게이트가 사람 눈으로 막아주지만, 그것은 마지막 방어선이지 판정이 아니다. 조치: group 우선순위를 결정론으로 두거나(`organization` > `contacts` > `history`), 복수 주소일 때 `entity_preflight`의 `ENTITY-CLARIFY`로 넘겨 소유자에게 되묻는다. **현재 오발송 사례 없음 · 심각도: 중(승인 게이트가 유일한 방어선)**.
- **`skills/mail/deploy.sh`의 호스트 폴백이 리터럴 placeholder다** — `deploy.sh:5`가 `host="${DEPLOY_SSH_HOST:-<primary-node>}"`라, 환경변수를 빠뜨리면 해석되지 않는 이름으로 ssh를 시도해 실패한다. 엉뚱한 노드로 배포되지는 않으므로 안전 문제는 아니지만, 실패 메시지가 원인("변수를 안 줬다")을 가리키지 않아 배포 중에 한 번 더 막힌다. 조치: 미지정이면 그 사실을 적시하고 fail-closed로 거부한다. **보안 문제 아님 · 심각도: 낮음(배포 절차 마찰)**.

증적: PR #148·#149·#150·#151·#152, 배포 릴리스 `895b3b14a2a1a92e`.

## Hermes 무재시동 자체 업데이트로 게이트웨이 도구 계층이 죽었다 (2026-08-18)

`<primary-node>`의 agent 게이트웨이가 08-17 13:24부터 08-18 09:57(KST)까지 **모든 도구 호출**을 `ImportError: cannot import name '_plan_tool_batch_segments' from 'agent.tool_dispatch_helpers'`로 실패시켰다(errors.log 9회). 소유자의 메일 요청 2회가 이것으로 막혔고, 첨부 확인·수신자 조회·승인 초안이 전부 도구 호출이라 함께 죽었다(발송·초안 생성은 일어나지 않았다 — 승인 로그·draft store 신규 레코드 0). **코드가 아니라 프로세스가 원인이다**: 그 심볼은 디스크 파일에 정상 존재하고(`tool_dispatch_helpers.py:117`, `__all__` 등록), 새 인터프리터에서 `import agent.tool_executor`는 통과한다. 게이트웨이는 08-16 12:50 기동, `hermes-update`는 08-16 14:34·14:36 실행 — **떠 있는 프로세스 밑에서 소스 트리가 교체**됐고, 옛 모듈을 든 프로세스가 새 파일을 import하다 죽었다(트레이스백 줄 번호가 디스크 소스와 어긋나는 것이 같은 증거). agent·peer 게이트웨이를 함께 재시동해 해소했다(01:03 UTC, 실제 도구 호출 1건으로 검증).

- **`hermes-update`가 소스를 교체하고도 게이트웨이를 재시동하지 않는다** — 벤더 자체 업데이트 경로에는 우리 릴리스 리컨실러가 갖고 있는 `autophagy-gateway-pair restart`에 해당하는 단계가 없어, 업데이트 시점부터 다음 재시동까지 프로세스가 **옛 코드 + 새 파일**의 불일치 상태로 돈다. 이번엔 그 상태가 20시간 잠복하다 첫 도구 호출에서 드러났다 → 벤더 업데이트 뒤 게이트웨이 쌍 재시동을 자동화하거나(래퍼), 최소한 "게이트웨이 기동 시각 < `~/.hermes/hermes-agent` 소스 mtime"을 보는 프로브를 넣어 잠복을 깬다. **재발 확실 · 심각도: 높음(도구 계층 전체 정지)**.
- **liveness 관점에서는 장애가 아니었다 — 그래서 아무도 못 봤다** — 유닛은 내내 `active/running`이었고 Discord도 연결돼 있어 텍스트 응답은 정상으로 나갔다. 즉 `systemctl is-active`나 Discord 연결성에 기대는 프로브는 이 장애를 원리적으로 통과시킨다. 소유자는 자기 요청이 두 번 실패한 뒤에야 알았다 → 위 mtime 프로브와 함께 `agent.conversation_loop`의 `Outer loop error` 연속 발생을 사건으로 승격하는 기준을 정한다. **탐지 공백 · 심각도: 중(장애 지속 시간이 소유자의 다음 요청 시점에 좌우된다)**.
- **벤더 버전이 계정별로 갈라진다 — 오늘 맞췄지만 재발을 막는 것은 없다** — 발견 시점에 agent는 v0.20.1, peer는 **v0.18.2**(07-14자)로 문제의 심볼이 아예 없었다 — 자기 코드끼리 일관돼 정상 동작했기 때문에 갈라짐 자체가 아무 신호를 내지 않았다. 「게이트웨이 재시동 규칙」은 두 계정을 항상 쌍으로 다루는데 벤더 업데이트는 계정별로 따로 일어나 그 쌍 가정이 깨져 있었다. 2026-08-18 소유자 지시로 ① peer를 agent 커밋 `7095e23eb`(v0.20.1)으로 ff해 같게 만든 뒤 ② 둘 다 upstream 최신 `a3995f8`(**v0.20.3**, 2026.8.16.2)로 `hermes update`했다 — 두 계정이 같은 커밋·같은 버전 문자열임을 대조하고(업스트림은 두 업데이트 사이에 움직일 수 있으므로 사후 대조가 필수다), config 포맷은 양쪽 모두 v33→v37로 마이그레이션됐다. 롤백 지점은 양쪽 `backup/pre-latest-20260818T012445Z`. 다만 **갈라짐을 막거나 보이게 하는 장치는 여전히 없다** — 다음 `hermes update`가 한 계정에서만 돌면 그날로 다시 갈라진다 → 두 계정의 벤더 버전을 함께 올리는 절차를 정하거나, 버전 대조를 헬스체크에 드러낸다. **현재 일치 · 심각도: 중(재발 시 한쪽만 회귀해 원인 규명이 어려워진다)**.
- **`docs/guide/operations.md`가 안내하는 `userctl` 래퍼가 노드에 없다** — 표의 복구 명령 `userctl <node> <account> restart hermes-gateway.service`가 `command not found`로 실패해, 이번 재시동도 `sudo -u <account> env XDG_RUNTIME_DIR=/run/user/$(id -u <account>) systemctl --user restart hermes-gateway.service`로 우회했다 → 래퍼를 설치하거나 문서를 실제로 동작하는 명령으로 고친다. **동작 영향 없음 · 심각도: 낮음(장애 대응 중에 한 번 더 막힌다)**.
- **같은 업데이트가 agent의 `hermes_compat` 패치 3종을 통째로 떨괴뜨렸다 — 재작성은 끝났고(PR #147) 배포만 남았다** — 장애 조사 중 부수적으로 드러났다. agent의 `gateway/run.py`·`plugins/platforms/discord/adapter.py`에서 마커 `_hermes_pgd_done`·`_hermes_busyfifo_done`·`_hermes_receipts_done`이 **세 개 모두 없고**, 변경분은 08-16 업데이트가 만든 autostash(2파일 252줄)에 남아 있었다 — stash는 됐지만 복원이 안 됐다. 빠진 것 중 `busy-path-pre-gateway-dispatch`는 busy 경로 메시지가 skill-generation 관찰(W6-4)과 **meeting-gate fail-closed veto(W2-3)** 훅을 타지 못하는 문제를 메우는 것이라 단순 편의 패치가 아니다. **제거가 아닌 재작성임을 먼저 확인했다** — v0.20.3에서도 `pre_gateway_dispatch` 호출 지점은 `_handle_message` 한 곳뿐이고(`run.py:16309`) `_handle_active_session_busy_message`(`run.py:9920`)는 여전히 busy 핸들러로 배선돼 있어 세 `removal_condition` 중 어느 것도 충족되지 않았다. 원본은 `archive/hermes-compat-v0.18.2-patches`(`ab5d6d97c`)와 `~agent/.hermes/hermes-compat-patches-v0.18.2.diff`(sha256 동일 확인, 0600)로 이중 보존해 둔 것을 기준으로 썼다 → 남은 일은 PR #147 머지 후 배포와 마커 3개 verify뿐이며, **그전까지는 그 두 훅이 busy 경로에서 미발동임을 전제한다**. **프로덕션이 08-16부터 무패치로 구동 중 · 심각도: 높음(게이트 인접 훅이 꺼져 있다)**.
- **재작성이 남긴 축소 1건: 복구 경로는 영수증 수신 표시를 받지 않는다** — v0.20.3이 ingress 승인 블록을 **동기** `_discord_message_admission()`으로 추출해, `await`하는 영수증 코드가 그 자리에 존재할 수 없게 됐다. 승인 직후 첫 비동기 지점인 `_dispatch_discord_message()`로 옮겼고 라이브 경로는 예전대로 물리 DM당 1회 발화하지만, 복구 경로 `_dispatch_recovered_message()`(`claim=False`)는 👀와 ledger `record_received` 행을 받지 못한다 — 해결(resolve)은 MOD1이 쓰는 metadata를 읽으므로 정상 동작하고 수신측 breadcrumb만 빠진다(원본 패치도 라이브 경로 한 곳만 덮었으므로 범위는 같다) → 복구 경로에도 영수증을 달지 결정하려면 그 경로가 재생하는 메시지의 👀가 소유자에게 혼란을 주는지부터 정한다. **동작 결함 아님 · 심각도: 낮음(드문 경로의 관측성 공백)**.
- **패치 carrier를 올리는 deploy 스크립트가 둘이고 목적지도 다르다 — 한 쌓만 돌리면 절반만 복구된다** — 노드의 `~/.hermes/hermes-compat/hermes_compat/`에는 `patch_busy_dispatch.py`만 있다. 실측해 보니 누락이 아니라 설계가 그렇다 — `deploy.sh`는 `patch_busy_dispatch.py`를 `hermes_compat/`에, `deploy-owner-dm.sh`는 `patch_busy_fifo.py`·`patch_discord_receipts.py`를 **`appliers/`라는 다른 디렉터리**에 올리며, 후자의 preflight도 그 둘만 검증한다("prove BOTH patches") → 3종을 다 올리려면 **두 스크립트를 모두** 실행하고 끝에 마커 3개를 일괄 verify해야 한다. 장기적으로는 한 경로로 모으는 것이 맞지만, 그러면 `owner-dm-txn.sh`·`owner-dm-restore.sh`의 롤백 매니페스트도 함께 바꿉다. **배포 절차의 선행 조건 · 심각도: 중(모르고 시작하면 절반만 복구된다)**.
- **무패치 구동 탐지가 healthcheck 에 배선되지 않았다** — 이제 `python3 -m automation.hermes_compat.patch_state` 로 마커 상태를 기계 판정할 수 있고(기능 소개: [hermes-compat 무패치 구동 탐지](기능소개/hermes-compat-무패치-구동-탐지.md)), 매니페스트 notes 가 없는 검사를 있다고 말하던 오류도 제거했다. 다만 아직 사람이 돌려야 돌다 → healthcheck 는 ops 계정으로 도는데 대상은 `~agent/.hermes/hermes-agent` 라 권한 설계가 먼저 필요하다(같은 이유로 mailon 런타임 프로브도 배선이 미완이다). **탐지 공백 · 심각도: 중(지금은 사람이 돌려야 보인다)**.

## 수리 티켓 스윕-2 종결 중 발견한 후속 과제 (2026-08-17)

TRACK-A(PR #125) · TRACK-BC(PR #123) · TRACK-D(PR #129)를 착지시키고 보드·증적을 정리하며 남은 것들. 기능은 [todo 소유자-DM 승인 경로](기능소개/todo-소유자-DM-승인-경로.md) · [승인 게시 복구와 강화 저널](기능소개/승인-게시-복구와-강화-저널.md) · [2-store 메모리 재배치](기능소개/2-store-메모리-재배치.md).

FS3 정정(2026-08-20): 「신규 티켓 3건은 스윕-2가 의도적으로 처리하지 않았다」는 세 티켓이 상위 래퍼 계획에 모두 등록된 `ALREADY-FIXED` 행이므로 열린 불릿에서 제거했다. 재판정은 PR #175, 최종 replay와 보드 정리는 FS3 todo 16 증적에 남긴다.

- **승인 단일성 E2E 재평가 조건이 발동했다(OBSERVE#5)** — 원 OBSERVE 원장의 기준은 “게이트 스키마/파사드 변경 시 재검토”이고, TRACK-BC가 공유층 `automation/interop/approval_lifecycle.py`와 `approval_lease.py`에 enriched journal·probe 복구 분기를 추가해 그 조건을 충족했다. 기존 단위·인터리빙 검사는 green이지만 producer 간 E2E 교차 케이스는 부재한다 → 다음 승인 생명주기 작업에서 새 복구 분기를 포함한 교차 E2E를 복원할지 재판정하고 근거를 원장에 남긴다. **알려진 동작 결함은 없음 · 심각도: 중(공유 승인층 회귀 탐지 범위)**.
- **`docs/guide/gate-ledger-inventory.md`가 이번 변경만큼 낡았다** — 그 문서는 스윕-2 freeze 목록에 있어 이번 사이클에서 손대지 않았다(변경 0 확인). 그 사이 `todo` 승인이 `~/.hermes/todo-approvals/` 아래 approval store·lease·posting-journal 디렉터리를 새로 쓰고, 메모리 재배치의 posting journal 레코드가 3필드에서 5필드(`message_id`·`channel_id` 추가)로 늘었다 → freeze가 풀리면 등록부에 이 경로·권한(0600/0700)과 레코드 스키마를 반영한다. **경로 계약은 코드가 정본이라 동작 영향 없음 · 심각도: 낮음(문서 지연)**.
- **수리 systemd 유닛의 `ExecStart`에 티켓 인자가 없다(현재도 유효한 잠복 관측)** — `autophagy-repair-agent.service`는 `repair_ops_cli.py`를 인자 없이 띄우지만 CLI는 티켓 id 하나를 필수로 요구한다. 현재 유닛은 static/inactive이고 실제 승인 워처는 CLI에 티켓 id를 직접 넘기므로 라이브 장애는 아니다. freeze가 풀려 이 유닛을 활성 경로로 쓸 때 큐 래퍼 또는 `%i` 템플릿으로 인자를 공급하고 회귀 검사를 추가한다. **동결 파일은 읽기만 함 · 심각도: 중(직접 기동 시 즉시 실패)**.
## 수리 스윕 3차·개인 서버 대화 채널 후속 과제 (2026-08-17)

완료 기능은 [개인 서버 대화 채널](기능소개/개인서버-대화-채널.md)과 [기관메일 발신자·전체 폴더·검색](기능소개/기관메일-발신자-전체폴더-검색.md), 완료 수리는 [peer trust-root 진단 분리](patch/2026-08-17-skill-gate-peer-trust-root-diagnostic.md)다.

- **RAG healthcheck의 일시 실패 원인을 귀속할 당시 관측치가 없다 (`t_029a7e08`)** — 같은 tick에서 embedding·Qdrant가 실패하고 5분 뒤 회복했지만 probe별 rc·latency·SSH/HTTP 구분과 당시 서비스 로그가 보존되지 않아 transport·서비스·자원 중 하나를 고를 수 없다 → 비공개 런타임 증적에 probe별 원인·시간을 먼저 남긴 뒤 재현된 원인에만 retry·timeout·서비스 임계값을 적용한다. **현재 서비스 정상·추측성 수정 금지 · 심각도: 중(재발 원인 미확정)**.
- **Discord의 중간 진행·도구 출력 억제는 Hermes vendor 수정이 필요하다 (`t_db6a60e8`)** — repo의 protocol transport는 일반 agent turn 렌더링을 소유하지 않아 동결 경로를 우회해도 해결되지 않는다 → vendor freeze가 풀린 별도 계획에서 gateway/Discord adapter 또는 `hermes_compat` 패치와 회귀를 함께 설계한다. **현재 계획에서는 BLOCKED-freeze · 심각도: 중(대화 표면의 불필요한 내부 상태 노출)**.
- **다섯 cron 래퍼가 폐기된 스킬 경로를 검사해 거짓 장애를 낸다** — budget·report·coordination·calendar·research-trends가 레거시 사용자 홈 경로를 하드코딩해 `not mounted` 또는 import 오류를 내지만 governed live 심링크는 정상이다 → mount 판정을 `automation/skill_mount_drift.py`와 같은 `/srv/autophagy-skills/live/<skill>` 정의로 통일한다. **보안 문제·실제 마운트 손상 아님 · 심각도: 중(주기 작업 실패·오진)**.

## 에이전트 자가 스킬 공존(SS-1) 작업 중 발견한 후속 과제 (2026-08-15)

FS3 K2-A는 이 묶음 중 워크트리 평면 ref와 노드 설정 선진단 행을 정산했다. 초기 원장
해시는 불변이므로 불릿은 최종 보드 정리 단계까지 보존하며, 완료 근거는
`.omo/evidence/fs3/completions/task-9.json`이 소유한다.

자가 스킬 루트 반전과 감사 원장을 만들며 발견했다. 기능은 [소개](기능소개/에이전트-자가-스킬.md).

FS3 정정(2026-08-20): 「`test_healthcheck_checkout_ticket` 환경 의존」은 현재 회귀 7건이 통과하는 `ALREADY-FIXED` 행이므로 열린 불릿에서 제거했다. 재판정은 PR #175, 최종 replay와 보드 정리는 FS3 todo 16 증적에 남긴다.

- **`automation/worktree.sh start`가 `session`이라는 평면 브랜치 하나 때문에 전부 막힌다** — 로컬에 `session` 브랜치가 있으면 git의 D/F 충돌 규칙상 `session/<이름>` 브랜치를 만들 수 없어 모든 세션 시작이 실패하는데, 오류 문구가 그 원인을 말해주지 않아 원인을 찾는 데 시간이 든다 → `start`가 브랜치 생성 전에 네임스페이스 접두사와 같은 이름의 평면 ref가 있는지 확인해 사유와 조치(그 ref 이름 변경)를 함께 내게 한다. **데이터·보안 영향 없음 · 심각도 낮음(세션 시작 마찰)**.
- **Hermes 네이티브 충돌 거부는 우리 토폴로지에서 아예 동작하지 않는다(2026-08-16 정정 · 예방 미구현)** — `_find_skill`이 `rglob("SKILL.md")`로 훑는데 governed 루트가 심링크 팜이라 한 건도 못 본다(`_find_skill("mail")` → `None`). 그래서 자가 스킬이 배포본 이름을 선점해 **승인 게이트를 가릴 수 있다** → 예방은 live 루트를 심링크가 아닌 실디렉터리로 두거나 업스트림에 보고해야 하고, 지금은 `selfskill_audit`의 `SHADOWS-GOVERNED` **탐지**만 있다. **upstream 보고서 작성 완료**: [hermes-find-skill-symlink-blindness.md](troubleshooting/hermes-find-skill-symlink-blindness.md) — 재현 실측과 제안 패치(`os.walk(followlinks=True)` + 심링크 순환 가드)를 담았다. 소유자가 벤더에 전달하면 된다. **두 선택지의 비용을 실측했다(2026-08-16)**: 실디렉터리 전환은 심링크 팜을 만드는 주체가 `skill_store.py`=root NOPASSWD 3종 중 하나인 `/usr/local/libexec/autophagy-install-skill`이라 **가장 특권 있는 경로를 고치는 일**이고, 더 큰 문제는 이 저장소의 배포 판정 자체가 `readlink /srv/autophagy-skills/live/<skill>` 해시(「커밋됨 ≠ 배포됨」)라는 점이다 — 실디렉터리로 바꾸면 그 판정 기전이 사라지고 `skill_mount_drift.py`·`skill_mount_probe.sh`·`selfskill_root_probe.sh`·`deploy-skill.sh`·`land.sh`가 함께 따라온다(원자적 교체도 rename 춤으로 다시 만들어야 한다). 반면 업스트림 보고는 `rglob`→`os.walk(followlinks=True)` 한 줄이고 반영 시점만 남의 손이다 → **탐지로 버티며 업스트림 보고를 먼저 하는 쪽을 권고**하되, 선택은 소유자 판단으로 남긴다. **심각도 높음(가려지면 게이트 우회)**. curator external-write 가드와 curator가 configured-external 스킬을 건드리지 않는 성질은 우리 코드가 아니라 벤더 동작이며, 회귀로 못박을 수단이 없다 → **`hermes update`마다 S4·S5 QA 명령을 재실행**해 두 성질이 유지되는지 확인한다(S4: 배포 스킬 이름으로 생성 지시 후 디렉터리 미생성, S5: `hermes curator archive <배포 스킬>` 거부 + live readlink 불변 + 쓰기 `Permission denied`). **현재 동작 정상 · 심각도 중간(업데이트로 이름 선점 방어가 조용히 약해질 수 있음)**.
- **curator 노브를 기본값 그대로 수용했다** — stale 30일 / archive 90일 / consolidate off는 벤더 기본값이며, 자가 스킬의 실제 사용 주기에 맞는지 근거가 아직 없다 → **첫 감사 리포트 몇 회분을 받아본 뒤** 재검토한다(자주 쓰지 않지만 필요한 스킬이 90일에 걸려 아카이브되면 원장에 `archived` 델타로 보이고 `restore`·`pin`으로 되돌릴 수 있으므로 유실은 아니다). **동작 결함 아님 · 심각도 낮음(정책 조율 미완)**.
- **운영자 워크스테이션에 `~/.hermes/node.toml`이 없으면 배포가 DNS 오류로 죽는다** — 비식별화 이후 `DEPLOY_SSH_HOST`가 예제 플레이스홀더(`example-primary-node`)로 해석돼 `Could not resolve hostname`으로 실패하는데, 실제 원인은 "노드 설정 미구성"이다(이번 샌드박스 검증에서 실측; `DEPLOY_SSH_HOST=<host>`를 명시해 우회했다) → 배포 진입점에서 노드 설정이 예제값그대로인지 확인해 사유와 조치(`~/.hermes/node.toml` 생성)를 먼저 낸다. **브랜치 무관·트렁크 전체 영향 · 심각도 중간(첫 배포 시도가 원인 모를 오류로 막힐다)**.
- **peer의 `~/.hermes/skills/prompt` 잔여물은 배포를 막을 뿐 아니라 지금 배포본을 가리고 있다(2026-08-16 실측 보강)** — 루트 반전 이후 그 경로는 read-only bind가 아니라 peer가 소유한 **1차 루트**이고 1차 루트가 발견에서 이긴다. `/srv/autophagy-skills/live/prompt`가 존재하므로 이것은 위 S4 항목이 말하는 **`SHADOWS-GOVERNED` 조건이 실제로 성립한 상태**다(peer 자가 루트 3건 = 현역 2 + 이 잔여물). `prompt`는 외부효과 스킬이 아니라 프롬프트 자산이라 승인 게이트 우회는 아니고 **peer가 낡은 사본을 쓰는 문제**지만, 다음 감사 리포트에 `SHADOWS-GOVERNED`로 뜨는 것은 오탐이 아니라 설계대로의 탐지다 — 그 사본의 SKILL.md에는 `author: autophagy-agents` 마커가 없어(리포 원본에도 없다) 새 분류기가 `foreign`으로 판정해 fail-closed로 차단한다 — 설계대로의 동작이지만 원인이 오래된 잔여물이다(2026-08-01 배포 잔재, 모드 775) → 소유자가 `sudo -n -u peer rm -rf ~peer/.hermes/skills/prompt` 한 번으로 정리하면 이후엔 자가 치유된다(정리 수리가 이번 PR에 포함). 다른 5종은 이번 검증에서 실제로 정리됐다(봉인된 `coordination` 잔여물 포함). **심각도 낮음(해당 스킬 1종 배포만 지연)**.
- **노드의 `/root/.hermes/node.toml`을 확인할 수 없다 — 자동 재개가 이것에 달려 있다** — 승인 재개 헬퍼는 `env -i HOME=/root`로 파이프라인을 돌리므로(`autophagy-resume-deploy:60`) 노드 설정을 `/root/.hermes/node.toml`에서 읽는다. 그 파일이 없으면 시드 기본값(`peer_attest_mode = signed`)으로 해석돼 `discord` 바인딩 레코드와 어긋나고, 소유자가 ✅를 눌러도 마운트가 조용히 실패한다. 이번 5종은 결국 착지했으나 **오케스트레이터는 root 읽기 권한이 없어 그 파일의 존재를 확인하지 못했다** → 다음 배포 전에 소유자가 존재·내용을 한 번 확인하고, 없으면 워크스테이션과 같은 내용으로 만든다. **현재 배포는 동작 · 심각도 중간(다음 자동 재개가 원인 모르게 멈출 수 있다)**.
- **번들 카탈로그는 제거했지만 재시딩 경로가 완전히 닫힌 것은 아니다** — agent·peer 모두 `hermes skills opt-out --remove`로 정리하고 `~/.hermes/.no-bundled-skills` 마커를 남겼다. 다만 (a) 빈 카테고리 디렉터리와 `.bundled_manifest`는 그대로 남아 있고, (b) 마커를 존중하지 않는 경로(`hermes update`·프로필 재생성 등)가 있으면 다시 시드될 수 있다 → `hermes update` 직후 `hermes skills list`로 builtin 수를 확인한다. 원장 쪽 방어는 이미 있다(`.bundled_manifest` 기준 제외, PR #113). **현재 정상 · 심각도 낮음(재발 시 감사 리포트가 아니라 프롬프트 크기 문제)**.
- **`selfskill_audit/ledger.py`가 249 순수 LOC로 천장(250) 코앞이다** — PR #113에서 이미 `store.py`(신뢰 경계 JSON I/O)로 한 번 쪼갰는데, PR #116의 `removed` 델타 추가로 다시 한 줄 차이까지 왔다. 지금은 규약 위반이 아니지만 **다음 변경이 무엇이든 천장을 넘긴다** → 다음에 이 파일을 열 때 줄을 더하지 말고 분할한다(후보: 스냅샷 수집 `_scan`/`_snapshot` 계열을 `scan.py`로, `_diff`+`Action`을 `delta.py`로). **동작 결함 아님 · 심각도 낮음(다음 작업자가 천장에 부딪혀서야 알게 되는 것이 비용)**.

인벤토리가 짚은 **governed CLI 경로 12건은 이번 PR에서 전부 수정됐다**(후속 과제 아님) — calendar·coordination·mail·meeting·patent-prep 워처와 플러그인, `migrate-cha-wiki.sh`, W3 remote E2E 둘이 모두 불변 live 스토어 기본값으로 옮겨갔고 `tests/unit/test_governed_skill_paths.py`가 그것과 override 우선순위를 고정한다. 반전 전에 그대로 둓다면 승인 워처와 회의록 플러그인이 통째로 멈추었을 곳이다.

증적: `docs/qa/SS-1/reference-inventory.md`(참조 154건 분류 · 위 12개 코드 행의 원문 판정 포함).

## 재개 백오프는 릴리스가 바뀔 때만 앞당겨진다 (2026-08-17 실측)

`retry_due` 는 **기록된 지문의 릴리스 sha ≠ 현재 릴리스 sha** 일 때 백오프를 무시하고 즉시 재시도한다
(`supply_chain_watch.py:88-99`). 설계대로 동작하지만, 운영 중 이것을 모르면 판단을 계속 틀리게 한다.

- **수정을 배포해도 그 틱에 재시도가 돌면 새 지문으로 백오프가 다시 걸린다** — 릴리스가 바뀌면 즉시
  재시도가 돌지만, 그 시점에 노드측 설치본(예: `/usr/local/libexec` 헬퍼)이 아직 옛 것이면 그대로 실패하고
  **현재 릴리스 지문으로 ~58분 백오프가 재무장**된다. 그 뒤로는 릴리스가 또 바뀌기 전까지 아무리 고쳐도
  재시도가 없다. 실측: 06:46 릴리스 수렴 → 재시도 → 구 헬퍼로 실패 → 06:50 `attempt 1, retry in 3474s`.
  즉 **릴리스 랜딩과 노드 재프로비저닝의 순서가 어긋나면 한 사이클(약 1시간)을 통째로 잃는다** →
  헬퍼·유닛 등 노드 설치 자산을 바꾸는 변경은 랜딩 후 **재프로비저닝을 먼저 끝내고** 나서
  릴리스를 한 번 더 움직이거나, 백오프 만료를 기다린다. **동작 결함 아님 · 심각도 낮음(운영 지식)**.

증적: `journalctl -u autophagy-supply-chain-watch`(2026-08-17 06:34 실패 → 06:46 릴리스 변경 →
06:50 백오프 재무장), `automation/supply_chain_watch.py:88-99`.

## 시나리오가 두 개의 환경 계약으로 실행된다 (2026-08-17 실측)

FS3 K2-A는 비동결 `skill_review`·`peer_attest` 실행 환경을 공용 러너로 통일했다. 동결된
`deploy-skill.sh` 내부 구성과 자격증명 격리는 todo 14 조율 범위로 남긴다.

승인이 끝난 `todo` 배포가 `PEER-ATTEST-BLOCK` 으로 마운트되지 못한 사고에서 드러났다. 스킬 쪽 원인은
수정했으나(`skills/todo/scripts/scenario.sh`), 그 사고를 가능하게 한 구조는 그대로 남아 있다.

- **stage 1 과 peer 재검토가 같은 `scenario.sh` 를 다른 환경으로 실행한다** — `deploy-skill.sh` 의 stage 1 은
  `AUTOPHAGY_REPO_ROOT`(릴리스 루트)와 `INTEROP_RUNTIME` 을 함께 주는데, `skill_review._scenario_passes(dir, None)`
  는 `HOME`·`PATH`·`AUTOPHAGY_DEMO_SECRET` 만 주고 같은 스크립트를 재실행한다. 그래서 **"시나리오가 통과한다"의
  정의가 파이프라인 안에 둘** 존재하고, 런타임 루트를 필요로 하는 스킬은 stage 1 은 통과하고 peer 는 실패한다
  (agent 리뷰는 stage 1 이 캡처한 출력을 grep 할 뿐이라 그 불일치를 못 잡는다). `mail` 이 무사했던 것은
  그 시나리오가 루트를 안 쓰기 때문일 뿐이다 → 환경 생성·timeout·`SCENARIO-PASS` 판정을 소유하는 **단일
  러너**를 두고 stage 1 과 peer 가 각각 그것을 독립 호출하게 한다(재실행의 독립성은 유지, 계약만 일치시킴).
  post-mount smoke 가 세 번째 환경 정의를 갖고 있다는 지적도 함께 확인해야 한다. **심각도 중(다음 스킬에서 재발)**.
- **peer 검토는 자격증명을 든 계정에서 untrusted 시나리오를 실행한다** — stage 3 은 peer 의 `.env.secrets` 를
  로드한 뒤 같은 계정에서 시나리오를 돌린다. `env -i` 와 임시 `HOME` 은 파일시스템 접근을 막지 않으므로,
  악의적 시나리오가 실제 peer home 을 역산해 봇 토큰·서명키에 접근할 여지가 있다 → 시나리오 실행을
  credential-free 전용 UID 또는 동등한 mount namespace 로 분리한다. **현재 악용 사례 없음 · 심각도 중**.

증적: PR #139, peer 계정 실측(`scenario` False→True), Oracle 판정(단일 러너 권고).

## 승인된 배포 마운트가 아직 실물로 확인되지 않았다 (2026-08-17)

장벽 3겹(설정 미해석 → 재개 재게시 거부 → 릴리스 `__pycache__` 오염)을 차례로 걷어냈고 전제 조건은
모두 확인했으나, 실제 마운트는 백오프(약 45분) 만료 후에야 일어나므로 세션 안에서 보지 못했다.

- **`todo`·`mail` 마운트가 미확인이다** — 재개 헬퍼의 승인 바인딩 전달(설치본 반영 확인), 리컨실러의
  `PYTHONDONTWRITEBYTECODE`(유닛 반영 확인), 릴리스 `__pycache__` 0개까지 전부 확인했고 백오프도
  `attempt 11 → 1`로 리셋됐다(릴리스 변경으로 실패 지문 갱신). 그러나 `readlink live/todo` 는 여전히
  `aff99eb0…` 다 → 다음 재개 틱 뒤 `readlink /srv/autophagy-skills/live/{todo,mail}` 로 확인하고,
  또 실패하면 `journalctl -u autophagy-supply-chain-watch` 가 다음 장벽을 가리킨다(오늘 세 번 그랬다).
  **소유자 ✅ 2건과 pending 레코드는 보존됨 · 심각도 중**.
- **mail 의 게시↔판독 레이스가 재현되는지 미검증이다** — 증명 게시 1초 뒤 `absent` 판정이 반복되던
  현상은, 재개가 재게시 대신 곧장 검증·마운트로 가게 되면서 그 재증명 루프 자체가 사라졌을 수 있다.
  그러나 실물로 확인하지 않았다 → 위 마운트 확인 시 `REJECTED: valid peer attestation absent` 가
  다시 나오는지 함께 본다. **심각도 낮음(재발 시 별도 수정 필요)**.

증적: PR #132·#133, `journalctl -u autophagy-supply-chain-watch`(2026-08-17 06:11 `RELEASE-STORE-BLOCK`).

## 승인 후 배포 재실행이 그 승인을 무효화한다 (2026-08-16 실측)

FS3 K2-A는 승인된 pending을 nonce 생성 전에 판별해 재개 워처로 양보하도록 정산했다.

승인된 `todo` 배포가 자동 재개 실패로 멈췄을 때, 재개를 앞당기려고 `deploy-skill.sh todo`를 다시 돌렸다가 발견했다.

FS3 정정(2026-08-20): 「증명 TTL 앵커」는 증명 시각 기준 검사가 이미 존재하는 `ALREADY-FIXED` 행이므로 열린 불릿에서 제거했다. 기존 해소 근거 PR #132와 재판정 PR #175는 완료 기록에 보존한다.

증적: 이 세션의 `deploy-skill.sh todo` 재실행 로그와 `automation/skill_gate.py:55-58,509`.

- **재실행이 새 `deploy_nonce`를 만들어 소유자 승인을 쓸 수 없게 만든다** — `skill_gate._REQUEST_BINDING`은 요청 메시지에서 `skill`·`sha256`·**`deploy_nonce`** 셋을 모두 대조하고, 하나라도 현재 실행의 값과 다르면 `_peer_attestation_evidence`가 `None`을 반환한다. 그런데 `deploy-skill.sh`는 실행마다 새 nonce를 만들므로, **이미 ✅를 받은 요청 메시지의 옛 nonce와 영원히 어긋난다**. 겉으로는 `PEER-ATTEST-PASS` 직후 `REJECTED: valid peer attestation absent` → `PEER-ATTESTATION-REFRESH-REQUIRED`가 반복돼 peer 증명 문제처럼 보이지만, 실제 원인은 nonce다(실측: 승인 메시지 nonce `61a5e7de…`, 재실행은 매번 새 값). 즉 **승인을 받은 뒤에는 재개 경로(`supply-chain-watch`)만이 마운트할 수 있고**, 사람이 재실행으로 앞당기려는 시도는 반드시 실패한다 → 재실행 시 pending 레코드에 저장된 nonce를 재사용하거나, 최소한 "이 요청은 이미 승인되어 재개 대기 중이므로 재실행하지 말 것"을 사유로 적시해 즉시 정지한다. **데이터 손상 없음 · 소유자 ✅는 소비되지 않고 보존됨 · 심각도 중간(운영자가 원인을 peer 증명으로 오진하게 만든다)**.

## 릴리스 수렴이 조용히 멈춰 있었다 (2026-08-16 실측)

FS3 K2-A는 `land.sh` 실행비트와 공용 helper-drift 훅을 정산했다. 이 묶음의 다른 관측·수렴
항목은 각 원장 consumer가 계속 소유한다.

자가 스킬 감사 수리를 배포하려다 발견했다. 릴리스가 `f02bfc0e`에 멈춘 채 여러 세션의 머지가 프로덕션에 도달하지 못하고 있었고, **발견은 순전히 우연이었다**.

FS3 정정(2026-08-20): 「`ProtectHome=tmpfs` 설정 경로 무효화」는 선택적 node config bind가 유닛에 반영된 `ALREADY-FIXED` 행이므로 열린 불릿에서 제거했다. 재판정 PR #175와 설치기 정합화 PR #182를 근거로 한다.
- **수렴 불가가 실패로 집계되지 않아 알람이 없다** — `deploy_reconcile_cli.py:254`의 `UPDATE-TRUST-BLOCK`은 `return 0`이라 `consecutive_failures`가 0으로 유지되고 소유자 알림 임계값에 영원히 닿지 않는다. 구조적으로 만족 불가능한 조건(태그 없는 사설 origin + `require_signed_updates=true`)에서는 이 스킵이 **무한 반복**되는데도 상태 파일상 정상이다 → 같은 사유의 스킵이 N회 연속되면 드리프트로 승격해 기존 소유자 알림 경로를 태운다. **심각도 높음(프로덕션이 낡은 채 조용히 방치된다)**.
- **[해소 2026-08-19, 이 세션] 이 정지의 진짜 기전은 `rollback-pending` 지문 교착이었다 — `UPDATE-TRUST-BLOCK` 이 아니다** — `/srv/autophagy-private/deploy-reconcile/failed-release.json` 이 08-16 의 실패(`failed_sha=8a36d3dd`·`prior_sha=f02bfc0e`·`reason=gateway-restart`)를 `rollback-pending` 으로 든 채 남았고, `apply_release_update` 의 첫 분기가 그것을 보고 `_rollback` 으로 직행해 **새 sha 를 한 번도 보지 않았다**. 그 사이 사람이 `land.sh` 로 릴리스를 올려 라이브 포인터가 두 sha 어느 쪽도 아니게 되자 `pointer_restored` 가 영원히 거짓이 되어 `phase` 가 매 틱 `rollback-pending` 으로 되쓰였다. 실측(08-19 13:00 UTC): 틱이 3초 만에 끝나고 `converge()` 호출 0회 — 노드 값 그대로 로컬 재현해 `rc=6`·`converge_calls=0`·상태 파일 동일 형상을 확인했다. 즉 자동 수렴은 **08-16 13:11Z 부터 3일간 0건**이었고 그동안의 릴리스 전진은 전부 사람 손이었다. `_stranded`/`_settle_stranded` 게이트로 해소(회귀 2건 RED→GREEN 고정).
- **`UPDATE-TRUST-BLOCK` 항목(윗줄)은 이 사건의 원인이 아니었지만 그대로 유효하다** — 이 노드는 `/etc/autophagy/node.toml` 이 `require_signed_updates = false` 라 그 경로를 타지 않는다(그래서 08-16 오후부터 저널에서 사라졌다). 서명 모드를 켠 설치에서는 여전히 무한 스킵이 알람 없이 반복되므로 조치는 그대로 남긴다.
- **[해소 2026-08-19] 유닛 샌드박스가 `/run/user` 까지 덮어 `autophagy-gateway-pair` 가 매번 실패했다 — 모든 수렴이 빌드 직후 롤백됐다** — 지문을 치우자 08-19 13:24:25 틱이 실제로 수렴을 시도해 릴리스 `a9c8c7d2` 를 7초에 빌드·활성화했으나, 이어진 `gateway-pair restart` 가 **1초 미만에 비영으로 끝나** `install-release rollback` 으로 `b970c630` 로 되돌아갔다. 같은 헬퍼를 로그인 세션에서 돌리면 `active`/`active` rc=0 이라 변수는 유닛의 마운트 네임스페이스 하나뿐이었다. **원인은 `/home` 이 아니라 `/run/user` 였다** — systemd.exec(5) 는 `ProtectHome=` 이 "the directories /home/, /root, and **/run/user**" 를 빈 채로 덮는다고 명시하며(노드 systemd 255 man 페이지 직접 확인), 그곳이 바로 `systemctl --user` 가 물어야 할 `$XDG_RUNTIME_DIR/systemd/private` 가 있는 자리다 — 유닛 자신의 주석도 이미 그렇게 적어 두고 `/home/ops/.ssh` 하나만 되돌려 놓고 있었다. 조치: 유닛에 `BindPaths=/run/user`(read-write — unix 소켓 connect 는 파일시스템 읽기가 아니라 read-only 되노출로는 성립하지 않는다; 계정별 `/run/user/<uid>` 는 여전히 0700 이라 실제 재시작을 수행하는 root 헬퍼가 이미 닿던 것 외에 열리는 것은 없다) + `_run_release_command` 가 실패 시 헬퍼 stderr 를 저널에 남기도록 수정(3일간 원인이 안 보인 직접 원인이 `capture_output=True` 로 삼킨 stderr 였다). 회귀 고정 `tests/unit/test_deploy_reconcile_cli.py`. **노드 반영은 유닛 파일 설치라 root 가 필요하다** — 머지 후 `sudo bash /srv/autophagy-agent-current/automation/provision-deploy-reconcile.sh`(멱등) 재실행.
- **[해소 2026-08-19] 게이트웨이를 고치자 다음 단계가 드러났다 — 수렴 직후 스모크가 **구조적으로 통과 불가능**했다** — 15:03 틱이 처음으로 진짜 수렴을 시도해 `converge` → `gateway-pair restart` 까지 통과했으나 `deploy-smoke.sh` 에서 `sudo: a password is required` → `SELF-SKILL-COLLISION-BLOCK` 으로 죽고 롤백됐다. 원인: `apply_release_update` 의 `smoke_script` 가 **다른 용도의 스크립트**를 가리키고 있었다 — `automation/deploy-smoke.sh` 는 자체 프로비저너·자체 일일 타이머를 가진 독립 위생 스모크이고, 하는 일은 `deploy-skill.sh hello-autophagy --sandbox-only`(=peer 계정에 실제 staging)다. 리컨실러는 `User=ops` 로 도는데 ops 의 sudo 권한은 릴리스 헬퍼 5줄이 전부라 agent 로도 peer 로도 갈 수 없다 — 즉 이 스모크는 처음부터 통과할 수 없었고, 그 증거로 ops 의 `~/.hermes/deploy-smoke/tick.json` 은 **한 번도 생성된 적이 없다**. 조치: 두 스모크를 합치지 않고(목적도 권한도 다르다) 리컨실러 전용 `automation/release-smoke.sh` 를 신설해 ops 권한으로 답할 수 있는 것만 묻는다 — ① 스토어가 포인터를 인정하는가 ② 릴리스 트리가 온전한가(`ast.parse` — import 하면 모듈 레벨 부작용까지 실행된다) ③ 게이트웨이 쌍이 restart 가 아니라 **health** 로 돌아왔는가(성공 경로는 지금까지 재시작 rc 만 보고 health 를 확인하지 않았다). 노드 실측: 신규 스모크 ops 실행 rc=0(`store agrees, tree imports, gateway pair healthy`), 구 스모크 동일 계정 실패. 일일 sandbox 스모크와 그 미설치 타이머는 그대로 둔다.
- **`automation/deploy_reconcile_cli.py` 가 250 pure-LOC 상한에 정확히 닿았다** — 위 stderr 수정을 넣으면서 250/250 이 됐다(그래서 근거를 docstring 이 아닌 `#` 주석으로 옮겨 새 예외 등록을 피했다 — pure-LOC 는 주석만 제외하고 docstring 은 센다). 다음 변경은 무엇이든 분할 또는 등록을 강요받는다 → 자연스러운 경계는 세 덩어리다(업데이트 타겟 해석·특권 헬퍼 실행·미러 동기화) — 헬퍼 실행부부터 모은다. **동작 영향 없음 · 심각도: 낮음(다음 사람이 작은 수정을 하려다 막힌다)**.
- **끝내 완료되지 않는 롤백은 소유자에게 한 번만 알린다** — `reconcile_tick` 의 `rc == FAILED_RELEASE_RC` 분기는 `deliver()` 를 부르지 않고 `notified_target` 만 전진시키며 `pending_notice` 를 지우고, `_rollback` 안의 `_deliver_once` 도 `notice_sent=true` 면 no-op 이다. 그래서 위 두 정지 모두 **DM 0건**이었다(실측: 08-16 이후 드리프트 통지 없음, 소유자는 우연히 물어봐서 알았다). 조치: 완료되지 않은 롤백이 N틱 지속되면 기존 소유자 알림 경로를 태운다 — `FAILURE_NOTICE_THRESHOLD` 패턴 재사용. **탐지 공백 · 심각도: 중(정지 지속 시간이 소유자가 묻는 시점에 좌우된다)**.
- **`automation/land.sh`에 실행 권한이 없다** — `automation/land.sh`로 직접 실행하면 `Permission denied`이고 `bash automation/land.sh`로만 돈다(2026-08-16 실측). 문서와 AGENTS.md는 전자를 안내한다 → git 모드 비트(`chmod +x`) 커밋. **심각도 낮음(우회 가능하나 첫 사용자가 원인 모를 오류를 만난다)**.

- **특권 릴리스 설치기는 릴리스가 바뀌어도 스스로 따라오지 않는다(2026-08-17 실측)** — `/usr/local/libexec/autophagy-install-release`는 `automation/release_store.py`의 **사본**이고, 그 사본을 갱신하는 것은 root가 손으로 돌리는 `automation/provision-release-store.sh`뿐이다. 실측: 설치본 `a9655e67`(08-04) vs 릴리스 소스 `7e61cf06` — 그 사이 두 커밋(`a5076e90 feat(release): add atomic failed-release rollback`, `d28fe503`)이 빠진 채 **원자적 실패-롤백 없는 설치기**가 2주간 모든 릴리스를 설치했다. 프로브는 정확히 탐지하지만(`release_helper_probe.sh`, healthcheck FAIL) 수렴 주체가 없고, 지금은 그 탐지가 티켓조차 되지 못한다(같은 문서의 rc=126 항목) → 랜딩 출력에 helper-drift 확인을 엮거나 릴리스 런북에 재프로비저닝 단계를 명시한다. 재프로비저닝 자체는 안전하다(소스 확인: `install -d` no-op·`install -m` 단일 경로 교체·`visudo -cf` 선검증·`systemctl` 호출 0건). **동작은 정상이나 안전장치 결손 · 심각도 중**.
- **[해소 2026-08-19] 그 프로브가 보던 것은 단 한 파일이었다** — 윈줄이 "프로브는 정확히 탐지한다"고 적었으나, 실제 `probe_release_helper_drift` 는 `autophagy-install-release` 와 `release_provenance.py` **둘만** 비교했다. 같은 부류의 root 자산인 `/etc/systemd/system/autophagy-deploy-reconcile.{service,timer}` 와 `/usr/local/libexec/autophagy-gateway-pair` 는 아무도 보지 않았고, 그중 유닛 하나가 빠진 `BindPaths=/run/user` 때문에 프로덕션이 3일간 얼었다 — 그동안 헬스체크는 내내 PASS 였다(구 프로브를 드리프트된 유닛에 직접 돌려 rc=0 으로 재현 확인). 조치: 프로브가 프로비저너가 설치하는 자산 집합을 따라가게 확장했고(템플릿은 `node_asset_renderer` 로 렌더 후 비교 — 원문 비교는 모든 노드를 상시 red 로 만든다), 실패 시 재프로비저닝 명령을 함께 출력한다. `tests/unit/test_release_helper_probe.py` 가 "프로비저너가 설치하는 것을 프로브가 본다"를 기계적으로 고정한다. **sudoers 는 의도적 제외** — 0440 root:root 라 cron 계정이 읽을 수 없고, 프로비저너가 설치 시점에 `sudo -n -l -U` 로 실효성을 이미 증명한다.

- **공개 릴리스 번호를 사람이 고르는 한 두 태그 계열은 랜딩마다 다시 갈라진다(2026-08-17 실측)** — `land.sh`의 `next_release_tag`는 **private origin**의 태그를 읽어 랜딩마다 자동 증분하는데(그래서 오늘 v1.0.17), `public_export.sh`는 `--version`을 사람에게서 받는다. 실측: 공개 `cytoplasm`은 v1.0.1에 멈춰 있었고 private는 이미 v1.0.16이었다 — 같은 코드가 채널마다 다른 이름을 갖고 있었다. 이번 컷은 공개도 v1.0.17로 맞춰 같은 소스 커밋(`36180eb3`)이 두 채널에서 같은 번호를 갖게 했지만, **다음 랜딩이 private v1.0.18을 컷하는 순간 다시 갈라진다**. 진짜 비용은 이름 혼동이 아니라 롤백 방지 floor다 — 유지보수자 노드를 private에서 공개 채널로 옮기면 floor(지금 1.0.17)보다 낮은 공개 태그는 `RELEASE-ROLLBACK`으로 영구 거부되고 태그를 지워도 풀리지 않는다 → 공개 컷의 버전을 `released_tag_at <sha>`처럼 **그 커밋에 이미 붙은 태그에서 유도**하거나, 두 계열이 독립임을 매뉴얼에 못 박고 채널 전환 절차에 floor 확인을 넣는다. **현재 동작 정상 · 심각도 중(채널 전환 한 번으로 노드가 영구 정지)**.
- **매뉴얼이 유지보수자 자신의 노드가 어느 채널로 업데이트되는지 말하지 않는다(2026-08-17)** — `manual-maintainer.md` §0의 두 저장소 표는 "노드의 `origin`: private=아님 / public=맞음"이라고만 적는데, 실제로 이 노드의 `origin`은 **private repo**이고 그래서 private에 v1.0.1~v1.0.17 서명 태그 계열이 존재한다. 표만 읽으면 그 태그들이 D8이 금지한 "private에서 먼저 서명한 릴리스 태그"로 오해된다(실제로는 `land.sh:ensure_signed_tag`가 만드는 별개의 노드 업데이트 채널이다) → §0 표에 유지보수자 노드가 private 채널을 쓴다는 사실과 그 태그 계열의 출처를 한 줄로 명시한다. **문서 지연 · 심각도 낮음(오해하면 정상 태그를 사고로 오진한다)**.

증적: PR #116·#119, `docs/qa/SS-1/` 및 이 문단의 실측 인용.


## 그룹 발행 공지(W-F3-C) 작업 중 발견한 후속 과제 (2026-08-15)

F4 해소 기록(2026-08-21): 복구 서브커맨드는 구현됐다. 남은 owner action은 라이브 메시지가
`delivered`인지 `not-delivered`인지 판정하는 것뿐이며 OWNER-37이 그 범위만 소유한다.

- **공지 전송이 애매하게 실패하면 그 릴리스의 공지가 사람 손 없이는 영영 막힌다** — `announce_ledger`는
  `PostingJournal` 예약을 일부러 남겨 다음 실행을 `POSTING_JOURNAL_STALE`로 거부한다(실패한
  send가 실제로는 도착했을 수 있어 재시도가 이중 게시가 되기 때문 — 의도된 fail-closed).
  그런데 예약을 감사와 함께 해제하는 공용 탈출구(`approval_lease.abandon`)을 announce 쪽에서
  부를 CLI verb가 없다 → 운영자가 `~/.hermes/managed-skills/announce/*.posting.json`을 손으로
  지우는 대신 그 탈출구를 부르는 서브커맨드를 추가한다. **보안·발행 결함 아님 — 공지는
  알림이고 발행은 그대로 성공한다 · 심각도 낮음(운영 절차 공백)**.
- **`AUTOPHAGY_ROSTER` 경로 해석이 두 곳에 사본으로 있다** — 이번에 `group_roster/parser.py`에
  `roster_path()`를 두었고, `managed_sync/cli.py`에도 동일한 `ROSTER_ENV`/`DEFAULT_ROSTER_PATH`/
  `roster_path()`가 그대로 있다 → `managed_sync` 쪽을 `group_roster`의 것으로 수렴시킨다.
  당시 그 파일은 병렬 W-F2-D 세션의 작업 영역이라 diff를 만들지 않았다. **동작 차이 없음
  — 두 구현이 바이트 동일 · 심각도 낮음(중복 정의가 나중에 갈라질 위험)**.

## 메모리 승격 확인 종결 H4 — 라이브 14건은 OWNER 인계 (2026-08-05)

- **코드와 dry-run 계약만 완성됐고 라이브 14건은 의도적으로 건드리지 않았다** → PR 머지와 배포 뒤 OWNER가 `closure_cli --dry-run`의 `CLOSE`·`UNBOUND`·`ORPHAN` 원장을 검토한 다음 기존 큐레이터 tick으로 정리한다. **동작·보안 결함 아님 · 심각도 중(운영 인계)** — 이 작업에서 실 Discord 편집·실 배포·라이브 정리는 금지 범위다.
- **`abandoned`는 ⛔ 시점에 saved 초안이 unlink되어 교차 바인딩이 소멸한다** → 현재는 항상 `UNBOUND`로 열거하고 편집·archive 0회를 보장한다. 미래 취소 경로가 삭제 전 archive하도록 바꾸는 일은 kanban-routing 소유 계획과 조율한다. **안전 우선 fail-closed · 심각도 낮음(정리 자동화 한계)**.

증적: `.omo/evidence/fs2/task-4-parallel-followup-sweep-2.txt`

## 자동 배포가 한 번 실패하면 그 스킬만 조용히 빠진다 (2026-08-04 실측 · 해소)

소유자 ✅ → 자동 마운트 계약은 실제로 동작한다(coordination·wiki가 `done (owner-approved)`로 완주). 그러나 같은 날 나머지 3건은 승인이 있었는데도 마운트되지 않았다. **두 결함 모두 PR #55로 해소**했고(`4be42d0`·`070eafd`, 릴리스 `5cad5e47`로 수렴 확인), 남은 것은 아래 잔여 두 줄이다. 증적 `docs/qa/SCW-1/summary.txt`.

FS3 정정(2026-08-20): 「사라진 요청의 `tick.json` 억제 레코드」는 live key 차집합 정리가 구현된 `ALREADY-FIXED` 행이므로 열린 불릿에서 제거했다. 재판정 PR #175와 반복 수렴 정합화 PR #177을 근거로 한다.

- **[해소] 재개 실패 뒤 유예가 원인 해소 후에도 풀리지 않는다** — 11:05 tick에서 `budget·calendar·mail`이 아카이브 결함으로 `failed (resume-exit:5)`를 냈고, 결함을 고쳐 릴리스가 바뀐 뒤에도 여덟 tick(11:07~11:29)이 그 세 건을 **로그에 언급조차 하지 않았다**. 결국 사람이 `deploy-skill.sh <skill>`을 직접 돌려 마운트를 끝냈다. **안전 문제 아니었고**(fail-closed로 멈췄을 뿐) 심각도는 중이었다 — “✅ 한 번이면 끝”이라는 계약이 조용히 깨졌다.
  **앞서 적어둔 조치안은 틀렸다** — “유예 지문에 릴리스 sha를 포함시키라”고 적었으나 지문은 이미 `f"{release_sha}:{reason}"`였다(`supply_chain_watch.py:80`). 진짜 결함은 **자격 판정이 그 지문을 한 번도 읽지 않은 것**이었다 — `eligible`이 오직 시계만 봤고(`now >= next_attempt_at`), `exit 5`는 transient가 아니라 3600초 고정 유예를 받아 정확히 한 시간의 침묵이 됐다. → `retry_due`(`4be42d0`)가 지문의 릴리스를 현재 릴리스와 정확 대조해, 다르면 시계를 기다리지 않고 재시도한다. 반사실 검증(사고 레코드 실물로, tick @ 11:24:50Z): 배포 전 `eligible=False`(정지) → 배포 후 `eligible=True`(즉시 재개), 3건 전수.
- **[해소] 유예 중인 요청이 로그에서 사라진다** — `main()`의 결과 루프가 `backoff`를 맨손 `continue`로 넘겨 한 줄도 남기지 않았다. journalctl에서 “기다리는 중”이 아니라 “사라졌다”로 읽힌다. → `070eafd`가 매 tick `[supply-chain-watch] <key> backoff (attempt N, retry in Ns)` 한 줄을 남긴다. `continue`는 그대로 둔다 — 흘려보내면 아래 `else`의 `pop()`이 억제 레코드를 지워 매 tick이 새 재시도가 된다.
- **잔여 — 소유자에게 밀어주는 통지는 여전히 없다** — 위 두 수정은 정지를 자가치유시키고(릴리스 교체 시) 기다림을 보이게 하지만, 둘 다 노드 로컬 journal에만 남는다. 즉 같은 릴리스에서 지속적으로 실패하는 경우의 통지는 「수리 티켓 경로」 묶음의 PATH 결함에 그대로 종속된다. **심각도 낮음으로 하향**(종전 “중”) — 사고의 실제 침묵 모드는 사라졌고 남은 것은 push 통지뿐이다. 조치: 그 묶음의 PATH 결함을 고치면 이 부류가 자동으로 티켓화된다. **`tick.json`을 읽는 healthcheck 프로브는 검토 후 채택하지 않았다** — healthcheck는 ops crontab에서 돌고 로컬 프로브에는 ssh·sudo가 없으며(rc=126), `tick.json`은 agent 소유 0600이라 소유자 프로비저닝(allowlist sha256 또는 sudoers)이 필요해 “최소 코드 수정”을 넘고, 이미 그 묶음이 담당하는 통지 경로와 병렬 구조가 된다(「병렬 confirm 구조 신설 금지」).
## 스킬 배포 파이프라인이 3겹으로 막혀 있었다 (2026-08-04 실측 · 전부 해소)

FS3 K2-A는 기존 `release_helper_probe.sh`의 실설치 자산 검사를 랜딩 출력에도 재사용했다.
금지된 `deploy-skill.sh` 실행 없이 저장소 회귀와 읽기 전용 프로브 계약만 검증했다.

위 「마운트된 스킬 5종…」의 **근본 원인**이다. 재배포를 실제로 시도해서야 드러났다 — 마운트가 낡은 것이 아니라 **마운트할 수가 없었다**. 하나를 풀자 다음 것이 드러나길 세 번 반복했고, 세 개 모두 **이번 스윕(2026-08-03)이 직접 만들었거나 드러낸** 것이다. 실제 재배포 없이는 세 겹 중 어느 것도 보이지 않았다.

- **[해소] ① `deploy-skill.sh`가 stage 1에서 죽었다 — `release-store: error: unrecognized arguments: --git-root`** — 노드의 root 헬퍼 `/usr/local/libexec/autophagy-install-release`가 **2026-07-31 13:37 버전**에서 멈춰 있었고(`--git-root` 문자열 0건), 옆에 있어야 할 `release_provenance.py`도 설치되지 않았다. 반면 `converge-release-runtime.sh`는 `install … --git-root <MIRROR>`를 넘긴다. 즉 G3(PR #43)가 설치시점 provenance 검증을 넣으면서 헬퍼 재설치가 같이 가지 않았다. 소유자가 `sudo bash /srv/autophagy-agent-current/automation/provision-release-store.sh`를 실행해 해소(검증: `git-root` 0→3, `release_provenance.py` 설치됨, 이후 릴리스 수렴 성공).
- **설치본 특권 헬퍼의 드리프트를 감지하는 것이 없다** — 이전에도 같은 부류가 기록됐으나(`PYTHONDONTWRITEBYTECODE=1` 누락, 「조치 불요」로 종결) 이번엔 **인자 불일치로 배포 자체가 죽었다** — “무해한 드리프트”라는 앞서의 판단이 일반화될 수 없음을 보인다. **심각도 중**. 조치: 헬퍼 해시를 릴리스 원본과 대조하는 healthcheck 프로브를 두거나, 릴리스 수렴 직후 provisioner를 멱등 실행해 항상 같은 버전이 되게 한다.
- **[해소] ② `hermes skills list` 판정이 원리적으로 불가능해졌다** — `a824c4f`(2026-08-03 22:42)가 `managed-X`↔`X` 오탄을 막으려 `grep -Fq`를 `-Fxq`(줄 전체 일치)로 바꿈. 그러나 `hermes skills list`는 테두리 테이블을 출력하므로 줄 전체가 이름과 같은 행은 존재할 수 없다 — **설치돼 목록에 보이는 스킬조차 exit 1**이었다(실측). 모든 배포가 SANDBOX에서 죽었다. 이름 셀(왼쪽 테두리+이름+패딩)을 매칭해 `a824c4f`의 의도를 지키면서 복구했다(`ba63235`). **기존 테스트가 소스에 `grep -Fxq`가 있는지만 검사해 이 회귀를 원리적으로 잡을 수 없었다** — 실제 렌더 출력에 대해 검증하도록 함께 고쳤다. 남는 한계: 컬럼 폭보다 긴 이름은 `…`로 잘려 매칭되지 않는다(현재 배포 스킬 중 해당 없음, 미스는 fail-closed로 배포를 막음).
- **[해소] ③ 직전 배포본을 지울 수 없었다** — E9가 릴리스를 0555/0444로 봉인하고 tar가 그 모드를 보존하므로, 직전 배포가 남긴 사본이 쓰기 불가 상태로 놀인다. 쓰기 권한 없는 디렉터리는 **소유자도** 그 안의 항목을 지울 수 없어 `rm -rf`가 Permission denied로 죽었다 — peer 샌드박스와 agent review-staging 양쪽에서. `rm` 직전 `chmod -R u+w`로 복구했다(`efe1767`, 실제 0555 트리를 만들어 RED→GREEN 고정).
- **세 겹 모두 “실제로 돌려보기 전에는” 보이지 않았다** — 유닛 3112건·ruff·보드 conformance가 전부 green이었고 릴리스도 최신이었다. 공통 성질은 **노드 설치본·외부 CLI 출력·파일 모드처럼 레포 밖에 사는 상태**에 의존한다는 점이다. **심각도 중** — 조치: 배포 경로에 주기적인 스모크(예: 하루 1회 `--sandbox-only` 드라이런)를 두어, 다음 실배포가 아니라 그 드라이런이 먼저 깨지게 한다.

## 처리 끝난 승인 메시지가 소유자 DM에 쌓인다 (2026-08-04 실측)

소유자가 “1개만 승인했는데 나머지는 안 보인다”고 보고해 조사한 결과다.

- **메모리 승격 확인 메시지 14건이 전부 살아있다 — 이미 처리된 것도 지워지지 않는다** — `wiki-gate/drafts`에 `memory-promoted-*` 초안이 14건(전부 `status=saved`), 대응 Discord 메시지도 14건 전부 ALIVE이며 그중 10건에는 **소유자 ✅가 이미 달려 있다**. 반면 `memory-curator/state.json`의 승격 레코드는 10건이고 상태는 `reconciled` 6 · `abandoned` 4로 **파이프라인은 정상 종결됐다**. 즉 결정은 소비됐는데 그 사실을 알리는 메시지가 그대로 남아, 소유자 DM이 “대기 중인 승인”처럼 보인다. **동작·보안 결함 아님**(안전 불변식은 지켜졌고 `USER.md`는 1086자로 이미 회수 반영된 값) **· 심각도 중(UX·관측성)** — 소유자가 무엇을 더 눌러야 하는지 알 수 없게 된다. 조치: `reconciled`·`abandoned`로 종결된 승격의 확인 메시지를 삭제하거나 처리됨 표시로 바꾸는 종결 단계를 두고, 초안 레코드(`saved`)도 함께 회수한다. 삭제는 외부효과라 소유자 승인 경로를 거친다.
- **초안 14건 vs 승격 레코드 10건의 불일치** — 4건은 `state.json`에 대응 레코드가 없다(조회 시 반응 사용자 목록도 비어 있었다). 레거시 또는 중단된 시도의 잔재로 보인다. **심각도 낮음** — fail-closed라 임의 실행은 없다. 조치: 위 종결 단계를 만들 때 고아 초안 판별을 함께 넣는다.

## 마운트된 스킬 5종이 릴리스보다 낡았다 — 머지된 수정이 미발효 (2026-08-04 실측)

OWNER 체크리스트 9번·F4 판정 중 발견. `skill_mount_drift.py`를 라이브에 직접 돌렸다.

- **`budget`·`calendar`·`coordination`·`mail`·`wiki` 다섯 종 전부 `SKILL-STALE`이다** — 릴리스와 마운트 digest가 전면 불일치한다(예: mail 릴리스 `019a405f…` vs 마운트 `091ddd36…`). 즉 **코드에는 있지만 프로덕션에서 도지 않는 수정이 다섯 스킬에 걸쳐 쌓여 있다** — 429 백오프(PR #31·#32), G2 승인 판정 단일화(PR #50), G5 다이제스트 JSON 계약 하드닝(PR #47)이 여기 해당한다. **동작·보안 결함은 아니다**(구버전이 정상 동작 중이고 게이트는 fail-closed) **· 심각도 중** — 고쳐놓은 결함이 계속 재발한다는 뜻이다. 조치: `automation/deploy-skill.sh`로 다섯 종을 재배포한다(스킬당 소유자 ✅ 1회, 총 5회). 배포 뒤 `readlink /srv/autophagy-skills/live/<skill>`이 릴리스 digest와 같아지는지로 판정한다.
- **이 드리프트를 상시 감시하는 경로가 사실상 무력화돼 있다** — `healthcheck.sh`의 `skill_mounts_current` 프로브가 이것을 보고 있지만, 실패해도 수리 티켓은 위 「수리 티켓 경로」 묶음의 PATH 결함으로 생성되지 않고, 알림도 노드 로컬 로그에만 남는다. **심각도 중(관측성)** — 두 결함이 겹쳐 “드리프트가 나도 아무도 모른다”가 된다. 조치: PATH 결함을 먼저 고치면 이 부류의 발견이 자동화된다(그 묶음에 종속).

## 수리 티켓 경로 — allowlist 뒤에 PATH 결함이 숨어 있었다 (2026-08-04)

OWNER 체크리스트 2번(`report_repair` allowlist 등록) 처리 중 발견. allowlist는 등록도론 rc=126은 해소됐고([소개](기능소개/배포-체크아웃-지연-감지.md) 「잔여」 항목), 그 뒤에 가려 있던 다음 결함이 드러났다.

- **등록해야 할 해시는 1개가 아니라 13개다** — `healthcheck.sh:133`이 `check_name`을 명령 문자열에 직접 삽입하므로 체크마다 해시가 달라진다. `LIVE_CHECKS` 12개 + `--synthetic-failure` 1개를 전부 등록했다(기존 7건 유지, 회귀 0 · 미지 해시 차단 검증 완료). **구조적 취약점이 남는다**: `LIVE_CHECKS`의 체크 이름을 바꾸거나 항목을 추가하면 해당 해시가 즉시 무효해져 그 체크만 조용히 티켓을 못 만든다. **심각도 중**(관측성 손실, 동작·보안 무관). 조치: allowlist를 명령 전문이 아닌 **고정 래퍼 + 인자 검증** 형태로 바꿔 `check_name`이 해시에서 빠지게 하거나, `LIVE_CHECKS` 변경 시 allowlist 갱신을 강제하는 회귀 테스트를 둔다.
- **allowlist를 통과해도 티켓은 여전히 안 생긴다 — `hermes`가 PATH에 없다** — 실측: 게이트 통과 후 `repair error: [Errno 2] No such file or directory: 'hermes'`(rc=1). 원인은 `healthcheck.sh:133`의 `sudo -n -u agent -H`가 로그인 셸을 거치지 않아 sudo secure_path만 받기 때문이다 — `hermes`는 `/home/agent/.local/bin/hermes`에 있고 그 디렉터리는 secure_path에 없다(agent 로그인 셸 PATH에는 있음). 즉 **자동 수리 티켓은 여전히 0건**이며, 드리프트를 *감지*해도 *티켓*은 안 된다는 상태는 그대로다(증상만 rc=126→rc=1로 바뀌었다). **동작·보안 무관 · 심각도 중**(관측성). 조치: `healthcheck.sh:133`의 명령을 `sudo -n -u agent -H env PATH=/home/agent/.local/bin:<secure_path> python3 -I ...`로 바꾸되, **명령이 바뀌면 해시 13개가 전부 무효해지므로** 신·구 해시를 함께 등록해 반영 사이의 회귀 창을 없앱 뒤 구 해시를 제거한다(미러 ff-pull은 리컨실러가 2분 주기로 따라오므로 창이 실재한다).

## 배포 미러 임시 워크트리 누수 — trap 정리 실패 (2026-08-04 실측)

OWNER 체크리스트 1번(`.git` 권한 회수) 조사 중 발견. 권한 자체는 해소됐다(2775→2755, 그룹쓰기 1629개→0, agent 소유 356개→0).

- **`origin_snapshot.sh`의 스냅샷 워크트리가 trap 정리를 빠져나간다** — DG-2가 배포마다 만드는 `/tmp/autophagy-snapshot.*/tree`가 4개 남아 미러 `.git/worktrees`에 등록된 채 56MB를 점유하고 있었다(전부 dirty 0 · 미착지 0 · origin/main 조상으로 확인 후 정리함). **동작·보안 무관 · 심각도 낮음** — 용량과 등록 목록 오염뿐이다. 조치: `origin_snapshot.sh`의 trap이 어떤 경로에서 안 도는지(조기 exit·신호·SSH 끊김) 확인해 정리를 보장하거나, 배포 진입 시 오래된 스냅샷을 먼저 prune한다.
- **수리 워크트리 1건이 스테이징된 미커밋 변경 12건과 함께 남아 있다** — `/tmp/t_6f29fb9b-review`(브랜치 `fix/memory-relocate-single-approval-t_6f29fb9b`, HEAD `1c3c33e`). HEAD는 origin/main 조상이고 **미착지 커밋은 0건**이라 손실된 커밋은 없지만, 인덱스에 `memory_relocate` 계열 12파일이 스테이징된 채 남아 그 내용이 현재 origin/main과 다르다(같은 주제의 수리는 `docs/patch/2026-08-02-memory-relocate-approval-channel-binding.md`로 이미 반영됨 — 이 인덱스는 그 시점의 중간 스냅샷으로 보인다). **동작·보안 무관 · 심각도 낮음**(15MB 점유). 「다른 세션의 미커밋 작업은 되돌리지 않는다」에 따라 **의도적으로 보존했다**. 조치: 소유자가 인덱스 diff를 확인해 버릴지 판단하고, 버린다면 `git diff --cached HEAD`를 패치로 뽑아 `/srv/autophagy-private/`에 보관한 뒤 제거한다.
- **수리 자동화가 미러에 남긴 로컬 브랜치 다수** — `task/*` 3건·`kanban/*` 8건·`wt/*` 2건·`fix/*` 1건·`repair/*` 1건이 미러의 로컬 ref로 남아 있다(전부 미착지 0건 확인). **동작 무관 · 심각도 낮음**. `backup/pre-*`·`pre-realign-*`는 정렬 사고 대비로 **의도적으로 남긴 것**이므로 제외한다. 조치: 수리 라이프사이클이 종료 브랜치를 정리하도록 하거나, 주기적 정리 기준을 정한다.

## G3 — 릴리스 스토어

기능은 [소개](기능소개/릴리스-리텐션-검증.md). keep-last-5와 설치 전 blob 검증, 세대·용량
healthcheck까지 구현했다.

- **용량 경계가 절대값 1 GiB다** — 세대 수는 retention 계약으로 제한되지만 실제 파일 크기와 `/srv` 여유 공간은 노드마다 달라 절대값만으로는 작은 디스크의 압박을 늦게 알릴 수 있다. **동작·보안 문제 아님·심각도 낮음** — 현재는 총 바이트와 세대 수가 매 healthcheck 로그에 남고 상한 초과는 실패한다. → 실제 운영 수치를 축적한 뒤 free-space 비율 경보를 추가할지, `RELEASE_STORE_MAX_BYTES` 기본값을 조정할지 결정한다. 이번 작업은 실제 `/srv` 실행 금지라 로컬 기본값만 검증했다.

## 에이전트가 배포 미러의 git 내부를 쓸 수 있다 (2026-08-03 실측)

cha가 보고한 "Hermes가 테스트용 코드를 만들고 커밋하면 워크플로우 전체가 망가진다"의 물리적 원인이다.

- **`/srv/autophagy-agents/.git`과 `.git/refs`가 `2775`(그룹 쓰기 가능)이고 `agent`가 `autophagy` 그룹이다** — 워킹트리 쪽도 `AGENTS.md`·`automation/`·`prompts/`가 그룹 쓰기 가능하다. 실측: 미러에 **agent 소유 파일 1108개**(`.git/objects` 295 + `tests/unit` 126 + `skills/mail` 72 + `automation/memory_curator` 64 …). 즉 커밋 거부 훅은 `git commit`만 막고, `--no-verify`나 ref 직접 조작은 파일시스템 권한이 그대로 허용한다. **코드 주입 경로는 아니다** — 배포 소스는 root 소유 읽기 전용 릴리스이고 그 트리는 `origin/main`과 바이트 일치해야 하므로(DONE「봉인된 릴리스의 배포 provenance」) 에이전트가 쓴 코드는 배포될 수 없다. 실제 피해는 **DoS·감사 오염**이다: 미러의 `origin/main` ref가 어긋나면 ⑤ 재조정이 잘못 수렴하려 하거나 provenance 검증이 모든 배포를 막는다(이번 진단 중 실수로 재현했고 ops 인증 fetch로 즉시 복구). **심각도 중**. 조치: `.git` 그룹 쓰기 비트를 회수한다 — 단, `/tmp`의 스냅샷·수리 worktree 메타데이터가 그 권한을 쓰고 있어 단순 `chmod`가 아니다. 무엇이 정당하게 쓰는지 먼저 확정하고 ops 경유로 재배치한 뒤 회수한다.
## 메모리 재배치 승인 채널 바인딩 유실(MC-4) 수리 중 발견한 후속 과제

수리는 `docs/patch/2026-08-02-memory-relocate-approval-channel-binding.md`. 노드 자율 제안 경로에서만 드러난 결함이다.

- **`USER.md`는 cap의 79.0%(1086/1375자)다** — 승격 진행분을 반영한 실측이며 97.9%라는 옛 수치는 폐기한다. **심각도 중** — 유일한 회수 경로는 트윈 승격이다. → 소유자가 대기 중인 승격 초안 6건에 ✅/⛔를 처리한다(전부 승인 시 754자 회수).
## 배포 가드 보강(DG-7) 작업 중 발견한 후속 과제

본 작업은 `land.sh`의 부수 push와 provenance 디렉터리 blind spot을 닫았다. 아래는 같은 작업에서 드러난 잔여부채다.

- **`gh pr create`가 문서에만 있고 구현이 없다 — BLOCKED 유지, 조율안 산출됨(2026-08-05, H7)** — `AGENTS.md`「수리 반영 경로 규칙」은 브랜치 push 후 PR 생성을 요구하지만 코드에 호출이 0건이다(2026-07-31 선례: 브랜치만 push되고 PR이 없어 cha가 머지할 방법이 없었다). **유실 위험·심각도 중.** 이음새 `repair_ops_work_clone.py`는 비동결이나 (a) 동결된 호출자 `repair_ops_cli.py:163`이 `branch`·`push_error`만 계약에 실고, (b) 실제 소실 경계는 그 앞의 승인 워처다 — `repair_ops_reaction_watch.py:74-86`이 자식 stdout을 `capture_output`으로 삼킨 뒤 불리언만 반환하고 `main()`(214-230)은 항상 0을 반환하므로 `pr_error`는 journal에도 남지 않는다. (c) 자격증명 배선도 공유 `/etc/autophagy/repair-approval.env`(root:ops 0640, 재조정 유닛과 공유)에 손대야 한다. 조치: 구현 전 freeze 해제 조율이 선행 조건이며, 해제 조건 3절(결과 계약 확장·전용 drop-in 배선·비대화식 gh 스케치)을 [조율안](guide/수리-PR-자동생성-조율안.md)으로 명세했다 → `.omo/plans/repair-report-core.md` 소유자 결정 대기.
증적: `docs/qa/DG-7/summary.txt`
## 배포 스냅샷 + 불변 런타임 루트(DG-2~DG-6) 작업 중 발견한 후속 과제

기능은 DONE「배포 스냅샷 + 불변 런타임 루트 (DG-2~DG-6)」 참조, 계획 `.omo/plans/deploy-snapshot-runtime.md`

- **DG-5 4.4(수리 systemd 유닛 이관)는 부분 해소 상태다** — `docs/qa/DG-5/rollout-partial.txt`는 4.4를 defer로 기록했으나, 이후 repair-report-rollout의 coordination amendment로 `autophagy-repair-agent.service`는 `/srv/autophagy-agent-current`로 이관됐다(라이브 확인: `WorkingDirectory=/srv/autophagy-agent-current`, `NeedDaemonReload=no`). **정정(2026-08-10)**: `autophagy-repair-approval-watch.service`는 "미설치"가 아니다 — 도입 커밋 `aae36d2`부터 의도적으로 **system 스코프**(`/etc/systemd/system/` + `.timer`, `User=ops`)로 설치돼 있으며, 이전의 "빈 `FragmentPath`" 관측은 잘못된 `systemctl --user` 조회의 산물이었다. 진짜 문제는 **설치본 내용이 낙았다**는 것이다 — `fdc995e`(DG-5)도 `7ea6a8c`(미러 쓰기 제거)도 반영되지 않은 pre-`7ea6a8c` 바이트라 `ReadWritePaths`에 `/srv/autophagy-agents` 미러 쓰기가 남고 런타임도 미러를 가리켰다. **심각도 중간** — 이 유닛은 이번 rollout의 주 보고 경로(cha ✅ → approval-watch → complete/reopen → enqueue)를 타므로 활성화 전에 수렴되어야 한다. 조치: 2026-08-10 별도 system-scope 수렴 runbook(소유자 root 게이트)으로 설치본을 post-DG-5 `BASE` 바이트로 맞춘 뒤 `repair-report-rollout` B1.2 ⓔ·F4가 system 스코프로 검증한다(근거 사슬: `.omo/notepads/repair-report-rollout/decisions.md`).
## 배포 체크아웃 드리프트 가드(DG-1) 작업 중 발견한 후속 과제

기능은 DONE「배포 체크아웃 드리프트 가드 (DG-1)」 참조, 증적 `docs/qa/DG-1/summary.txt`

- **수리 티켓 경로가 여전히 allowlist에 거부된다** — `report_repair`의 SSH 명령(`repair_cli.py detect`)도 sha256 allowlist에 없어 `REPAIR_TICKET_FAILED rc=126`가 난다(추정 아닌 실측). 즉 A가 드리프트를 *감지*해도 자동 *티켓*은 안 된다(FAIL 로그로만 드러남). **이번 범위 밖**: 노드 `~<operator-account>/.local/libexec/autophagy-healthcheck-probe`의 allowlist 갱신은 소유자 작업이다. **심각도 중** — A + B로 드리프트 빈도가 분단으로 줄어 방치해도 큰 사고로 번지지는 않는다. 조치: cha가 allowlist에 checkout probe와 report_repair 명령 해시를 등록(또는 checkout probe는 이제 로컬이라 불필요 — report_repair만 남음). **2026-08-17 재확인**: 여전히 살아 있다 — cron 틱 10:45·10:50·10:55·11:00·11:05 전부 `REPAIR_TICKET_FAILED rc=126` ×2(수동 실행만의 인공물이 아님을 cron 로그로 확정). 지금은 실제 FAIL 2건(HELPER-DRIFT·SKILL-STALE)이 티켓화되지 못하고 있다.
- **마운트 ABI 검사는 지금 WARN 전용이다** — C(DG-1)는 deploy-skill.sh·land.sh에서 라이브 스킬 ABI 파손을 감지하면 `MOUNT-ABI-WARN`/`LAND-ABI-WARN`으로 알리고 계속 진행한다(배포 중 차단은 이미 소비된 승인을 고아로 만들어 더 나쁘 실패모드). 실제 파손은 승인 흐름의 fail-closed(게시 거부)로 나타난다. **심각도 중** — 가드는 있으나 자동 차단은 아니다. 조치: `DEPLOY_ABI_STRICT=1`/`LAND_ABI_STRICT=1` 옵인을 오탐율 관찰 후 기본값으로 승격할지 결정한다.
- **미추적 파일 드리프트는 여전히 보이지 않는다** — 로컬 probe도 `--untracked-files=no`를 유지한다(`logs/` 오탐 방지용 의도된 계약). 주 사고 유형(로컬 커밋·추적 파일 수정)은 ahead/dirty로 덮인다. 조치: 미추적 드리프트가 실제 관측되면 화이트리스트 탐지 검토. 심각도 중.
## 수리 승인 내용 바인딩(RTS-4) 작업 중 발견한 후속 과제

기능은 [소개](기능소개/수리-승인-내용-바인딩.md), 증적 `docs/qa/RTS-4/r2-content-binding.txt`

- **적용 직전 TOCTOU가 완전히 닫히지 않았다** — `ManualOwnerApproval.permits`가 바이트를 읽은 뒤 `GitRepository.apply`가 같은 파일을 다시 읽고(`repair_ops_git.py:48`), `git apply <path>`가 또 한 번 읽는다. 즉 같은 실행 내부에 짧은 교체 창이 남는다. **심각도 낮음** — 교차 실행 간 공격(승인 후 패치 교체)은 이번에 닫혔고, 남은 창은 ops 전용 0700 경로에 대한 동시 쓰기 권한을 요구한다. 조치: `GitRepository`에 `expected_patch_sha256`를 두고 `_apply_approved`→`_run`→`_agent`로 스레딩한 뒤 `git apply -`로 검증된 바이트를 stdin으로 넘긴다. **이번에 미수행한 이유**: `repair_ops_cli.py`가 `.omo/plans/repair-report-core.md:220,237`에서 불변으로 선언되어 기계 검사(`git diff BASE..HEAD | wc -l == 0`)로 강제되며, 같은 계획이 :189에서 그 불변성을 전제로 다른 설계 타협을 소유자 승인으로 수용했다. 해당 계획과 순서를 조율한 뒤 진행한다.
- **`_run`이 `AWAITING_APPROVAL`에도 exit 0을 돌려준다**(`repair_ops_cli.py:174`). 워처는 자식이 0이면 성공으로 보고 레코드를 회수한 뒤 approvals 로그에 `"status":"approved"`를 남긴다 — 아무것도 적용하지 않은 채로. **이번에는 거부를 `False`가 아닌 예외로 만들어 우회했다**(non-zero 종료 → 레코드 보존, 감사 위조 없음). 근본 수리는 위와 같은 이유로 보류. 조치: 적용 경로의 `AWAITING_APPROVAL`을 전용 exit code로 분리한다. 심각도 중 — 감사 무결성 문제이지 오적용은 아니다.
- **승인 거부 시 TTL까지 매 tick마다 샌드박스가 다시 돌아간다** — `RepairAgent.repair()`가 planner·sandbox를 먼저 돌리고 그 다음에야 `approval.permits`를 부른다(`repair_ops_core.py:134-144`). 내용 불일치로 거부되는 티켓은 24h TTL로 자가치유되기까지 매번 최대 900초 샌드박스를 소모한다. **동작·보안 문제 아니고 빈도도 낮다**(patch.diff는 티켓당 한 번 쓰이고 planner가 재기록하지 않음). 조치: `_apply_approved`에 값싼 사전 가드를 두어 planner 이전에 끝낸다 — 역시 `repair_ops_cli.py` 조율 후.
- **`_assert_scope`가 삭제 패치를 표현하지 못하고 rename의 원본 경로를 검사하지 않는다** — `+++ b/` 스캔이라 `+++ /dev/null`(삭제)은 조용히 거부되고 rename은 삭제측이 unstaged로 남는다. 이번에 만든 `parse_patch_changes`가 양쪽을 정확히 알므로 그것을 소비하면 “승인한 것 = 범위 검사한 것 = 적용한 것”이 된다. **이번에 미수행한 이유**: 보안 수리 중에 게이트의 허용 집합을 넓히는 것은 별개 결정이다. 심각도 낮음 — 파일 삭제가 필요한 수리를 지금은 아예 적용할 수 없다는 기능 제약이지 구멍은 아니다.
## 저장·라우팅 스윕(RTS) — doctype 개인노트 진입점만 실사용 이력이 없다 (2026-08-04 갱신)

앞서 「티켓 3건이 열려 있다」고 적어둔 것은 **낡았다**. 2026-08-04 실측으로 정정한다 — `t_1b8aab9b`·`t_929ca5ad`·`t_f92027cb` 세 티켓 모두 **`done`**(2026-07-29 15:08 종료)이고, 막혀 있던 「승인 게이트 경유 실제 볼트 쓰기」도 이미 수행됐다. 상세는 DONE「저장·라우팅 스윕(RTS) 종단 검증」 참조.

- **`doctype`의 “개인노트로 저장해” 진입점은 아직 실사용된 적이 없다** — 실증된 5건은 전부 `memory_relocate`(운영사실 재배치) 경로였고, `doctype_cli`의 개인노트 분기는 같은 `obsidian_write`를 쓰지만 진입점이 다르다. **심각도 낮음으로 하향**(종전 “중”) — 공유 하단(PARA upsert → commit → push → 원격 read-back)이 5회 실증돼 미지의 영역이 라우팅 분기 하나로 즐었고, 종단 회귀는 `tests/unit/test_doctype_save_routing_e2e.py` 5건이 고정하고 있다. 조치: 소유자가 처음 “개인노트로 저장해”를 쓸 때 결과를 한 번 확인한다(별도 작업 불필요).
## 배포 샌드박스 자격증명 격리(NF-1) 중 발견된 후속 과제

기능은 DONE「배포 샌드박스 자격증명 격리 (NF-1)」·[소개](기능소개/배포-샌드박스-자격증명-격리.md) 참조, 증적 `docs/qa/NF-1/`

- **`automation/skill_review.py:134` `_scenario_passes()`는 임시 HOME을 쓰면서 `INTEROP_RUNTIME`을 전달하지 않는다** — NF-1이 `deploy-skill.sh`에서 고친 것과 같은 형태의 누락이다. 파이프라인에서는 **죽은 경로**라 지금은 무해하다(배포가 항상 `--scenario-output-file`을 넘겨 시나리오를 재실행하지 않는다). 심각도 낮음. 조치: 그 경로가 되살아나면 mail·calendar·budget·wiki가 조용히 `fail-closed-only`로 저하되므로, 되살릴 때 `INTEROP_RUNTIME`을 함께 넘긴다.
## 승인 생명주기 공용화(2026-07-25 배포) 중 발견된 후속 과제

증적 `docs/qa/E12/00-single-live-approval.txt`, 패치 `docs/patch/2026-07-25-approval-lifecycle-consolidation.md`

- **승인 단일성 불변식의** e2e 교차 케이스는 drive-archive 시나리오에 있었으나 E11 폐기(2026-07-31)로 함께 제거됨. 나머지 9개 게이트는 단위+인터리밙 테스트로만 고정함(각 스킬의 일반 e2e 시나리오는 별도로 존재). 우선순위 낮음 — 불변식 자체는 이미 검증됨.
## 승인 표면 단일화(AS) R1 중 발견된 후속 과제

- R1 배포에서 샌드박스가 5회 차단됐고 전부 실제 결함이었다(스테이징 목록 미갱신 / 표시 문구용 eager import / `AUTOPHAGY_REPO_ROOT` 전역 덮어쓰기 회귀 / secret-scan 오탐 `bot identity` / 비-스노우플레이크 픽스처). 각각 가드를 신설했지만 공통 원인은 **스킬 시나리오가 마이그레이션에 뒤처져도 배포 전에는 드러나지 않는다**는 것. **배포 전 발견 비용만 영향 · 동작·보안 문제 아님 · 심각도 낮음.** 조치: 시나리오를 유닛 스위트에서 격리 실행하는 스모크 테스트 검토.
## 승인 표면 단일화(AS) R3 중 발견된 후속 과제

- **계획서의 시나리오 S5 절차가 배포된 CLI보다 낡았다** — 실행을 시도해 확인(2026-07-27). 계획서는 `draft --uid X "A"` 다음에 `draft --uid X "B"`로 supersede를 유발하라고 적었으나, 같은 uid에 두 번째 `draft`는 `GATE-REFUSED … 초안이 이미 있음`으로 거부되고 `--force`/`--replace` 옵션도 없다. CLI 자체 도움말에 따르면 재게시는 cron `watch` tick의 역할이며 **같은 action hash라 멱등**이라 `Reason.CONTENT_CHANGED`도 발생하지 않는다. **제품에 없는 경로를 만들면 검증이 아니라 연출이 되므로 라이브 레그를 중단했다. 계획 절차 정확성만 영향 · 동작·보안 문제 아님 · 심각도 낮음.** 조치: `discard`+재초안이 의도된 content-change 경로인지, 아니면 supersede가 compose 등 다른 producer에서만 도달 가능한지 설계를 확정한 뒤 절차를 다시 쓴다. 테스트를 통과시키려고 `--force` 플래그를 신설하는 식은 금지. 같은 내용을 계획서 S5 절에 경고문으로 박아두었다.
  상세: `docs/qa/AS-3/as-3-2-red.txt`
## 소유자 대시보드 자격증명 회전(2026-07-27) 잔여 후속 과제

기능과 해소된 2건은 DONE「운영 (2026-07-27)」 참조. 아래는 그 작업 중 새로 발견된 미처리 건이다.

- **(이번에 새로 발견)** 소유자의 자격증명 조회 수단인 `autophagy-cred`/`kanban-cred`/`reporthub-cred` alias가 `~<operator-account>/.bash_aliases`에만 있고 **어떤 provisioning 스크립트에도 없다**(`provision-agent.sh`·`bootstrap-accounts.sh` 모두 미포함). 노드를 재구축하면 소유자가 자기 비밀번호를 꺼내 보는 유일한 표면이 조용히 사라진다. **재구축 복구성만 영향 · 현재 동작·보안 문제 없음 · 심각도 낮음.** 조치: alias 정의를 프로비저닝(또는 `docs/guide/onboarding-kit.md`의 복구 절차)에 편입 검토.
## 저장 경로간 ‘의미 중복’ 가능성 — 기억해(위키) · 개인노트 저장(Obsidian) 분류기가 서로를 모름

(cha 지적, 2026-07-29)

FS3 정정(2026-08-20): 「RAG 미러와 쓰기 클론의 공존」은 두 기본 경로가 분리돼 있고 이중 인덱싱이 없는 `ALREADY-FIXED` 행이므로 열린 불릿에서 제거했다. 재판정 PR #175의 결정적 경로 검증을 근거로 한다.

- **문제**: B트랙의 `classify_memory_request`는 canonical을 **위키 노트**(`wiki:` 키)로, A트랙의 `classify_save_request`는 개인노트를 **Obsidian**(`obsidian:` 키)으로 보낸다. 둘 다 RAG 소스라, 같은 내용을 “기억해”로 한 번 · “개인노트로 저장해”로 한 번 요청하면 **서로 다른 source_key로 두 벌이 인덱싱**된다. 파일 중복이 아니라 의미 중복이며, 두 분류기가 상대를 모르므로 현재 코드는 이를 막지 못한다.
- **영향 범위**: recall 검색 품질(같은 사실이 두 출처로 나와 가중치 왜곡) · 트윈 판단 근거의 이중 계산. **보안 문제 아님** — 둘 다 소유자 게이트를 거치고 민감도 판정도 그대로 적용된다. 심각도 중 — 사용 패턴 의존적이라 실제 관측 전에는 빈도를 알 수 없다.
- **조치(관찰 후 결정)**: 지금 상위 라우터를 선제적으로 두면 사용되지 않을 추상을 하나 더 얹는 셈이다. 먼저 **중복이 실제로 관측되는지** 확인한다 — `personal_cha`에서 같은 내용이 `wiki:`와 `obsidian:` 두 키로 나오는 사례를 수집. 관측되면 (a) 두 분류기 앞에 공유 상위 판정(“이 요청은 기억인가 문서 저장인가”)를 두거나, (b) recall 단계에서 동일 내용의 교차 출처를 병합해 보여주는 방법 중 선택. calendar↔coordination의 `classify_meeting_request` 선례처럼 **공유 판정 함수 + 모호하면 clarify**가 (a)의 형태가 된다.
## G5 — 스킬 위생 + mail 다이제스트

구현 내용은 [소개](기능소개/승인표면-메일다이제스트-정리.md). 아래는 코드 완료 뒤에도 소유자 권한·라이브 관측이 필요해 이번 PR에서 실행하지 않은 체크리스트다.

- **[배포 체크리스트·미완료] 라이브 cron은 아직 `--deliver local`로 관측됐고 코드만 `--deliver discord`로 수렴해 있다** → G1 착지 뒤 owner-approved 세션에서 mail·calendar·coordination·wiki를 각각 해시 승인·재배포하고, `skills/mail/deploy.sh` 실행 후 cron의 `Deliver: discord`를 확인한다. 07-31 누락분은 dry-run 건수 확인 뒤 한 번만 재전송한다. **심각도 중** — 배포 전까지 다음 실패 알림도 조용할 수 있으나 이번 세션은 노드 동작을 전혀 시도하지 않았다.
- **[배포 체크리스트·미완료] vendored mailon의 미사용 import 7건은 소스에서 제거됐지만 라이브 mailon 릴리스에는 아직 반영되지 않았다** → 수정 커밋이 `origin/main`에 착지한 뒤 별도 owner-approved mail 재배포를 요청하고 `~/.hermes/mailon-runtime/current`가 새 vendor digest를 가리키는지 확인한다. **동작·보안 문제 없음 · 심각도 낮음(배포 대기)** — 제거된 import는 실행에 쓰이지 않았고 unit 3391건·vendor offline 58건·저장소 전체 Ruff가 통과했으며, 이번 repair-report rollout에서는 승인 게이트가 필요한 외부효과를 수행하지 않는다.
- **[canary 체크리스트·미완료] GLM payload 전달 여부는 worktree에서 증명할 수 없다** → owner 세션에서 비민감 합성 입력으로 비-4xx·유효 JSON·reasoning tokens 0을 확인한다. 기존 proxy가 필드를 버린다는 증거가 생길 때만 `configs/litellm-staging/config.yaml`을 별도 변경한다. **심각도 중** — 현재는 fail-open과 항목 재시도가 동작을 보전하며, 이 PR은 gateway config를 수정하지 않았다.
- **[조사·노드 확인 미완료] 리포 증적상 mail은 pending 0이고 최신 skill-gate 잔재 11종 목록에도 mail·wiki가 없다** → 재배포 직전 노드에서 두 스킬의 실제 pending 상태를 read-only로 다시 확인한다. 성공한 배포는 stage 4 직후 정확한 `(skill, hash, message_id)`만 `consume`하므로 새 요청은 자동 정리되지만, 이미 결정된 구레코드가 발견되면 무조건 덮어쓰지 않고 소유자 판단으로 `skill_gate abandon`을 사용한다. **보안 문제 아님·심각도 낮음** — fail-closed 잔재가 있으면 배포가 멈추는 가용성 문제다.
- **[재평가] JSON 계약 하드닝 뒤에도 항목별 1회 재시도가 필요한지는 라이브 실패율이 없다** → 배포 후 `classification_failed` 빈도와 추가 호출 비용을 관찰하고, 충분한 무실패 기간 전에는 회귀 보험을 제거하지 않는다. **심각도 낮음** — 재시도는 캘린더 위임 전에 현재 메일 분류만 반복해 외부효과 중복은 없다.
## G2 — 승인 게이트·공급망 워처

기능은 [소개](기능소개/승인게이트-공급망워처-정리.md), 작업 배분은 `.omo/plans/parallel-followup-sweep.md` §5 G2다.

- **레거시 pre-schema 레코드의 자동 종결은 금지하고 소유자 실행 경로만 마련했다** — `action_hash`·`kind`·`channel_id`·`policy_version`·`surface`가 빠진 레코드는 현대 승인으로 승격하지 않는다. **안전 문제 아님·심각도 낮음** — fail-closed 보류가 유지된다. cha가 이미 실현된 효과와 정확한 `(skill, hash, message_id)`를 확인한 뒤 `abandon --legacy-only`를 실행하며, 현대 스키마 레코드는 이 경로가 거부한다. 현재 procurement 1건의 실행 여부는 소유자 판단으로 남긴다.
- **[해소 2026-08-04] pending 잔재가 0건이 됐다** — 기록된 요청은 그사이 성공한 배포들이 `consume`으로 자동 회수해 실측 시점엔 예제 스킬 1건만 남아 있었고, 소유자 지시로 `abandon --legacy-only`를 실행해 회수했다(감사 `logs/approval-abandons.jsonl`, actor=`<operator-account>`). Discord 메시지는 규약대로 건드리지 않았다. 절차는 `docs/guide/skill-gate-pending-cleanup.md`에 그대로 유효하다.
- **`managed-activate`·`skill-publish` 자동 재개는 의도적으로 구현하지 않았다** — 전자는 레코드에 `--activate-managed <quarantine-dir>`가 없고 후자는 별도 프로그램과 `--managed-repo`가 필요하다. **추측 실행은 공급망 안전 위험·심각도 높음**. 자동화하려면 이 맥락을 승인 레코드 필드에 영속할지, 검증된 전용 상태 파일에 둘지 먼저 설계하고 그때 `SUPPORTED_KINDS`를 확장한다.

## G8 — LOC 등록부

기능은 [소개](기능소개/loc-등록부-재측정.md), 작업 배분은 `.omo/plans/parallel-followup-sweep.md` §5 G8이다. 8개 코드 그룹이 전부 머지된 HEAD(`2383a92`)에서 전수 재측정해 **초과 29건 · 등록 3건**이던 상태를 등록 29건으로 맞췄고, LOC 게이트는 `EXCEPTION 29 / VIOLATION 0 · LOC RESULT: PASS`가 됐다.

- **진짜 분할 후보 5건은 "naturally large"가 아니라 "미룸 + 왜"로 등록했다** — `deploy-skill.sh`(550, 인자 파싱·실행 lease·ABI 스캔 분리 가능) · `triage_gate.py`(467, mailon/gmail 두 실행 백엔드) · `calendar_confirm.py`(437, 워처 HMAC 인가 블록 약 100줄) · `skill_gate.py`(429, G2 요청분) · `research_trends.py`(262, 수집/LLM/전달 계층). **현재 동작·보안 영향 없음 · 품질 부채** → 각각 별도 사이클에서 다룬다. 나머지 24건은 지금 쪼개면 오히려 나빠지는 것들이라 사유만 남겼다(vendored 6 · 승인 게이트 단일 절차 7 · argparse 표면 2 · 특권/주입 경계 3 · 순수 로직 5 · 셸 자기완결 1).
- **F2 감사는 LOC를 고쳐도 여전히 red다 — `f2_quality.sh:41`이 `ruff check .`를 그대로 돌린다** — CI는 `--exclude skills/mail/vendor`를 주는데(`.github/workflows/ci.yml`, "고칠 수 없는 트리를 린트하면 CI가 영구히 빨간불") F2만 안 준다. 실측: F2의 ruff 지적은 **전량 vendor 트리**이고 CI 기준으로는 `All checks passed!`다. **보안·동작 무관 · 심각도 중(감사 신호 손실 — LOC에서 방금 없앤 것과 같은 종류의 상시 red)** → `f2_quality.sh:41`에 같은 exclude를 주면 F2 전체가 green이 된다. 이번 범위(등록부)를 벗어나 손대지 않았다.

증적: `docs/qa/F2/module-loc.txt` (`EXCEPTION 29 / VIOLATION 0`), 전체 스위트 3111 passed, `ruff check . --exclude skills/mail/vendor` 통과.

## H3 배포 위생 도구 반영 후 OWNER 실행 항목

기능은 [소개](기능소개/H3-배포-위생.md).

- **일일 sandbox 스모크는 코드·격리 검증까지만 완료되어 노드 timer가 아직 설치되지 않았다** → PR 머지 뒤 OWNER가 `provision-deploy-smoke.sh`를 실행하고 첫 tick의 `~/.hermes/deploy-smoke/tick.json`을 확인한다. **배포 안전 관측성·심각도 중** — 설치 전에는 다음 실배포가 여전히 첫 노드 종단 검증이다.
- **미러 writer inventory와 로컬 브랜치 후보는 실제 노드에서 실행하지 않았다** → OWNER가 read-only inventory와 `docs/guide/미러-로컬-브랜치-정리.md` 기준을 적용해 검토하며, 삭제는 별도 OWNER 판단으로 수행한다. **동작·보안 영향 없음·심각도 낮음** — 도구 개발 중 실제 미러·ref는 변경하지 않았다.
  **근거 없는 값이라 재분류 시 오도할 수 있음 · 심각도 낮음** — 조치: 해당 묶음을 다음에 건드릴 때 불릿에 영향 범위·심각도를 명시해 표와 일치시킨다.

## H1 — 헬스체크 릴리스 관측성 OWNER 인계 (2026-08-05)

기능은 [소개](기능소개/헬스체크-릴리스-관측성.md), 증적은 `.omo/evidence/fs2/task-1-parallel-followup-sweep-2.txt`다.

- **노드 wrapper의 실제 알고리즘 재검증과 15개 명령 해시 등록은 아직 OWNER 몫이다** — 커밋 manifest는 원문 생성·shim 대조의 기준이며 실제 등록을 대신하지 않는다. **자동 수리 티켓 관측성 영향 · 심각도 중, 보안 하향 없음** → OWNER가 배포 노드의 agent UID·sudo secure_path를 명시 입력해 manifest를 재생성하고 wrapper 알고리즘으로 검증·등록한다.
- **`RELEASE_STALE_PROBE_ENFORCE`는 기본 0(WARN)이다** — 독립 프로브가 먼저 shadow 관측되도록 강제 승격하지 않았다. **릴리스 stale 단독 경로는 경고만 남는 롤아웃 단계 · 심각도 중** → OWNER가 오탐 여부를 확인한 뒤 원장에 승격 결정을 기록하고 1로 전환한다.
- **문서 갱신 대기: `operations.md:105` (freeze 해제 후)** — 현재 문구는 수리 티켓 allowlist가 없다고 적어 manifest 도입 뒤 낡지만 freeze 때문에 수정하지 않았다. **운영 안내 정확성만 영향 · 심각도 낮음** → freeze 해제 후 D1 명령과 manifest/OWNER 등록 절차로 갱신한다.

## repair-report-core — RRC-3 증적의 OBS-JSON 라인은 verbatim이 아니다 (2026-08-05)

F3 최종 검증 웨이브가 A3.1 샌드박스 E2E를 4회 신규 재실행하며 발견했다. 판정 자체는 APPROVE다 — 격리 주장(실 ssh 0 · 네트워크 0 · 실 `/srv`·홈 쓰기 0)은 strace로 커널 수준에서 독립 재측정했다.

- **`docs/qa/RRC-3/01-sandbox-e2e.md`는 캡처 전체가 verbatim이라고 선언해 두고, 마지막 `OBS-JSON:` 요약 라인만 손으로 축약해 자기 문서의 `STEP`/`ISOLATION` 라인과 9개 중 8개 섹션에서 어긋난다**(`path_entries`·`capability_files`·`tickets`·`operations`·`reason_codes`·`hermes_argv`·`completed`·`receipts` 등 누락). 코드 회귀가 아님이 증명됐다 — 시나리오 `main()`의 `OBS-JSON` 출력 경로는 `sort_keys=True` 단 하나이고 STEP 라인과 같은 객체에서 만들어지며, F3의 신규 실행에서는 불일치가 0이었다(RRC-3 라인의 `isolation` 키 순서가 미정렬인 것이 수기 작성의 방증). **기능·격리·PASS 판정 무관 · 심각도 낮음(증적 신뢰도)** — 그 라인을 회귀 비교의 기준으로 믿는 미래 감사자만 오도된다(`STEP` 라인은 진짜 verbatim이다). → 그 라인을 실제 출력의 마스킹 캡처로 교체하거나, 축약 편의 라인일 뿐이고 위의 `STEP`/`ISOLATION`이 정본임을 문서에 명시한다.

증적: `docs/qa/RRC-F/f3.md` (§A), 대상 문서 `docs/qa/RRC-3/01-sandbox-e2e.md`

## repair-report cron 활성화 중 발견한 후속 과제 (2026-08-13)

- **Hermes Python no-agent job은 정상 실행의 stdout도 cron output artifact에 보존하지 않을 수 있다** — `repair-report-consumer`뿐 아니라 `daily-cost-report`·`budget-watch`·`reminder-poller`의 실제 성공 run도 `Status: silent (empty output)`로 남았다. **수리 보고 처리 자체와 보안에는 영향 없음 · 심각도 낮음(스케줄러 관측성)** → Hermes의 Python no-agent stdout 수집 경로를 별도 조사하고, 그 전에는 성공 여부를 stdout 본문이 아니라 `Last run` 전진과 도메인 부작용의 exact readback으로 증명한다.
- **`automation/repair/cron/repair_report_consume_watch.py:main()`은 모든 예외를 stderr에 기록한 뒤에도 항상 exit 0을 반환한다** — consumer 내부 실패가 발생해도 Hermes cron은 run을 `ok`로 표시할 수 있다. **실패 탐지 지연 가능 · 심각도 중(운영 신뢰성), 외부효과 승인 우회는 아님** → 별도 code-plan에서 예외 시 non-zero를 반환하도록 바꾸고, 실패 재시도·ACK 미생성·scheduler 상태를 함께 회귀 테스트한다.

증적: `docs/qa/RRO-2/03-cron.md` §16 및 최종 활성화 절, 구현 근거 `automation/repair/cron/repair_report_consume_watch.py:30-39`.

## 제3자 런타임 전제(P0-6) 작업 중 발견한 후속 과제

- **소유자 DM 승인 표면은 설치 전에 확인되지 않는다** — `discord_check.py`는 길드 채널 3종만 검증하는데(계획 P0-6 ①의 명시 범위), 실제 승인 대부분(메일·예산·캘린더·위키·수리)은 봇↔소유자 DM으로 간다. DM은 owner id 없이 조회할 수 없고 DM 열기(`POST /users/@me/channels`)는 쓰기라 read-only 계약을 깬다. **보안 결함 아님 · 심각도 낮음(설치 전 진단 커버리지)** → 설치기(W-F1-B)가 owner id를 확보한 뒤 이미 열린 DM만 조회하는 확장을 별도로 판단한다.

증적: `automation/install/discord_check.py`(REQUIRED_CHANNELS), 승인 표면 라우팅은 `automation/interop/AGENTS.md`.

## W-F3-A principal 일반화 작업 중 발견한 후속 과제

발행·검증에서 `publisher-cha@autophagy` 상수를 걷어내며 발견했다. 기능은 [소개](기능소개/발행자-구독자-principal-일반화.md).

- **managed-sync 런타임 config의 `publisher` 키는 필수인데 아무것도 강제하지 않는다** — `SyncConfig.publisher`는 파싱만 되고 `manifest.publisher`와 대조되지 않아, 설정해도 검증에 쓰이지 않는 "믿음 손잡이"로 남는다(주체 검증은 principal이 전담하므로 **보안 하향은 아님 · 심각도 낮음**). → W-F3-B/C에서 `manifest.publisher`와의 대조를 추가하거나 키를 제거한다. 제거는 기존 런타임 config를 unknown-key로 깨뜨리므로 config-before-code 순서를 지켜야 한다.
- **roster 경로 해석이 `automation/managed_sync/cli.py` 한 곳에만 있다** — `AUTOPHAGY_ROSTER`/`~/.hermes/roster.yaml` 해석기가 구독자 CLI 안에 있는데, W-F3-C의 그룹 announce도 같은 roster를 읽어야 한다. **현재 동작 영향 없음 · 심각도 낮음** → 두 번째 소비자가 실제로 생길 때 공용 위치로 올린다(지금 올리면 소비자 하나짜리 추상이 된다).

## 설치 문서(P0-5) 작업 중 발견한 후속 과제

- **`python3 -m automation.install`의 `--config` 기본값이 cha 프로덕션 값이다** — `load_node_config(None)`이 `configs/node.example.toml`과 같은 값으로 해석되므로, 제3자가 `--config`를 빠뜨리면 남의 노드 이름·origin·계정으로 계획이 만들어진다. `--dry-run`이면 무해하지만 실제 실행이면 잘못된 origin을 clone한다. **보안 결함 아님 · 심각도 중(오설치 위험)** → 현재는 `docs/guide/install.md`가 항상 `--config` 명시를 요구하는 것으로 막고 있다. 설치기 쪽에서 `--config` 없는 **실제 실행**(dry-run 아님)을 거부하거나 기본값 사용을 경고하는 편이 구조적이며, 설치기를 소유한 wave에서 판단한다.
- **설치기가 ops 계정의 `known_hosts`를 시드하지 않는다** — clone은 `StrictHostKeyChecking=yes`로 수행되는데(`apply._repository`) 설치기는 `~/.ssh` 디렉터리만 만든다. 배포 키를 정상 등록해도 호스트키가 없으면 `repository` 액션에서 인증 이전 단계로 실패한다. **fail-closed라 안전 · 심각도 낮음(첫 설치 마찰)** → 현재는 문서 §6.2가 `ssh-keyscan` + 지문 대조를 안내한다. 자동 시드는 호스트키 신뢰를 설치기가 대신 결정하는 것이므로 신중해야 하며, 한다면 지문을 번들에 고정하는 형태여야 한다.
- **`--dry-run` rc 0이 "전제 충족"으로 오독될 수 있다** — dry-run은 `check` 액션을 실행하지 않고 계획에 자리만 표시하므로, rc 0은 "계획을 계산할 수 있다"는 뜻이다. **오독 위험만 · 심각도 낮음** → 문서에서 명시적으로 구분했다. 설치기 출력에도 "checks are not executed in dry-run" 한 줄을 넣는 편이 낫다.

증적: `docs/qa/P0-5/`(전사 3건 + summary), 근거 `automation/install/{installer,apply,plan}.py`.

## 구독자 sync 자동화 배포(W-F3-B) 작업 중 발견한 후속 과제

틱을 배포물로 만들며 발견했다. 기능은 [소개](기능소개/구독자-sync-자동화-배포.md).

- **MS-E1 E2E 뱅크가 W-F3-A 이후 깨져 있다** — `tests/e2e/drivers/ms_managed_channel_actor.py`의 `World.create`가 W-F3-A가 필수로 만든 두 런타임 파일(`~/.hermes/managed-skills/publisher.json`, `~/.hermes/roster.yaml`)을 쓰지 않아, 9개 케이스 중 8개가 `PUBLISH-BLOCK: publisher config not found`로 죽는다(실측 2026-08-15). **프로덕션 사용자 영향 없음·심각도 중** — 버그가 아니라 관리형 채널의 회귀 뱅크가 지금 아무것도 지키지 못한다는 점이 문제다. → `World.create`에 두 파일 생성을 추가한다(W-F3-B 검증 하네스가 정확히 그 두 파일만 더해 15/15로 돌았으므로 변경량은 작다). 이번 범위 밖이고 W-F2-C 세션이 같은 actor를 건드릴 가능성이 있어 공유 파일을 손대지 않았다.
- **`automation/install/assets.py`가 250 pure-LOC 천장에 4줄 남았다** — opt-in 레지스트리를 `components.py`로 분리해 246으로 내려놓았으나, 다음 always-on 유닛·helper 추가가 F2 게이트를 깨뜨린다. **현재 동작 영향 없음·심각도 낮음** → 다음에 `assets.py`를 만지는 wave가 파일 종류별(systemd·sudoers·libexec·hooks) 빌더로 쪼개거나 사유와 함께 등록부에 올린다.

### FS3 F4 해소 기록

설치 완료 체크리스트가 quarantine pending과 owner-notice 자격증명 설정 여부를 읽기 전용으로
확인하고, 실제 통지 경로 `automation/owner_notice.py`를 명시한다. 원래 알림 불릿은 종결했다.

증적: `docs/qa/W-F3-B/summary.txt`(15/15 + 증명된 것/불가한 것 분리).

## 서명 roster 배포(W-F2-C) 최종 검증 중 발견한 후속 과제

FS3 정정(2026-08-20): 「Ruff 잔여 4건」과 「ROS `launch_testing` 자동 로드」는 각각 전체 lint green과 `pytest.ini` 플러그인 차단으로 종결된 `ALREADY-FIXED` 행이라 열린 불릿에서 제거했다. 재판정 PR #175의 두 검증을 근거로 한다.

근거: `tests/unit/test_{interop_channel_routing,rag_ingest_discord_identity}.py`, `ruff check .` 실측(2026-08-15).

## 공개 컷 전 인가 감사(2026-08-15) 중 발견한 후속 과제

FS3 정정(2026-08-20): 「update 채널 단조성 검사 부재」는 서명 검증 뒤 release floor를 전진시키는 경로가 이미 존재하는 `ALREADY-FIXED` 행이므로 열린 불릿에서 제거했다. 재판정 PR #175와 공개 릴리스 정책 PR #189를 근거로 한다.
- **peer-attestation 신뢰 앵커가 설정된 ops 계정이 아니라 하드코드 이름 `"ops"`를 쓴다** — `automation/peer_attestation.py::_trusted_owner_uids`가 `pwd.getpwnam("ops")`를 하드코드하는데 `ops_account`는 설정 가능한 필드이고 설치기가 실제로 지원한다(`test_install_plan.py`=`member-ops`, `test_install_assets.py`=`third-ops`, `test_node_asset_renderer.py`=`infra`). 문서 계약은 "root 또는 ops 소유"(`docs/guide/onboarding-kit.md:438`, `configs/AGENTS.md:19`)이므로 문서-구현 불일치다. **보안 취약점 아님**을 확인했다 — 이름이 엉뚱하게 해석돼도 `/etc/autophagy`가 root 소유 0755라 비-root는 그 안에 파일을 만들 수 없어 넓어진 uid 집합에 적용될 파일이 없다. 실제 영향은 반대 방향의 **가용성**이다: `ops_account`를 바꾼 제3자 설치가 문서대로 `/etc/autophagy/peers.yaml`을 자기 ops 계정 소유로 두면 `getpwnam("ops")`가 KeyError→`{0}`이 되어 discord 모드 배포가 매번 "valid peer attestation absent"로, **원인을 가리키지 않은 채** 실패한다. **심각도 낮음 · 소유자 결정 필요(이번 범위에서 미수정)** → 후보 수정안이 둘 다 대가를 요구한다: `load_node_config()`는 **agent가 쓸 수 있는** `~/.hermes/node.toml`을 읽으므로 E7 위협모델에서 agent 제어 입력을 신뢰 앵커에 넣는 퇴행이고, 게이트에 `node_config.py`+시드 TOML을 staging하면 `deploy-skill.sh`의 `validate_gate_staging_imports`가 전이 import를 강제해 배포 경로가 커진다. 최소한 문서와 구현 중 하나는 맞춰야 한다.
- **root 소유 `managed-skills-allowed-signers`가 2개 이상 principal을 담게 되면 roster가 principal 선택 수단이 된다** — `managed_sync/cli.py:90-102`의 `_publisher_principal()`은 신뢰 principal 이름을 **roster 문서에서 직접 읽는다**(`roster.admin.publisher_principal`). 지금 안전한 이유는 `plan_signer_install`이 그 root 소유 파일에 **정확히 1개 entry**만 쓰기 때문이지(이름이 안 맞으면 `ssh-keygen -Y verify`가 거부) roster가 서명되기 때문이 아니다. 다중 그룹이나 키 로테이션 과도기로 entry가 늘면 roster를 쥔 쪽이 어느 principal을 쓸지 고르게 된다. **현재 동작 영향 없음 · 심각도 낮음** → entry를 늘리는 변경이 생기면 그때 principal 선택을 roster 밖(대역외로 고정한 로컬 설정)으로 옮긴다.

증적: `.omo/notepads/public-release/issues.md`의 「[2026-08-15] Security audit — auth/authorization」 두 번째 항목(수정된 roster replay 1건 + 위 3건의 근거와 기각 사유 포함).

## 공개 export 감사 지적 수정(2026-08-15) 중 발견한 후속 과제

- **`docs/` 하위 나머지 트리는 여전히 fail-open이다** — C1이 도입한 공개 결정 원장(`configs/public-export-review.txt` + `tests/unit/test_public_export_manifest_coverage.py`)은 `configs/`·`docs/guide/`·`docs/patch/` 세 디렉터리만 governed로 둔다 — manifest가 지금 손으로 denylist를 유지하는 곳이 거기뿐이기 때문이다. 그래서 `docs/기능소개/`(71개)·`docs/troubleshooting/`(2개)·`docs/` 최상위 파일(6개)에 사적인 문서가 새로 생기면 **여전히 아무 신호 없이 공개된다**. 메타 가드가 있어 누군가 그 디렉터리에 **제외 항목을 처음 추가하는 순간**에는 실패하지만, 그 전까지는 침묵한다. **보안 결함 아님 · 심각도 중(범위 밖이라 미수정)** → `docs/`를 통째로 governed에 넣고 원장을 ~157줄로 확장한다. 비용은 「기능 소개 문서 규칙」으로 기능마다 생기는 `docs/기능소개/*.md`가 매번 원장 한 줄을 요구하게 되는 것이다 — 그 마찰을 감수할지가 소유자 판단 사항이라 단독으로 넓히지 않았다.
- **`public_export_redaction.py`는 스냅샷 안에 자기 복사본이 없으면 export를 차단한다** — `public_export.sh:247`이 `python3 "$snapshot/automation/public_export_redaction.py"`를 부르므로, 내보낸 트리에 그 파일이 없으면 `vendored public-snapshot de-identification failed`로 멈춘다(로컬 bare remote 종단 검증 중 실측 — `automation/`이 없는 샌드박스 소스에서 재현). 실제 저장소엔 항상 있으므로 **프로덕션 영향 없고 fail-closed 방향이 올바르다 · 심각도 낮음** → 그 단계를 만든 세션이 소유하는 문제다. 스크립트가 스냅샷 사본 대신 소스 체크아웃의 모듈을 부르게 하거나(내보낸 트리가 자기를 재가공하는 순환을 끊는다), 부재 시 명시적인 사유를 내도록 한다.

증적: `.omo/notepads/public-release/issues.md`의 「[2026-08-15] Task: 공개 export 감사 지적 수정」 항목.

## 설치 편의 래퍼(W-F2.5-E 사전 준비) 중 발견한 후속 과제

FS3 정정(2026-08-20): 「`third-party-runtime-prereqs.md` 공개 제외」는 공개 결정 원장에 있고 denylist에는 없는 `ALREADY-FIXED` 행이므로 열린 불릿에서 제거했다. 재판정 PR #175와 공개 정책 정합화 PR #189를 근거로 한다.

증적: `docs/qa/W-F2.5-E/quickstart-wrapper.md` · `.omo/notepads/public-release/learnings.md`의 「[2026-08-15] Task: W-F2.5-E 사전 준비」 항목.

- **`install.md` §6이 `sudo`의 환경 제거를 언급하지 않는다** — 설치기의 `discord-readiness` 체크는 `DISCORD_BOT_TOKEN`을 환경변수에서만 읽는데(`discord_check.py`), 문서의 `sudo python3 -m automation.install`은 그것을 넘기지 않아 `[FAIL] discord-readiness: discord_check.py rc=2`가 난다. **보안 결함 아님 · 심각도 낮음(오해 유발)** → `quickstart.sh`는 `--preserve-env=DISCORD_BOT_TOKEN`을 probe해 우회하지만, 문서대로 손으로 실행하는 사람은 여전히 걸린다. §6에 한 줄을 넣거나 설치기가 토큰 부재를 전제 이름으로 보고하게 한다.
- **설치기 진입점의 "깨끗한 호스트에서 실행된다" 성질을 지키는 것이 단위 테스트 2건뿐이다** — `tests/unit/test_install_third_party_boundary.py`가 PyYAML 부재를 `sys.modules` 주입으로 흉내낼 뿐, 진짜 clean container smoke는 CI에 없다. 이번에 실제로 한 번 깨졌다(`dcddaa21` 이후 모듈 스코프 roster import → traceback). **심각도 낮음** → `python:3.12-slim`에서 `--dry-run` rc 0을 확인하는 CI 잡을 추가하면 import 표면이 넓어지는 순간 바로 잡힌다.
## 플랫폼 운영자 매뉴얼(W-M3) 작성 중 발견한 후속 과제

- **`trust_key_bootstrap install`은 신뢰키 회전의 전환기 도구가 될 수 없다** — `plan_signer_install`이 **단일 엔트리**로 `/etc/autophagy/update-allowed-signers` 전체를 렌더해 교체하므로, 그것으로 새 키를 넣으면 구 키가 사라진다. 그런데 회전은 신키로 서명한 릴리스를 컷하기 **전에** 전 노드가 두 키를 함께 신뢰해야 안전하다. 파일 형식·`parse_allowed_signers`·`render_allowed_signers`·`_check_content`(`any(...)`)는 이미 다중 엔트리를 지원하므로 막힌 곳은 CLI 하나뿐이다. **보안 결함 아님 · 심각도 중** — 순서를 어기면 노드가 fail-closed로 멈출 뿐 잘못된 코드를 받지는 않는다. 다만 **`UPDATE-TRUST-BLOCK`은 rc 0이라 알람이 없어** 발견이 늦는다 → 기존 엔트리를 보존하며 병합하는 `install --add`(또는 `add-signer`) 서브커맨드를 추가하고, 매뉴얼 §3.4의 수동 파일 조립 단계를 그것으로 대체한다. 현재는 `docs/guide/manual-maintainer.md` §3.3이 이 제약을 명시해 문서로 덮고 있다.

F4 해소 기록(2026-08-21): 404 링크 제거와 신규 링크 conformance는 완료됐다. 남은 owner
action은 네 제외 런북을 공개 대상으로 승격할지 결정하는 것뿐이며 OWNER-29가 그 범위만 소유한다.

아래 불릿은 초기 131행과 결합된 회계 정본 원문이라 수정하지 않는다. 현재 열린 범위는 위
해소 기록의 owner action으로 대체됐다.

- **공개본에서 끊기는 문서 링크가 있다** — 공개 원장에 있는 매뉴얼들이 export 제외 문서를 링크한다: `manual-group-admin.md` → `managed-skill-channel.md`(기존), `manual-maintainer.md` → `operations.md`·`incident-response.md`·`reboot-recovery.md`(신규). 공개 트리에서는 그 대상이 존재하지 않아 404가 된다. **보안·동작 무관, 문서 탐색성만 영향 · 심각도 낮음** → 세 가지 중 하나를 고른다: (a) 해당 문서를 공개 대상으로 승격 (b) 공개되는 문서에서 링크를 걷어내고 산문으로만 언급 (c) export 시 제외 대상 링크를 검출하는 conformance 테스트를 추가해 최소한 새로 늘지 않게 한다. 신규 3건은 이번에 `*(개발 저장소 전용 — 공개본에 포함되지 않는다)*` 주석을 달아 오해만 막아 두었다.

증적: `.omo/notepads/public-release/learnings.md`의 「[2026-08-15] Task: W-M3 플랫폼 운영자 매뉴얼」 항목.

## Google Tasks 승인 쓰기 PR 리뷰 중 발견한 후속 과제

FS3 정정(2026-08-20): 「todo 승인 파일 pure-LOC 초과」는 두 파일이 모두 250줄 이하인 `ALREADY-FIXED` 행이므로 열린 불릿에서 제거했다. 재판정 PR #175의 LOC 검증을 근거로 한다.

근거: PR #125 코드 품질 리뷰, `skills/todo/scripts/todo_{approval,confirm_reaction_watch}.py` pure-LOC 측정.

## 그룹 채널 실제 원격 전파 검증(GROUP-1) 중 발견한 후속 과제

FS3 정정(2026-08-20): 「managed-sync 직접 실행 무출력」은 패키지 `__main__` 진입점으로 종결됐고, 「원격 삭제 태그 보존」은 decision 16의 의도된 no-prune 정책인 `ALREADY-FIXED` 행이다. 재판정 PR #175와 공급망 검증 PR #188을 근거로 열린 불릿에서 제거했다.

증적: `docs/qa/GROUP-1/summary.txt`(부록 절).

## K4-b 설치기·신뢰키 위생(FS3 todo 12) 중 발견한 후속 과제

- **`automation/healthcheck.sh`가 249/250 pure LOC인데 LOC 예외에 등록되어 있지 않다** — probe 함수 하나(약 15줄)만 더해도 `f2_quality.sh`의 LOC 루프가 `VIOLATION`을 내므로, 다음에 헬스체크 항목을 추가하려는 사람은 자기 작업과 무관한 분할부터 해야 한다. 실제로 이번 스윕에서 quarantine 미확인 릴리스 probe를 넣으려다 여기서 막혔다. **현재 동작·보안 영향 없음 · 심각도 낮음(다음 작업자가 천장에 부딪혀서야 알게 되는 것이 비용)** → probe 함수군을 `healthcheck_probes.sh`로 떼어 source하거나, 분할이 부적절하다면 사유와 함께 `automation/final/f2_loc_exceptions.txt`에 등록한다.
- **스킬 시나리오 17개 중 배포 전에 실제로 실행되는 것은 3개뿐이다** — 이번에 더한 `tests/unit/test_skill_scenario_drift.py`는 실행 없이 보이는 드리프트(사라진 저장소 경로 참조, `bash -n` 파싱 실패)만 잡는다. calendar·mail·wiki 외 14개는 여전히 배포 시점에야 깨진 것이 드러난다. **배포 전 발견 비용만 영향 · 동작·보안 문제 아님 · 심각도 낮음** → 스킬별로 임시 HOME·더미 자격증명에서 시나리오를 실행하는 하네스를 하나씩 늘린다. 한 번에 17개를 유닛 스위트에 넣는 것은 금물이다 — 메일 발송·브라우저 기동이 섞여 있어 외부효과가 난다.
- **`docs/guide/operations.md`가 자격증명 조회 alias를 「프로비저닝에 없다」고 계속 말한다** — `automation/provision-agent.sh`가 이제 복원하므로 낡은 서술이지만, 그 문서는 `configs/freeze-inventory.txt`의 동결 대상이라 이번에 고치지 않았다(같은 문구를 가진 `onboarding-kit.md`와 기능소개 문서는 갱신했다). **오안내 위험만 · 동작 영향 없음 · 심각도 낮음** → 그 계획의 동결이 풀리는 사이클에서 한 문단을 함께 고친다.
- **게이트가 import하는 모듈은 `deploy-skill.sh`가 동결인 동안 분할할 수 없다** — `peer_attestation.py`의 진단을 별도 모듈로 떼자 `validate_gate_staging_imports`가 `STAGE-BLOCK: imported gate module is not staged`로 배포를 막았고(실측), staging 목록은 동결된 `deploy-skill.sh` 안에 있다. 그래서 그 모듈은 250줄 천장 안에서 해결해야 했다(현재 249). 같은 제약이 `skill_gate.py`(553, 예외 등록)에도 이미 걸려 있다. **현재 동작·보안 영향 없음 · 심각도 중(구조적 — 천장에 닿은 게이트 모듈이 늘수록 선택지가 사라진다)** → staging 목록을 `deploy-skill.sh` 밖의 기계 판독 파일로 옮기는 안을 todo 14 조율안의 해제 조건과 함께 검토한다.

증적: `.omo/evidence/fs3/task-12-parallel-followup-sweep-3.txt`, 소개 [설치기 위생과 신뢰키 회전 병합](기능소개/설치기-위생과-신뢰키-회전-병합.md).

## FS3 K4-a 공급망·그룹 채널 위생 후속 과제

F4 해소 기록(2026-08-21): posting journal의 감사형 복구 절차와 CLI는 완료됐다. 남은 owner
action은 라이브 Discord 메시지가 `delivered`인지 `not-delivered`인지 판정하는 것뿐이며
OWNER-37이 그 외부 상태 판정만 소유한다.

아래 불릿은 초기 131행과 결합된 회계 정본 원문이라 수정하지 않는다. 현재 열린 범위는 위
해소 기록의 owner action으로 대체됐다.

- **공지 전송 결과가 모호한 posting journal은 자동으로 지울 수 없다** — 재시도하면 이미
  전달된 공지를 중복 게시할 수 있어 이번의 외부효과 0 검증만으로 안전한 복구를 결정할
  수 없다 → 소유자가 해당 Discord 메시지 존재를 확인한 뒤 journal 유지·제거를 선택하는
  절차를 정한다. **발행·구독 동작 영향 없음 · 심각도 낮음(공지 재개에 사람 판단 필요)**.

증적: `.omo/evidence/fs3/task-11-parallel-followup-sweep-3.txt`

## F4 스코프 감사의 `approvals-send-log` 가 라이브 노드에서 FAIL (2026-08-21 발견)

병렬 후속 스윕 3 마감 중, `k-fix4` 워크트리에 커밋되지 않은 `automation/final/f4_scope.sh`
재실행 결과가 남아 있었다(라이브 노드 대상, `F4 RESULT: FAIL`).

- 소유자 승인 대비 전송 로그가 맞지 않는다 — `owner_approved_records=207`,
  `send_logged_records=25`, `sent_records=55`, **`unmatched_sends=31`**(전부
  `method='manual_reaction'`, 직전 증적 2026-07-18 에서는 0). 노드에서 31건 중 1건을 골라
  승인 레코드와 전송 로그를 직접 대조해 **(a) 검사기가 `manual_reaction` 을 매칭하지 못하는 것**과
  **(b) 실제 전송이 로깅되지 않는 것**을 가른 뒤, (a)면 매칭 규칙에 추가하고 (b)면 로깅 누락을 수리한다.
  **심각도: 중**(가른 뒤 (b)면 높음 — 승인-전송 감사 추적의 구멍이라는 뜻). 영향 범위는 승인 게이트의
  사후 감사이며 승인 자체나 외부효과 차단에는 영향이 없다.

이번 스윕 범위 밖인 이유: 이 스윕은 후속 과제 원장의 정산 회계를 다루고 이 검사는 라이브 노드의
운영 상태를 본다. 재실행 결과는 이전 웨이브(2026-07-18)의 `docs/qa/F4/` 증적을 덮어쓰므로
커밋하지 않고 복원했다 — 위 수치가 그 내용이다.

## 게이트웨이 커맨드 동기화 bulk 전환(2026-08-22) 중 발견한 후속 과제

429 자가지속 루프(8/18 Hermes 업데이트 후 safe sync가 커맨드 버킷을 소진, 매 부팅 재시도,
승인 요청 auto-thread 생성까지 429로 사망)의 근본대책으로 노드 두 계정에
`DISCORD_COMMAND_SYNC_POLICY=bulk` drop-in을 프로비저닝했다([소개](기능소개/게이트웨이-커맨드-동기화-bulk.md)).

- **설치기(`automation/install/plan.py`)는 구독자 노드 게이트웨이 unit에 이 drop-in을 렌더하지
  않는다** — 재시동이 잦은 구독자 노드는 같은 429 루프에 그대로 노출된다 → 설치기 unit 렌더에
  `30-command-sync.conf`를 추가한다. **구독자 노드 한정 · 심각도 낮음(우리 노드는 반영 완료)**.

## Hermes kanban 열린 이슈 정리(2026-08-22) 중 발견한 후속 과제

보드의 열린 카드 8건을 정리하며 healthcheck 허용목록 래퍼(`<primary-node>`·`<rag-node>`)를 재생성하고
runtime-package 프로브의 `cron/` 오탐을 고쳤다. 그 과정에서 드러난 구조적 틈이다.

- **수리 occurrence가 done 티켓에 계속 붙는다** — repair-detector가 signature로 기존 티켓을 찾을 때
  상태를 보지 않아, 완료된 healthcheck 티켓(t_6f3f7e1e)에 재발 occurrence가 55건까지 조용히 쌓였고
  (`created:false`) 보드의 열린 목록에는 아무것도 나타나지 않았다 → signature 티켓이 done/archived면
  새 티켓을 만들거나 reopen한다. **재발 가시성 영향 · 심각도 중**.
- **허용목록 래퍼의 드리프트 지문이 프로브 명령 본문을 덮지 않는다** — `wrapper_inputs_digest`는
  `LIVE_CHECKS`와 매니페스트 2종만 해시하므로, 프로브가 원격 명령 문자열만 바꾸면 지문은 같은데
  명령은 exit 126으로 거부돼 UNKNOWN이 조용히 난다(그래서 이번 runtime-package 수정은 릴리스 쪽
  필터만 바꿨다) → 지문에 기록된 명령 해시 목록을 함께 넣는다. **탐지 누락 위험 · 심각도 중**.
- **RAG 노드(`<rag-node>`)의 래퍼는 드리프트 프로브 대상이 아니다** — `healthcheck probe allowlist
  matches the checks`는 PRIMARY_NODE만 보고, RAG 노드의 래퍼는 07-14 손유지본(해시 3개)이라
  rag_stack 프로브와 그 티켓 명령이 거부되고 있었다(UNKNOWN·`REPAIR_TICKET_FAILED rc=126`). 이번에
  `--print <rag-node>`로 생성해 수동 설치했다(구본 `.bak-20260822-handmaintained`) → RAG 노드용 래퍼
  프로브 행을 추가한다. **수동 설치로 해소 · 심각도 낮음**.
- **mail `scenario.sh`의 파사드 probe도 호출자 cwd에 민감하다** — `skills/mail/scripts/scenario.sh`가
  topics와 같은 `PYTHONPATH=… python3 -c 'import automation.…'` 형태로 분기를 고르므로, 샌드박스가
  릴리스 루트를 cwd로 물려주면 probe는 import에 성공하고 격리 CLI는 다른 답을 낼 수 있다(topics는
  이 불일치로 ✅ 뒤에도 매 틱 `SCENARIO-FAIL evidence list`였다 — PR #242로 수정). mail은 오늘
  샌드박스를 통과했지만 같은 모양이다 → `python3 -I` + 명시 `sys.path`로 고치고 cwd=checkout 회귀를
  둔다. **잠재 플레이크 · 심각도 낮음(현재 통과)**.
- **수동 healthcheck 실행도 수리 occurrence를 낸다** — `read_only=true`를 찍으면서도 REPAIR_TICKET
  경로는 항상 활성이라 진단용 실행이 카드에 occurrence를 더한다 → 수동 진단용 opt-out(예:
  `HEALTHCHECK_NO_REPAIR=1`)을 둔다. **노이즈만 · 심각도 낮음**.

## 지식 계층 4단계(2026-08-22) 중 발견한 후속 과제

위키 스키마 v2와 Obsidian→위키 큐레이션을 착지시키며 남은 것들이다([소개](기능소개/위키-큐레이션과-스키마-v2.md)).
같은 묶음에 있던 「RAG compose 프로젝트 이름 분열」은 `compose.yaml`을 유닛과 같은 `personal_rag`로
맞추고 두 파일을 대조하는 회귀를 걸어 해소했다.

- **엔터티 추출이 원천 frontmatter에만 의존한다** — Obsidian 노트에 `entity` 키가 없으면 제안된 초안의
  `entity`가 비어 검색 앵커가 생기지 않는다(증류 LLM은 본문 요약만 돌려준다) → 앵커 없는 제안이 많아지면
  `twin_distill`처럼 LLM 출력에서 엔터티를 함께 받아 검증한다. **노트 제안 자체는 정상 · 심각도 낮음**.
- **`relations`는 저장·인덱싱까지만 흐르고 랭킹에 쓰이지 않는다** — `rank.py`가 소비하는 것은 `entity`와
  `event_date`뿐이다 → 관계 질의가 실제로 필요해지는 시점에 랭킹 신호로 승격한다. **심각도 낮음**.
- **컴파일된 `.pyc` 하나가 공개 릴리스를 막는다** — `tests/unit/test_f2_secret_pattern.py:32`는 규약대로
  토큰을 런타임에 조립하는데(`"ghp_" + (...)`), 컴파일된 `.pyc`는 그 결과를 **상수로 굳힌다**. 공개 export의
  스캔은 카나리아 설계상 `.gitignore`된 파일까지 훑으므로(`gitleaks dir`) 그 `.pyc` 하나로 릴리스가 멈춘다
  (2026-08-22 실측: 워킹트리 스캔 leak 1건 → 비-venv `__pycache__` 정리 후 0건) → export 절차에 정리 단계를
  넣거나 스캔에서 `*.pyc`를 제외할지 판단한다. **실제 비밀 아님 · 심각도 낮음(릴리스 때만 드러나는 운영자 함정)**.
- **`wiki_store.SCHEMA_GUIDE`가 "필수 키 5개 정확히, 그 외 키 금지"로 남아 있다** — v1 twin 키 때부터
  있던 불일치이고 v2로 더 벌어졌다. 거부 판정 자체는 정확하고 **안내 문구만 실제 허용 범위보다 좁다**
  → 스키마와 같은 자리에서 안내를 함께 갱신한다. **심각도 낮음**.

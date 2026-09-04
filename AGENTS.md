# AGENTS.md — autophagy-agents repo, orchestrator/agent instructions
> **Tool-call encoding**: Always write Korean (and other non-ASCII) strings in tool-call parameters as literal UTF-8; never as `\uXXXX` unicode escapes.


**Generated:** 2026-07-20 · **Updated:** 2026-07-30 · **Commit:** 157dbd1 · **Branch:** main

## OVERVIEW
cha의 개인 Hermes 에이전트 시스템. Discord로 타 연구자 에이전트와 상호운용하며, 설치별 `<primary-node>` / `<rag-node>` 역할로 구동한다. Python 3.12 **stdlib 중심**. 주 대화 모델은 `openai-codex/gpt-5.6-sol`(구독, LiteLLM 예산 밖), `glm-main`(LiteLLM)은 폴백+배치 파이프라인 전용(2026-07-22 스왑). **모든 외부효과는 소유자(cha) 승인 게이트 경유** — 이것이 아키텍처의 중심 불변식이다.

## STRUCTURE
```
autophagy/
├── skills/       # 19개 스킬 디렉터리 (18 기능 + hello-autophagy 데모). 각 <name>/{SKILL.md, scripts/*_cli.py, scripts/*watch.py, deploy.sh}. 에이전트가 스스로 만든 자가 스킬은 이 트리에 없다(각 계정의 `~/.hermes/skills`)
├── automation/   # 오케스트레이션·게이트·워처·배치 코어 (interop, repair, rag_ingest, report_hub, skill_generation, selfskill_audit, twin_distill, twin_observe, managed_skills, managed_sync ...)
├── configs/      # routing-policy.md, peers.example.yaml, sensitivity-rules.yaml, external-effect-tools.yaml, rag/, litellm-staging/
├── prompts/      # 버전형 LLM 프롬프트 자산
├── docs/         # guide/(설계규약) patch/(인프라 변경) qa/(W#-# 웨이브 증적) troubleshooting/
├── tests/        # unit/ + e2e/(drivers, scenarios, fixtures)
├── .omo/         # 계획·오케스트레이션 상태 — plans/autophagy-agents.md = 설계 단일 진실
└── AGENTS.md · README.md
```

## WHERE TO LOOK
| 작업 | 위치 | 비고 |
|------|------|------|
| 새 스킬 제작·배포 | `skills/<name>/`, `automation/deploy-skill.sh` | 4단계 게이트, `docs/guide/스킬-제작.md` |
| 외부효과 승인 게이트 | `automation/interop/external_effect_gate.py` | fail-closed, 해시 바인딩 |
| 인터롭 규약(보고/질의/조율) | `docs/guide/interop-규약.md`, `automation/interop/` | 단일 진실 |
| 워처/cron 설계 규칙 | `docs/guide/watcher-cron-설계규약.md` | no-agent cron 필수 규약 |
| 음성 녹취 → 회의록 | `skills/speechtotext/` | Drive 감시 폴더 → 전사본(.md) → meeting 체인. 전사는 기본 로컬(whisper.cpp) — 민감도 게이트는 텍스트만 보므로 외부 API는 원음을 게이트 **전에** 내보낸다 |
| 기능 현황·웨이브 맵 | `docs/features.md` → `.omo/plans/autophagy-agents.md` | 계획 문서가 단일 진실 |
| 의사결정 트윈 스키마 | `docs/guide/decision-twin-스키마.md` | 트윈 키·판단 근거 규약 |
| 라우팅·모델·예산 정책 | `configs/routing-policy.md`, `configs/*.yaml` | 주=gpt-5.6-sol, glm-main=폴백·배치, `<monthly-soft-cap>`/`<monthly-hard-cap>`, patent-sensitive=GLM 403 |
| 참고자료(근거) 조회 | `automation/drive_reference.py`, `skills/recall/scripts/recall_reference.py` | 소유자가 Drive 에 모아 둔 참고자료에서 근거 구절을 찾는 **읽기 전용** 경로. 루트는 `DRIVE_REFERENCE_ROOT`(기본 `KIMM`)이며 산출물 루트(`autophagy/`)와 **다른 트리**다 — 그 폴더는 소유자의 보관함이라 폴더를 만들지 않는다(`ensure_folder_path` 금지, `find_folder_path` 사용). 회의록은 참고자료를 발표자료와 같은 `Deck` 경로로 받아 민감도 게이트에 함께 합산하며, 질의는 회의 라벨·과제명에 **전사 본문 상위 낱말**을 더해 만든다. 읽을 수 없는 형식과 크기는 `reference_rank.refusal` 이 **내려받기 전에** 판정한다 |
| Drive 산출물 발행 | `automation/drive_outputs.py`, `docs/guide/drive-publish.md` | 스킬 산출물은 단일 루트 `autophagy/`에 파사드로만 발행 — 규약은 guide가 단독 소유. `project=` 를 주면 카테고리와 연도 사이에 **과제 한 단**이 들어가고(전사본·회의록), 주지 않으면 경로는 예전과 동일해 나머지 스킬은 무영향 |
| QA 증적 | `docs/qa/<wave-id>/` | 마스킹된 증적 (원시는 ops 전용) |
| Discord 2-서버·승인 채널 구조 | `docs/guide/discord-server-architecture.md`, `automation/interop/approval_surface.py` | **소유자 전용 승인=개인 서버 `#agent-chat`의 **요청별 스레드**(2026-09-01 — 요청 하나에 스레드 하나, 승인·리마인더·결과 통지가 거기서 완결되고 종결 시 상태 접두어+아카이브, 「요청별 승인 스레드 규칙」; v7~v8 은 kind별 고정 스레드 `승인-<kind>` 였고 v8 부터 repair 포함·Ops 봇 초대 전제, 초대 전 릴리스 금지) / 스킬 공급망 승인=개인 서버 `#approvals`**(배포·peer attest·발행·managed 활성화 — 2차 주체인 peer 봇이 같은 채널을 봐야 하므로). 893da68은 승인을 공유 Lab이 아닌 **개인 서버**로 옮긴 결정이며 공급망에 한정된다. 표면은 `approval_surface.py`가 단일 결정하고 conformance 테스트가 강제한다 — 산문이 아니라 코드가 진실. 해석 키는 `~/.hermes/interop/config.json`의 `agent_chat_channel_id`(미설정=fail-closed). 정기 통지(일일 지출·주간 동향·아침 감사·헬스체크·리마인더)는 별도 `#notifications` — `owner_notice_channel_id`(미설정=DM), 발신은 `automation/owner_notice.py` 파사드 단일 경로(ON-2 이관, ON-3 conformance: 파사드 밖 DM 오픈 신설=RED) |
| 장애 대응·운영 제약 | `docs/guide/incident-response.md`, `docs/guide/operations.md` | 원인 확인 전 재시작/설정변경/키 재발급 금지 |
| 관리형 스킬 발행 | `automation/managed_skills/`, `docs/guide/managed-skill-channel.md`, `docs/guide/manual-group-admin.md` | 발행자(cha) 전용. 대상 repo = 그룹 스킬 채널 `orientpine/ribosome`(private) — 코드 repo도, 공개 배포본도 아니다(「세 저장소 구분 규칙」) |
| 개인→그룹 스킬 제출 | `automation/managed_skills/submission_*.py` | personal provenance 재사용, 그룹 `#approvals` 검토, 자동 import 없음 |
| 관리형 스킬 구독 | `automation/managed_sync/`, `~/.hermes/managed-sync/` | 구독자(연구원) 전용 — `orientpine/ribosome`에서 받는다(소프트웨어 업데이트는 `cytoplasm`에서 별도로 온다). 틱 배포는 `deploy.sh`(Hermes cron) 또는 설치기 **opt-in** `--with-component managed-sync`(systemd) — 둘 다 같은 래퍼를 돌린다. **배달은 자동, 마운트는 아니다**(D3) |
| 공개 릴리스 컷 | `automation/public_export.sh`, `docs/guide/manual-maintainer.md` | private가 유일한 개발 origin, public(`orientpine/cytoplasm`)은 이 스크립트로만 갱신되는 일방향 파생본 — 손 push 금지(D8)
| 에이전트 자가 스킬 루트·감사 원장 | `<계정 홈>/.hermes/skills`(계정 소유 0700, 1차 루트), `automation/selfskill_audit/`, `~/.hermes/selfskill-audit/{state.json,ledger.jsonl}` | 자가 저작은 **승인 없이 착륙**하고 사후 감사한다(소유자 결정 2026-08-15 옵션 B). 관리자 배포 스킬은 `/srv/autophagy-skills/live`에 남아 Hermes `skills.external_dirs`로 읽기 전용 발견된다. 회수는 `hermes curator archive/pin`. 상세: [소개](docs/기능소개/에이전트-자가-스킨.md) |

## CODE MAP
| Symbol | Type | Location | Role |
|--------|------|----------|------|
| `evaluate_tool_call` | fn | `automation/interop/external_effect_gate.py` | 외부효과 denylist 게이트 — 읽기 허용, mutation은 소유자 승인 레코드 필요 |
| `DiscordTransport` | class | `automation/interop/discord_transport.py` | 순차 청킹 전송 + 429 Retry-After 백오프 (8 callers) |
| `origin_notice` | module | `automation/interop/origin_notice.py` | 승인 **결과** 통지 배달(주입 전용: api·transport_factory·fallback) — 레코드 `approval_thread_id`(요청별 승인 스레드)가 있으면 **스레드 생성 없이 거기에** 게시하고 `outcome=ThreadOutcome`(✅ 완료·⛔ 취소·⌛ 만료)이면 이름에 접두어를 붙여 아카이브(`close_thread`, best-effort `THREAD-CLOSE-FAIL`); 없으면 지시 메시지 앵커 스레드(400=재사용) → 채널 스레드 → 실패 시 `NOTIFY-THREAD-FAIL`+폴백. 전 스킬 공유 단일 구현, 사본 금지(「결과 통지 원채널 스레드 규칙」·「요청별 승인 스레드 규칙」) |
| `RequestThread` | class | `automation/interop/approval_surface.py` | 요청별 승인 스레드 스펙(제목·origin 채널/메시지 id; frozen) — `resolve_new_binding(kind, directory, owner, request=…)` 가 `AGENT_CHAT_THREAD` 안에서 `approval_directory.agent_chat_request_thread`(지시 메시지가 agent-chat 소속이면 앵커, 400=message id 재사용, 아니면 채널 스레드 `<라벨> · <제목≤40>`)를 고른다. 표면 facts 는 그대로라 정책 bump 없음(S6), `request=None` 은 레거시 kind 스레드. 범위 내 호출부는 `tests/unit/test_request_thread_adoption_conformance.py` 가 `request=` 를 강제 |
| coordination | module | `automation/interop/coordination.py` | 에이전트간 일정 조율 순수 상태머신 (deadlock/재협상 규칙) |
| `skill_gate` | module | `automation/skill_gate.py` | 스킬 배포 승인 게이트 (✅ 리액션, `GATE_DIR`, `APPROVAL_LOG`) |
| `deploy-skill.sh` | script | `automation/deploy-skill.sh` | 배포 4단계: SANDBOX→REVIEW→owner ✅→MOUNT. 요청만 올리면(`--request-only`) **✅ 한 번으로 2분 내 자동 마운트**. **`--release-approval`(VA-2)**은 per-skill ✅를 릴리스 승인으로 대체하되 SANDBOX·REVIEW·peer attestation을 유지한다. release plan은 매번 전체 governed skill digest를 담아 늦게 발견한 stale mount도 같은 release ✅로 수렴 가능하다. stage 3에서 node current의 canonical SHA가 로컬 HEAD와 같으면 이미 sealed release이므로 unsigned origin/main 재수렴을 생략하고, 다를 때만 기존 서명 수렴을 호출한다. 단독 수동 배포는 기존 per-skill 승인 그대로(§10-3 핫픽스 경로) |
| `repair_capability` | module | `automation/repair/repair_capability.py` | repair 티켓·occurrence HMAC capability의 경쟁 안전 발급·게시·검증·소급 보정 |
| `repair_report_queue` | module | `automation/repair/repair_report_queue.py` | capability-bound enum-only 보고 요청 큐 + 엄격 파서 + 신원 대조 영수증 기반 compact |
| `repair_report_send` | module | `automation/repair/repair_report_send.py` | repair lifecycle 보고 직접 전송 + watermark 범위 정확 조회 |
| `repair_report_consumer` | module | `automation/repair/repair_report_consumer.py` | agent 소유 내부 큐 executor — 카드 readback 전이, 의미 예약, 보고 확정, 불변 영수증 |
| `repair_report_reconcile` | module | `automation/repair/repair_report_reconcile.py` | ops 종결 lifecycle에서 누락된 보고 요청을 멱등 복구하는 보정기 |
| `repair_report_consume_watch` | cron | `automation/repair/cron/repair_report_consume_watch.py` | Discord 수신 없이 agent 소유 런타임의 repair 보고 큐를 실행하는 no-agent 래퍼 |
| `rag_ingest` | pkg | `automation/rag_ingest/` | 개인 RAG 인제스트 (content-hash 멱등, patent-sensitive 태깅) |
| `governed_copy_refusal` | fn | `automation/skill_mount.py` | **배포됨 ≠ 실행됨** 가드의 **단일 정의**(2026-09-03) — `governed_copy_refusal(skill, script, *, env)` 하나가 판정하고, 스킬은 `skills/<skill>/scripts/<skill>_governed.py`(calendar·budget·todo·wiki·coordination, **v1.1.0 에서 meeting·speechtotext·doctype·proposal·report·procurement·patent-prep·prompt 추가 = mutating CLI 14/14**)에 상수만 남긴 채 지연 import 로 위임한다. mail 은 `mail_runtime` 이 같은 함수에 위임하되 automation import 가 불가능한 사본을 위해 인라인 fail-closed 폴백을 유지한다(`<root>/mail/scripts` 가 있으면 거부). mutating CLI 를 가진 스킬이 전부 채택했는지, 판정 본문 사본이 없는지는 `tests/unit/test_governed_copy_guard_conformance.py` 가 기계적으로 강제한다 — 산문이 아니라 코드가 진실이다. 이 호스트에 `/srv/autophagy-skills/live/mail` 마운트가 있으면 그 실경로가 아닌 사본(관측 미러 `/srv/autophagy-agents`, 리뷰 체크아웃, 자가 루트)의 mutating 서브커맨드를 `STALE-SKILL-COPY-BLOCK`(exit 3)으로 거부하고 올바른 경로를 안내한다. 마운트 없는 호스트는 무가드, 마운트 판독 불가는 거부. live 루트 주입 이름은 `skill_mount.LIVE_ROOT_ENV` 하나 — 샌드박스(`deploy-skill.sh` stage 1 의 `env -i`)·REVIEW/peer attest(`scenario_runner._environment`)·e2e 가 자기 사본 루트를 선언한다. **2026-09-03 까지 샌드박스는 선언하지 않았다**: v1.1.0 에서 가드 채택 스킬 13개가 stage 1 에서 전부 `STALE-SKILL-COPY-BLOCK` 으로 막혀 SKILL-STALE 로 남았다(PR #377). 시나리오가 자기 CLI 를 다시 `env -i` 로 돌리면 그 변수를 넘겨야 하고(`test_governed_copy_guard_conformance.test_scenario_env_scrubs_forward_the_live_root` 가 강제 — coordination 이 exit 3 은 맞췄지만 마커가 가드 메시지였다), heredoc 은 `scripts` 디렉터리에서 직접 import 한다 — live 마운트(`/srv/autophagy-skills/releases/<skill>/<sha>`) 위에는 `skills` 패키지가 없어 `from skills.<skill>.scripts import …` 가 post-mount smoke 에서 죽는다(prompt·doctype; `tests/unit/test_scenario_deployed_layout.py` 가 그 레이아웃에서 시나리오를 돌린다, PR #378). 2026-09-01: 인용 릴리스 마운트 87분 뒤의 회신이 121커밋 뒤처진 미러에서 만들어져 인용 없이 나갔다 |
| `mail_attachment_archive` | module | `skills/mail/scripts/mail_attachment_archive.py` | 첨부 아카이브의 **Drive 를 모르는 절반**(2026-09-03 분리) — 런타임 state.db 에서 첨부를 발견하고, 안정된 원격 이름(NFC 정규화·240바이트 상한·`Save`/`Download all` 같은 가짜 이름 배제)을 정하며, `~/.hermes/mail-attachment-drive/archive.db` 스키마와 멱등 기록 계약을 소유한다. 순수 계획이라 네트워크 없이 시험하고, 디스크 계약(archive.db·folders.json·CLI 플래그)은 분리 전과 같다. 378 LOC 로 F2 등록부 예외였던 파일을 185/154 로 갈라 예외에서 내렸다 |
| `mail_attachment_drive_sync` | module | `skills/mail/scripts/mail_attachment_drive_sync.py` | 2026-08-29 소유자 지시로 시작한 MailOn 첨부파일 Drive 아카이브의 **실행·CLI 절반** — 계획·이름·상태 DB 는 `mail_attachment_archive` 가 갖고, 여기에는 `DriveClient` seam 으로 폴더 보장·upsert·owner-only·sha256Checksum 메타데이터 검증을 통과한 뒤 기록을 남기는 효과 경계와 플래그 배선만 남는다. mutating 동기화는 `governed_copy_refusal`로 `/srv/autophagy-skills/live/mail/scripts/` 실경로에서만 허용한다 |
| `reaction_approval` | module | `automation/interop/reaction_approval.py` | Discord 리액션 승인 transport + ✅→게이트 기록 전사 + 승인 스레드 재사용의 **공용 단일 구현**(2026-09-03). `plaud_sync` 와 `memory_relocate` 가 바이트 동일한 사본을 들고 있었던 이유는 설계가 아니라 회피였다 — `memory_relocate.effects_live` 를 import 하면 memory_curator 사슬 전체가 plaud 워처로 끌려왔다. 이 모듈은 memory_curator·memory_relocate·plaud_sync 를 하나도 import 하지 않으므로 복사 사유가 사라졌고, 리액션 읽기의 429 예산·404=MISSING·봇 플래그 판정을 한 번 고치면 두 워처에 동시에 반영된다. 메시지가 사라져 재게시할 때는 레코드의 `approval_thread_id` 를 먼저 재사용해 한 요청에 스레드가 둘 생기지 않는다 |
| `clone_lock` | module | `automation/obsidian_write/clone_lock.py` | `write_note` 가 쓰는 **공유 클론의 배타 lock**(비대기)·tmp_pack 잔해 청소·blobless fetch 설정. `plaud_sync` 와 `memory_relocate` 가 서로 다른 틱에서 같은 작업 트리를 만져 한쪽의 `git reset --hard` 가 다른 쪽이 방금 스테이지한 노트를 조용히 지웠다. lock 은 클론 **옆**에 둔다 — 클론 안은 `reset --hard`·`clean` 사정권이고 첫 클론 전에도 존재해야 한다. 진 쪽은 기다리지 않고 retryable 로 양보한다(규약 (n)). **class 기반 컨텍스트 매니저인 이유**: `contextlib` 생성기 형태는 frozen+slots 예외의 `__traceback__` 대입에서 `TypeError` 를 내 진짜 실패를 삼킨다. 회귀는 `tests/unit/test_obsidian_write_clone_lock.py` |
| `approval_kpi` | pkg | `automation/approval_kpi/` | 승인 원장 **read-only KPI 집계기**(K8, v1.1.0) — skill-gate `approvals.jsonl`·interop `PostingJournal` 을 읽어 kind 별 건수·일평균·p50/p95 대기·재요청률을 내고, `policy_table.py` 가 kind 별 TTL·리마인더를 **소스에서 읽은 값**으로만 적는다(모르면 UNKNOWN). `python3 -m automation.approval_kpi --root <dir> [--json]`, 빈 루트는 exit 0 + `no records`. 쓰기 0, 승인 불필요. 규약·표: `docs/guide/approval-kpi.md`, [소개](docs/기능소개/승인-원장-KPI.md) |
| `drive_client_cache` | module | `automation/drive_client_cache.py` | `DriveClient.ensure_folder_path` 의 폴더 id 캐시(`~/.hermes/drive-publish/folders.json`) 절반 — 캐시된 id 를 `files get fields=id,trashed,parents` 로 **재검증**하고 휴지통·부재면 그 키와 하위 키를 버리고 재조회한다(2026-08-26 옮겨진 옛 `회의록/2026` 폴더로 조용히 게시된 사고). Drive I/O 는 주입된 `run`·`find_folder`·`create_folder` 만 쓴다 |
| `relocation_store` | module | `automation/memory_relocate/relocation_store.py` | `RelocationStore` 의 단독 거처(2026-09-03 분리) — `effects_live.py` 를 261→219 pure LOC 로 내려 F2 예외 등록부에서 제거했다. `effects_live` 가 재수출하므로 호출부 무변경 |
| `stt_split` | module | `skills/speechtotext/scripts/stt_split.py` | 화자 배정 **직전**의 순수 분할 단계 — `split_on_turns` 가 턴 경계(겹침 ≥1초)와 15초 마크에서 가장 가까운 띄어쓰기를 찾아 문장을 자른다. 문장 하나에 화자 하나를 주므로 문장이 화자보다 길면 분리 결과가 문서에 도달할 수 없다(실측: 구두점 없는 4인 57초 샘플이 158자 한 문장으로 나와 통째로 화자1). `stt_polish`·`stt_blocks` 는 구두점을 보지만 여기서는 **시각**을 본다. 자를 근거(시각·띄어쓰기)가 없으면 그대로 통과시키고 절대 예외를 던지지 않는다(fail-soft) |
| `deploy_reconcile_backlog` | module | `automation/deploy_reconcile_backlog.py` | 릴리스 백로그 다이제스트 **문구만** — 미배포 커밋 수·경과일·origin/main·runtime 에 더해 `mirror_state` 를 실어 관측 미러가 dirty/ahead 로 동결됐으면 비파괴 복구(`git format-patch`, `reset --hard` 금지)를, behind 면 릴리스 후 자동 추종을 말한다. 통지 주기·에피소드 경계는 순수 상태 기계에 남겨 사고 시계와 산문이 한 파일에서 LOC 한도를 밀지 않게 한다 |
| `deploy_reconcile_mirror` | module | `automation/deploy_reconcile_mirror.py` | 관측 미러의 판정(`probe_mirror_verdict` — 공용 shell probe 결과만 사용)과 안전한 ff-pull 동기화. 통지용 상태 번역(`mirror-dirty`→`dirty` 등)과 실제 이동을 한 모듈에 모아, CLI 배선과 통지가 서로 다른 안전 규칙을 만들지 못하게 한다 |
| `local_log` | module | `automation/selfskill_audit/local_log.py` | 자가 스킬 감사의 **노드 로컬 원장**(2026-09-03) — 소유자 통지는 의도적으로 휘발성이라 조용한 감사를 나중에 되짚을 근거가 없었다. 매 실행(델타 0 포함)을 `<state root>/logs/selfskill-audit/<YYYY-MM>.jsonl` 에 덧붙이고, 기능 겹침은 `selfskill-audit/pending-overlaps.json` 에서 발생·갱신·해소로 추적해 미결 건수를 아침 보고에 싣는다. 디렉터리는 0700, 로컬 기록 실패는 `LOCAL-LOG-FAIL` 한 줄일 뿐 통지·워터마크·종료코드를 바꾸지 않는다 |
| `mail_quote` | module | `skills/mail/scripts/mail_quote.py` | 기관메일 회신·후속메일의 **원문 인용** 순수 로직 — wrapper `get --body` 의 mailon markdown 을 파싱해 `-----원본 메시지-----` 헤더+본문을 발송 argv 하단에 붙인다(vendor 에 답장 명령이 없어 새 compose 로 나가므로). draft `body` 는 검토용 회신문만, `quote` 는 인용, 둘 다 승인 해시에 바인딩. `reply_all_cc` 가 To∪Cc−소유자−발신자를 Cc 로 만들며, 인용은 실행 경계(mail_preflight)에서 다시 붙는다 |
| `mail_preflight` | module | `skills/mail/scripts/mail_preflight.py` | 엔티티 프리플라이트 실행 경계 — 소유자가 검토한 회신문만 재작성하고 `mail_quote.with_quote`로 인용을 다시 붙인다; 2026-09-03 전달 메일의 원문 인용 소실 사고를 막는다 |
| `triage_llm_routing` | module | `skills/mail/scripts/triage_llm_routing.py` | 다이제스트 GLM 불가 폴백·런 단위 래치 — 재시도 뒤 `LlmUnavailableError`인 비민감 호출만 codex 티어로 강등하고, `llm-calls.jsonl` 표식과 소유자 DM의 단일 경고 줄을 남긴다 |
| `recall` | module | `skills/recall/scripts/recall_cli.py` | RAG 검색 (v2: patent-sensitive는 주 모델 non-GLM 기계검증 시만 센티널 부착 포함, 그 외 제외 — GLM 폴백은 LiteLLM 센티널 403이 차단) |
| `obsidian` | module | `automation/rag_ingest/sources/obsidian.py` | Obsidian RAG 소스 (read-only git 미러, 민감 태깅) |
| `wiki_store` | module | `skills/wiki/scripts/wiki_store.py` | 위키 저장소 (5필수 + 트윈 키 스키마 v1) |
| `twin_consult` | module | `skills/wiki/scripts/twin_consult.py` | 트윈 판단 근거 랭킹/충돌 판정 (read-only) |
| `twin_distill` | pkg | `automation/twin_distill/` | LLM 기반 판단 근거 추출 (inferred) |
| `twin_observe` | pkg | `automation/twin_observe/` | 게이트 이력 기반 판단 근거 분석 (observed) |
| `ManagedManifest` | class | `automation/managed_skills/manifest.py` | 관리형 스킬 릴리스 매니페스트 (v1) |
| `resolve_signed_update` | fn | `automation/update_trust.py` | 공개 origin의 현재 head와 결합된 SSH-signed annotated tag만 업데이트 SHA로 승인 |
| `public_export.sh` | script | `automation/public_export.sh` | 공개 배포본 단일 갱신 경로 — fresh-history 스냅샷 커밋 + **그 커밋에** update-trust 서명 태그 + `push --atomic`이 한 실행 안에서 일어난다. 카나리아 검증된 gitleaks 스캔(private 워킹트리·전이력·공개 트리·공개 이력)·내보낸 트리에서의 `pytest tests/unit`·push 전 자체 `verify-tag`·push 후 원격 read-back을 모두 통과해야 성공한다 |
| `publish_cli` | module | `automation/managed_skills/publish_cli.py` | 관리형 스킬 발행 CLI (SSH 서명 태그) |
| `submission_cli` | module | `automation/managed_skills/submission_cli.py` | 개인 HEAD의 sha 고정 제출 + 기존 승인 표면 검토 요청(발행·마운트 없음) |
| `managed_sync` | pkg | `automation/managed_sync/` | 관리형 스킬 동기화 (fetch→verify→quarantine) |
| `managed_sync_watch` | cron | `automation/managed_sync/cron/managed_sync_watch.py` | 구독자 틱 **단일 구현** — Hermes cron(`deploy.sh`)과 systemd 타이머(`systemd/`, 설치기 opt-in)가 둘 다 이것을 돌린다. 겹친 틱=`FileKeyLease`로 무음 exit 0, 자식 env에 자격증명+두 runtime root 명시 전파, skill tag sync 성공 뒤 같은 mirror의 roster ref를 **별도 fetch**(부재/거부가 skill rc를 바꾸지 않음), staged 릴리스가 생긴 틱에만 기존 `owner_notice`로 best-effort 알림 1건(거부는 저널에만 — 매 틱 반복되므로 DM 홍수 방지) |
| `OPT_IN_COMPONENTS` | registry | `automation/install/components.py` | 설치기 opt-in 컴포넌트 단일 레지스트리. **이름을 대지 않으면 파일도 타이머도 생기지 않는다**(disabled가 아니라 absent). 멱등성은 기존 `build_plan`의 digest·활성타이머 대조에서 그대로 온다 |
| `install-managed` | verb | `automation/skill_store.py` | 관리형 스킬 전용 루트 설치 헬퍼 |
| `drive_taxonomy` | module | `automation/drive_taxonomy.py` | Drive 산출물 이름·배치 순수 로직 — 카테고리 레지스트리(단일 출처), 기간 키(ISO주/월/최초생성일), depth 5 상한, gate_only(patent) 거부 |
| `drive_outputs` | module | `automation/drive_outputs.py` | 산출물 발행 **단일 파사드** — (이름,부모) upsert로 사본 1개 유지, owner-only + 재다운로드 sha256 검증, 번들/companion 처리. `publish_best_effort`는 `DRIVE_PUBLISH_ENABLED=1` 옵트인. 사본 금지, `tests/unit/test_drive_outputs_conformance.py`가 강제 ([규약](docs/guide/drive-publish.md)) |
| `drive_migrate_outputs` | module | `automation/drive_migrate_outputs.py` | 레거시 Drive 배치 → 새 트리 1회성 마이그레이션 CLI — dry-run 기본, mutation은 `--apply`에만, 삭제 없이 trash만 |
| `drive_reference` | module | `automation/drive_reference.py` | 소유자 참고자료 폴더 **읽기 전용** 조회 — 옵트인(`DRIVE_PUBLISH_ENABLED=1`) 없으면 클라이언트도 만들지 않고, 루트가 없으면 **만들지 않고** `REFERENCE-ROOT-MISSING`. 깊이 4·폴더 60·파일 400 상한의 결정적 walk → 랭킹 → 상위 N건만 내려받아 근거 구절. **형식·크기는 내려받기 전에 판정**하므로 설문·구형 hwp·64MiB(`MAX_REFERENCE_BYTES`) 초과는 자리를 차지하지 않는다. 랭킹은 `reference_rank` 가, 본문은 `document_text` 가 소유하고 여기는 Drive I/O 와 오케스트레이션만 남는다 |
| `reference_rank` | module | `automation/reference_rank.py` | 참고자료 후보 줄 세우기 **순수 로직**(Drive 미의존) — `refusal()` 이 메타데이터만으로 거부 사유를 내고 그것이 정렬의 첫 열쇠라 **읽을 수 없는 파일이 fetch 슬롯을 못 먹는다**. 결과 순서는 `coverage`(맞은 낱말 **가짓수**) 우선 — 전사 본문에서 뽑은 질의어에는 아무 문서에나 있는 낱말이 섞이므로 횟수만 세면 엉뚱한 문서가 올라온다 |
| `document_text` | module | `automation/document_text.py` | 파일 → 순서 있는 단위별 본문 **단일 정의**(pdf·pptx·docx·hwpx·xlsx·md·txt·csv). raise 하지 않고 `Extracted.status` 로 사유를 돌려주며, 구형 바이너리 `.hwp` 는 **행동 가능한 안내**("hwpx 나 pdf 로 저장해 주세요")로 거부한다(hwpx-core 스킬도 같은 규칙). `meeting_slides` 는 이제 사본을 갖지 않고 여기에 위임해 `[슬라이드 N]` 라벨만 붙인다 |
| `drive_publish_cli` | module | `automation/drive_publish_cli.py` | 세션이 손으로 산출물을 발행하는 **유일한 명령** — 인자만 파싱해 `drive_outputs.publish` 에 넘긴다(업로드 로직 0, 그래서 conformance 가 그대로 성립). 모르는 kind·gate-only kind·**스킬이 소유한 kind(`meeting`·`transcript`)**·없는 파일은 Drive 를 건드리기 전에 거부(exit 2)하고 — 회의록을 손으로 써서 발행하면 원장을 거치지 않아 관리번호가 존재할 수 없다(2026-08-27 실측) —, `DRIVE_PUBLISH_ENABLED` 미설정이면 조용히 넘기지 않고 `DRIVE-PUBLISH-DISABLED` 로 실패한다(exit 3) — 손으로 돌리는 사람이 침묵을 성공으로 읽으면 안 된다 |
| `classify_save_request` | fn | `skills/doctype/scripts/doctype_routing.py` | 문서 저장 목적지 결정론 라우터 — 개인노트=Obsidian 단독 / 목적지 미지정=Drive 비공개 / 모호=clarify(fail-closed). `doctype_cli`가 mutation 직전에 강제(RTS-A) |
| `obsidian_write` | pkg | `automation/obsidian_write/` | Obsidian 승인형 쓰기 — RAG 미러와 **분리된 클론** + `-rw` 키, PARA 결정적 upsert → commit → push → 원격 read-back 해시 검증. 외부효과 게이트 바인딩(SI-5 개정) |
| `memory_routing` | pkg | `automation/memory_routing/` | ‘기억해’ 저장 분류·단일 흐름 — canonical=위키 노트(기존 wiki_gate 재사용), 짧고 안정적 사실만 MEMORY.md 병기, 임시 상태는 비영속, 모호하면 보수적 기본값 |
| `plaud_sync` | pkg | `automation/plaud_sync/` | Plaud lifelog 동기화 — MCP 를 에이전트에 등록하지 않고(no-agent cron 이 `npx @plaud-ai/mcp@0.3.10` 과 stdio JSON-RPC 로 직접 대화, 버전 고정) 녹음 건별 노트(`## 요약` 위+`## 전문` 아래)를 `~/.hermes/plaud-sync/notes/` 에 동결, 건별 소유자 ✅(OBSIDIAN_WRITE 재사용·요청별 스레드) 후 `obsidian_write.write_note` 로 push 하고 RAG 가 자동 소비한다. 본문 sha 불일치는 게시·push 모두 거부(fail-closed), 회의록 체인은 자동 호출하지 않는다. cron `plaud_sync_watch`(10분 틱, Plaud 폴은 내부 30분 게이트). [소개](docs/기능소개/plaud-lifelog-동기화.md) **2026-09-02 v2**: 카드가 동결 본문 `## 요약`(없으면 `## 전문`) 상위 5줄을 인용한다(`render.summary_preview`, 바인딩 해시 불변). 카드 **형식**만 바뀌면 파사드는 재게시하지 않으므로 `plaud_sync_watch.py --repost-posted` 가 정상 틱(✅ 소비)→옛 카드 삭제·planned 복귀(`repost.py`)→틱 순으로 **같은 스레드**에 다시 올린다 — `agent_chat_request_thread` 는 조회 없이 매번 새 스레드를 만들기 때문에 `effects_live.thread_candidates` 가 레코드의 `approval_thread_id` 를 재사용 후보로 넣는다. 상태 조회는 governed 스킬 `skills/plaud`(`plaud_cli.py status`, stdlib·읽기 전용, 트리거 "plaud 상태"). **2026-09-04 v3(로컬 전사)**: 발견은 `transcribing` 으로 동결하고, 틱이 watch.lock 을 **푼 뒤** `transcribe_live` 가 `pipeline_lock`(speechtotext 와 공유) 아래 `get_file` presigned URL 로 오디오를 내려받아 speechtotext CLI(`SPEECHTOTEXT_BACKEND=local` 고정·`DRIVE_PUBLISH_ENABLED=0`)로 전사한 뒤 `transcripts/<stem>.md` 에 남기고 노트 `## 전문` 을 그것으로 재조립해 `planned` 로 올린다(commit 은 watch.lock blocking 재획득 + 상태·hash 재검사). 환경 실패(rc 3/4·MCP·네트워크)는 무카운트 재시도, 녹음 실패는 2회면 클라우드 전사 폴백(출처 줄에 명시). 킬스위치 `PLAUD_SYNC_TRANSCRIBE=0`. [소개](docs/기능소개/plaud-녹음-로컬-전사.md) **2026-09-04 노트 v2 양식(B안)**: 노트가 소유자 Obsidian Linter 가 그대로 두는 frontmatter(tags→title→source→created→modified, 필요할 때만 따옴표 — vault 의 실제 플러그인 빌드로 61개 제목·렌더 샘플 3종을 헤드리스 멱등 검증, `docs/qa/PLV2`)로 시작하고 `## 한눈에`(녹음·주제·사람·장소·한 줄 Dataview 필드) → `## 요약` → `## 결정 · 할 일` → 접힌 `## 전문` → 출처. 사람·장소·결정·할 일은 `lifelog_extract_live.build_extractor`(규칙 파일→patent-sensitive→`LITELLM_AGENT_KEY`→템플릿 순 게이트, glm-main)가 **로컬 전사가 들어온 `transcribe.finalize`** 에서 뽑고(클라우드 초안은 LLM 미호출), 생략 사유는 한눈에 줄에, 전송·파싱 실패는 무카운트 대기. `obsidian_write.render_note` 는 body 선두 frontmatter 를 제목 위로 올리고 callout 을 생략한다(그 외 노트 바이트 동일). 카드 v3 는 한눈에 줄을 먼저 인용. [소개](docs/기능소개/plaud-lifelog-노트-v2-양식.md) |
| `entity_preflight` | pkg | `automation/entity_preflight/` | 외부 쓰기 전 개인 고유명사 해석 — 관계 기반 질의 재작성 + 로컬 RAG·주소록 대조, 고신뢰 단일 후보만 자동 정규화, 모호하면 `ENTITY-CLARIFY`(승인 게이트 아님) |
| `todo` | skill | `skills/todo/scripts/todo_cli.py` | Google Tasks 쓰기의 리포측 소유자 — `gws_tasks_mutation` denylist 규칙로 게이트 경유, 등록 후 `tasks get` 재조회 검증. 쓰기는 **소유자 ✅ 사이클**을 먼저 거치며, 승인 표면은 산문이 아니라 `approval_surface`가 정한다 — 정책 v7에서 `ApprovalKind.TODO`는 `#agent-chat`의 `승인-todo` 스레드다(오너 DM 아님). `todo_approval*.py`(승인 파사드 producer; `request`가 동결한 tasklist·제목·notes·due를 레코드에 함께 적는다 — 마스킹된 `argv_summary`만으로는 무엇을 쓸지 복원할 수 없다) · `todo_confirm_reaction_watch.py`(owner-only 리액션 폴링 **+ 같은 틱에서 승인된 등록까지 수행**: `execute_approved_writes()`가 (archive된 approved 세대 × claim receipt)로 매 틱 유도하는 멱등 리컨실러라 상태를 따로 저장하지 않고, 중간에 죽어도 다음 틱이 이어받되 두 번 쓰지 않는다) · `todo_execution_claim.py`(`O_EXCL` 일회 claim; `write_started` 잔존 시 exit 7로 자동 재삽입·거짓보고 모두 거부). 상세 [소개](docs/기능소개/todo-승인-후-자동-등록.md) |
| `provision-skill-roots.sh` | script | `automation/provision-skill-roots.sh` | 스킬 루트 토폴로지 **반전** 프로비저너(멱등, root 실행) — 레거시 read-only bind 해제와 fstab 2행 제거, agent 1차 루트를 0700 agent 소유로, `.hub` 상태를 taps.json sha256 readback과 함께 이관, config에 `external_dirs`+`guard_agent_created` 패치(기존 `skills:` 블록은 넘겨짚지 않고 `SKILLS-BLOCK-BLOCK`으로 멈춤), 끝에 `hermes skills list`로 external 발견 선검증. peer arm은 잔여 사본을 3중 조건(이름·author 마커·repo 존재)으로만 정리하고 현역 자가 스킬 2개를 pin |
| `speechtotext` | skill | `skills/speechtotext/scripts/speechtotext_cli.py` | 음성 → 전사본(.md) → **meeting CLI 자식 호출**로 회의록. 회의록 도메인(민감도 게이트·칸반·통지·Drive 발행)은 meeting 이 그대로 소유하고 여기서 재구현하지 않는다. 전사 백엔드는 `auto`(로컬 whisper.cpp 우선)·`local`(도구 없으면 exit 4, **네트워크 폴백 금지**)·`api`(OpenAI 호환, 25MiB 초과 시 15분 창+10초 겹침 분할). Drive 감시 폴더는 `speechtotext_drive_watch.py`(no-agent cron, 폴더 미설정=무동작·소유자 단독 소유만·성공 후 마킹). 전사본은 **한 줄에 한 문장**이고 블록마다 `[HH:MM:SS] 화자N · 이름` 헤더가 붙으며, 화자 분리(`stt_diarize`)와 이름(`stt_speakers`)은 둘 다 fail-soft — 없으면 화자 없는 문장 줄 문서가 그대로 나온다 |
| `stt_blocks` | module | `skills/speechtotext/scripts/stt_blocks.py` | 전사본 본문의 **문법 한 곳** — 블록(빈 줄로 구분) + 한 줄에 한 문장, 헤더 `[HH:MM:SS] 화자N · 이름`. whisper 토큰 오프셋을 문장 구간으로 되살려 각 블록이 언제 시작했는지 적는다(예전 경로는 구간을 한 문자열로 이어 붙이며 그 값을 버렸다). `render()` 가 쓰고 `parse()` 가 되읽어 디스크의 옛 문단 전사본과 새로 다듬은 전사본이 같은 문서로 수렴한다. 화자가 없을 때만 옛 문단 규칙(4문장·180자)으로 끊는다 |
| `stt_diarize` | module | `skills/speechtotext/scripts/stt_diarize.py` | 화자 분리 — sherpa-onnx 바이너리를 노드에서 실행(**음성은 노드 밖으로 나가지 않는다**), 진단 섞인 stdout 에서 turn 줄만 파싱, 문장은 겹침 최대 turn → 2초 내 최근접 turn → 직전 화자 순으로 배정. 도구·모델이 없거나 실패하면 `DIARIZE-FAIL <사유>`(stderr) 후 화자 없이 계속하는 fail-soft이며, 전사를 절대 중단시키지 않는다 |
| `stt_speakers` | module | `skills/speechtotext/scripts/stt_speakers.py` | `화자N` 라벨의 **이름과 그 출처** — 자기소개 규칙(각 화자 앞 12문장, 한 이름을 둘이 주장하면 폐기)·LLM 제안·소유자 override 를 신뢰 순(소유자>자기소개>LLM)으로 병합하고, 규칙과 LLM 이 어긋나면 `LLM 제안: <이름>` 을 근거로 남긴다. 범례 `- 화자: …` 한 줄을 쓰고 되읽는다(미상도 적는다 — 누락과 미상은 다른 사실) |
| `stt_speaker_flow` | module | `skills/speechtotext/scripts/stt_speaker_flow.py` | 전사 → 다듬기 → 이름 → meeting 자식 호출을 잇는 배선. meeting `ingest` 의 **마지막 stdout JSON** 의 `speakers` 만 받아들이고, 범례가 실제로 바뀔 때만 전사본을 다시 쓴다(정본은 하나). 첫 다듬기의 교정 영수증은 보존하고 두 번째 패스는 이름만 렌더한다 |
| `stt_polish` | module | `skills/speechtotext/scripts/stt_polish.py` | 전사본을 **읽을 수 있게** 다듬는다(요약 아님) — 문장 분할 후 `stt_blocks` 블록으로 묶기(한 줄 한 문장 + 화자·시각 헤더 + 머리말 화자 범례), 연속 완전중복만 접기, 용어집 치환. 같은 용어집이 전사 전 `--prompt` 힌트와 전사 후 치환에 함께 쓰인다. 해석(결정사항·액션아이템)은 meeting 이 소유하고 이 모듈은 그 경계를 넘지 않는다 |
| `stt_coverage` | module | `skills/speechtotext/scripts/stt_coverage.py` | 장시간 녹취의 **조용한 잘림** 탐지 — 구간 타임스탬프 합집합 vs `ffprobe` 실제 길이. 침묵은 결함이 아니므로 커버리지 비율로 판정하지 않고 설명되지 않는 앞/뒤·내부 공백만 문제 삼으며, 길이를 모르면 완결을 주장하지 않는다(불충분하면 exit 8) |
| `meeting_minutes` | module | `skills/meeting/scripts/meeting_minutes.py` | 회의록 문서 서식의 **단일 정의** — 메타 헤더→한눈에 보기→결정사항→액션 표→마일스톤→논의→미결→다음 회의→부록 순서, 빈 조건부 섹션 생략, 그리고 **근거 하단 규칙**(본문엔 `[근n]` 마커만, 근거 원문·`[E1]` 출처·원문 전사본은 `## 부록 · 근거와 원문` 아래). `[^1]` 각주 문법은 쓰지 않는다 — 지원 렌더러가 정의를 문서 끝으로 옮겨 우리 제목을 비우고 Drive 프리뷰 지원도 미확인이다. `meeting_actions.write_note`는 이 렌더러에 위임만 하며 사본 금지(고정: `tests/unit/test_meeting_minutes.py`). `--project` 가 있으면 이 순서 대신 **과제 회의록 양식의 절 순서**(`meeting_template`)로 배치하고, 어느 경로든 문서 끝은 미결·신규 **Action Item 표**다 |
| `meeting_template` | module | `skills/meeting/scripts/meeting_template.py` | 과제 회의록 양식(.md/.markdown/.txt)의 절 번호·제목·순서를 파싱해 회의록 블록을 그 자리에 넣는다 — 절 제목을 슬롯으로 분류(`classify`), 하위 절이 이미 채우는 상위 절은 제목만 찍고(`_covered`), 회의가 채울 것 없는 절은 `- (해당 없음)`. 양식이 없거나 못 읽으면 호출부가 내장 골격으로 되돌아간다 |
| `meeting_action_db` | module | `skills/meeting/scripts/meeting_action_db.py` | 과제별 action item 원장(`action-items.csv`) 의 **단일 정의** — 로드/덤프, 미결·신규·완료 병합(`merge`; 완료 행은 삭제하지 않고 status만 전환), 그리고 `관리번호 \| 내용 \| 조치기한 \| 담당기관` 표 렌더. `render_sections` 와 `split_sections` 는 서로의 역이라 같은 모듈에 둔다 |
| `meeting_action_id` | module | `skills/meeting/scripts/meeting_action_id.py` | 관리번호 10자(`<영문4><연도2><일련4>`, 예 `HOGS260015`) 의 발급·파싱 — 한글 초성 로마자화로 후보 코드를 만들고, `project-codes.csv` 레지스트리와 대조해 **전 과제에 걸쳐 유일한** 코드만 배정한다(충돌 시 마지막 글자를 민다). 일련번호는 같은 코드·연도 중 최대치 +1 이고 과제·연도당 10,000건이 한도다. **번호는 발급 시점에 정해져 바뀌지 않는다** — 미결/신규는 번호의 속성이 아니라 상태이며, 한도 소진은 `ActionIdError` 로 fail-closed 하고 CLI 가 그것을 받아 회의록을 잃지 않는다 |
| `pipeline_lock` | module | `automation/pipeline_lock.py` | 녹음→전사본→회의록 파이프라인의 **단일 lock**(`hold` 는 2026-09-03 부터 class 기반 CM — `contextmanager` 생성기는 frozen+slots 예외를 `TypeError` 로 가린다, `clone_lock` 과 같은 이유). `speechtotext_drive_watch` 와 `meeting_pending_transcript_watch` 가 같은 파일을 잡는다 — 전사본 발행과 회의록 생성 사이(실측 258.9초)가 야간 워처의 미처리 판정 조건과 정확히 겹치고, `*/5` 는 `:00` 에도 돌아 자정에 둘이 동시에 시작하기 때문이다. 워처마다 자기 lock 을 두면 자기 겹침만 막고 서로는 모른다. 잡지 못하면 rc 0 양보(다음 틱), lock 을 열지도 못하면 진행하지 않는다(fail-closed). 규약 (n), 회귀 `tests/unit/test_pipeline_lock.py` |
| `meeting_pending_transcript_watch` | cron | `skills/meeting/scripts/meeting_pending_transcript_watch.py` | 매일 **00:00 KST** 미처리 전사본을 회의록으로 만드는 no-agent 워처. Drive 만 폴링해 실시간 에이전트와 경쟁하지 않고, `~/.env.secrets` 를 자가 로드해 `DRIVE_PUBLISH_ENABLED` 까지 자식 env 에 명시 전파한다(그게 빠지면 원장 없는 회의록이 조용히 나온다). 마운트는 `automation/skill_mount.py` 가 단독 판정하고 못 찾으면 자식을 띄우지 않는다. **stdout 이 곧 통지**라 만들 것이 없는 밤은 침묵한다. 다건은 상한까지 순차 처리 — 대화형 `!meeting` 의 "고르지 않고 멈춤"을 야간에 그대로 쓰면 매일 밤 같은 이유로 선다. cron 표현식은 노드 TZ(UTC)가 아니라 **스케줄러의 +09:00** 으로 해석된다 |
| `meeting_runtime` | module | `skills/meeting/scripts/meeting_runtime.py` | 마운트된 스킬이 `automation` 을 찾는 **단일 정의**. 갈라져 있던 동안 `meeting_project._repo` 만 `parents[3]` 깊이 추측을 들고 있었고, 라이브 마운트 실경로(`/srv/autophagy-skills/releases/meeting/<digest>/scripts`)에 repo 가 없어 `ModuleNotFoundError` 로 죽었다 — 그 실패가 `BOARD-FETCH-FAIL` 로 삼켜져 양식·원장·미처리 전사본 조회가 전부 조용히 무력했다(2026-08-28 실측). 회귀는 마운트와 같은 깊이에서 별도 프로세스로 import 하는 `tests/unit/test_meeting_runtime_root.py` 가 고정한다 |
| `meeting_project` | module | `skills/meeting/scripts/meeting_project.py` | 과제 폴더의 Drive 상태(양식·원장·레지스트리)를 읽고 쓰는 **best-effort 경계** — 실패는 `BOARD-FETCH-FAIL`/`BOARD-SAVE-FAIL` 한 줄로 축약되어 회의록 생성을 절대 막지 않고, 읽을 수 없는 양식(.hwp/.docx)은 추측 대신 `TEMPLATE-UNREADABLE`. 과제 없음·민감 회의·`DRIVE_PUBLISH_ENABLED≠1` 이면 Drive 를 아예 건드리지 않는다. `pending_transcripts` 는 `전사본/<과제>/<연도>/` 를 훑어 회의록 폴더에 `회의록-<전사본 파일명>` 이 없는 것만 고른다 — 이름 대조이지 내용 해시가 아니다(전사본이 다시 다듬어져 발행되면 지문은 바뀌지만 같은 회의다). `detect_project` 는 `--project` 도 전사본 경로도 없을 때 라벨을 **이미 있는 과제 폴더 이름**과 대조해 과제를 찾는다 — 지어내지 않으므로 없는 폴더를 만들지 않고, 중첩 이름은 최장 일치, 무관한 두 이름이 함께 걸리면 아무것도 고르지 않는다(엉뚱한 과제에 원장을 쓰는 것이 과제 없이 가는 것보다 나쁘다) |
| `meeting_types` | module | `skills/meeting/scripts/meeting_types.py` | 추출 결과의 **모양**(데이터클래스)과 인용 정리 변환 하나. `meeting_schema`(LLM JSON 을 어디까지 봐줄 것인가)와 갈라져 있고 파서를 import 하지 않는다(순환 없음) — `meeting_schema` 가 전부 재수출하므로 기존 호출부는 그대로. `ResolvedAction.id` 는 생성 산문이 아니라 원장 키라 정리 대상에서 제외한다 |
| `meeting_slides` | module | `skills/meeting/scripts/meeting_slides.py` | 발표자료(pdf/pptx/md/txt) 텍스트 추출 — 대명사·모호 지시어 교정 재료. **fail-soft**(스캔본·미지원·부재는 예외가 아니라 `Deck.status`로 돌아오고 ingest는 계속) + **fail-closed**(`gate_text()`로 회의 본문과 함께 민감도 게이트에 합산 — 빠뜨리면 특허 슬라이드가 GLM으로 샌다) |
| `selfskill_audit` | pkg | `automation/selfskill_audit/` | 자가 스킬 사후 감사 — `ledger.audit()`가 계정 스킬 루트와 `.archive`를 스캔해 콘텐츠 해시(`skill_review.skill_digest`) 기준 델타(created/edited/archived/restored/removed — 아카이브 없이 사라진 것도 보고)를 append-only `ledger.jsonl`에 적재, `report.run_once()`가 미보고분만 마스킹 요약으로 소유자 DM한 뒤 watermark를 전진시킨다(전송 실패시 전진 없음). 아침 보고에는 기능 겹침 advisory(SC-4, `overlap.py` — 다른 이름·같은 기능 자가 스킬을 description·tags 토큰 containment ≥0.5·겹침 ≥5 로 `OVERLAPS-GOVERNED:<skill>` 보고, 오탐 상한은 governed 18개 상호 대조 0건으로 회귀 고정)가 함께 실린다. cron `selfskill_audit_watch.py`(agent·peer 각 09:00 no-agent) |
| `state_backup` | pkg | `automation/state_backup/` | 주 1회 암호화 상태 백업(SC-3) — `~/.hermes` allowlist tar → 로컬 키(`~/.hermes/backup/backup.key`, 부재/노출 권한=fail-closed, 키 미업로드) openssl 암호화 → 기존 `DriveClient`로 전용 루트 `autophagy-backups/<계정>/` 업로드(owner-only+read-back) → 8세대 밖 trash. 매일 03:15 cron + 배달 주간 워터마크(검증 후에만 전진)로 주 1회·실패 주 재시도. 백업은 산출물이 아니라 taxonomy 비적용, Drive I/O 는 저수준 클라이언트 재사용이라 conformance 성립. [소개](docs/기능소개/로컬-상태-주간-암호화-백업.md) |

| `deploy_all` | script | `automation/deploy_all.sh` | origin/main **전량 수렴** 오케스트레이터(RC-3/4) — 판정은 노드 릴리스 트리의 `deploy_all_probe.py`(관측)+`deploy_all.py`(순수 판정)가 자기 세대 코드로 내고, 실행은 기존 배포기 호출뿐(사본 0). 스킬 action은 반드시 `deploy-skill.sh <skill> --release-approval`로 릴리스 ✅ 하나를 재사용한다(단독 핫픽스는 기존 per-skill 경로). `--plan` 판정 / `--verify` 전량 일치 시 영수증(`/srv/autophagy-private/deploy-all/receipt.json`) / `--apply` 배포기 실행→플러그인 변경 시 agent+peer 재시동→**전량 재판정이 하드 게이트**. 영수증은 clean 에만 서명되고(코드가 거부) `release_fully_deployed` 로컬 프로브가 현재 릴리스와 상시 대조 — 없음·불일치·판독불가 전부 FAIL. ⑤root·⑥RAG 는 상시 프로브 위임(`delegated` 필드). **미선언 관측(2026-09-03)**: 프로브는 선언된 행만 대조해 `~/.hermes/scripts` 의 손배포 사본을 보지 못했다 — 이제 매니페스트 계정마다 `scripts/`·`plugins/*/` 를 열거해 `HOME_DEPLOYED_PATTERN` 에 맞지만 선언되지 않은 경로를 `undeclared` 관측(계정·경로·sha 접두)으로 내고, 판정은 **경고**라 clean 영수증을 막지 않되 `--strict-undeclared` 면 drift 로 승격한다. 열거 실패는 여전히 관측 불가(exit 4), JSON 스키마는 키 추가만 |
| `release.sh` | script | `automation/release.sh` | 릴리스 버전 승인→서명 태그 컷(VA-1, **워크스테이션 전용**) — **`--bump {major,minor,patch}`(기본 patch, v1.1.0)** 로 버전 자리를 고르고, `release_plan.major_signals` 가 base..head 의 `POLICY_VERSION`·`SCHEMA_VERSION` 상수·`node_config._FIELD_NAMES`·interop 필수 키 변경을 찾으면 `--bump patch/minor` 를 `RELEASE-PLAN-BLOCK`(exit 4) 으로 거부하고 `--bump major` 의 패치노트에 `MAJOR: 운영자 조치 필요 —` 줄을 싣는다(승인 레코드 스키마 불변 — 산문만). `ensure_signed_tag` 는 같은 HEAD 에 다른 이름의 태그가 있으면 성공으로 끝내지 않고, prerelease 접미 태그는 최신 판정에서 무시한다; `release_version_for` 가 HEAD 에 이미 붙은 태그를 재사용해 **재실행이 재개**가 된다 — 완결기가 deploy 실패 뒤 next 를 다시 계산하면 그 이름 불일치 검사가 자기 태그를 거부한다(2026-09-03 v1.1.1 attempt 2 실측, PR #378)([소개](docs/기능소개/릴리스-버전-자리-선택.md)) — 새 본문은 사람이 이해할 bundle 이름·커밋 설명·전체 action hash를 우선한다. **renderer는 append-only 버전형**: 필드 없는 기존 레코드=v1 동결 문구, 신규=`render_version=2`; 게시된 문구를 같은 버전에서 바꾸면 과거 ✅가 무효가 되므로 금지. 승인은 노드 agent가 게시·판정하고, 다음 요청 전 `retire --head <latest-signed-head>`가 저장 HEAD 일치+Discord APPROVED를 재검증한 이전 레코드만 byte-exact 0600 `release-history/`로 원자 이동한다. 기본은 결정까지 무기한 대기해 ✅ 즉시 태그를 자른다. 낡은 pending 요청은 감사형 abandon 후 1회 재요청하며 결정 레코드는 불변이다. 전량 반영·영수증은 `deploy_all.sh --apply`가 완성한다 |
| `provision-healthcheck-probe.sh` | script | `automation/provision-healthcheck-probe.sh` | healthcheck SSH 강제명령 래퍼의 owner-run 멱등 설치기(RC-2) — 기존 관측 생성기 `healthcheck_probe_wrapper.sh --print`만 호출해 bash 문법검사→동일 바이트+0755 무동작→같은 디렉터리 원자 교체→sha256 read-back. `healthcheck_allowlist_manifest.sh --probe-hashes`·래퍼·입력 지문이 생성기의 같은 `sha256<TAB>command`를 공유하며, 기존 생성기 `--install`도 여기로 위임(설치 사본 0). 실행은 노드 소유자 작업이라 세션은 안내만 남긴다 |
| `watcher_manifest` | module | `automation/watcher_manifest.py` | 계정 홈 배포물 선언의 **단일 정의**(RC-1) — 선언은 각 배포기 옆 `deploy-manifest.txt`, 중앙 `configs/watcher-deploy-manifest.txt` 는 `emit` 서브커맨드의 파생물(손 편집 금지). 파생 일치·선언 누락·소유 정합·목적지 유일성은 `tests/unit/test_watcher_manifest_declarations.py` 가 강제하고, 홈 배포물 모양(`scripts/`·`plugins/`)은 `HOME_DEPLOYED_PATTERN` 이 단일 정의한다 |
| `skill_mount` | module | `automation/skill_mount.py` | governed live 마운트 경로의 **단일 정의** — 다섯 no-agent cron 래퍼(budget·report·coordination·calendar·research-trends)가 여기서만 판정한다. 해결할 수 없으면 미마운트로 fail-closed 하며 자가 스킬 루트(`~/.hermes/skills`)로 **폴백하지 않는다**(그 루트는 배포본을 담지 않는다). 드리프트는 `tests/unit/test_skill_mount_definition.py` 가 잡는다 |
| `gateway_runtime_probe` | module | `automation/hermes_compat/gateway_runtime_probe.py` | 게이트웨이 무재시동 드리프트(기동 시각 < 벤더 소스 mtime)와 agent·peer 소스 지문 갈라짐을 판정하는 **탐지 전용** 프로브. `patch_state.py` 와 같은 계약 — 벤더 CLI·원격 셸·유닛 관리자를 부르지 않고, unknown(2)이 실패(1)보다 높다 |
| `approvals_send_log_audit` | module | `automation/final/approvals_send_log_audit.py` | F4 승인-전송 대조를 `approval-missing`·`send-log-row-missing`·`method-not-matched` 로 사유별 분류(분류는 면제가 아니라 설명 — 셋 다 unmatched 로 세고 exit 1). `f4_scope.sh` 가 노드 stdin 으로 흘려보내므로 stdlib 전용·부수효과 없음 |
| `healthcheck_probe_evidence` | script | `automation/healthcheck_probe_evidence.sh` | 프로브별 rc·소요 ms·transport/service 경계를 비밀 없이 기록(명령 출력·URL·계정·SSH 대상 미포함). 기록 실패가 프로브 판정·종료코드를 바꾸지 않는다 |
| `release_floor` | fn | `automation/update_trust_state.py` | 롤백 방지 앵커는 root 소유 `/var/lib/autophagy/update-trust/release-floor.json`. ops 사전게이트 `advance_release_floor` 는 읽고 비교만 하고, root 헬퍼 `privileged_advance_release_floor` 만 서명 재검증 뒤 단조 전진시킨다 — 부모 디렉터리가 root 0755 라 ops 는 앵커를 지울 수 없다 |
| `local_ci` | script | `automation/local_ci.sh` | PR 전 검증 단일 진입점 — 워크플로와 같은 세트(lint · `pytest tests/unit` · 빈 `python:3.12-slim` 컨테이너의 설치기 dry-run)를 돌리고 **전 단계 통과 시에만** tree 키 영수증(`~/.hermes/local-ci/<tree>.json`, 0700/0600)을 발급한다. `verify <sha>` 는 `automation/hooks/pre-push` 가 부르며 영수증 없는 브랜치 push 는 거부된다 — 브랜치 보호가 403 이라 push 가 유일하게 구속력 있는 길목이다 |
| `merge-pr.sh` | script | `automation/merge-pr.sh` | PR 머지의 **유일한 경로** — OPEN·base main·모든 체크 완료+성공일 때만 머지한다. 체크 0건은 PENDING 이지 통과가 아니다(PR #269 의 구멍). **태그는 자르지 않는다**(VA-3: 머지=축적, 태그·배포 인가는 `release.sh`). 탈출구 `MERGE_PR_ALLOW_UNCHECKED=1` 은 체크가 **나오지 않을 때만**이고 실패한 체크는 덮지 못한다 |
## CONVENTIONS (repo 고유)
- **stdlib 전용 지향.** `from __future__ import annotations` + `@dataclass(frozen=True, slots=True)` + 엄격 타입(`TypeAlias`/`Protocol`). 외부 의존은 함수 내부 lazy import + fail-closed 가드(참조 procurement `_import()`).
- **모든 외부효과(메일·캘린더·예산·배포·위키)는 소유자 승인 게이트 경유** — 직접 실행 금지. 승인 이모지 ✅/⛔ (아래 Owner-confirm 규칙).
- **"커밋됨 ≠ 배포됨".** 배포 표면은 **셋이고 서로 닿지 않는다**: ① 릴리스 트리 `/srv/autophagy-agent-current` — 2분 리컨실러가 서명 태그를 보고 자동 수렴. ② 스킬 마운트 — 판정은 `readlink /srv/autophagy-skills/live/<skill>` 해시, 갱신은 소유자 ✅. ③ **계정 홈**(`~/.hermes/scripts/`·`~/.hermes/plugins/`) — 선언된 홈 패키지는 VA-2(2026-08-31)부터 `release.sh` → `deploy_all.sh --apply`로 자동 수렴하고, 선언 밖 사본만 사람이 해당 `deploy.sh`를 돌려 갱신해야 한다; 낡아도 ①②는 멀쩡해 보인다. ③의 드리프트는 `configs/watcher-deploy-manifest.txt` + healthcheck 프로브가 유일한 탐지 수단이다 — **등록 지점은 각 배포기 옆 `<package>/deploy-manifest.txt`**(RC-1, 2026-08-28)이고 중앙 표는 `python3 -m automation.watcher_manifest emit` 의 파생물이다(손 편집 금지). 손 편집·emit 누락·선언 없는 배포기는 전부 `tests/unit/test_watcher_manifest_declarations.py` 가 RED 로 막는다. 2026-09-03: 손배포로 돌던 `send_cost_report.py`·`poll_reminders.py`·`repair_report_consume_watch.py`·`05-skill-generation` 플러그인도 `automation/{cost-report,reminder_poller,repair,skill_generation}/deploy-manifest.txt` 로 선언됐다 — 선언이 없으면 프로브가 드리프트 '0' 을 보고하고, 이제 선언 밖 사본은 `deploy_all_probe` 의 `undeclared` 경고로 드러난다. 게이트웨이 플러그인은 여기에 더해 **프로세스 시작 시 로드**되므로 파일이 맞아도 재시동 전까지 도는 코드가 다르다 — 배포와 반영이 별개인 유일한 표면이다(2026-08-28: 홈 플러그인이 5일 낡아 `!meeting` 이 이미 없어진 규칙으로 거부됐고, 매니페스트 정규식이 `scripts/` 만 봐서 사각지대였다). **④ 그리고 배포됨 ≠ 실행됨(2026-09-01)**: 세 표면이 전부 맞아도 에이전트가 **다른 사본**을 실행하면 무효다 — 원문 인용 릴리스가 마운트된 87분 뒤의 회신이 관측 미러 `/srv/autophagy-agents/skills/mail/...`(미커밋 편집으로 08-29부터 동결, 121커밋 뒤)에서 만들어져 인용 없이 나갔다. 유일한 배포본은 `readlink /srv/autophagy-skills/live/<skill>` 의 실경로뿐이며, SKILL.md 예시 경로는 그 live 경로로 적고(`~/.hermes/skills/<skill>` 은 SS-1 이후 없다), mutating CLI 는 자기 사본이 그 실경로인지 판정한다(단일 정의 `skill_mount.governed_copy_refusal`, 채택은 `<skill>_governed.py`).
- **상태 마킹은 성공 이후** — claim → 작업 → 성공 시에만 processed 기록, 실패 시 release.
- `fail-closed`가 반복 원칙 — 설정/권한/확인 불가 시 실행하지 않는다.
- `configs/rag/*` 하위 서비스는 `uv` + Ruff `ALL`(line-length 100) + basedpyright `all`. 메인 트리는 위 코드 스타일을 관찰로 강제(루트 매니페스트 없음).
- **vendored 하위 패키지도 격리 예외.** `skills/mail/vendor/mailon/`은 외부 리포(구 `orientpine/emailAutomation`)를 통합한 **무수정 vendoring** 소스로, 4개 서드파티(`pyotp`/`python-dotenv`/`beautifulsoup4`/`lxml`)를 `vendor/requirements.txt` 고정 버전 + 스코프 venv로 쓴다(메인 트리 stdlib-전용의 명시적 예외, RAG 하위서비스와 동일 취지). 소스는 byte-identical 유지 — 고칠 것이 있으면 upstream 성격에 맞게 vendor 소스를 직접 고치고 재배포하되, PROJECT_ROOT 상대 런타임 쓰기(data/logs) 규약은 건드리지 않는다. 런타임은 체크아웃 밖 `~/.hermes/mailon-runtime/`(위 「불변 시드」와 동일 이유). 상세: `docs/guide/기관메일-인터페이스.md`.
- **추적 config = 불변 시드.** 런타임 상태는 체크아웃 밖(~/.hermes/…, /srv/autophagy-private/…)에만 기록한다 — 추적 파일을 런타임에 mutate하면 ops 체크아웃이 dirty해져 git pull --ff-only / peer-attest sync가 막힌다. 선례: configs/mail-mode.default.json(시드) vs ~/.hermes/mail-triage/mail-mode.json(런타임). triage_mode 가드가 시드/체크아웃 경로 쓰기를 fail-closed 거부.
- **`managed-` 접두사 예약.** 관리형 스킬은 반드시 `managed-`로 시작하며, 일반 스킬 배포는 이 접두사를 사용할 수 없다.
- **충돌 시 우선순위 없음.** 일반 스킬과 관리형 스킬 이름 충돌 시 양방향 fail-closed 차단 — 소유자가 하나를 제거(`--remove`)해야 한다.
- **자가 스킬 루트는 통제 공간이 아니다.** 각 계정의 `~/.hermes/skills`는 그 계정이 소유한 쓰기 가능 1차 루트이며, 에이전트가 만든 스킬은 승인 게이트를 거치지 않고 그대로 착지한다. 관리자 배포본은 그 루트에 마운트되지 않고 `/srv/autophagy-skills/live`(root 소유 read-only)에서 `skills.external_dirs`로 발견된다 — 즉 `~/.hermes/skills` 아래에 있는 것을 관리자 배포본으로 읽으면 안 된다(반전 전에는 그 경로가 live의 read-only bind였다).

**자가 스킬 이름 충돌은 한쪽만 막힌다 — 반대쪽은 탐지로 메운다(2026-08-16 정정).** self→governed 방향(자가 스킬이 배포 스킬 이름을 선점)은 **Hermes가 막지 못한다**: `skill_manage(create)`의 충돌 검사 `_find_skill`은 `rglob("SKILL.md")`로 훑는데 우리 governed 루트 `/srv/autophagy-skills/live`는 릴리스로 가는 **심링크 팜**이고 파이썬 `rglob`은 디렉터리 심링크를 따라가지 않는다 — 실측으로 `_find_skill("recall")`·`_find_skill("mail")`이 모두 `None`이었고, 에이전트가 `recall` 이름의 자가 스킬을 실제로 만들었다(즉시 제거). 1차 루트가 발견에서 이기므로 그런 자가 스킬은 **승인 게이트를 강제하는 배포본을 가린다**. 벤더 쪽을 고칠 수 없으므로 `selfskill_audit`이 자기 루트와 live 이름을 대조해 `SHADOWS-GOVERNED`로 소유자에게 알린다(델타가 없어도 보고). **SC-1(2026-08-30)로 이 탐지는 일 1회에서 2분으로 당겨졌다** — `automation/supply_chain_shadow_watch.py`가 같은 walk(`scan._skill_dirs`)의 이름 대조만을 기존 `supply_chain_watch` 2분 틱에 편입해 새 그림자에 통지 1건(전송 실패 시 다음 틱 재시도), 지속 중 저널 한 줄, 해소 후 재발은 새 사건으로 재통지한다(fail-soft — 탐지 실패가 승인 재개 틱을 세우지 않는다). governed→self 방향(배포가 자가 저작물을 덮어씀)은 `deploy-skill.sh`가 막는다 — agent 루트와 peer 루트를 각각 분류해 자가 저작물이면 `SELF-SKILL-COLLISION-BLOCK`(exit 4)으로 멈추고, 읽거나 분류할 수 없어도 같은 코드로 멈춘다(fail-closed). 관리형 스킬 충돌과 같은 원칙이다 — 우선순위는 없고, 소유자가 한쪽을 치워야(`hermes curator archive <name>` 또는 해당 계정 `~/.hermes/skills`에서 디렉터리 제거) 배포가 재개된다.

## ANTI-PATTERNS (금지)
- cron 워처가 Discord **메시지/첨부를 폴링** — 실시간 에이전트와 경쟁 소비자. 워처는 **리액션만** 폴링.
- 자식 subprocess에 자격증명 미전파(`env=` 누락) — no-agent cron은 os.environ에 시크릿을 안 넣으므로 명시 전파 필수.
- 채널·기능별 **병렬 confirm 워처/resolve 신설** — 기존 게이트에 `channel_id` 바인딩으로 재사용.
- 도메인 중첩 스킬에서 SKILL.md 라우팅에만 의존 — mutating 경로에 결정론적 코드 가드 필수(선례 calendar `94d625f`). **가드는 양방향이어야 한다**: 한쪽만 막으면 LLM이 두 경로를 동시 발동해 이중 쓰기(선례 사고 2026-07-20: `peer-test 오전 10시` 요청이 calendar 07-22 10:00 + coordination 07-29 09:00 이중 등록). calendar↔coordination는 공유 판정 `calendar_routing.classify_meeting_request`(calendar|coordination|clarify)로 정확-단일-시각=calendar / 피어+범위+조율의사=coordination / 모호=clarify(fail-closed)를 양쪽 CLI가 함께 강제한다.
- 토큰 모양 문자열(`Bot `, `sk-`, `ghp_`)을 **주석/문서에도** 사용 — secret-scan 오탐이 배포를 막는다.
- 프로덕션 게이트웨이에 `E2E_TEST_MODE=1` — production guard가 부팅 거부.

## COMMANDS
```bash
pytest tests/unit                     # 단위 테스트 (루트에서 실행, .pytest_cache)
ruff check .                          # 린트
automation/deploy-skill.sh <skill>    # 스킬 배포 (4단계 게이트; --sandbox-only / --request-only / --remove)
automation/local_ci.sh run            # PR 전 로컬 CI (통과 시 push 게이트 영수증 발급)
automation/merge-pr.sh <pr>           # 체크 green 확인 → 머지 → 릴리스 태그
automation/deploy_all.sh --plan       # 전 표면 전량 수렴 판정 (--verify 영수증 / --apply 수렴)
automation/healthcheck.sh             # 양 노드 read-only 헬스체크
python3 -m automation.rag_ingest      # 개인 RAG 인제스트
```

## NOTES
- 설계·작업분해의 단일 진실: `.omo/plans/autophagy-agents.md` (Work Plan). `docs/features.md`는 요약 뷰 + 아이디어 수집함.
- 배포는 오케스트레이터에서 `DEPLOY_SSH_HOST`(`<primary-node>`)로 SSH + `sudo -n -u agent|peer`.
- `logs/`는 git 미추적(디렉토리만 유지). `configs/rag/*/.venv`·`__pycache__`·`*_cache`는 소스 아님.
- `.git/hooks/`는 미추적 — clone마다 gitleaks pre-commit 훅 재설치 필요. `gitleaks` 바이너리가 PATH에 없으면 커밋이 fail-closed 차단된다(`docs/troubleshooting/gitleaks-setup.md`).


## 커뮤니케이션 언어 (사용자 지시, 2026-07-14)

**사용자(cha)에게 말하는 모든 내용(채팅 응답, 상태 보고, 질문, 요약)은 한국어로 작성한다.**

- 적용 대상: Atlas(오케스트레이터)가 cha에게 보내는 모든 메시지.
- 적용 제외: 코드, 커밋 메시지, 파일 내용, 서브에이전트에게 보내는 delegation 프롬프트,
  `.omo/plans/`·`.omo/notepads/`·`docs/` 등 산출물 자체의 언어는 이 규칙과 무관 — 기존
  계획 문서가 이미 한국어/영어 혼용이므로 각 문서의 기존 스타일을 따른다.
- 이 지침은 세션이 바뀌어도 유지되어야 하므로 repo 루트에 기록한다(사용자 요청: "지침에 추가해").

## Owner-confirm 이모지 컨벤션 (cha 지시, 2026-07-17)

모든 소유자 확정/승인 흐름(mail·budget·calendar·coordination 및 향후 추가되는 모든 confirm)은 동일한 이모지 리액션으로 처리한다:

- **✅ (U+2705, `:white_check_mark:`) = 승인 / 확정 / 실행**
- **⛔ (U+26D4, `:no_entry:`) = 거부 / 취소**

공통 규칙 (모든 confirm 게이트·워처):
- **소유자(cha) 리액션만 유효** — 봇/타인 리액션은 무시(reactor id == owner_id AND bot == false).
- **⛔ 우선** — ✅와 ⛔가 함께 있으면 취소로 처리한다(외부효과 fail-safe).
- **hash 바인딩 + fail-closed** — 대상 메시지가 드래프트 sha256을 참조해야 하며, 확인 불가/모호하면 실행하지 않는다.
- 확정 메시지에는 봇이 ✅·⛔를 미리 부착한다(cha는 탭 한 번). 텍스트 `실행/취소 <id>`는 하위호환 fallback일 뿐, 에이전트는 "실행 <id> 답장"류 텍스트 지시나 별도 DM을 보내지 않는다.
- 신규 confirm 워처(Hermes no-agent cron)의 배포 파일명은 `~/.hermes/scripts/` 안에서 **스킬별로 고유**해야 한다(예: `calendar_confirm_reaction_watch.py`) — 동명 파일 충돌 금지.
- **워처(cron)는 Discord 리액션만 폴링한다 — 메시지 텍스트/첨부의 수신·처리는 실시간 에이전트 단독 소유(경쟁 소비자 금지)**
- no-agent cron은 `~/.env.secrets` self-load + repo sys.path 래퍼 필수 — 상세: `docs/guide/watcher-cron-설계규약.md` — 워처가 자식 프로세스를 spawn할 때 자격증명을 자식 env에 명시 전달(자식 폴백 의존 금지, 규약 (b-2)).
- 도메인 중첩 스킬(calendar↔coordination 등)은 SKILL.md 라우팅만으로 부족, 양보 스킬의 mutating 경로에 결정론적 가드(등록 레지스트리 매칭, 소유자 제외, fail-closed)로 거부+안내(선례 calendar `94d625f`), 상세: `docs/guide/watcher-cron-설계규약.md`.
- **단일 게이트 절차 재사용 — 병렬 confirm 구조 신설 금지 (2026-07-19 mail compose 사후 반영)**: 신규 confirm 표면(owner DM 등)이 필요하면 해당 스킬의 **기존** 게이트를 재사용한다 — draft/pending 레코드·render·resolve·watch cron은 하나만 존재하며, 승인 채널은 레코드에 바인딩된 `channel_id` 필드로만 달라진다. 채널별·기능별로 별도 render/별도 resolve/별도 워처를 만드는 것은 금지. 선례: mail 스킬의 개인 서버 #approvals 회신 초안과 owner-DM compose 초안은 같은 `watch` cron·같은 `resolve_reaction`·같은 render 함수를 공유한다.

## 문서 갱신 규칙 (cha 지시, 2026-07-20)

**코드 작업은 관련 문서 갱신까지 마쳐야 종결된 것으로 간주한다.**

- 기능을 추가/변경/제거하면 **같은 커밋·배포 사이클에서** 해당 리포의 AGENTS.md·docs/·SKILL.md 중 낡아지는 문구를 함께 갱신한다.
- 적용 대상: autophagy-agents와 에이전트가 참조하는 모든 산출물 리포(emailAutomation 등). 에이전트는 문서를 사실 근거로 답하므로, 낡은 문서는 곧 소유자에게 잘못된 안내가 된다.
- 배경(사후 반영): 2026-07-20 mailon 보낸메일함 동기화가 배포됐으나 emailAutomation AGENTS.md의 "받은편지함만 지원" 문구가 남아, 에이전트가 기능이 없다고 잘못 안내함.

## 다른 세션 작업 덮어쓰기 방지 (cha 지시, 2026-07-21)

**작업 시작 전, 대상 영역의 최신 상태를 반드시 확인한다 — 특히 설계·아키텍처 결정.** 여러 세션/에이전트가 같은 repo에서 동시·연속으로 작업하므로, 내 기억이나 이전 세션의 이해는 이미 낡았을 수 있다.

- **설계·규약 문서를 수정하기 전에**: 해당 문서의 `git log`를 확인해 **최근 커밋이 다른 세션의 확정 결정인지** 살핀다. 최근에 그 주제를 바꾼 커밋이 있으면 그 커밋 메시지·diff를 읽어 **왜 그렇게 결정됐는지(rationale)** 파악한 뒤에만 손댄다. 내 이해와 충돌하면 덮어쓰지 말고 사용자에게 확인한다.
- **"내가 아는 설계"와 repo의 현재 상태가 다르면, repo가 진실이다.** 옛 이해로 최신 결정을 되돌리지 않는다. 사용자가 "최근 세션에서 해결했다"고 하면 반드시 그 커밋/세션을 먼저 찾아 확인한다(`git log --oneline -S <키워드>`, session 도구, 커밋 diff).
- **다른 세션의 미커밋 작업(dirty 워킹트리)은 되돌리지 않는다.** stash/reset/checkout/discard 금지 — 소유자 명시 승인이 있을 때만, 복원 가능한 방식(stash 보관 후 pop)으로 처리하고 작업 종료 시 반드시 복원한다.
- **배경(사후 반영)**: 2026-07-21, 한 세션이 확정·커밋한 Discord 2-서버 설계(893da68 — "모든 승인=개인 서버, peer attestation의 peer는 자기 두 번째 봇")를 다른 세션이 옛 이해("배포 승인은 공유 Lab")로 문서·config를 덮어써, 존재하지 않는 공유 Lab 채널을 가리켜 배포가 404로 실패했다. 최신 커밋을 먼저 확인했다면 예방 가능했다.

## 게이트웨이 재시동 규칙 (cha 지시, 2026-07-22)

**게이트웨이 재시동은 항상 agent와 peer를 포함한 모든 Hermes gateway를 함께 재시동한다 — 한쪽만 재시동 금지.**

- 기존 불변식 유지: healthy 서비스는 재시동하지 않고, 원인 확인 후에만 재시동한다. 이 규칙은 **재시동이 결정된 뒤의 범위**(항상 agent+peer 전 계정 세트, 향후 게이트웨이 계정 추가 시 그 계정도 포함)만 규정한다.
- 적용 대상: gateway를 재시동하는 모든 경로 — 수동 운영, 장애 대응, 재부팅 복구, 배포 스크립트(`automation/skill_generation/deploy.sh` 등). 상세 절차: `docs/guide/operations.md` §2 게이트웨이 재시동 규칙(incident-response.md·reboot-recovery.md 동기 갱신됨).
- caveat: 재시동 창 동안 진행 중인 peer attestation/승인 응답은 fail-closed 타임아웃될 수 있다 → 재시동 완료 후 재요청(차단 아님).
- LiteLLM(ops)·dashboard 등 다른 unit은 이 규칙 대상이 아니다 — Hermes gateway(agent·peer) 세트에만 적용.

## 기능 소개 문서 규칙 (cha 지시, 2026-07-25)

**기능(작업)이 완료되면, 완료된 기능을 소개하는 문서를 함께 만든다.**

- 위치: `docs/기능소개/<기능-slug>.md` (기능당 1개). 작성 후 `docs/features.md`의 해당 항목에서 링크한다.
- 길이: 1페이지 내외로 **간결하게**. 내부 구현 덤프가 아니라 "무엇을/왜/어떻게 쓰는가" 중심.
- 필수 포함:
  - **무엇을**: 이 기능이 무엇을 하는지 한두 문장.
  - **왜**: 어떤 문제를 해결하는지(기존 한계/장애).
  - **사용 시나리오**: 소유자(cha) 관점의 구체적 예시(입력→동작→결과). 최소 1개 happy-path + 실패/거부 경로가 있으면 함께.
  - **관련**: 스킬/티켓/핵심 파일 경로. 승인·게이트가 관여하면 명시.
- 민감정보 규칙 동일: 토큰·원문 로그·실제 문서 본문은 넣지 않는다(마스킹).
- 「문서 갱신 규칙」과 병행한다: 코드 완료 = 낡은 문구 갱신 + 이 소개 문서 작성까지가 종결 조건.
- 배경: 2026-07-25 cha 지시 — 완료 기능을 소유자가 바로 이해·사용할 수 있도록, 사용 시나리오 중심의 짧은 소개 문서를 남긴다.

## 커밋 규칙 (cha 지시, 2026-07-25)

**작업할 때는 단위 작업(logical unit)마다 커밋한다 — 여러 변경을 하나의 큰 커밋으로 몰지 않는다.**

- 하나의 논리적 변경(기능 추가·버그 수정·문서 갱신·리팩터 등)이 끝날 때마다 그 단위로 커밋한다. 서로 무관한 변경을 한 커밋에 섞지 않는다.
- 적용 대상: autophagy-agents에서 작업하는 모든 에이전트·세션. 커밋 메시지는 repo 기존 스타일(Conventional Commits: `feat(scope):`, `fix(scope):`, `docs(scope):` 등)을 따른다.
- 이유: 리뷰·되돌리기(revert)·이분 탐색(bisect)·다른 세션과의 병합을 쉽게 하고, "커밋됨 ≠ 배포됨" 판정과 프로덕션 핫픽스 유실 방지에 도움이 된다.
- 「문서 갱신 규칙」과 병행한다: 코드 단위 작업이 문서를 낡게 만들면 같은 단위·커밋 사이클에서 문서도 함께 갱신한다.

## 배포 provenance 규칙 (cha 지시, 2026-07-25)

**prod에는 `origin/main`에 있는 코드만 배포한다 — 미커밋·미푸시·뒤처진 체크아웃에서의 배포는 차단된다.**

- 모든 deploy 스크립트는 로컬 체크아웃의 파일을 prod로 복사한다. 병렬 세션 환경에서는 (a) 미커밋 파일 배포, (b) 커밋했지만 push 안 한 파일 배포, (c) origin/main보다 **뒤처진 체크아웃**에서의 배포가 쉽게 일어난다. 그러면 prod가 git에 없는 코드를 돌리고, 다음에 누군가 깨끗한 체크아웃에서 같은 스크립트를 돌리면 **말없이 되돌려진다**.
- 강제 수단: `automation/deploy_provenance.sh`의 `deploy_provenance_check <repo_root> <경로>...`가 각 파일의 워킹트리 blob 해시와 배포 기준(`origin/main`)의 blob 해시를 대조해 불일치면 `DEPLOY-BLOCK`으로 중단한다. 신규 deploy 스크립트는 **반드시** 이 가드를 호출해야 한다.
- **디렉터리 인자는 tracked 대조만으로 부족하다 (2026-08-01 보강).** 호출자는 디렉터리를 통째로 `tar`/`rsync`로 보내는데 가드는 `git ls-files`(tracked)만 열거했다 — 그래서 디렉터리 안의 **untracked 새 파일**이 `OK`를 받고 prod로 실려, 커밋·`origin/main`·PR을 **전부 우회**했다(실측: `automation/hermes_compat`에 untracked `.py` 1개 → `OK: 17 file(s) match origin/main`, rc 0). 이제 디렉터리 인자는 `ls-files --others --exclude-standard`로 untracked 비-ignored 파일을 함께 검사해 있으면 경로를 적시하고 `DEPLOY-BLOCK`한다. `--exclude-standard`는 의도적이다 — `.gitignore`가 비소스로 선언한 잔여물(`__pycache__`·`.venv`)까지 막으면 모든 실배포가 깨진다. **디렉터리 인자 호출자에는 `deploy-skill.sh`(스킬 마운트 경로)도 포함**되므로 이 보강은 최고 권한 경로에도 적용된다.
- 예외(의도적): `deploy-skill.sh --sandbox-only` / `--activate-managed` / 내부 staged-source용 `SKILL_SRC_DIR` 지정, 그리고 워킹트리를 통째로 rsync하는 `regression_bank/deploy_lab_node.sh`(푸시 전 테스트가 목적). 개인 저작은 이 우회를 쓰지 않고 `--personal <name>`이 별도 `personal_provenance_check`(독립 repo의 clean·committed HEAD + untracked 비-ignored 부재 + 승인 SHA 바인딩)를 통과한다.
- 탈출구 `DEPLOY_ALLOW_UNPUSHED=1`은 **샌드박스/실험 전용**이다. 배포를 통과시키려고 상습적으로 쓰면 가드가 무의미해진다 — 올바른 순서는 상시 "커밋 → 푸시 → 배포"다.
- 배경(사후 반영): 2026-07-25 mail 승인 워처 수리 중, 수정된 `mail_digest_watch.py`가 미커밋 상태로 prod에 배포돼 다른 세션의 배포 한 번으로 유실될 수 있는 상태였다. 같은 계열의 선례: 2026-07-21 옛 체크아웃이 최신 Discord 2-서버 결정을 덮어써 배포가 404로 실패.

## 작업 종결 규칙 (cha 지시, 2026-07-25)

**작업이 끝나면 커밋 → 푸시 → 배포까지 마쳐야 종결이다 — "코드만 고쳐두고 보고"는 미완이다.**

- 순서는 항상 **커밋 → 푸시 → 배포**다. 「배포 provenance 규칙」의 가드가 미푸시 코드의 배포를 차단하므로 순서를 바꾸면 배포가 막힌다. 이 순서 자체가 프로덕션 핫픽스 유실 방지 장치다.
- 배포 대상 판정: 스킬 = `automation/deploy-skill.sh`(4단계 게이트), 워처 = 각 `deploy.sh`, ops 체크아웃에서 실행되는 코드(`/srv/autophagy-agents/automation/**`) = ops 계정 `git pull --ff-only`. 배포할 것이 없는 작업(문서·테스트만)은 커밋·푸시로 종결한다.
- 배포 후 **검증까지가 종결 조건**이다: live 심링크 해시 / 배포본 파일 해시 / 실제 실행(cron tick·import·smoke) 중 해당하는 증적을 남긴다("커밋됨 ≠ 배포됨").
- 소유자 승인이 필요한 배포는 승인 요청 게시까지 진행하고, cha의 ✅ 이후 남은 단계(MOUNT·검증·kanban 종결)도 같은 세션에서 마무리한다.
- 다른 세션의 미커밋 작업·로컬 전용 커밋은 절대 함께 커밋하지 않는다. 로컬 브랜치가 갈라져 있으면 `origin/main` 기반 worktree에서 **내 변경만** 커밋·푸시한다(선례 2026-07-25).

## 릴리스 패치노트 작성 규칙 (cha 지시, 2026-08-30)

**릴리스 승인 메시지는 소유자와 다른 참여자가 무엇을 받아들이는지 이해할 수 있어야
한다. 해시 목록은 패치노트가 아니다.**

- 패치노트는 **무엇이 바뀌는지 → 왜 필요한지 → 사용자·운영 영향**이 드러나는
  제목으로 쓴다. 티켓 번호·내부 파일명·`fix` 같은 분류만 있는 제목은 불충분하다.
- `release.sh`는 직전 릴리스 이후의 커밋 제목을 패치노트 원문으로 쓴다. 따라서 logical
  commit 제목 자체가 위 기준을 만족해야 한다. 릴리스 직전에 이해 불가능한 제목을
  발견하면 승인 메시지를 억지로 줄이지 말고 해당 변경 설명을 보강한 뒤 다시 계획한다.
- 승인 본문에는 version·HEAD, **변경 표면 이름**, 사람이 읽는 변경 설명, 전체
  `action_hash`를 싣는다. `release_nonce`와 표면별 64자 digest는 기계 바인딩
  레코드에만 보관한다 — 원시 해시가 설명 공간을 밀어내면 승인자가 내용을 검토할 수
  없다.
- Discord 한도 때문에 자동 생성 설명을 줄일 때는 완전한 줄 단위로만 줄이고 생략 줄
  수를 명시한다. 중요 변경이 생략 구간에만 남는 릴리스는 승인 요청을 게시하지 말고
  커밋 설명을 정리하거나 릴리스 범위를 나눈다.
- ✅ 이후 별도 수동 재개를 요구하지 않는다. 워크스테이션의 `release.sh`는 기본적으로
  결정을 계속 기다리고, 소유자 ✅를 감지하면 곧바로 서명 태그를 자른다. deadline은
  테스트·의도적 제한 운영에서만 명시한다.

## 릴리스 태그 규칙 (cha 지시, 2026-08-20)

**서명 릴리스 태그가 없으면 프로덕션은 전진하지 않는다. 태그는 릴리스 승인(`automation/release.sh`, 소유자 ✅ 1회)이 자른다 — 머지는 축적이다(VA-3, 2026-08-30 개정: `merge-pr.sh` 의 태그 자동 호출 제거).**

- **왜**: 2분 리컨실러가 부르는 `converge_origin_main.sh` 는 **인자를 받지 않는 것이 계약**이다(MD-1). 자동 트리거가 설치될 sha 를 고를 수 있으면 "PR 머지 = sudo 임의 코드 실행"이 되기 때문이고, 그래서 무엇을 설치할지는 **서명만이** 정한다 — `origin/main` HEAD 가 annotated 서명 태그의 peel 대상일 때만 수렴한다. 태그가 없으면 매 틱 `UPDATE-TRUST-BLOCK` 으로 서고 rc 0 으로 안전하게 건너뛴다.
- **왜 지금 규칙이 되었나**: 태그를 자르는 코드는 `land.sh` 안에만 있었고, 「세션 워크트리 규칙」대로 브랜치 작업은 land 가 아니라 **PR 머지**로 main 에 도달한다 — 즉 우리가 실제로 쓰는 경로에는 그 단계가 아예 없었다. 2026-08-20 실측: PR 6건이 태그 없이 들어가 리컨실러가 **132회 연속 실패**했고 프로덕션이 2커밋 뒤에 얼어 있었다. 그동안 프로덕션을 밀어올린 것은 사람이 `land.sh` 를 돌릴 때뿐이었다.
- **머지 직후에 실행한다**: 태그는 **HEAD 를 정확히 맞혀야** 한다. 병렬 세션이 그 사이 머지하면 태그는 이전 커밋에 남고 노드는 계속 선다(2026-08-16 에 main 이 세 번 전진하며 실제로 그랬다). 그래서 `release-tag.sh` 는 자른 뒤 HEAD 가 아직 그 sha 인지 다시 확인하고, 어긋났으면 성공으로 끝내지 않는다 — **조치는 재실행**이며 멱등이라 안전하다.
- **묶음 단위도 괜찮다**: 매 머지마다 자를 필요는 없다. 연속 머지 후 마지막에 한 번이면 프로덕션이 그 지점으로 간다 — 태그를 자르는 순간이 곧 "여기까지를 프로덕션에 올린다"는 선언이다.
- **키는 로컬에만 둔다**: `UPDATE_TRUST_SIGNING_KEY`(기본 `~/.ssh/autophagy_update_trust.pub`)는 어떤 저장소에도 커밋하지 않고 노드·CI 에 두지 않는다(「공개 릴리스 규칙」 D8 과 같은 키). **CI 가 서명하게 만들면 MD-1 이 막으려던 그 escalation 이 그대로 되살아난다** — 자동화하려면 서명 주체가 아니라 실행 시점만 자동화한다.
- **서명 없는 head 는 사고가 아니라 릴리스 백로그다(VA-3 재설계)**: 릴리스 사이의 unsigned `origin/main` 은 정상 상태이므로 sha별 즉시 통지를 보내지 않는다. 리컨실러는 백로그 에피소드(마지막 릴리스 이후 첫 unsigned tick)의 나이를 재서 **3일 임계를 넘긴 기간마다 1건**의 다이제스트("미배포 커밋 N건 · X일 경과 · `automation/release.sh` 안내")를 보내고, 릴리스가 착지하면 리셋한다. sha 가 전진해도 나이는 이어진다 — sha별로 리셋하면 활발히 머지할수록 다이제스트가 오지 않는다. 커밋 수는 미러 `rev-list --count` 로 읽되 관측 불가면 "수 미상"으로 말한다(추측 금지). raw SHA는 통지 전용이며 설치 대상은 계속 서명 검증 결과만 쓴다([소개](docs/기능소개/반복-수렴-스킵-알림.md)).
- **✅ 뒤는 사람 손이 아니다(2026-08-31, cha 지시)**: 릴리스 승인이 스킬별 ✅를 대체해도(VA-2) 실행은 자동이 아니었다 — `release.sh` 의 폴링은 세션과 함께 죽고, 태그 뒤 마운트는 `deploy_all.sh --apply` 를 누군가 따로 돌려야 했다(v1.0.140: 태그 뒤 스킬 6개 stale). 이제 `release.sh` 는 태그 뒤 **스스로** 노드 수렴을 기다려 `deploy_all.sh --apply --wait-converge` 까지 잇고(옵트아웃 `--no-deploy`, 실패는 exit 10 — 태그는 되돌리지 않는다), 워크스테이션 `systemd --user` 타이머(`automation/release_complete_install.sh`, 2분)가 **이미 ✅된 요청만** 완결한다 — `request`/`retire`/`plan` 은 절대 부르지 않고(회귀가 subcommand 를 대조), sha 별 3회 상한으로 지속 결함의 매 틱 전량 재배포를 막는다. 완결 주체가 워크스테이션인 이유는 위 「키는 로컬에만 둔다」 그대로다. 설치는 **메인 체크아웃**에서 한다(세션 워크트리는 `finish` 때 사라진다). 상세: [소개](docs/기능소개/릴리스-승인-자동-완결.md)
- 구현은 `automation/release_tag_lib.sh` 하나이고 `land.sh` 와 `release-tag.sh` 가 공유한다(회귀 고정: `tests/unit/test_release_tag.py`). 사본을 다시 만들지 않는다 — 갈라지면 두 경로가 서로 다른 태그를 자른다.
- 이 규칙은 **private repo 의 태그**를 다룬다. 공개 배포본(`cytoplasm`)의 릴리스 컷은 「공개 릴리스 규칙」·`public_export.sh` 가 따로 소유한다 — 「세 저장소 구분 규칙」 참조.

## 승인 메시지 단일성 규칙 (cha 지시, 2026-07-25)

**승인 게이트는 동일한 논리적 요청에 대해 이미 게시된 메시지가 있다면 이를 조용히 덮어쓰거나 중복 게시하지 않는다.**

- **금지**: (1) 동일한 `action_hash`를 가진 메시지를 중복 게시하는 것, (2) 저장된 `message_id`를 새 메시지 ID로 덮어써서 이전 메시지를 고아(Orphan)로 만드는 것.
- **절차**: 모든 승인 요청은 `automation/interop/approval_lifecycle.py` 파사드를 경유한다. 파사드가 lease 점유 → 상태 probe(6종 타입) → supersede(delete 후 record drop) → journal → 게시 → 커밋까지 임계구역 전체를 소유한다. 소유자가 이미 ✅/⛔를 누른 요청은 건드리지 않고 워처에게 양보한다(DEFER).
- **강제**: 산문만으로는 부족하다(이 문서의 원칙: 「mutating 경로에 결정론적 코드 가드 필수」). `tests/unit/test_approval_lifecycle_conformance.py`가 승인 producer 인벤토리를 들고 파사드 경유 여부를 기계적으로 검증한다. 예외는 소스 주석이 아니라 테스트 내 `_EXEMPT` 맵에 사유와 함께 등록해야 한다.
- **참조**: 상세 규약은 `docs/guide/watcher-cron-설계규약.md §(j)`, 구현 불변식과 금지사항은 `automation/interop/AGENTS.md`에 있다.
- **배경(사후 반영)**: 2026-07-25 drive-archive digest 중복 게시 및 message_id 덮어쓰기로 인해 소유자의 승인 ✅가 실종된 결함을 수리하며 도입되었습니다.
## 결과 통지 원채널 스레드 규칙 (cha 지시, 2026-08-22/23)

**승인 게이트를 가진 모든 스킬은 실행/취소/만료 결과를 지시가 시작된 채널의 스레드로 통지한다 — 2026-09-01 부터 승인(✅/⛔) 요청 자체가 `#agent-chat`의 **요청별 스레드**에 게시되므로 결과는 레코드의 `approval_thread_id` 로 **그 스레드**에 돌아온다(「요청별 승인 스레드 규칙」; v7~v8 의 kind별 `승인-<kind>` 스레드는 폐지). 구현은 `automation/interop/origin_notice.py` 하나뿐이고, 앞으로 만드는 스킬도 예외가 아니다.**

- **왜**: 2026-08-22 agent-chat에서 시작한 메일 발송의 결과가 "메일 발송 취소됨" 한 줄로 DM에 흩어졌다. 지시가 온 곳으로 결과가 돌아가야 대화가 이어지고, DM에는 승인 버튼만 남아야 소유자가 무엇을 눌러야 하는지 분명하다. 같은 날 mail에 적용했고 2026-08-23 소유자 지시로 전 스킬(budget·calendar·coordination·todo·meeting)에 일반화했다([소개](docs/기능소개/결과-통지-원채널-스레드-전스킬.md)).
- **새 스킬이 지켜야 할 것**: ① mutating CLI에 `--origin-channel-id`/`--origin-message-id`(선택)를 받아 레코드에 `origin_channel_id`/`origin_message_id`로 저장하되 **승인 해시 바인딩 밖**에 둔다(레거시 레코드는 필드 부재 허용). ② 결과 통지는 `origin_notice.deliver(api=…, transport_factory=…, record=…, thread_name=…, content=…, fallback=…, outcome=…)` 경유 — 스레드 해석(레코드 `approval_thread_id` 최우선(생성 0) → 지시 메시지 앵커, 400=기존 스레드 재사용 → 채널 스레드)·종결 표시(`ThreadOutcome` 접두어+아카이브, `THREAD-CLOSE-FAIL`)·`NOTIFY-THREAD-FAIL` 마커·폴백 의미는 헬퍼가 소유한다. 종결 결과(실행·취소·만료)에는 `outcome=` 을 넘기고 중간 ACK 에는 넘기지 않는다. **자체 스레드 생성 코드 금지**(사본 증식은 「승인 메시지 단일성 규칙」이 막으려던 것과 같은 문제). ③ 문구는 대상·id·사유를 담는다("…됨" 무맥락 고정문구 금지). ④ 통지는 best-effort — 어떤 실패도 tick·exit code·영수증·원장을 바꾸지 않고 `NOTIFY-FAIL`로 남긴다. E2E·주입 승인은 `NOTIFY-SKIP`으로 실제 통지를 열지 않는다. ⑤ 승인 요청·리마인더·게이트 경로는 건드리지 않는다 — 이 규칙은 결과 통지의 목적지만 정한다.
- **민감도 규칙이 우선한다**: 스킬의 기존 마스킹 규칙(캘린더 내용·금액·위키 본문·특허)은 스레드 문구에도 그대로 적용된다. 채널에 내보낼 수 없는 정보는 마스킹(calendar: action 종류·draft id만)하거나 통지 자체를 제외(wiki·patent-prep)하고, 그 사유를 conformance 예외 맵에 적는다.
- **강제**: `tests/unit/test_origin_notice_adoption_conformance.py` — 승인 producer를 가진 모든 스킬(`approval_conformance_inventory.APPROVAL_PRODUCERS`)은 스크립트가 `origin_notice`를 참조해야 하고, 아니면 `_RESULT_NOTICE_EXEMPT`에 사유와 함께 등록되어야 한다. 스레드 생성 API 호출은 헬퍼 밖에서 금지(`_THREAD_API_ALLOWED` 예외: meeting 게이트웨이 플러그인 — INTEROP_RUNTIME 경로를 보장받지 못함). 산문이 아니라 코드가 진실이다.
- **배포 주의**: 헬퍼는 interop 런타임(`INTEROP_RUNTIME`)에 실려야 한다 — 스킬만 재배포하고 런타임이 낡으면 통지가 `NOTIFY-FAIL`로 남는다(실행 자체는 영향 없음).

## 요청별 승인 스레드 규칙 (cha 지시, 2026-09-01)

**소유자 전용 승인은 요청 하나에 스레드 하나다 — 승인 카드(✅/⛔)·리마인더·실행/취소/만료 결과가 `#agent-chat` 아래 같은 스레드에서 완결되고, 종결되면 이름에 상태 접두어(`✅ 완료 ·`/`⛔ 취소 ·`/`⌛ 만료 ·`)를 붙여 아카이브한다. kind별 고정 스레드(`승인-<kind>`)는 레거시 호출(`request=None`)에만 남고 범위 내 생산자는 쓰지 않는다.**

- **왜**: 승인은 kind 스레드(`approval_directory.agent_chat_thread`)에, 결과는 지시 메시지 스레드(`origin_notice`)에 갈라져 한 건의 처리 경로를 두 곳에서 따라가야 했다(2026-09-01 소유자: "일의 처리 경로가 한번에 관리"). 요청 단위로 묶고 종결 시 아카이브하면 `#agent-chat` 의 **활성 스레드 목록이 곧 진행 중 요청 보드**가 된다([소개](docs/기능소개/요청별-승인-스레드.md)).
- **새 생산자가 지켜야 할 것**: ① `resolve_new_binding(kind, directory, owner, request=RequestThread(title=…, origin_channel_id=…, origin_message_id=…))` — 지시 메시지가 `#agent-chat` 소속이면 그 메시지에 앵커(400=이미 있음 → message id 재사용), 아니면 채널 스레드 `<kind 라벨> · <제목≤40>`. 제목은 **생산자가 이미 마스킹한 값**이다: mail·budget=제목, todo=할 일 제목, coordination=조율 라벨, **calendar·wiki·patent-prep·obsidian·repair=id 만**. ② `binding.channel_id`(=스레드 id)를 레코드에 `approval_thread_id` 로 **승인 해시 밖**에 저장한다(레거시 레코드는 필드 부재 허용 — `origin_notice` 는 그때 옛 경로를 탄다). ③ 결과 통지 dict 에 그 필드를 실어 `deliver(..., outcome=ThreadOutcome.DONE|CANCELLED|EXPIRED)` — 종결에만, 중간 ACK 는 outcome 없음. ④ 자체 스레드 생성·PATCH 코드 금지 — 종결 표시도 `origin_notice.close_thread` 하나다.
- **정책 버전은 올리지 않았다(S6)**: 요청별 스레드도 `AGENT_CHAT_THREAD` facts(type 11/12 + parent=agent-chat)를 그대로 만족하므로 저장된 v7/v8 바인딩 해석·리액션 워처·리마인더·lifecycle probe 는 전부 무변경이다(모두 레코드 `channel_id` 를 읽는다).
- **강제**: `tests/unit/test_request_thread_adoption_conformance.py` — 배포 소스의 `resolve_new_binding` 호출부 인벤토리에서 `request=` 없는 호출은 RED, 같은 패키지에 `approval_thread_id` 가 없어도 RED. 예외는 공급망 표면(`skill_gate_surface.SupplyChainSurface.new` — skill-*·release 는 peer 봇이 봐야 하는 `#approvals`)뿐이고 사유와 함께 `_KIND_THREAD_EXEMPT` 에 적는다. 산문이 아니라 코드가 진실이다.
- **배포 순서**: interop 런타임이 **먼저** 새 `resolve_new_binding` 시그니처를 가져야 한다 — 스킬만 앞서면 옛 런타임이 `request=` 를 `TypeError` 로 거부해 승인이 게시되지 않는다(fail-closed, 조용하지 않다). `release.sh` 의 전량 수렴이 둘을 같이 올린다.
- **Discord 제약**: 스레드 이름 100자(`request_thread_name` 이 절단), 길드당 활성 스레드 1,000개 — 종결 아카이브가 상한을 지키고, 아카이브된 스레드는 새 메시지에 자동으로 다시 열린다. 이름 변경·아카이브는 best-effort 라 실패해도 실행·영수증·exit code 는 불변이다.

## 후속 과제 기록 규칙 (cha 지시, 2026-07-26)

**작업 중 발견했으나 이번 범위에서 처리하지 않은 사항은, 요청 사항을 마무리한 뒤 `docs/features.md`에 후속 과제로 기록한다 — 기록까지 마쳐야 작업이 종결된다.**

- **왜**: 후속 과제는 작업 과정에서만 발견된다. 제때 기록하지 않으면 QA 증적이나 세션 로그 속에 묻혀 사라지며, 세션 종료 후에는 그 맥락을 아는 주체도 없어진다.
- **어디에**: **`docs/follow-ups.md`** (2026-08-03 분리 — features.md가 103KB까지 부풀어 무엇이 남았는지 안 보였다. 현황판에는 묶음별 잔량 요약표만 둔다)(2026-07-29 분리 — PLAN의 "신규 아이디어"는 이제 진짜 새 기능 아이디어 전용이다). 기능/작업 단위로 **묶음 항목 1개 + 하위 불릿**으로 작성하고, 상세 증적 경로(`docs/qa/<wave-id>/...`)는 묶음 끝에 한 번만 병기한다. 계획 문서에 이미 반영된 건은 wave ID를 병기하여 PLAN "개발 예정"으로 옮긴다(features.md 사용법 규칙 준수).
- **손댈 수 없는 것은 별도 문서로 옮긴다 (cha 지시, 2026-08-26)**: 소유자·노드에서만 닫히는 것(OWNER), 동결·벤더·외부에 막힌 것(BLOCKED), 조건이 성립하기 전에는 조치하지 않는 것(OBSERVE), 이미 닫힌 것(해소)은 **`docs/follow-ups-deferred.md`**로 옮긴다. `follow-ups.md`에는 **지금 이 저장소가 손댈 수 있는 열린 작업만** 남는다. 이것은 **삭제가 아니라 이동**이며 원 `##` 헤딩을 양쪽에서 그대로 유지해야 한다 — 회계 가드 `tests/unit/test_features_board_conformance.py` A9가 두 문서를 합쳐 읽어 이동과 삭제를 가르기 때문이다(헤딩이나 불릿 첫 줄을 고치면 삭제로 읽힌다). 배경: 그 가드가 FS3 baseline 불릿을 고정하고 있어 「계속 개선하라」는 지시에도 문서가 줄지 않았다 — 129건 중 73건이 물리적으로 삭제 불가였다.
- **무엇을 적나**: 각 불릿은 **"무엇이 문제인지 → 어떻게 조치할지"** 형태로 작성하며, **영향 범위와 심각도**를 명시한다(예: "보안 문제 아님 — 라이브 readlink가 권위 소스", "검증 메시지 오류일 뿐 마운트 로직과 무관"). 판단 근거를 남기지 않으면 다음 작업자가 처음부터 다시 조사해야 하는 낭비가 발생한다.
- **즉시 수정 vs 후속 과제**: 요청 범위를 벗어나거나, 안전 불변식을 위반하거나, 프로덕션 동작을 저해하는 결함은 **후속으로 미루지 않고 즉시 수정한다**. 후속 과제로 남기는 것은 (a) 동작은 정상이나 개선 여지가 있는 사항, (b) 범위 밖의 인접 결함, (c) 설계 판단이 필요하여 별도 논의가 필요한 사항에 한한다.
- **없으면 적지 않는다**: 형식을 채우기 위해 빈 항목이나 억지스러운 항목을 만들지 않는다.
- 「작업 종결 규칙」·「문서 갱신 규칙」·「기능 소개 문서 규칙」과 병행한다: 커밋 → 푸시 → 배포 + 낡은 문구 갱신 + 기능 소개 문서 + **후속 과제 기록**까지가 한 사이클의 종결 조건이다.
- **배경(사후 반영)**: 2026-07-26 관리형 스킬 채널(MS) 라이브 롤아웃에서 실제 결함 7건이 발견되어, 프로덕션을 저해하는 3건은 즉시 수정하고 나머지 4건은 후속으로 남겼다. 그러나 해당 4건이 초기에는 QA 증적 내에만 머물러 있어, 소유자가 후속 과제 문서화 여부를 확인하지 않았다면 유실될 위험이 있었다.

## ops 체크아웃 단방향 규칙 (cha 지시, 2026-07-27)

**노드의 배포 체크아웃(`/srv/autophagy-agents`)은 `origin/main`의 단방향 거울이다 — 그 안에서 커밋을 만들지 않는다.**

- 「배포 provenance 규칙」이 **밖으로 나가는** 코드를 통제한다면(=origin/main에 없는 코드는 배포 금지), 이 규칙은 **반대 방향**을 막는다: **배포 체크아웃이 코드의 출처가 되어서는 안 된다.** 그 안에서 허용되는 쓰기는 `git fetch` / `git pull --ff-only`뿐이며, 편집·`git add`·`git commit`·`git stash`는 금지다. 고칠 것이 있으면 개발 체크아웃에서 커밋 → 푸시 → 배포한다.
- **에이전트가 런타임에 스스로 학습한 내용도 예외가 아니다.** 소유자와의 실제 작업에서 나온 진짜 지식이라도 배포 체크아웃에 직접 커밋하면 위험이 이중이다 — (a) git에 없는 코드가 되어 다음 정렬 때 **유실**되고, (b) 그때까지 **모든 세션의 `git pull --ff-only`가 막혀** 배포 파이프라인이 선다. 「추적 config = 불변 시드」와 같은 이유로, 런타임에 생기는 것은 체크아웃 밖(`~/.hermes/…`)에 쓰거나 개발 경로로 되돌려 보낸다.
- **커밋은 반영이 아니다 — 에이전트에게도 똑같이 적용된다.** 스킬 문서를 고쳐 커밋해도 마운트본은 그대로이므로, 배포(소유자 ✅) 전까지 그 변경은 **조용히 무효**다. ff-pull 차단은 시끄럽게 실패하지만 이쪽은 아무 신호도 없어 발견이 늦는다 — 에이전트는 배웠다고 여기지만 실제로는 구버전이 돈다. 판정은 언제나 `readlink /srv/autophagy-skills/live/<skill>`("커밋됨 ≠ 배포됨").
- **규칙은 산문이 아니라 훅으로 강제된다.** `automation/bootstrap-accounts.sh`의 `install_commit_refusal_hook`이 배포 체크아웃의 `.git/hooks/pre-commit`에 **모든 커밋을 무조건 거부**하는 훅을 설치한다(멱등 — `install`이 단일 경로를 교체하므로 재실행해도 훅은 정확히 1개). 스캔하지도 판단하지도 않는다 — 조건부 거부는 "예외를 아는 사람"에게만 통하는데, 사고를 낸 주체는 언제나 예외를 모르는 쪽이었다. 거부 메시지는 개발 체크아웃에서 커밋하는 방법과 비파괴 복구 절차(format-patch→am)를 함께 안내한다. `git fetch`/`git pull --ff-only`는 커밋을 만들지 않으므로 영향이 없다(회귀 고정: `tests/unit/test_deploy_checkout_commit_refusal.py`). 이것은 거부(구조)와 탐지(아래 프로브)의 2중 방어다 — `--no-verify`로 훅을 뚫어도 프로브가 잡는다.
- **탐지는 운에 맡기지 않는다.** `automation/healthcheck.sh`의 `checkout_mirrors_origin` 프로브가 배포 체크아웃이 origin/main과 어기나는지를 확인해 FAIL·수리 티켓을 낸다. **검증은 체크아웃이 로컬인 cron 호스트(`<primary-node>`)에서 로컬로 돌리므로 ssh도 sudo도 쓰지 않는다** — 원건 이전엔 SSH allowlist와 중첩 sudo가 둘 다 rc=126으로 프로브를 죽였다(2026-07-29 실측). 검증은 세 가지다: 추적 파일 미커밋 변경(dirty), HEAD가 로컬 origin/main의 조상이 아님(ahead=로컬 커밋), 그리고 **origin보다 뒤처짐(behind)** — 뒤처짐은 `git ls-remote`(네트워크 읽기지만 로컬 ref를 쓰지 않음)로 진짜 origin과 대조해 잡는다. **뒤처짐의 심각도는 프로덕션이 무엇을 돌리는가로 채점한다**(DG-6 정합): 릴리스가 `origin/main`에 있으면 관측소만 지연된 것이므로 PASS + `MIRROR-BEHIND-WARN`, 릴리스가 낡았으면 `release-stale`로 FAIL, 릴리스가 없으면(롤백 형태) 미러가 곧 프로덕션이므로 FAIL이다 — 단 **아직 수렴 중인 드리프트는 유예한다**(`RELEASE-CONVERGING-WARN`). 그 판단은 우리가 새로 내리지 않고 리컨실러의 상태 파일을 읽어 위임한다 — 그쪽이 이미 `DRIFT_NOTICE_SECONDS`(600초)·`FAILURE_NOTICE_THRESHOLD`(3회)로 "이게 사건인가"를 소유자에게 알릴지 결정하고 있으므로, 같은 질문을 두 곳에서 따로 판정하면 방금 고친 그 어긋남이 릴리스 쪽으로 옮겨갈 뿐이다. 유예는 fail-closed다: 상태 파일이 없거나·깨졌거나·리컨실러가 자기 임계값만큼 침묵하면 그대로 FAIL이다(침묵이 조용함을 사지 못하게 한다 — 2026-08-02 실측) — 조용해지려고 탐지를 내주지 않는다(`release == origin/main`을 보는 전용 프로브가 없어 낡은 릴리스는 오직 여기서만 보인다). dirty/ahead는 모드와 무관하게 FAIL이다. 그래도 **프로브는 `git fetch`를 안 한다** — read-only 불변식은 유지된다(origin 불통은 `BEHIND-UNKNOWN`으로 degrade). `logs/` 같은 미추적 파일은 오탐이 아니다. 티켓은 체크 이름만 실지 않고 비파괴 복구 절차를 함께 실는다(`checkout_mirror_guidance`: ahead=`format-patch`, behind=`land.sh`). 프로브·채점·가이드는 `automation/checkout_mirror_probe.sh`에 분리되어 land.sh와 공유된다 — healthcheck는 **탐지**, land.sh와 리컨실러는 **수렴**이며, land.sh는 그 함수를 노드에 값으로 실어 보낸다(`declare -f`; 노드 사본을 source하면 방금 경고로 낮춘 그 미러의 셸을 ops로 실행하게 된다 — 그래서 실리는 3개 함수는 자기완결적이어야 하고, 채점 헬퍼를 거기서 호출하면 노드에서 "command not found"가 된다). **push+노드 동기화는 `automation/land.sh` 하나로 수행해 수동 2단계의 망각을 없앤다**. 상세: [소개](docs/기능소개/배포-체크아웃-지연-감지.md)·[소개](docs/기능소개/단일-랜딩-명령.md), 증적 `docs/qa/DG-1/summary.txt`.
- **수렴도 사람에게 맡기지 않는다.** 릴리스 수렴은 detached 스냅샷 워크트리에서 이뤄져 미러의 HEAD를 설계상 건들지 않는다. 그래서 예전엔 사람이 `land.sh`를 돌릴 때만 미러가 따라왔는데, **브랜치 작업은 PR 머지로 main에 도달해 `land.sh`를 거치지 않는다** — 즉 아무도 되돌리지 않았고, 헬스체크는 매 5분 같은 실패를 반복했다(실측 2026-07-29~08-03 `mirror-behind` 447회). 이제 2분 리컨실러가 같은 틱에서 미러를 `git pull --ff-only`로 따라오게 한다(`sync_mirror`) — 단, **판정이 정확히 `mirror-behind`일 때**와 **프로덕션이 이미 `origin/main`에 도달한 뒤**에만. dirty/ahead는 절대 건들지 않고(그 작업은 다른 어디에도 없다), 릴리스가 낡은 동안에는 미러의 지연이 그 사실의 유일한 증거이므로 일부러 남긴다. 이것은 이 규칙의 예외가 아니다 — `pull --ff-only`는 위에서 허용한 바로 그 쓰기다. 상세: [소개](docs/기능소개/관측-미러-자동-수렴.md).
- **DG-5 이후 이 체크아웃은 런타임이 아니다 — 관측소다.** cron 워처·마운트 스킬·`peer_attest`·`deploy-skill.sh`는 공용 리졸버로 불변 릴리스 `/srv/autophagy-agent-current`를 먼저 찾는다([소개](docs/기능소개/불변-런타임-릴리스.md)). 그래서 **`land.sh`의 계약은 노드 상태에 따라 갈린다**: 릴리스가 살아 있으면 미러의 dirty/ahead는 `LAND-MIRROR-WARN`으로 낮추고(그 상태가 프로덕션을 좌우하지 않으므로 — 무관한 랜딩을 거부하던 것이 원래 사고였다) 하드 사후조건은 **릴리스가 방금 push한 sha에 도달했는가**로 옚긴다. `current`를 지워 롤백한 상태(폴백 모드)에서는 미러가 다시 프로덕션이므로 **옛 하드 계약이 그대로 되살아난다**. 끊어진 심링크처럼 **깨진 `current`는 부재가 아니라 손상**이라 랜딩이 거부된다 — `[[ -e ]]`가 거짓이라는 이유로 릴리스 노드를 낡은 미러로 조용히 강등하면 안 된다. 이 규칙(커밋 금지·ff-pull만 허용)은 모드와 무관하게 유지된다.
  - **예외 1건(2026-08-10 시점)**: `autophagy-repair-approval-watch.service`는 system 스코프 유닛이라 DG-5의 `--user` 이관 절차가 닿지 않았고, 라이브 설치본이 여전히 미러(`/srv/autophagy-agents`)를 WorkingDirectory·PYTHONPATH·ExecStart로 쓰고 있었다 — 즉 이 한 유닛에 한해서는 미러가 아직 런타임이었다. 2026-08-10의 별도 system-scope 수렴 작업으로 해소되며, 그 전에는 위 문단을 이 유닛에 적용하지 않는다(근거: `.omo/notepads/repair-report-rollout/decisions.md`).
- **권한이 규칙을 이긴다(2026-09-01 실측).** 미러 디렉터리가 `ops:autophagy 2775`(group 쓰기)라 agent 가 파일을 만들고 추적 파일을 덮어쓸 수 있었고, 실제로 2026-08-29 에 첨부 아카이브 구현을 미러 안에 써서 3일간 미러를 동결시켰다(커밋 거부 훅은 커밋만 막는다). 미러 트리는 ops 만 쓰기 가능해야 한다 — 소유자 항목으로 기록(`docs/follow-ups-deferred.md`).
- **복구는 파괴적으로 하지 않는다.** 앞서 있는 커밋은 `git format-patch` → 개발 체크아웃에서 `git am`으로 **작성자·타임스탬프를 보존한 채** main 위에 올리고, blob 바이트 동일성을 확인한 뒤 체크아웃을 정렬한다. 되돌림 대비 ref를 노드에 남긴다(선례 `785eb34`/`67fd9e9`, 유실 0건). `reset --hard`로 먼저 맞추는 것은 학습분을 지우는 행위다.
- **배경(사후 반영)**: 2026-07-27, 노드의 agent 계정이 `skills/mail/SKILL.md`(세미나 출장 신청 안내 표준, v1.5.3→1.5.5)를 배포 체크아웃에 직접 커밋해 prod가 git에 없는 코드를 들고 있었고, ops가 origin보다 2커밋 앞서 모든 ff-pull이 막혔다. 동시에 그 학습분은 배포되지 않아 마운트본은 구버전이었다 — 두 실패가 한 원인에서 나왔다.

## 수리 반영 경로 규칙 (cha 지시, 2026-07-29)

**수리 에이전트는 배포 체크아웃에서 커밋하지 않는다. 전용 작업 클론에서 작업해 `repair/t_<ticket>` 브랜치로 push하고, 그 브랜치→main PR까지 에이전트가 생성한다. main 머지는 cha가 GitHub에서 한다.**

- **작업 위치**: 수리의 apply·commit·패치문서·회귀시나리오 등록은 전용 작업 클론에서만 수행한다. 배포 체크아웃(`/srv/autophagy-agents`)은 「ops 체크아웃 단방향 규칙」그대로 `git fetch`/`git pull --ff-only`만 허용된다. 샌드박스 단계는 이미 `git clone --shared`로 격리돼 있으므로(`repair_ops_adapters.py`) 그대로 둔다.
- **자격증명**: 저장소 **한정** write deploy key를 사용한다(fine-grained PAT 아님 — 범위가 더 좁기 때문). 배포 체크아웃의 ops 키는 **계속 read-only**로 둠 — 두 키를 섞지 않는다. 키는 `/srv/autophagy-private/repair_push_key`(ops:600)에 둔다 — **홈에 두면 안 된다**: 두 수리 유닛이 `ProtectHome=yes`라 `/home`이 서비스에게 빈 디렉터리로 보이고, 파일이 디스크에 멀쩡히 있는데도 런타임에만 "키 없음"으로 실패한다(회귀 고정: `tests/unit/test_repair_push_key_sandbox.py` — 유닛 파일에서 제약을 역산하므로 `ProtectHome`이 바뀌면 테스트도 따라 바뀐다). 호스트 키도 같은 이유로 `/srv/autophagy-private/repair_known_hosts`에 **고정**한다 — ssh는 `~/.ssh/known_hosts`를 passwd 엔트리로 해석하므로 `ProtectHome`이 가리고, 노드에 `/etc/ssh/ssh_known_hosts`도 ssh_config 전역 설정도 없어 유닛 안에서는 검증할 DB가 아예 없다(실증: 호스트 키 DB를 끊으면 `ls-remote`가 실패). `accept-new`로 우회하지 않는다 — write 자격증명이 오가는 유일한 경로에서 아무 키나 신뢰하게 되기 때문이며, 파일이 없으면 역시 exit 4다. **같은 덫이 2026-08-21에 다시 걸렸다(2026-08-26 수리)**: `approval_reminder_config` 의 폴백이 `~/.hermes/config.yaml` 을 `Path.is_file()` 로 찔렀는데 CPython 이 EACCES 를 삼키지 않아 예외가 났고, 그 raise 가 `except (ImportError, ModuleNotFoundError)` 절 **안에서** 일어나 형제 `except Exception` 이 잡지 못해 그대로 탈출했다 — repair 승인 워처가 5일간 매분(5,329회) 기동 즉시 죽어 ✅ 를 아무도 소비하지 않았다. 교훈: `ProtectHome` 유닛에서 홈을 찌르는 코드는 **답해야지 던지면 안 된다**. 보이지 않는 설정은 없는 설정과 같다(`9e1b7ad0`). 경로는 `REPAIR_PUSH_KEY`로 덮어쓸 수 있고, 키가 없으면 **push를 시도하지 않고 exit 4로 실패**한다 — 조용히 ops read-only 키로 폴백하면 진짜 원인에서 먼 곳에서 실패하기 때문.
- **push 대상은 브랜치뿐**: `repair/t_<ticket>` 패턴으로만 push한다. **`main` 직접 push 금지**, 자동 ff-머지도 금지.
- **PR 생성까지가 에이전트 종착점 (cha 지시, 2026-07-31)**: 브랜치를 push했으면 에이전트가 곧바로 `repair/t_<ticket>`→`main` PR을 `gh pr create --base main --head repair/t_<ticket>`로 생성한다(제목·본문은 커밋 스타일; 본문에 티켓 id·수정 요약·검증 증적·**공개 적합성 항목**(채널 id·과제명·개인 경로 하드코딩 없음 확인 — 「개인화 코드 금지 규칙」), 실수신자/본문 등 민감정보 마스킹). **push만 하고 멈추지 않는다** — cha에게는 GitHub에서 브랜치 diff를 눈으로 확인해 Merge 버튼을 누르는 트리거가 필요하고, PR이 없으면 그 트리거 자체가 없다(2026-07-31 선례: 브랜치만 push되고 PR이 없어 cha가 머지할 방법이 없었다). 이미 열린 PR이 있으면 새로 만들지 않고 그 PR을 재사용한다(push만으로 헤드가 갱신됨). `gh` 미설치·미인증이면 설치·인증 후 진행하고, 그래도 불가하면 PR을 만들 수 없음을 명시해 cha에게 알린다.
- **main 머지 주체 = cha**: cha가 GitHub PR에서 diff를 확인하고 Merge 버튼으로 머지한다. 이것이 자동화의 의도된 종착점이다 — PR까지 자동이면 급한 불은 이미 꺼지고, main 반영만 사람 눈을 거친다. **에이전트는 main에 직접 머지·push하지 않는다.** 머지 후 노드는 평소대로 `pull --ff-only`로 받고, 에이전트는 반영을 확인한 뒤 kanban 티켓 완료 처리까지 이어간다.
- **승인은 패치 내용에 바인딩된다 (완료, 2026-07-29)**: `action_hash`는 `sha256:<canonical(ticket_id, patch_name, 패치 바이트 sha256, 변경파일·라인증감 요약)>`이고, 승인 DM은 변경 파일 목록·라인 증감·`patch_sha256`을 실는다. 패치 본문은 여전히 Discord에 노출하지 않고 `/srv/autophagy-private/`의 경로만 안내한다. 적용 게이트는 적용 직전 디스크의 패치로 해시를 재계산해 소유자가 실제로 승인한 해시와 대조하므로, **승인 뒤 `patch.diff`가 바뀌면 적용되지 않는다**. 불일치는 `False`가 아니라 **예외**로 표면화한다 — `_run`은 `AWAITING_APPROVAL`에도 exit 0을 돌려주고(`repair_ops_cli.py:174`) 워처는 자식이 0이면 레코드를 회수한 뒤 approvals 로그에 `approved`를 남기기 때문에, `False`로 거부하면 아무것도 적용하지 않은 채 감사 기록만 생긴다. 구스키마(이름만 해싱) 레코드는 읽힐 수는 있으나 **인가하지 못한다**; 소유자가 반응하지 않은 것은 새 요청이 교체하고, 이미 누른 것은 파괴하지 않고 24h TTL로 정리된다. 승인 메시지 렌더러 **v1은 동결**이다 — 이미 게시된 메시지는 저장 레코드의 재렌더와 정확히 일치해야 하며, 문구를 바꾸려면 v3을 만든다(2026-07-29 `5ef869d` 선례). 구현 `automation/repair/repair_patch_{diff,binding}.py`·`repair_approval_render.py`, 상세 [소개](docs/기능소개/수리-승인-내용-바인딩.md), 증적 `docs/qa/RTS-4/r2-content-binding.txt`.
- **롤아웃 순서(완료, 2026-07-29)**: ① apply 대상을 작업 클론으로 이관(`08a221d`) → ② systemd `ReadWritePaths`에서 `/srv/autophagy-agents` 제거(`7ea6a8c`) → ③ 브랜치 push 추가(`b86e1df`, 키 경로 수정 `d30b4a1`) → ④ 배포 체크아웃 커밋 거부 훅 롤아웃(노드 설치 완료). 순서를 바꾸면 훅이 수리 apply 경로를 깨뜨리므로, 재구축 시에도 이 순서를 지킨다. ④ 증적: 배포 체크아웃에서 `git commit --allow-empty`가 exit 1로 거부되고 HEAD 불변, `git fetch`·`git pull --ff-only`는 정상 동작.
- **선행 충돌 주의**: `.omo/plans/repair-report-{core,rollout}.md`(타 세션, 미추적 상태)가 `repair_ops_core.py`·`repair_core.py`·`automation/interop/report.py`·**`automation/repair/systemd/*`(예외 없음)**·일부 승인 흐름의 변경을 금지하고 있다. 위 롤아웃 ②는 systemd 유닛을 건드리므로 **해당 계획과 순서를 조율한 뒤** 진행한다. 먼저 손대면 다른 세션의 확정 설계를 덮어쓰게 된다(2026-07-21 선례).
- **배경**: 2026-07-28 드리프트 복구 중, 배포 체크아웃의 미병합 커밋을 생산한 주체가 사람이 아니라 **수리 자동화**임이 드러났다(`repair_ops_cli.py:59` 기본값 `REPAIR_CHECKOUT=/srv/autophagy-agents`, `repair_ops_adapters.py:233-244,272`의 `git commit`/`--amend`/`revert`, 유닛의 `ReadWritePaths`). ops deploy key가 read-only라 그 커밋들은 origin에 도달할 수 없어 — 프로덕션이 git에 없는 코드를 돌고, 다음 정렬 때 유실되며, 그전까지 모든 세션의 ff-pull이 막힌다. 즉 즉시 반영이라는 장점을 유실·파이프라인 정지로 지불하는 구조였다.

## 세션 워크트리 규칙 (cha 지시, 2026-08-03)

**세션마다 워크트리를 하나 만들어 그 안에서 작업한다 — 여러 세션이 한 워킹트리를 공유하지 않는다.**

- **왜**: 워킹트리를 공유하면 같은 파일을 동시에 만진다. 2026-08-03 실측 — `docs/features.md`를 두 세션이 함께 편집해 한쪽이 남의 묶음 헤더를 덮어썼고, 커밋 스테이징에도 남의 미완성 편집(문장이 끊긴 채로)이 섞였다. 「다른 세션 작업 덮어쓰기 방지」·「커밋 전 diff 확인 규칙」이 사람의 주의로 막던 것을 구조로 막는다.
- **비용은 작다**: 추적 파일 14MB, 생성 0.1초(실측). gitignore된 대용량 잔여물(`configs/rag/*/.venv` 5.5GB)은 따라오지 않으므로, **그것이 필요한 작업은 메인 체크아웃에서** 한다.
- **훅·가드는 그대로 작동한다**: 워크트리는 `.git/hooks`를 공유하므로 gitleaks pre-commit이 그대로 돈다(고엔트로피 토큰 실제 차단 확인). provenance 가드와 전체 테스트도 워크트리에서 통과한다(3012 passed). `.git`을 디렉터리로 가정하는 6곳은 전부 노드측 경로(미러·배포 체크아웃·전용 클론)라 개발 워크트리는 도달하지 않는다.

```bash
automation/worktree.sh start <이름> [--paths <손댈 경로>...]   # 세션 시작
automation/worktree.sh finish <이름>                          # 세션 종료
```

**시작 전에 기반이 최신인지 확인한다 (cha 지시)** — 낡음에는 세 종류가 있고 각각 다른 곳에서 잡힌다:

- **① 로컬 `origin/main` ref가 낡음** → `start`가 fetch를 **강제**하고, 실패하면 시작하지 않는다. 캐시된 ref를 조용히 쓰는 것이 정확히 2026-07-21 사고(옛 체크아웃이 최신 결정을 덮어써 배포 404)의 모양이기 때문에 경고가 아니라 거부다. 따라잡은 커밋 목록을 출력해 **무엇을 놓치고 있었는지** 보여준다.
- **② 작업 중 origin/main이 전진** → 시작 시점에는 못 잡는다(2026-08-03 하루에 3회 전진). PR 시점의 mergeable 판정과 `finish`의 fetch가 잡는다.
- **③ 손댈 파일을 다른 세션이 방금 바꿈** → ref가 최신이어도 모른다. `--paths`를 주면 그 경로의 최근 커밋과 **메인 체크아웃의 미커밋 변경**을 함께 보여준다. 「다른 세션 작업 덮어쓰기 방지」가 요구하는 확인을 기억이 아니라 출력으로 만든다.

**종료 시 착지 여부를 확인한다** — `finish`는 미커밋 변경이나 `origin/main`에 착지하지 않은 커밋이 있으면 **제거를 거부**한다. 2026-08-03 실측: 이미 머지된 PR의 브랜치로 증적을 push해 브랜치에는 올라갔으나 착지하지 못했고(push가 성공하므로 아무 신호도 없다), 정리 직전 이 확인이 QA 증적 65줄을 살렸다.

**머지된 브랜치에는 다시 push하지 않는다** — 새 작업은 새 브랜치. 2026-08-03에 두 번 어겼고 한 번은 유실 직전까지 갔다. `start`가 이름·브랜치 중복을 거부하는 것이 이것을 돕는다.

**원격 브랜치는 자동으로 지우지 않는다** — 공유 영향이라 사람이 판단한다(회귀로 고정: `tests/unit/test_session_worktree.py`).

## PR 전 검증 규칙 (cha 지시, 2026-08-25)

**브랜치 push 는 그 트리의 로컬 CI 영수증이 있어야 나간다 — 「PR 전에 CI 를 돌려라」는 산문이 아니라 `automation/hooks/pre-push` 가 강제한다.**

- **왜 훅인가**: 이 저장소는 브랜치 보호를 쓸 수 없다 — private + Free 조합이라 `gh api repos/.../branches/main/protection` 이 **403** 이다. 즉 GitHub CI 는 머지를 막을 권한이 없는 **권고**였고, 2026-08-25 에 실제로 빨간 CI(결제 실패로 잡이 시작조차 못 한 것) 위에서 머지가 이뤄졌다. 지침으로 적으면 그 문장을 읽는 주체가 지키는 만큼만 작동한다 — 「배포 provenance 규칙」의 가드와 「ops 체크아웃 단방향 규칙」의 커밋 거부 훅이 산문에서 코드로 옮겨간 것과 같은 이유다.
- **순서는 커밋 → `automation/local_ci.sh run` → push** 다. `run` 은 워크플로와 같은 세트(lint · `pytest tests/unit` · 빈 `python:3.12-slim` 컨테이너의 설치기 dry-run + 서드파티 경계 테스트)를 돌리고 **전 단계 통과 시에만** 영수증을 `~/.hermes/local-ci/<tree>.json`(0700/0600, 체크아웃 밖)에 쓴다. 어느 단계든 실패하면 영수증은 만들어지지 않는다.
- **영수증은 commit 이 아니라 tree 에 묶인다.** 문구만 고쳐 amend·rebase 하면 그대로 유효하고, 내용이 한 글자라도 바뀌면 무효다. 워크플로 자신의 sha256 도 담기므로 `.github/workflows/ci.yml` 이 바뀌면 그 전 영수증은 무효가 된다. 더러운 트리에서는 발급하지 않는다 — 검사한 것과 push 하는 것이 달라지면 영수증이 거짓말이 되기 때문이며, 유일한 예외는 `.omo/senpi-task/`(하네스가 매 턴 다시 쓰는 세션 장부, 어떤 검사도 읽지 않는다)다.
- **범위는 `refs/heads/*` 중 main 이 아닌 것**이다 — **태그 push 는 대상이 아니다**. 릴리스 태그는 이미 착지한 커밋을 가리키므로 거기서 막으면 보호되는 것 없이 리컨실러만 선다(「릴리스 태그 규칙」이 경고한 132회 연속 실패가 정확히 그 모양이다). 2026-08-25 실측: 범위를 `main 이 아닌 ref`로 쓴 첫 판본은 실제로 태그 push 를 거부했다. main 착지는 `automation/land.sh` 가 자기 가드로 소유하고, 세션 워크트리의 main 직접 push 거부는 그대로다. 게이트가 생기기 전에 딴 브랜치는 리포 안에 구제 수단이 없으므로 **경고 후 통과**시킨다 — 판정 불가는 위반이 아니다(`runtime_package_probe` 의 UNKNOWN 과 같은 취급). 그 브랜치도 main 에 rebase 하는 순간 게이트에 든다.
- **로컬 세트가 CI 에서 갈라지지 않는다**: `tests/unit/test_local_ci_push_gate.py` 가 `.github/workflows/ci.yml` 의 `run:` 명령을 뽑아 `local_ci.sh` 와 **기계 대조**한다. 로컬에서 실행하면 안 되는 명령(개발자 환경을 변형시키는 `pip install -r requirements-dev.txt`)은 스크립트 주석이 아니라 그 파일의 `_LOCAL_ONLY` 에 사유와 함께 등록한다 — 주석에 적으면 grep 은 통과하고 동작은 없는 상태가 조용히 만들어진다. 예외가 낡는 것도 테스트가 잡는다.
- **탈출구는 `LOCAL_CI_ALLOW_UNVERIFIED=1` 하나뿐이고 샌드박스/실험 전용이다.** 통과시키려고 상습적으로 쓰면 가드가 무의미해진다(`DEPLOY_ALLOW_UNPUSHED` 와 같은 성격). 쓰면 매 push 마다 stderr 에 남는다.
- **비용 결정(옵션 A)**: `.github/workflows/ci.yml` 에서 **main push 트리거를 제거**했다. PR 에서 이미 통과시킨 트리를 머지 직후 다시 돌리는 중복이었고 최근 100 회 실행 중 **48 회**가 그것이었다(2026-08-25 실측). `pull_request` 실행은 남긴다 — 깨끗한 러너에서의 독립 검증은 로컬 영수증이 대신하지 못한다. 월 3,890~4,862 분 → 약 2,000 분(Free 포함분 이내).
- **한계**: 영수증은 "이 트리가 **이 기계에서** 통과했다"를 증명하지 "깨끗한 호스트에서 통과한다"를 증명하지 않는다. 그 간극은 clean-host 컨테이너 단계가 메우고 완전히는 못 메운다. 훅은 `automation/worktree.sh start` 가 설치하므로 훅을 설치한 적 없는 clone 에는 게이트가 없다 — gitleaks pre-commit 과 같은 성질이다. 상세: [소개](docs/기능소개/push-게이트-로컬-CI-영수증.md)

## 머지 규칙 (cha 지시, 2026-08-26)

**PR 머지는 `automation/merge-pr.sh <pr>` 로만 한다 — `gh pr merge` 를 직접 부르지 않는다.**

- **왜 명령에 두나**: 브랜치 보호를 쓸 수 없다(private + Free = 403). 켤 수 있어도 에이전트 토큰이 `admin` 이라 기본 설정은 그를 통과시키고, 관리자까지 묶으면 `automation/land.sh:189` 의 main 직접 착지가 서버에서 거부된다. 그래서 판정을 서버가 아니라 머지 명령 자체에 둔다. 2026-08-25 에 두 번 뚫렸다 — #267 은 빨간 CI 위에서, #269 는 체크가 큐잉되기도 전에 머지됐다(둘 다 에이전트).
- **무엇을 판정하나**: PR 이 OPEN 이고 base 가 main 이며, `.github/workflows/ci.yml` 이 선언한 잡이 **전부 보고**했고 그 전부가 완료·성공일 때만 머지한다. 진행 중이면 기다리고(기본 900초), 마감을 넘기면 거부한다. **체크 0건도, 일부만 올라온 상태도 통과가 아니라 PENDING** 이다 — 2026-08-26 첫 실사용에서 그 순간 올라와 있던 체크 1건(GitGuardian)만 보고 통과시킨 적이 있다. 기다릴 잡 이름은 워크플로에서 **파생**하며 하드코딩하지 않는다. 머지 불가(`CONFLICTING`)는 `gh` 가 실패한 뒤가 아니라 판정 단계에서 거부하고 해소 절차를 안내한다.
- **태그는 더 이상 여기서 자르지 않는다(VA-3, 2026-08-30)**: 머지는 축적이고, 서명 태그는 릴리스 승인(`automation/release.sh`, VA-1)이 소유자 ✅ 1회를 받아 자른다. 머지마다 태그를 자르면 그 릴리스 승인이 있으나 마나가 된다. 태그 없는 창의 리컨실러 통지는 sha별 사고가 아니라 3일 임계의 릴리스 백로그 다이제스트다(「릴리스 태그 규칙」 참조).
- **PR 체크는 로컬 영수증과 다른 것을 덮는다**: GitHub `pull_request` 실행은 **머지 결과 트리**를, `automation/local_ci.sh` 의 영수증은 **브랜치 트리**를 검사한다. 2026-08-25 에 실제로 갈렸다(머지 결과 `43c50a44` vs 영수증 `57081383`). 둘 다 필요하다.
- **탈출구는 `MERGE_PR_ALLOW_UNCHECKED=1` 하나**이고 **체크가 아예 나오지 않을 때만**을 위한 것이다(예: Actions 결제 중단). **실패한 체크는 이것으로도 통과하지 못한다.** 쓰면 매번 stderr 에 남는다.
- **강제**: `tests/unit/test_merge_pr_gate.py` 가 `automation/`·`skills/`·`tests/` 의 `*.sh`·`*.py` 에서 래퍼 밖 `gh pr merge` 호출을 금지한다 — 주석에 적으면 grep 은 통과하고 동작은 없다.
- **한계(알고 쓸 것)**: 머지는 GitHub 서버에서 일어나므로 push 훅 같은 **구조적** 강제가 원리적으로 불가능하다. 이 게이트는 명령을 쓰는 주체에게만 구속력을 갖는다. 서버측 강제를 원하면 GitHub Pro 의 브랜치 보호가 유일한 길이고, 그때는 `land.sh` 를 PR 경유로 바꾸는 것이 선행 조건이다. 상세: [소개](docs/기능소개/머지-체크-게이트.md)

## 커밋 전 diff 확인 규칙 (cha 지시, 2026-07-29)

**`git add` 전에 반드시 `git diff --stat`(필요시 `git diff`)로 의도한 파일만, 의도한 방향으로 바뀌었는지 확인한다. 설명되지 않는 삭제나 내가 만들지 않은 변경이 섞여 있으면 그대로 커밋하지 않는다.**

- **왜 별도 규칙인가**: 「다른 세션 작업 덮어쓰기 방지」는 *내가 남의 것을 되돌리는* 방향을 막는다. 이 규칙은 **반대 방향**을 막는다 — *남이 되돌린 워킹트리를 내가 모른 채 커밋해 그 삭제를 굳혀버리는* 경우다. 병렬 세션·노드 에이전트가 같은 체크아웃을 공유하므로, 내가 고친 파일만 `git add` 해도 **그 파일 안에 남의 되돌림이 이미 들어 있을 수 있다**.
- **무엇을 보나**: ① 변경 파일 목록이 내가 손대길 의도한 집합과 일치하는가. ② **삽입/삭제 줄 수의 방향**이 맞는가 — 문서를 추가했는데 순삭제(예: `+9/-20`)면 즉시 멈춘다. ③ 문서·규칙 파일이면 삭제된 줄을 직접 읽는다. ④ `version:` 문자열이 **낮아지는** 변경은 거의 항상 되돌림이다(배포본과 대조할 것).
- **발견 시 조치**: 되돌림으로 판정되면 커밋하지 말고 `git show origin/main:<path>`로 정본을 대조해 복원한다. **이미 커밋된 정본을 되찾는 복원**(`git checkout -- <path>`)은 「미커밋 작업 보존」 원칙과 충돌하지 않는다 — 반대로 남의 **생산물**을 지우는 방향이면 지우지 말고 소유자에게 묻는다.
- **push 전에 한 번 더**: `git diff --stat <직전 정상 커밋>..HEAD`로 삭제가 0이거나 의도한 것뿐인지 확인한다. push 전에 잡으면 복구가 쉽고, 놓치면 다른 세션이 그 상태를 기준으로 작업해 유실이 번진다.
- **배경(사후 반영)**: 2026-07-29 한 세션에서 두 번 발생했다. (1) `docs/features.md` 편집 시 워킹트리에 이미 되돌림이 섞여 있었고 확인 없이 커밋해 **후속 과제 4건이 유실**됐다(`373a0fa` — 문서를 더하는 커밋이 `+9/-20`이었는데 그 신호를 놓쳤다). `7ea6a8c` 정본 기준 재구성으로 복구했다(`46c82de`, 삭제 0 확인). (2) 직후 검증에서 `AGENTS.md`의 「수리 반영 경로 규칙」 18줄·CODE MAP 5행 삭제, 기능소개 문서 삭제, `doctype/SKILL.md`의 **v1.3.1→v1.2.2 후퇴**(배포본과 불일치), 코드 8건 후퇴로 테스트 10건 실패가 관측됐다 — 모두 미커밋 상태여서 origin 정본으로 복원했다.

## 공개 릴리스 규칙 (cha 지시, 2026-08-15)

**private repo(`orientpine/autophagy-agents`)는 앞으로도 계속 유일한 개발 origin이고, 공개 repo(`orientpine/cytoplasm`)는 `automation/public_export.sh`로만 갱신되는 일방향 파생 아티팩트다 — 공개 repo에서 작업하지 않고, 공개 repo에 손으로 push하지 않는다.**

- **공개 릴리스가 나갔다고 private가 불필요해지지 않는다.** 이것은 가정이 아니라 실제로 나온 질문이다 — v1.0.0 직후 소유자가 "이제 공개 repo에서 작업을 이어가야 하는가, private는 불필요해지는가"를 물었고 둘 다 **아니오**다. 커밋·PR·머지·배포 provenance·전 노드 deploy key가 전부 private에 묶여 있다.
- **새 릴리스 = 같은 스크립트를 `--version`만 올려 다시 실행하는 것**이다. 공개 repo에 무언가를 밀어 넣는 것이 아니다. 스크립트는 공개 트리 커밋 생성 → **그 커밋에** update-trust 서명 태그 → `push --atomic`을 **한 실행 안에서** 한다. private에서 태그를 먼저 서명하는 순서가 아니다 — fresh history라 그 커밋은 공개 repo에 없고, 그러면 사용자 노드가 검증할 태그가 생기지 않아 자동 업데이트가 **연합 전체에서** 멈춘다(D8).
- **손 push는 조용하지 않지만 늦게 발견된다.** 다음 export가 `git rm -r --ignore-unmatch .` 후 private 스냅샷을 복사하므로 그 내용은 말없이 사라지고, 그 사이 공개 `main`이 서명 태그 커밋에서 벗어나 모든 노드가 `UNSIGNED-HEAD`로 전진을 멈춘다(리컨실러는 rc 0으로 종료해 알람이 없다).
- **서명키가 둘이다 — 혼동 금지**(D8). 업데이트 신뢰키(`update-trust@autophagy` / `/etc/autophagy/update-allowed-signers` / `update_trust.py`)는 업스트림 유지보수자가, 그룹 스킬 서명키(`publisher-<slug>@autophagy` / `/etc/autophagy/managed-skills-allowed-signers` / `managed_sync/verify.py`)는 그룹 관리자가 소유한다. **두 개인키 모두 어떤 git 저장소에도 커밋하지 않고 노드에 배포하지 않는다** — 노드에 놓이는 것은 공개키 한 줄뿐이다.
- **나쁜 릴리스는 옆으로도 뒤로도 갈 수 없고 앞으로만 되돌린다.** `update_trust_state.py`의 롤백 방지 floor가 옛 태그 재-push를 `RELEASE-ROLLBACK`으로 거부한다(공격자 강제 다운그레이드 방어 — 비대칭은 의도된 것이다). 취소는 문제를 revert해 private에 랜딩하고 **더 높은 버전**을 컷하는 것이다.
- 전체 절차(사전조건·매니페스트 원장·실행·릴리스 노트·신뢰키 회전·나쁜 릴리스 대응)는 [docs/guide/manual-maintainer.md](docs/guide/manual-maintainer.md)가 단독으로 소유한다. 이 절은 불변식만 들고 있고 절차를 복사하지 않는다 — 같은 절차를 두 문서가 설명하면 반드시 한쪽이 낡는다.
- **배경(사후 반영)**: 2026-08-15 W-F5-A로 실제 공개 repo `orientpine/cytoplasm`이 생기고 `v1.0.0`이 서명·push되자마자 소유자가 "이제 공개 repo에서 작업하는가"를 물었다. 이 저장소는 2026-07-21에 이미 같은 계열의 사고를 겪었다 — 확정된 설계에 대한 오해가 세션을 넘어 전달돼 배포가 404로 실패했다. 답을 산문으로만 두면 같은 일이 반복되므로 불변식을 여기에 박아 둔다.
- 세 저장소(private 개발 origin·공개 배포본·그룹 스킬 채널)의 구분은 아래 「세 저장소 구분 규칙」이 소유한다 — 이 절은 private↔public 방향만 다룬다.

## 개인화 코드 금지 규칙 (SC-2, 2026-08-30)

**개인 맞춤값(채널·메시지 id 상수, 과제명·기관명, 개인 경로·이름)은 코드에 하드코딩하지 않는다 — 자리는 `configs/*.example` 시드와 `~/.hermes/…`·`/srv/autophagy-private/…` 런타임 설정뿐이다.**

- **왜**: 이 저장소의 모든 코드는 PR→main→다음 릴리스에서 `public_export.sh`로 공개 배포본(`cytoplasm`)에 나간다. 시크릿은 gitleaks·`public_export_redaction`이 잡지만 **개인 맞춤 로직은 기계가 못 잡는다** — snowflake 상수 하나, 과제명 분기 하나가 그대로 공개된다. 특히 **수리(repair) 패치**는 프로덕션 관찰에서 태어나므로 개인화가 스며들기 가장 쉬운 경로다.
- **어떻게**: 설치별 값은 `configs/node.example.toml`·`configs/*.example`(파서 유효한 자리표시자) + 런타임 override(`~/.hermes/node.toml`, `/etc/autophagy/…`)의 기존 패턴을 따른다. 채널은 `approval_surface`/`approval_directory`·config 키로만 해석한다(기존 불변식 그대로). 테스트의 실측 예시 값(id·이름)은 허용된다 — 배포되는 코드가 아니다.
- **수리 PR 은 본문에 공개 적합성 항목을 싣는다**: "이 패치에 채널 id·과제명·개인 경로 하드코딩이 없다"를 확인란으로 명시한다(「수리 반영 경로 규칙」의 PR 본문 요구에 포함). PR 을 사람이 만들든 자동화(`docs/guide/수리-PR-자동생성-조율안.md`)가 만들든 같다.
- **repair 유래 파일이 `configs/`·`docs/` 에 새로 생기면** `configs/public-export-review.txt` 의 검토 절차(파일 헤더)를 따른다 — 공개/제외를 결정하기 전에 개인화 여부를 먼저 본다.
- **배경**: `.omo/plans/release-convergence-and-versioned-approval.md` §5.2 SC-2. `public_export.sh` 는 `git archive <commit>` 으로 스냅샷을 재물화하므로(2026-08-30 실측, A3 검증) 추적된 코드만 나간다 — 그래서 막을 곳은 정확히 "추적되는 코드 안의 개인화"다.

## 세 저장소 구분 규칙 (cha 지시, 2026-08-16)

**이 시스템에는 서로 다른 역할의 git 저장소가 셋 있고 — `orientpine/autophagy-agents`(private, 유일한 개발 origin) · `orientpine/cytoplasm`(public, 코드 배포 파생본) · `orientpine/ribosome`(private, 그룹 관리형 스킬 채널) — 셋은 합쳐지지 않는다. 어느 것을 말하는지 먼저 확정하고 답한다.**

| | `autophagy-agents` | `cytoplasm` | `ribosome` |
|---|---|---|---|
| 가시성 | private | **public** | private |
| 역할 | **유일한 개발 origin**(영구) | 코드 배포 파생본 | 그룹 관리형 스킬 채널 |
| 담기는 것 | 소스 전체 + `.omo/` + `docs/qa/` | 공개 스냅샷(fresh history) | 발행 스킬 릴리스 + 매니페스트 + `refs/heads/roster` |
| 갱신 경로 | 사람·에이전트의 커밋·PR·머지 | `automation/public_export.sh` 단독 | `automation/managed_skills/publish_cli.py` 3단계 게이트 |
| 서명키 | — | `update-trust@autophagy` | `publisher-<slug>@autophagy` |
| 신뢰 파일 | — | `/etc/autophagy/update-allowed-signers` | `/etc/autophagy/managed-skills-allowed-signers` |
| 검증 코드 | — | `automation/update_trust.py` | `automation/managed_sync/verify.py` |
| 팀원 접근 | 없음 | 공개 clone | read-only deploy key(= 제거 메커니즘) |
| 받는 쪽 동작 | — | 2분 리컨실러 자동 수렴 | fetch→검증→격리, **마운트는 본인 ✅**(D3) |
| 소유 매뉴얼 | — | [manual-maintainer.md](docs/guide/manual-maintainer.md) | [manual-group-admin.md](docs/guide/manual-group-admin.md) |

- **혼동의 진짜 원인: 설계상 소프트웨어 유지보수자와 그룹 관리자는 다른 사람이다**(D2, D1.1). 팀원의 소프트웨어 업데이트는 그룹 관리자를 거치지 않고 업스트림에서 직접 온다. cha가 지금 두 역할을 겸하고 있어 두 채널이 중복처럼 보일 뿐이며, 제3자가 자기 그룹을 열면 `ribosome`에 해당하는 자기 repo만 갖고 소프트웨어는 `cytoplasm`에서 받는다.
- **합칠 수 없는 이유 ①: 접근 통제가 정반대다.** `cytoplasm`은 공개라 누구나 clone한다. 그룹 스킬 repo는 팀원마다 read-only deploy key를 발급하고 **그 키 폐기가 곧 멤버 제거**다(D1.3) — 공개 repo에서는 제거라는 개념 자체가 성립하지 않는다.
- **합칠 수 없는 이유 ②: 검증 경로가 분리되어 있다.** 위 표의 서명키·신뢰파일·검증코드가 두 열 모두 다르고, 하나가 다른 하나를 대신하지 못한다. 산문이 아니라 코드가 진실이다 — `update_trust.py`(`UPDATE_ALLOWED_SIGNERS_PATH`)와 `managed_sync/verify.py`는 서로를 호출하지 않는 별개 경로다. D8의 「서명키가 둘이다 — 혼동 금지」가 가리키는 지점이 바로 여기다.
- **합칠 수 없는 이유 ③: 업데이트 성격이 다르다.** 코드는 root 권한으로 자동 수렴하고, 스킬은 배달만 자동이고 live 마운트는 소유자 승인이다(D3, 영구 비목표).
- **그룹이 언제 필요한가**(소유자가 실제로 물은 판단 기준): 팀원이 각자 자기 에이전트만 쓴다 → 그룹 **불필요**, 각자 `cytoplasm`에서 설치하면 끝. 에이전트끼리 Discord로 보고·조율한다 → **roster는 필요**(발신자 신원 대조, W-F2.5-B)하지만 repo는 선택이며 `~/.hermes/roster.yaml`을 손으로 배치해도 동작한다. 팀 공통 스킬을 배포한다 → 그룹 스킬 repo **필요**.
- 절차는 여기에 복사하지 않는다 — 릴리스 컷은 [manual-maintainer.md](docs/guide/manual-maintainer.md), 발행·roster는 [manual-group-admin.md](docs/guide/manual-group-admin.md)·[managed-skill-channel.md](docs/guide/managed-skill-channel.md), 설치는 [install.md](docs/guide/install.md)가 단독으로 소유한다.
- **배경(사후 반영)**: 2026-08-16 그룹 스킬 채널 repo `orientpine/ribosome`이 생기자마자 소유자가 세 저장소의 관계를 이해하지 못해 "내가 이해할 수 있게 도와줘"라고 물었다. 전날에도 같은 계열의 질문(공개 repo가 private를 대체하는가)이 나왔고, 2026-07-21에는 확정 설계에 대한 오해가 세션을 넘어 전달돼 배포가 404로 실패한 전례가 있다. 한 번 나온 혼동은 다음 사람도 겪으므로 채팅으로 한 번 답하지 않고 불변식으로 박아 둔다.

## 산출물 출처 규칙 (cha 지시, 2026-08-26)

**공정표·계획서·보고서 등 회의에서 파생되는 산출물은 완성 회의록에서만 작성한다 — 음성 전사본은 출처가 아니다.**

- **완성 회의록이 있는 곳**: Drive `autophagy/회의록/<과제명>/<연도>/`. `meeting_cli ingest --project <과제명>` 이 그 자리에 발행하고, 경로 규칙은 `automation/drive_taxonomy.py` 의 `meeting` 카테고리가 소유한다. 산출물을 만들기 전에 **거기부터 읽는다**. 읽기는 승인 게이트 대상이 아니다(denylist 는 `gws drive files create|+upload` 만 건다). `.md` 는 Google Docs 가 아니므로 `files export` 가 아니라 `files get --params {fileId, alt:media}` 이며, `gws` 는 cwd 밖 `--output` 을 거부하므로 목적지에서 실행한다.
- **로컬 노트(`~/notes/meetings/*.md`)는 정본이 아니다.** 그 파일은 `meeting_minutes.APPENDIX_HEADING`(`## 부록 · 근거와 원문`) 아래에 **원문 전사본을 통째로 안고 있다**. 부록 위만 회의가 결론 낸 것이고, 아래는 재료다. 파일을 통째로 읽으면 음성 인식 결과를 사실로 읽게 된다.
- **경계는 코드가 소유한다.** `meeting_minutes.finalized_view(text)` 가 부록 위만 돌려준다. 그 상수 옆에 붙어 있으므로 사본을 만들면 갈라진다 — 새로 자르지 말고 그 함수를 쓴다. 전사본 절은 렌더러가 `TRANSCRIPT_WARNING` 을 바로 아래에 찍어 금지를 유혹이 있는 자리에 둔다(회귀 고정: `tests/unit/test_meeting_skill.py`).
- **회의 상태 파일 두 개도 같은 트리에 있다.** 과제의 미결·완료 action item 원장(`회의록/<과제>/action-items.csv`)과 관리번호 코드 레지스트리(`회의록/project-codes.csv`)는 날짜 없는 상태 파일이며, 규약과 진입점(`drive_outputs.publish_state_file` / `fetch_state_file`)은 [drive-publish.md](docs/guide/drive-publish.md) 가 단독으로 소유한다.
- **카드도 정본을 먼저 가리킨다.** `--project` 를 준 인제스트의 카드 본문은 `출처(정본): Drive 회의록/<과제명>/` 을 먼저 적고 로컬 사본을 뒤에 적는다. 과제명을 모르면 없는 경로를 지어내지 않고 기존 로컬 경로만 적는다. 민감 회의의 마스킹 카드에는 과제명을 싣지 않는다(과제명은 항목 문자열 규칙 재검사를 거치지 않았다).
- **배경(사후 반영)**: 2026-08-26, 전사본에서 만든 공정표 템플릿이 기관명을 틀렸다 — `한정기술`(정본은 **한국전력기술**, 다른 회사), `현대중공업 서부기계`(정본 **현대중공업터보기계**), 열교환기 `대통`(정본 **계통**) 열수력. 마감일도 `2028-02-01/04-01`(정본 **02-29/04-30**)로 어긋났고, 회의록이 규정한 열 위치(B2=기관명, B~E=대·중·소분류, H~I=시작·종료일, N=결과물)를 무시한 간트 격자를 지어냈다. 그 파일은 8개 참여기관에 배포될 것이었다. 완성 회의록에는 「용어·명칭 교정 기준」 표가 있어 이 오기들이 이미 교정돼 있었다 — 읽을 곳을 안 읽은 것이 유일한 원인이다.
## 산출물은 Drive에 둔다 (cha 지시, 2026-08-26)

**에이전트가 cha에게 내놓는 산출물(문서·표·보고서)은 로컬 경로에 남기지 않고 Drive 표준 트리에 발행한다.**

- **발행 경로는 하나다** — `automation/drive_outputs.py` 파사드. 세션에서 손으로 올릴 때는 `python3 -m automation.drive_publish_cli --kind <종류> --title <제목> [--project <과제>] <파일>` 을 쓴다. `gws drive` 를 직접 부르거나 헬퍼를 vendoring 하는 것은 금지이며 `tests/unit/test_drive_outputs_conformance.py` 가 막는다. 그 CLI 는 업로드 로직을 갖지 않고 인자만 파싱해 파사드에 넘긴다 — 두 번째 발행 경로를 만들지 않기 위해서다.
- **카테고리는 `automation/drive_taxonomy.CATEGORIES` 가 유일한 출처다.** 일반 문서 산출물은 `doctype`(문서). `meeting`(회의록) 은 회의록이 사는 곳이므로 회의에서 **파생된** 산출물을 거기 넣지 않는다 — 넣는 순간 그 폴더가 "회의가 결론 낸 것"을 뜻하지 않게 된다.
- **과제가 있으면 `--project <과제명>`** 으로 한 단을 넣어 같은 과제의 전사본·회의록과 묶는다. 과제를 쓰면 파일이 정확히 depth 5 에 놓이므로 그 발행의 산출물은 1개여야 한다(2개 이상은 번들이 되어 depth 6, `TaxonomyError` 로 거부된다).
- **워크스테이션에서 만든 파일은 그대로 올라가지 않는다.** `gws` 는 노드 `agent` 계정에만 있다. 노드로 옮긴 뒤 배포 런타임(`/srv/autophagy-agent-current`)을 `PYTHONPATH` 에 두고 발행하고, 노드의 임시 사본은 발행 직후 지운다.
- **발행했다고 말하기 전에 Drive 를 다시 조회한다.** 파사드가 sha256 재다운로드로 내용은 검증하지만, **사본이 늘지 않았는지는 별도 조회로만 보인다**. 대상 폴더에 파일이 정확히 1개인지 확인하고 그 출력을 증적으로 남긴다.
- 절차·경로·환경변수의 단독 정본은 [docs/guide/drive-publish.md](docs/guide/drive-publish.md) 다. 이 절은 불변식만 들고 있고 절차를 복사하지 않는다 — 같은 절차를 두 문서가 설명하면 반드시 한쪽이 낡는다.
- **배경(사후 반영)**: 2026-08-26, 8개 참여기관에 배포될 용역공정표 템플릿을 만들어 `~/Documents` 에 두었다. 규약은 이미 Drive 를 말하고 있었지만 파사드를 부르는 것은 스킬 코드뿐이었고 세션이 쓸 명령이 없었다. **명령 없는 규칙은 지켜지지 않는다** — 그래서 규칙과 진입점을 같은 사이클에 함께 넣는다.

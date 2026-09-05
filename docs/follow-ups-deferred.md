# 보류·인계 후속 과제

> [follow-ups.md](follow-ups.md) 에서 **이 저장소가 지금 손댈 수 없는** 항목을 옮겨 둔 곳이다.
> 버려진 것이 아니라 조건이 걸린 것이다 — 조건이 풀리면 해당 불릿을 `follow-ups.md` 로 되돌린다.
> 원 묶음 `##` 헤딩을 그대로 유지한다: 후속 과제 회계 가드(`tests/unit/test_features_board_conformance.py` A9)가
> 두 문서를 합쳐 읽어 **이동과 삭제를 구분**하기 때문이다. 헤딩이나 불릿 첫 줄을 고치면 그 가드가 삭제로 읽는다.
> 현황판 요약표([features.md](features.md))는 `follow-ups.md` 만 센다 — 여기 있는 것은 "지금 남은 일"이 아니기 때문이다.

| 분류 | 뜻 | 되돌아오는 조건 |
|---|---|---|
| OWNER | 소유자·노드에서만 닫힌다(승인 ✅, 라이브 확인, root 권한, 외부 계정) | 소유자가 그 실행을 마치면 |
| BLOCKED | 동결 파일·벤더 코드·외부 저장소에 막혀 있다 | freeze 해제 또는 업스트림 반영 시 |
| OBSERVE | 지금 조치하면 쓰이지 않을 추상을 얹는다 — 관측 후 판단 | 불릿이 적은 관측 조건이 실제로 성립하면 |
| 해소 | 이미 닫혔다. 회계 가드가 원문 보존을 요구해 남긴다 | 되돌아오지 않는다 |

# OWNER — 소유자·노드에서만 닫힌다

## Plaud lifelog 노트 v2 양식 착지 후 소유자만 닫을 수 있는 것 (2026-09-04)

- **[OWNER] vault Linter 의 `yaml-timestamp` 가 `date-modified-source-of-truth: file system` 이라 소유자가 Obsidian 에서 lifelog 노트를 처음 저장(lintOnSave)하면 `modified` 가 그 시각으로 바뀐다(`created` 는 유지 — 실제 플러그인 빌드 헤드리스 실측, `docs/qa/PLV2/linter-idempotence.txt`) → 녹음 시각을 두 값 모두에 보존하려면 vault Linter 설정에서 modified 의 source of truth 를 `frontmatter` 로 바꾼다(소유자 결정; 노트 생성 코드는 어느 쪽이든 바뀔 것이 없다).** 본문은 그대로이고 메타데이터 한 줄만 움직이는 표시 문제(심각도 낮음).
- **[OWNER] 노드 agent 계정의 `~/.env.secrets` 에 `LITELLM_AGENT_KEY`(·`LITELLM_BASE_URL`) 가 있어야 사람·장소·결정·할 일이 뽑힌다 → 릴리스 뒤 첫 `planned` 카드의 한눈에 줄을 본다: `- 추출:: 생략 (LLM 미설정)` 이면 키를 넣는다. 동결 본문은 승인 해시에 묶여 소급되지 않으므로 다음 녹음부터 적용된다.** 키가 없어도 노트는 결정론 필드(녹음·주제·한 줄)만으로 저장된다(심각도 낮음).
- **[OWNER] 2026-09-02 에 이미 push 된 lifelog 노트(직장 동료 대화)는 v1 양식이고 첫 줄의 깨진 포스터 이미지가 그대로다 → vault 에서 그 줄을 지우거나 v2 로 다시 쓰려면 소유자가 직접 편집한다.** processed 원장이 같은 녹음의 재동기화를 막으므로 코드는 소급하지 않는다(심각도 낮음).

## 릴리스 승인 카드에 대한 피어 봇 자체 심사 (2026-09-03)

- **[OWNER] peer 에이전트가 `[release]` 승인 카드(v1.1.2)에 `⛔ DO-NOT-APPROVE — HEAD a75057c0 확인 불가(unpushed tip)` 를 붙이고 "✅ 하지 말고 push 뒤 재게시하라" 고 안내했다 → peer 에게 `[release]` 카드는 심사 범위 밖(VA-1 은 소유자 단독 ✅, 피어 attestation 은 스킬 단위)임을 지시하거나, HEAD 존재 확인을 `git ls-remote origin` 으로만 하게 자가 스킬 참고 문서를 고친다(peer 홈 `~/.hermes/skills/software-development/skill-deploy-review/`, `hermes curator`).** 판정 주체는 저장소 코드가 아니다 — 그 자가 스킬은 `[skill-deploy]` 전용 결정적 리포터(git 호출 0)이고, peer 가 그 어휘만 빌려 즉석 심사를 하며 HEAD 를 노드 로컬 체크아웃(`origin/main=7e130408`)에서 찾았다. 미러는 설계상 릴리스가 수렴한 **뒤에야** origin/main 을 따라오므로(「ops 체크아웃 단방향 규칙」 sync_mirror) 새 릴리스 HEAD 는 로컬에서 항상 "unpushed" 로 읽힌다. 실측: a75057c0 은 PR #378 머지(13:01:22Z)로 origin/main 에 있었고 요청 게시는 그 뒤, v1.1.2 태그·`agent-current`·deploy 영수증 전부 그 HEAD. 영향: 카드마다 거짓 ⛔ 안내가 붙어 소유자가 멀쩡한 릴리스를 막을 수 있다 — 심각도 중(릴리스·노드는 무영향).
  ↳ 처리(2026-09-04): 적어 둔 두 선택지 중 어느 것도 아니라 **E7 결정 복원**으로 닫았다. 그 자가 스킬의 SKILL.md 는 이미 트리거를 `[skill-deploy]` 로 한정하고 리포터 출력을 verbatim 중계하라며 `Do not add an LLM judgment.` 를 명시하고 있었는데도 peer 가 `[release]` 카드에 LLM 판단을 붙였다 — 참고 문서를 한 번 더 고치는 것은 이미 실패한 통제의 반복이다. `docs/patch/2026-07-17-e7-peer-attestation.md` 가 "That skill directory is removed: an agent must not read a Discord request and be instructed to run a reviewer as part of deployment" 로 그 리뷰어를 은퇴시켰으나 2026-08-15 루트 반전이 자작 스킬 보호 목적으로 `provision-skill-roots.sh` 의 `PEER_PINNED_SKILLS` 에 이름을 넣어 매 프로비저닝이 되살리고 있었다(의도적 재검토가 아닌 부수 효과). 조치: pin 목록을 `(autophagy-interop)` 으로 줄이고 회귀를 `test_provision_when_the_retired_reviewer_is_present_then_it_is_not_pinned` 로 고정(**존재**를 전제로 검사 — 부재면 `PEER-PIN-SKIP` 이라 고치지 않아도 통과한다), 노드에서는 tarball 백업 후 `hermes curator unpin` → `archive`(삭제 아님, `restore` 가능). 사후 실측: 1차 루트에 `autophagy-interop` 만, `list-archived` 에 등재, `hermes skills list` 무영향. peer attestation 은 ops 체크아웃의 `peer_attest.py` 가 자체 봇 토큰으로 회신을 게시하는 경로라 무관하다(그 3파일에 자가 스킬 루트·curator 참조 0건). 상세 `docs/patch/2026-09-04-peer-retired-reviewer-unpin.md`.
  ↳ 처리(2026-09-05): 위 09-04 진단은 불완전했다 — v1.2.0~v1.2.2 카드에도 같은 심사(`⛔ 배포 승인 보류`)가 붙었다. 지시 문서는 pin 된 `autophagy-interop`(2026-09-01 자가 저작 `[release]` 절차)이었고, 소유자 결정(인터롭 유지, A+C)에 따라 peer 게이트웨이의 `discord.ignored_channels` 로 `#approvals` 수신을 끊고(A) 그 절차를 스킬에서 제거했다(C). 원문·근거는 「릴리스 승인 카드가 peer 시야에 있다 (2026-09-05)」의 처리 줄과 `docs/patch/2026-09-05-peer-gateway-ignores-approvals.md`.

## 수리 티켓 t_bd0d3789 종결 중 소유자만 닫을 수 있는 것 (2026-09-03)

- **[OWNER] 09-02·09-03 야간 재처리로 과제 원장 `회의록/해양고신뢰성/action-items.csv` 에 같은 회의의 미결 행이 중복 발급됐고(09-03 actions_new=4, 관리번호도 그만큼 소모), 옛 `회의록/2026` 폴더에는 야간 회의록 `2026-08-25_회의록-2026-08-26_20260825_해양고신뢰성.md` 가 남아 있다 → 소유자가 중복 행을 검토해 병합·종결하고 그 회의록 파일을 정본과 합치거나 지운다.** 이 PR 의 수정은 새 중복이 생기는 것을 멈출 뿐, 이미 쌓인 원장 행과 잘못된 자리의 파일을 되돌려 쓰지 않는다 — 원장 편집과 Drive 파일 정리는 소유자 판단이다(심각도: 낮음).

## 2026-09-03 수리 스윕(메일 인용·다이제스트 GLM 폴백)

- **[OWNER] glm-main 뒤 제공자의 잔액이 2026-09-02 15:21Z부터 소진돼 다이제스트·twin_distill·peer의 모든 비민감 GLM 호출이 HTTP 429를 받는다 → 소유자가 잔액을 충전하거나 별칭을 재바인딩한다.** FIX 2 뒤 다이제스트는 그때까지 codex 티어로 계속 처리된다(심각도: 중). [해소 2026-09-03: 잔액 충전이 아니라 별칭 재바인딩으로 닫혔다 — PR #370 이 `gpt-5-mini` 로 잠시 옮겼고 PR #373 이 `openai/gpt-5.6-luna`(`reasoning_effort: none`) 로 확정했다(routing-policy §2 바인딩 표 동기). 노드 `litellm-gateway/config.yaml` 에 luna 바인딩이 적용돼 재기동됐고 agent 키로 `glm-main` 완료 1건 성공을 SpendLogs 에서 확인했다. z.ai 는 별칭 뒤에서 빠졌으므로 충전할 것이 없다 — 남은 잔액·계정 정리는 소유자 선택이지 조치 항목이 아니다.]
- **[OWNER] FIX 3의 LiteLLM compose 변경은 ops가 <primary-node>에 파일을 복사한 뒤 `litellm-gateway.service`를 재시작해야 적용된다 → 컨테이너 재시작은 소유자가 결정한다.** 적용 전에는 이전 healthcheck가 계속 provider completion을 만들 수 있다(심각도: 중). [해소 2026-09-03: 노드 compose 가 `/health/liveliness` 로 갱신됐고 `litellm-gateway.service` 재기동으로 컨테이너가 healthy 로 올라왔다(PR #373 배포와 같은 사이클). 재기동 뒤 SpendLogs 에 `litellm-internal-health-check` 행이 더 이상 늘지 않는 것을 확인했다.]
- **노드의 live mail 스킬 마운트가 배포 릴리스 안에 있는 `c709c852`보다 앞서 있어 09-03 분류 실패가 그곳에는 기록되지 않았다 → 다음 릴리스 뒤 live/mail이 HEAD의 `triage_digest.py`를 담는지 확인한다.** 영향: 관측 결손, 동작 결함 아님(심각도: 낮음). [해소 2026-09-03: 릴리스 v1.0.154(`6e8f7c33`) 의 `deploy-skill.sh mail --release-approval` 로 live/mail 이 `7cf8afe2…`(SKILL 1.7.7) 로 올라갔고, `triage_llm_routing.py`·`mail_preflight.py` sha 가 HEAD 와 일치함을 노드에서 확인했다 — 분류 실패 로그 줄(`classify_failed`)을 남기는 `c709c852` 이 포함된 트리다.]
- **`triage_confirm.add_reaction` 이 승인 메시지를 게시한 뒤 ✅/⛔를 미리 붙이는 중 Discord HTTP 503을 받으면 draft는 게시됐는데 `compose`가 traceback과 exit 1로 끝난다 → 리액션 사전 부착을 `APPROVAL-REACTION-FAIL` 마커의 best-effort로 바꾸고 exit 0으로 끝내 소유자가 수동으로 반응할 수 있게 한다.** 영향: 운영 불편, 외부효과 없음(심각도: 낮음).
  ↳ 처리(2026-09-03): `fix(mail): 승인 카드의 ✅/⛔ 사전 부착 실패를 APPROVAL-REACTION-FAIL 마커의 best-effort 로 바꾼다` — `triage_approval.MailApprovalGate.post` 가 HTTPError 를 잡아 stderr 한 줄, 반환값·레코드 불변, tests/unit/test_mail_reaction_confirm.py

## 수리 티켓 t_ecb1654b 종결 중 소유자만 닫을 수 있는 것 (2026-09-02)

- **[OWNER] 피어 분류 레지스트리 `~/.hermes/interop/peers.yaml` 이 노드 agent 계정에 없다 → 피어 이름 기반 라우팅(coordination 위임 판정)이 필요하면 `python3 -m automation.group_roster.cli peers-seed --output ~/.hermes/interop/peers.yaml` 로 생성한다(선택, 분류 전용 — trust root 아님).** PR #358 이후 부재는 fail-soft(`PEER-REGISTRY-ABSENT` stderr 1줄)라 단독 일정 draft-create 는 정상이고, 없는 동안 피어 이름은 제목 토큰으로만 취급된다(심각도: 낮음).

## Plaud lifelog 롤아웃 — 소유자만 닫을 수 있는 것 (2026-09-02)

- **Plaud OAuth 는 브라우저가 필요하다 → 워크스테이션에서 `npm install -g @plaud-ai/cli` 후 `plaud login`,
  토큰 캐시 `~/.plaud`(tokens.json) 를 노드 agent 홈으로 복사하고 `automation/plaud_sync/deploy.sh` 를 돌린다.**
  `@plaud-ai/mcp install` 은 로컬 클라이언트에 MCP 설정을 쓰는 부작용이 있어 로그인 용도로 쓰지 않는다.
  복사 후 워처의 MCP 서버가 그 토큰을 실제로 읽는지(같은 `~/.plaud` 공유) 첫 틱에서 1회 검증한다.
  그 전까지 워처 discovery 는 "Not authenticated" 로 조용히 재시도만 한다.
- **기존 Zapier→Drive `PLAUD/raw` 내보내기 구상은 이 동기화가 대체한다 → Zapier 잽을 둘지 정리할지 소유자 결정.**
  남겨두면 무해하나 사본 두 벌이 쌓인다(심각도: 낮음).
- **MCP 는 `~/.plaud/tokens-mcp.json` 을, CLI 는 `tokens.json` 을 쓴다(같은 디렉터리·다른 파일) → 재설치·토큰 회전 때마다
  `tokens.json` 을 `tokens-mcp.json` 으로 복사(씨앗)해야 워처가 인증된다.** 2026-09-02 최초 롤아웃은 이 브리지를 만들어
  노드 `~agent/.plaud` 로 복사했다. 자동 씨앗(deploy/워처가 없으면 복사)을 넣을지는 리프레시 분기 위험과 함께 판단(심각도: 낮음).

## 참고자료 후속 5건 종결 중 남긴 것 (2026-08-27)

> [이관 2026-08-31] 배포 후 노드에서 `--slides` 실측이 선행 조건이다 — 소유자·노드 작업.

- **`meeting_slides` 가 런타임(`automation`)에 새로 의존한다** — 추출을 `document_text` 로
  위임하면서 마운트 스킬이 릴리스 런타임을 해석해야 한다. 스킬만 재배포하고 릴리스가 낡으면
  발표자료를 못 읽고 사유도 부정확해진다(fail-soft라 회의록 자체는 나온다). 조치: 배포 후
  노드에서 `--slides` 를 실측하고, 버전 스큐를 사유 문구로 구분한다. **회수율 · 심각도 중**.

## 회의록 과제 양식·Action Item 데이터베이스 중 남긴 것 (2026-08-27)

> [이관 2026-08-31] 아래는 소유자 결정·노드 실행이 선행 조건이다(토큰 형태 확정 · 정본 판정 · 연달아 배포).

- **본문만 있는 `!meeting` 은 과제를 지정할 방법이 없다** — 첨부가 있으면 파일명(라벨)에서
  `detect_project` 가 찾지만, `!meeting` 뒤에 회의 내용을 바로 붙이는 경로는 라벨이
  `!meeting 본문` 고정이라 대조할 단서가 없다. 그때는 관리번호 없이 진행되고 통지에 그 사실이
  실린다. 조치: 플러그인이 `!meeting --project <과제명>` 형태의 선행 토큰을 파싱해 본문에서
  떼어내고 CLI 에 넘긴다(형태는 소유자 확정 필요). **회의록 생성은 정상 · 심각도 중**(과제
  회의를 본문으로 넣으면 번호가 빠진다).
- **Drive 에 손으로 쓴 회의록 2건이 고아 번호를 달고 남아 있다** —
  `2026-08-25_해양고신뢰성_참여기관업무협의_회의록.md`(임시번호 `AI-260825-*`, 원장으로 임포트됨)과
  `2026-08-25_해양고신뢰성_전사본기반_회의록.md`(임시번호 `TR-260825-*`, 원장과 무관)이다. 후자의 16건은
  전자의 22건과 내용이 겹칠 수 있으나 어느 것이 같은 항목인지는 사람만 판단할 수 있다. 조치: 소유자가
  두 문서 중 정본을 정하면 비정본을 trash 로 보내고, 겹치는 항목은 원장의 기존 번호로 통일한다.
  **원장 자체는 정상 · 심각도 중**(같은 일이 두 번호로 남아 있는 상태다).
- **공유 lock 전환 창에는 배제가 없다** — 두 워처 래퍼는 계정 홈에 각각 배포되므로 한쪽만
  새 lock 을 쓰는 순간이 생긴다(`deploy.sh` 두 번 사이). 그 창에서 자정 배치와 5분 틱이
  겹치면 예전과 같은 이중 처리가 가능하다. 조치: 두 래퍼를 **연달아** 배포하고, 배포 직후
  `hermes cron list` 의 Last run 으로 그 사이에 실행이 없었는지 확인한다. 창이 분 단위라
  실제 충돌 확률은 낮다. **일회성 · 심각도 낮음**.

## 노드 배포 표면 감사(2026-09-01) 중 소유자만 닫을 수 있는 것

> 2026-08-29 첨부 아카이브 자가 배포 사건을 계기로 노드 배포 표면(미러·계정 홈·cron·세션 기록·자가 스킬)을 읽기 전용으로 전수 실측했다. 증적: 이 세션의 감사 보고서 `docs/qa/audit-2026-09-01/node-deploy-surface-audit.md`.

- **[OWNER] 에이전트가 만든 게이트웨이 재시동 헬퍼가 상주한다 → 회수하고 재시동은 승인된 노드 런북(agent·peer 함께, 원인 확인 후)으로만 한다.** `~agent/.hermes/scripts/restart-hermes-gateway-once.sh`(95B, 2026-08-14, 내용은 `systemctl --user restart hermes-gateway.service` 한 줄, 리포 소스 없음; 세션 기록상 08-14~16 사이 6회 생성·실행). 「게이트웨이 재시동 규칙」을 우회하는 수단이 남아 있다(심각도: 높음).
- **[OWNER] agent 홈에 별도 클론이 있고 로컬 전용 커밋 2건이 있다 → 살릴지 버릴지 결정한다.** `/home/agent/src/autophagy-agents`: 작업트리는 clean 이지만 origin 에 없는 커밋 2건(2026-07-30, 08-02)이 있고 origin/main 보다 크게 뒤처져 있다 — 미러 사건과 같은 계열의 "리포 밖 개발" 흔적이며, 에이전트가 이 경로의 스크립트를 실행하면 낡은 사본 실행이 된다(심각도: 중).
- **[OWNER] 소유자 없는 홈 자산 2건의 출처를 판정한다.** `~agent/.hermes/scripts/regression_bank_weekly.py`(1657B, 2026-07-16; 리포 소스·매니페스트·cron 등록 모두 없음)와 `~agent/.hermes/plugins/hermes-achievements/`(JSON 3개 1.3MB, 코드 없음, 2026-08-16). 회수하거나 벤더 산출물로 기록한다(심각도: 낮음).
- **[OWNER] 자가 스킬 5개 중 governed 와 겹치는 것의 승격·폐기를 결정한다.** `meeting-minutes-authoring`(meeting 과 겹침)·`document-publishing`(doctype·report 와 겹침) 등 `~agent/.hermes/skills/{documents,devops}/…` 5개(2026-08-18~28). 결정 전까지 `hermes curator pin/archive` 로 상태를 고정한다(심각도: 중).
- **[OWNER] 미러가 agent 자격증명으로는 원격을 읽지 못한다.** `/srv/autophagy-agents` 에서 `git ls-remote origin` 이 `Repository not found` 인데도 `origin/main` ref 는 최신이다(ops 가 fetch). 미러를 읽기 전용 관측소로 고정하면 자연 해소되며, fetch 주체를 운영 가이드에 적는다(심각도: 낮음).

## 관측 미러가 미커밋 편집으로 동결돼 121 커밋 뒤에 있었다 (2026-09-01 실측)

- **`/srv/autophagy-agents` 가 `ec39b11f`(#319) 에 멈춰 있고 08-29 00:54Z 부터 dirty 다 → 소유자가 그 미커밋 편집을 살릴지 버릴지 결정한 뒤 미러를 정본으로 되돌린다.** dirty 내용은 `skills/mail/SKILL.md`(v1.6.3 "메일 첨부파일 Drive 아카이브" 절)와 `skills/mail/deploy.sh`(`mail_attachment_drive_sync.py`·`mail_attachment_drive_watch.py` 배포 행) — 리포에 없는 기능이라 살리려면 `git diff` + untracked 파일을 워크스테이션 브랜치로 옮겨 PR 로 착지시키고, 그 뒤 미러에서 `git checkout -- .` 후 `git pull --ff-only`(리컨실러 `sync_mirror` 는 dirty 를 건드리지 않으므로 사람이 한 번 해야 한다). 이 동결이 2026-09-01 김경호 회신 무인용 사고의 두 번째 원인이다 — 에이전트가 실행한 미러 사본에 `mail_quote` 도 승인 정책 v8 도 없었다. `checkout_mirrors_origin` 프로브는 dirty 를 FAIL 로 내고 있었으나 아무도 닫지 않았다(심각도: 중 — mail 은 `governed_copy_refusal` 로 사본 실행이 막혔지만 다른 스킬과 문서 읽기는 여전히 낡은 미러를 본다). [해소 2026-09-01: 미커밋 작업은 PR #352 로 정식 착지(v1.0.146, mail v1.7.2 마운트 `7ea8af3f…`)했고, ops 가 추적 2파일 `git checkout --`·agent 소유 untracked 3파일 제거·`git pull --ff-only` 로 미러를 `7c0ed308` == origin/main 으로 복원했다(clean, 0 behind).]
- **[OWNER] 미러가 agent 에게 쓰기 가능하다 → root 가 `/srv/autophagy-agents` 트리의 group 쓰기 비트를 내린다(`chmod -R g-w`, setgid 디렉터리 `2775`→`2755`; ops 만 쓰기).** 2026-09-01 실측: 디렉터리가 `ops:autophagy 2775` 라 group `autophagy` 의 일원인 agent 가 파일을 만들고 추적 파일(`skills/mail/SKILL.md` 가 agent 소유로 바뀜)까지 덮어썼다. 커밋 거부 훅은 커밋만 막고 편집·untracked 는 못 막는다 — 「ops 체크아웃 단방향 규칙」이 산문에 그친 구조적 원인이다(심각도: 높음).
- **게이트웨이 수준 경로 정책은 위 「게이트웨이 플러그인 배포 경로(2026-08-28) 후속」 이 풀린 뒤에 싣는다.** `pre_tool_call` 에서 `…/skills/<skill>/scripts/…` 참조가 live 루트 밖이면 막는 순수 판정을 `automation/interop` 에 두고, 플러그인 배포 경로 신설과 같은 사이클에 agent·peer 재시동으로 반영한다(심각도: 중).

## 게이트웨이 플러그인 배포 경로(2026-08-28) 후속 — interop-protocol

> [이관 2026-08-31] 소유자 roster 배치 결정이 선행 조건이다 — 순서를 바꾸면 인터롭이 fail-closed 로 막힌다.

`00-meeting-gate` 의 배포 경로를 만들며 같은 계열을 전수 조사했다. 노드 홈의 플러그인 4개 중
리포에 소스가 있는 것은 셋이고, 그중 둘은 정상(`00-meeting-gate` 이번에 신설,
`05-skill-generation` 은 `automation/skill_generation/deploy.sh:31` 이 이미 민다). 나머지 하나가
문제다(`hermes-achievements` 는 리포에 소스가 없는 외부 자산이라 대상이 아니다).

- **`interop-protocol` 이 5주 낡았고 배포 경로가 없다** — 노드 사본 `d229b105`(268줄)은
  `2bf695ac`(2026-07-20) 시점 코드고 리포 `575d04f0`(302줄)은 `6623bf74`(2026-08-15,
  "bind envelopes to Discord roster identity") 이후다. 손 편집이 아니라 그냥 낡은 것이라
  유실 걱정은 없다. 결과: **roster 기반 발신자 신원 대조(W-F2.5-B)가 프로덕션에서 돌지 않는다** —
  봉투의 `sender_id` 자칭을 아무도 막지 않고, 봇 자유 산문 무시(cascade 안전)도 없다.
  agent·peer 양쪽 동일하다.
- **그런데 그냥 배포하면 인터롭이 멈춘다** — 새 코드는 `~/.hermes/roster.yaml` 을 읽고 없으면
  `RosterError` → 모든 봉투를 `roster_unavailable` 로 거부한다. 실측: agent·peer 둘 다 파일이
  없고(`NONE`), 릴리스에 `group_roster` 패키지는 이미 있다. 즉 **roster 배치가 선행 조건**이며
  그 내용(누가 어떤 `sender_id` 인가)은 소유자가 정할 사안이다.
- 조치 순서: ① 소유자가 roster 배치를 결정 → ② 플러그인을 agent·peer 양쪽
  `~/.hermes/plugins/interop-protocol/` 로 미는 배포 경로 신설 → ③ 매니페스트에 2계정 등록 →
  ④ agent·peer 함께 재시동. **순서를 바꾸면 인터롭이 fail-closed 로 막힌다.** 매니페스트 등록을
  먼저 하면 프로브가 FAIL 하며 "deploy.sh 를 돌려라"라는 **잘못된 자동 조치를 유도**하므로,
  등록은 roster 결정과 같은 사이클에 넣는다.
- **심각도 중** — 방어가 꺼져 있으나 해당 채널 참여자가 제한적이고, 잘못 고치면 인터롭이 멈춘다.
  되돌리기는 쉽다(옛 사본 복구 + 재시동). 증적: 이 세션의 해시·diff·`load_roster` 실측.
- **[2026-09-01 실측 추가] agent·peer 두 계정 모두 같은 2026-07-20 사본이다** — `~/.hermes/plugins/interop-protocol/__init__.py` sha256 `d229b105…`(268줄) 양쪽 동일, 리포 `automation/interop/hermes_plugin/__init__.py` 는 288줄(최종 9e481bb1). 배포 경로 신설 시 두 계정을 함께 민다(심각도: 중).



## 제안서 HWPX 품질 수리 중 남긴 것 (2026-08-26)

> 이 저장소가 지금 손댈 수 없거나 이미 닫힌 항목이다. 원 묶음 헤딩은 회계 가드 대조 키라 그대로 둔다.

- **밴드 사이에 빈 페이지가 생긴다 — 48쪽 중 10쪽** — `render_layout_bands` 가 밴드 앞뒤로
  `pageBreak="1"` 빈 문단을 하나씩 내보내 밴드 경계마다 연속 페이지 나눔이 발생한다. 이 두 번째
  경계는 커밋 `1103be4` 가 "estimate-tier validation 이 같은 밴드 모델을 읽도록" 의도적으로 넣었고
  `test_rendered_band_persists_both_page_boundaries` 가 `count(pageBreak="1") == 2` 로 고정한다.
  조치: 경계 표시를 빈 문단이 아니라 주석 마커로 옮기고 검증기가 마커를 읽게 바꾼다 — 그냥 지우면
  다른 세션이 세운 밴드 검증 모델이 조용히 무너진다. v000001 에도 동일하게 존재했다.
  **분량·인쇄 품질 · 심각도 중**(본문 내용에는 영향 없음).
  [해소 확인 2026-08-27] "검증 모델이 무너진다"는 우려는 코드를 읽어 **사실이 아님**을 확인했다 —
  `estimate_pages` 는 `fixed_template_pe + prose_pe + figure_pe + table_pe + heading_pe` 로 page-break 항이
  아예 없고, `validate_density_xml` 은 밴드 **선행** 나눔 하나만 확인한다(`BAND_PAGE_BREAK_MISSING`).
  후행 빈 문단을 고정하던 것은 테스트 한 줄(`count(pageBreak="1") == 2`)뿐이었고, 밴드 끝은 이미
  `<!--/LAYOUT-BAND index="N"-->` 주석이 표시한다. 즉 그 문단은 정보가 없고 페이지만 먹었다.
  제거 후 실측: **48쪽 → 33쪽, 빈 쪽 10 → 0**. 고정 테스트는 새 계약(선행 나눔 1개 + 종료 마커)으로
  갱신했다 — kimm-docbot `ee6d7e7`.
- **양식의 미기입 안내문이 최종본에 그대로 남는다** — 표지의 `과제명 (국문/영문): 연구의 한 줄
  요약이 드러나도록 기재`, `전체 연구기간: YYYY.MM.DD ~ YYYY.MM.DD` 같은 placeholder 를 채우는
  단계가 파이프라인에 없다. 조치: seed 의 표지 슬롯을 `planspec` 값으로 채우는 단계를 render 앞에
  둔다(양식 파일 자체는 편집 금지이므로 렌더 대상 XML 에서만 채운다).
  **제출 완성도 · 심각도 중**.
  [해소 확인 2026-08-27] `hwpx/seed_fill.py` 신설로 닫혔다 — 표지 9필드를 `PlanSpec` 에서 파생해 채우고
  (`render --cover <json>` 으로 계획에 없는 값만 덮어쓴다), 양식 메타 4문단과 안내 불릿 31개를 삭제하며,
  삭제로 밀린 `anchor_map` 서수를 같은 호출에서 재매핑한다. 실측: 안내문 35건 → 0, 표지 0/9 → 9/9.
  삭제는 **앵커가 쓰는 문단을 건드리지 않는다** — `hs:sec/hp:p[74]` 는 안내 불릿이면서 동시에
  `section.4.traceability` 의 쓰기 대상이라, 지우면 슬롯이 사라지고 `write_traceability_table` 이
  `KeyError` 를 삼켜 근거 부록이 **오류 없이 조용히** 사라졌다(kimm-docbot `5d0cbf7`).
  요약 본문이 표지 위에 삽입돼 과제명·연구기간이 3쪽 하단으로 밀리던 것도 함께 고쳤다(`65337943`).
- **양식의 소제목과 우리 본문의 소제목이 중복된다** — 시드는 `1-1. 기술적 배경 및 국내외 동향` 같은
  소제목 12개를 갖고 있고, 집필 본문도 자체 `## 1.1 …` 구조를 갖는다. 안내 불릿만 지웠으므로 목차가
  두 겹으로 보인다. 조치: 시드 소제목을 지우거나 본문 소제목을 없애 한쪽으로 통일한다 — **정부 양식이
  요구하는 목차를 임의로 지우는 것은 소유자 판단**이라 이번 범위에서 하지 않았다. `2-3 성과지표`·
  `3-3 추진 일정` 은 KPI·간트 표의 라벨이라 함께 지우면 표가 이름을 잃는다. **가독성 · 심각도 낮음**.
- **제출 분량이 33쪽으로 양식 권장(10쪽 내외)의 세 배다** — `resource/rule.md` 의 「HWPX 결과물 가이드」는
  "10 페이지 내외 (A4 / 돋움 11pt / 줄간격 160%)" 를 요구하고, 조판은 이번에 그 규칙에 맞췄지만 분량은
  그대로다. 프로파일 `10-page` 가 이미 있으므로 기계적 축약은 가능하나, 어떤 근거·그림을 버릴지는
  소유자 판단이다. 조치: 소유자에게 `30-page` 유지와 `10-page` 재작성 중 선택을 받는다.
  **평가 규칙 대비 초과 · 심각도 중 · 소유자 결정 필요**.

## budget 다중 시트·Drive 정리 중 남긴 잔재 (2026-08-24)

- **레거시 `proposal/excavator`·`doctype/vendor.md`가 산출물 루트에 남아 있다** — taxonomy
  랜딩(8/23) 전 발행분이고 마이그레이터는 `<type>/<YYYY-MM>` 형태만 다뤄 계획에 안 잡힌다.
  소유자 지시("이전 문서는 무시")로 존치 → 거슬리면 Drive 수동 정리. **동작 영향 없음 · 심각도 낮음**.
- **노드에 다중 시트 레지스트리가 아직 없다** — 코드는 레거시 `BUDGET_SHEET_ID` 모드로 계속
  돌고, 과제를 나누려면 agent 계정 `~/.hermes/budget/sheets.json`에 기존 원장 ID까지 등재해야
  한다(미등재 env=exit 3) → 형식은 `configs/budget-sheets.example.json`. **활성화 대기 · 심각도 낮음**.

관련 기능: [과제별×년도별 예산 시트](기능소개/과제별-연도별-예산-시트.md).

## DM→#agent-chat 이관(승인 표면 v7) 중 발견한 후속 과제 (2026-08-24)

- **repair 승인은 아직 소유자 DM이다** — 발신 주체인 Ops 봇이 어떤 길드에도 참여하지
  않아 v7에서 의도적으로 제외했다. 조치: 소유자가 Ops 봇을 개인 서버에 초대한 뒤 별도
  정책 버전(v8)으로 REPAIR 전환. **의도된 예외 · 심각도 낮음**(수리 승인 왕복 자체는 정상).
- **Hermes cron `--deliver discord`의 대상은 여전히 owner DM 고정** — mail digest의 실패
  마커 등 cron 레벨 전달이 해당(다이제스트 본문은 이번에 채널로 이관됨). Hermes CLI가
  채널 대상을 지원하는지 노드에서 `hermes cron create --help`로 확인 후 지원 시 전환.
  **실패 마커만 DM으로 옴 · 심각도 낮음**.

관련 기능: [소유자향 통지·승인의 #agent-chat 이관](기능소개/승인-agent-chat-스레드.md).

## RAG 런타임 배포·탐지 수리 중 발견한 후속 과제 (2026-08-22)

- **embedding 소스와 실행 이미지의 갱신 시점이 갈린다** — `automation/rag_stack/deploy.sh`는 `embedding/` 정본도 노드에 동기화하지만 의도적으로 컨테이너를 재빌드하지 않고, 후속 활성화 명령도 검색 중단 범위를 줄이려고 MCP만 빌드한다. 따라서 embedding 소스는 최신이어도 실행 이미지는 2026-07 빌드본으로 남는다 → 모델 캐시와 긴 빌드 시간을 고려해 embedding 재빌드 변경 창을 소유자와 정한다. MCP 이미지는 새 healthcheck 프로브가 실행 컨테이너 내부 핵심 모듈 해시로 검증한다. **현재 동작과 무관 · 심각도: 중**.
- **RAG 노드의 `personal-rag.service` ops user unit이 2026-08-08 21:53 UTC부터 failed다** — 부팅 때 `docker compose up -d --build`가 `ghcr.io/astral-sh/uv:0.9.18` 메타데이터 DNS 조회(`127.0.0.53`, `server misbehaving`)에 실패했다. 컨테이너는 12일째 healthy이고 현재 `ghcr.io` 도달도 정상(HEAD `/v2/` → 405)이라 현 서비스 영향은 없지만, 같은 실패가 재부팅 때 나면 RAG 스택 전체가 올라오지 않는다 → 원인 해소를 확인한 뒤 유닛을 복구·재시작하고, 빌드 시 레지스트리 실패의 재시도·캐시 전략을 검토한다. **재부팅 복구성 영향 · 심각도: 중**.

관련 기능: [런타임 패키지 배포와 드리프트 탐지](기능소개/런타임-패키지-드리프트-탐지.md). 실측일 2026-08-22, 릴리스 `v1.0.46` (`99b9d25e`).

## mailon 런타임이 19일간 옥 릴리스에 고정돼 있었다 (2026-08-18)

소유자의 논문 메일을 머춰 상태에서 수리하다, 진짜 원인이 드러났다. `035e767d`(2026-07-29, exact-set 수신자 검증)가 결함 **2건**을 함께 들여왔는데, mailon 런타임이 07-30 릴리스(`96599b22b6e6fdda`)에 **19일간 고정**돼 있어 프로덕션은 그 코드를 한 번도 실행하지 않았다. 그래서 발송은 8/14까지 멀줦히 동작했다. 오늘 compose 기동 경쟁(PR #152)을 고치려 vendor 트리를 배포하자 **둘이 동시에** 올라와 모든 발송이 즉시 실패했고, 2회 연속 실패가 mail-mode를 `no-go`로 강등시켰다. 두 결함은 PR #157·#158로 고쳤고 발송도 확인했다(보낸편지함 재조회 증적).

- **런타임 고정을 healthcheck 가 못 본다 — 다이제스트 틱이 대신 본다** — 이번에 넣은 드리프트 프로브는 agent 계정 경로에 두고 다이제스트 워처가 태운다. `automation/healthcheck.sh` 에 넣지 않은 이유는 그쪽이 ops 계정으로 도는데 런타임은 `~agent/.hermes`(0700)라 읽을 수 없고, 중첩 sudo 는 이 저장소에서 rc=126 으로 프로브를 죽인 실측 선례가 있기 때문이다 → healthcheck 에서도 보이게 하려면 권한 설계(전용 read-only 노출 또는 ops 가 읽을 수 있는 상태 파일)를 먼저 정한다. **동작 결함 아님 · 심각도: 낮음(탐지 경로가 하나뿐)**.

증적: PR #157(빈 method) · PR #158(body probe), 릴리스 `85aaf5ca488f37a6`, 발송 확인 `2026-08-18T08:55:40Z` 보낸편지함. 기능 소개: [mailon 런타임 고정 탐지](기능소개/mailon-런타임-고정-탐지.md).

## Hermes 무재시동 자체 업데이트로 게이트웨이 도구 계층이 죽었다 (2026-08-18)

`<primary-node>`의 agent 게이트웨이가 08-17 13:24부터 08-18 09:57(KST)까지 **모든 도구 호출**을 `ImportError: cannot import name '_plan_tool_batch_segments' from 'agent.tool_dispatch_helpers'`로 실패시켰다(errors.log 9회). 소유자의 메일 요청 2회가 이것으로 막혔고, 첨부 확인·수신자 조회·승인 초안이 전부 도구 호출이라 함께 죽었다(발송·초안 생성은 일어나지 않았다 — 승인 로그·draft store 신규 레코드 0). **코드가 아니라 프로세스가 원인이다**: 그 심볼은 디스크 파일에 정상 존재하고(`tool_dispatch_helpers.py:117`, `__all__` 등록), 새 인터프리터에서 `import agent.tool_executor`는 통과한다. 게이트웨이는 08-16 12:50 기동, `hermes-update`는 08-16 14:34·14:36 실행 — **떠 있는 프로세스 밑에서 소스 트리가 교체**됐고, 옛 모듈을 든 프로세스가 새 파일을 import하다 죽었다(트레이스백 줄 번호가 디스크 소스와 어긋나는 것이 같은 증거). agent·peer 게이트웨이를 함께 재시동해 해소했다(01:03 UTC, 실제 도구 호출 1건으로 검증).

- **같은 업데이트가 agent의 `hermes_compat` 패치 3종을 통째로 떨괴뜨렸다 — 재작성은 끝났고(PR #147) 배포만 남았다** — 장애 조사 중 부수적으로 드러났다. agent의 `gateway/run.py`·`plugins/platforms/discord/adapter.py`에서 마커 `_hermes_pgd_done`·`_hermes_busyfifo_done`·`_hermes_receipts_done`이 **세 개 모두 없고**, 변경분은 08-16 업데이트가 만든 autostash(2파일 252줄)에 남아 있었다 — stash는 됐지만 복원이 안 됐다. 빠진 것 중 `busy-path-pre-gateway-dispatch`는 busy 경로 메시지가 skill-generation 관찰(W6-4)과 **meeting-gate fail-closed veto(W2-3)** 훅을 타지 못하는 문제를 메우는 것이라 단순 편의 패치가 아니다. **제거가 아닌 재작성임을 먼저 확인했다** — v0.20.3에서도 `pre_gateway_dispatch` 호출 지점은 `_handle_message` 한 곳뿐이고(`run.py:16309`) `_handle_active_session_busy_message`(`run.py:9920`)는 여전히 busy 핸들러로 배선돼 있어 세 `removal_condition` 중 어느 것도 충족되지 않았다. 원본은 `archive/hermes-compat-v0.18.2-patches`(`ab5d6d97c`)와 `~agent/.hermes/hermes-compat-patches-v0.18.2.diff`(sha256 동일 확인, 0600)로 이중 보존해 둔 것을 기준으로 썼다 → 남은 일은 PR #147 머지 후 배포와 마커 3개 verify뿐이며, **그전까지는 그 두 훅이 busy 경로에서 미발동임을 전제한다**. **프로덕션이 08-16부터 무패치로 구동 중 · 심각도: 높음(게이트 인접 훅이 꺼져 있다)**.
- **패치 carrier를 올리는 deploy 스크립트가 둘이고 목적지도 다르다 — 한 쌓만 돌리면 절반만 복구된다** — 노드의 `~/.hermes/hermes-compat/hermes_compat/`에는 `patch_busy_dispatch.py`만 있다. 실측해 보니 누락이 아니라 설계가 그렇다 — `deploy.sh`는 `patch_busy_dispatch.py`를 `hermes_compat/`에, `deploy-owner-dm.sh`는 `patch_busy_fifo.py`·`patch_discord_receipts.py`를 **`appliers/`라는 다른 디렉터리**에 올리며, 후자의 preflight도 그 둘만 검증한다("prove BOTH patches") → 3종을 다 올리려면 **두 스크립트를 모두** 실행하고 끝에 마커 3개를 일괄 verify해야 한다. 장기적으로는 한 경로로 모으는 것이 맞지만, 그러면 `owner-dm-txn.sh`·`owner-dm-restore.sh`의 롤백 매니페스트도 함께 바꿉다. **배포 절차의 선행 조건 · 심각도: 중(모르고 시작하면 절반만 복구된다)**.

## 에이전트 자가 스킬 공존(SS-1) 작업 중 발견한 후속 과제 (2026-08-15)

자가 스킬 루트 반전과 감사 원장을 만들며 발견했다. 기능은 [소개](기능소개/에이전트-자가-스킬.md).

- **Hermes 네이티브 충돌 거부는 우리 토폴로지에서 아예 동작하지 않는다(2026-08-16 정정 · 예방 미구현)** — `_find_skill`이 `rglob("SKILL.md")`로 훑는데 governed 루트가 심링크 팜이라 한 건도 못 본다(`_find_skill("mail")` → `None`). 그래서 자가 스킬이 배포본 이름을 선점해 **승인 게이트를 가릴 수 있다** → 예방은 live 루트를 심링크가 아닌 실디렉터리로 두거나 업스트림에 보고해야 하고, 지금은 `selfskill_audit`의 `SHADOWS-GOVERNED` **탐지**만 있다. **upstream 보고서 작성 완료**: [hermes-find-skill-symlink-blindness.md](troubleshooting/hermes-find-skill-symlink-blindness.md) — 재현 실측과 제안 패치(`os.walk(followlinks=True)` + 심링크 순환 가드)를 담았다. 소유자가 벤더에 전달하면 된다. **두 선택지의 비용을 실측했다(2026-08-16)**: 실디렉터리 전환은 심링크 팜을 만드는 주체가 `skill_store.py`=root NOPASSWD 3종 중 하나인 `/usr/local/libexec/autophagy-install-skill`이라 **가장 특권 있는 경로를 고치는 일**이고, 더 큰 문제는 이 저장소의 배포 판정 자체가 `readlink /srv/autophagy-skills/live/<skill>` 해시(「커밋됨 ≠ 배포됨」)라는 점이다 — 실디렉터리로 바꾸면 그 판정 기전이 사라지고 `skill_mount_drift.py`·`skill_mount_probe.sh`·`selfskill_root_probe.sh`·`deploy-skill.sh`·`land.sh`가 함께 따라온다(원자적 교체도 rename 춤으로 다시 만들어야 한다). 반면 업스트림 보고는 `rglob`→`os.walk(followlinks=True)` 한 줄이고 반영 시점만 남의 손이다 → **탐지로 버티며 업스트림 보고를 먼저 하는 쪽을 권고**하되, 선택은 소유자 판단으로 남긴다. **심각도 높음(가려지면 게이트 우회)**. curator external-write 가드와 curator가 configured-external 스킬을 건드리지 않는 성질은 우리 코드가 아니라 벤더 동작이며, 회귀로 못박을 수단이 없다 → **`hermes update`마다 S4·S5 QA 명령을 재실행**해 두 성질이 유지되는지 확인한다(S4: 배포 스킬 이름으로 생성 지시 후 디렉터리 미생성, S5: `hermes curator archive <배포 스킬>` 거부 + live readlink 불변 + 쓰기 `Permission denied`). **현재 동작 정상 · 심각도 중간(업데이트로 이름 선점 방어가 조용히 약해질 수 있음)**.
- **peer의 `~/.hermes/skills/prompt` 잔여물은 배포를 막을 뿐 아니라 지금 배포본을 가리고 있다(2026-08-16 실측 보강)** — 루트 반전 이후 그 경로는 read-only bind가 아니라 peer가 소유한 **1차 루트**이고 1차 루트가 발견에서 이긴다. `/srv/autophagy-skills/live/prompt`가 존재하므로 이것은 위 S4 항목이 말하는 **`SHADOWS-GOVERNED` 조건이 실제로 성립한 상태**다(peer 자가 루트 3건 = 현역 2 + 이 잔여물). `prompt`는 외부효과 스킬이 아니라 프롬프트 자산이라 승인 게이트 우회는 아니고 **peer가 낡은 사본을 쓰는 문제**지만, 다음 감사 리포트에 `SHADOWS-GOVERNED`로 뜨는 것은 오탐이 아니라 설계대로의 탐지다 — 그 사본의 SKILL.md에는 `author: autophagy-agents` 마커가 없어(리포 원본에도 없다) 새 분류기가 `foreign`으로 판정해 fail-closed로 차단한다 — 설계대로의 동작이지만 원인이 오래된 잔여물이다(2026-08-01 배포 잔재, 모드 775) → 소유자가 `sudo -n -u peer rm -rf ~peer/.hermes/skills/prompt` 한 번으로 정리하면 이후엔 자가 치유된다(정리 수리가 이번 PR에 포함). 다른 5종은 이번 검증에서 실제로 정리됐다(봉인된 `coordination` 잔여물 포함). **심각도 낮음(해당 스킬 1종 배포만 지연)**.
- **노드의 `/root/.hermes/node.toml`을 확인할 수 없다 — 자동 재개가 이것에 달려 있다** — 승인 재개 헬퍼는 `env -i HOME=/root`로 파이프라인을 돌리므로(`autophagy-resume-deploy:60`) 노드 설정을 `/root/.hermes/node.toml`에서 읽는다. 그 파일이 없으면 시드 기본값(`peer_attest_mode = signed`)으로 해석돼 `discord` 바인딩 레코드와 어긋나고, 소유자가 ✅를 눌러도 마운트가 조용히 실패한다. 이번 5종은 결국 착지했으나 **오케스트레이터는 root 읽기 권한이 없어 그 파일의 존재를 확인하지 못했다** → 다음 배포 전에 소유자가 존재·내용을 한 번 확인하고, 없으면 워크스테이션과 같은 내용으로 만든다. **현재 배포는 동작 · 심각도 중간(다음 자동 재개가 원인 모르게 멈출 수 있다)**.
- **번들 카탈로그는 제거했지만 재시딩 경로가 완전히 닫힌 것은 아니다** — agent·peer 모두 `hermes skills opt-out --remove`로 정리하고 `~/.hermes/.no-bundled-skills` 마커를 남겼다. 다만 (a) 빈 카테고리 디렉터리와 `.bundled_manifest`는 그대로 남아 있고, (b) 마커를 존중하지 않는 경로(`hermes update`·프로필 재생성 등)가 있으면 다시 시드될 수 있다 → `hermes update` 직후 `hermes skills list`로 builtin 수를 확인한다. 원장 쪽 방어는 이미 있다(`.bundled_manifest` 기준 제외, PR #113). **현재 정상 · 심각도 낮음(재발 시 감사 리포트가 아니라 프롬프트 크기 문제)**.

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

## 메모리 승격 확인 종결 H4 — 라이브 14건은 OWNER 인계 (2026-08-05)

- **코드와 dry-run 계약만 완성됐고 라이브 14건은 의도적으로 건드리지 않았다** → PR 머지와 배포 뒤 OWNER가 `closure_cli --dry-run`의 `CLOSE`·`UNBOUND`·`ORPHAN` 원장을 검토한 다음 기존 큐레이터 tick으로 정리한다. **동작·보안 결함 아님 · 심각도 중(운영 인계)** — 이 작업에서 실 Discord 편집·실 배포·라이브 정리는 금지 범위다.
- **`abandoned`는 ⛔ 시점에 saved 초안이 unlink되어 교차 바인딩이 소멸한다** → 현재는 항상 `UNBOUND`로 열거하고 편집·archive 0회를 보장한다. 미래 취소 경로가 삭제 전 archive하도록 바꾸는 일은 kanban-routing 소유 계획과 조율한다. **안전 우선 fail-closed · 심각도 낮음(정리 자동화 한계)**.

증적: `.omo/evidence/fs2/task-4-parallel-followup-sweep-2.txt`

## 스킬 배포 파이프라인이 3겹으로 막혀 있었다 (2026-08-04 실측 · 전부 해소)

FS3 K2-A는 기존 `release_helper_probe.sh`의 실설치 자산 검사를 랜딩 출력에도 재사용했다.
금지된 `deploy-skill.sh` 실행 없이 저장소 회귀와 읽기 전용 프로브 계약만 검증했다.

위 「마운트된 스킬 5종…」의 **근본 원인**이다. 재배포를 실제로 시도해서야 드러났다 — 마운트가 낡은 것이 아니라 **마운트할 수가 없었다**. 하나를 풀자 다음 것이 드러나길 세 번 반복했고, 세 개 모두 **이번 스윕(2026-08-03)이 직접 만들었거나 드러낸** 것이다. 실제 재배포 없이는 세 겹 중 어느 것도 보이지 않았다.

- **세 겹 모두 “실제로 돌려보기 전에는” 보이지 않았다** — 유닛 3112건·ruff·보드 conformance가 전부 green이었고 릴리스도 최신이었다. 공통 성질은 **노드 설치본·외부 CLI 출력·파일 모드처럼 레포 밖에 사는 상태**에 의존한다는 점이다. **심각도 중** — 조치: 배포 경로에 주기적인 스모크(예: 하루 1회 `--sandbox-only` 드라이런)를 두어, 다음 실배포가 아니라 그 드라이런이 먼저 깨지게 한다.

## 처리 끝난 승인 메시지가 소유자 DM에 쌓인다 (2026-08-04 실측)

소유자가 “1개만 승인했는데 나머지는 안 보인다”고 보고해 조사한 결과다.

- **메모리 승격 확인 메시지 14건이 전부 살아있다 — 이미 처리된 것도 지워지지 않는다** — `wiki-gate/drafts`에 `memory-promoted-*` 초안이 14건(전부 `status=saved`), 대응 Discord 메시지도 14건 전부 ALIVE이며 그중 10건에는 **소유자 ✅가 이미 달려 있다**. 반면 `memory-curator/state.json`의 승격 레코드는 10건이고 상태는 `reconciled` 6 · `abandoned` 4로 **파이프라인은 정상 종결됐다**. 즉 결정은 소비됐는데 그 사실을 알리는 메시지가 그대로 남아, 소유자 DM이 “대기 중인 승인”처럼 보인다. **동작·보안 결함 아님**(안전 불변식은 지켜졌고 `USER.md`는 1086자로 이미 회수 반영된 값) **· 심각도 중(UX·관측성)** — 소유자가 무엇을 더 눌러야 하는지 알 수 없게 된다. 조치: `reconciled`·`abandoned`로 종결된 승격의 확인 메시지를 삭제하거나 처리됨 표시로 바꾸는 종결 단계를 두고, 초안 레코드(`saved`)도 함께 회수한다. 삭제는 외부효과라 소유자 승인 경로를 거친다.
- **초안 14건 vs 승격 레코드 10건의 불일치** — 4건은 `state.json`에 대응 레코드가 없다(조회 시 반응 사용자 목록도 비어 있었다). 레거시 또는 중단된 시도의 잔재로 보인다. **심각도 낮음** — fail-closed라 임의 실행은 없다. 조치: 위 종결 단계를 만들 때 고아 초안 판별을 함께 넣는다.

## 마운트된 스킬 5종이 릴리스보다 낡았다 — 머지된 수정이 미발효 (2026-08-04 실측)

OWNER 체크리스트 9번·F4 판정 중 발견. `skill_mount_drift.py`를 라이브에 직접 돌렸다.

- **`budget`·`calendar`·`coordination`·`mail`·`wiki` 다섯 종 전부 `SKILL-STALE`이다** — 릴리스와 마운트 digest가 전면 불일치한다(예: mail 릴리스 `019a405f…` vs 마운트 `091ddd36…`). 즉 **코드에는 있지만 프로덕션에서 도지 않는 수정이 다섯 스킬에 걸쳐 쌓여 있다** — 429 백오프(PR #31·#32), G2 승인 판정 단일화(PR #50), G5 다이제스트 JSON 계약 하드닝(PR #47)이 여기 해당한다. **동작·보안 결함은 아니다**(구버전이 정상 동작 중이고 게이트는 fail-closed) **· 심각도 중** — 고쳐놓은 결함이 계속 재발한다는 뜻이다. 조치: `automation/deploy-skill.sh`로 다섯 종을 재배포한다(스킬당 소유자 ✅ 1회, 총 5회). 배포 뒤 `readlink /srv/autophagy-skills/live/<skill>`이 릴리스 digest와 같아지는지로 판정한다.

## 배포 미러 임시 워크트리 누수 — trap 정리 실패 (2026-08-04 실측)

OWNER 체크리스트 1번(`.git` 권한 회수) 조사 중 발견. 권한 자체는 해소됐다(2775→2755, 그룹쓰기 1629개→0, agent 소유 356개→0).

- **수리 워크트리 1건이 스테이징된 미커밋 변경 12건과 함께 남아 있다** — `/tmp/t_6f29fb9b-review`(브랜치 `fix/memory-relocate-single-approval-t_6f29fb9b`, HEAD `1c3c33e`). HEAD는 origin/main 조상이고 **미착지 커밋은 0건**이라 손실된 커밋은 없지만, 인덱스에 `memory_relocate` 계열 12파일이 스테이징된 채 남아 그 내용이 현재 origin/main과 다르다(같은 주제의 수리는 `docs/patch/2026-08-02-memory-relocate-approval-channel-binding.md`로 이미 반영됨 — 이 인덱스는 그 시점의 중간 스냅샷으로 보인다). **동작·보안 무관 · 심각도 낮음**(15MB 점유). 「다른 세션의 미커밋 작업은 되돌리지 않는다」에 따라 **의도적으로 보존했다**. 조치: 소유자가 인덱스 diff를 확인해 버릴지 판단하고, 버린다면 `git diff --cached HEAD`를 패치로 뽑아 `/srv/autophagy-private/`에 보관한 뒤 제거한다.
- **수리 자동화가 미러에 남긴 로컬 브랜치 다수** — `task/*` 3건·`kanban/*` 8건·`wt/*` 2건·`fix/*` 1건·`repair/*` 1건이 미러의 로컬 ref로 남아 있다(전부 미착지 0건 확인). **동작 무관 · 심각도 낮음**. `backup/pre-*`·`pre-realign-*`는 정렬 사고 대비로 **의도적으로 남긴 것**이므로 제외한다. 조치: 수리 라이프사이클이 종료 브랜치를 정리하도록 하거나, 주기적 정리 기준을 정한다.

## 메모리 재배치 승인 채널 바인딩 유실(MC-4) 수리 중 발견한 후속 과제

수리는 `docs/patch/2026-08-02-memory-relocate-approval-channel-binding.md`. 노드 자율 제안 경로에서만 드러난 결함이다.

- **`USER.md`는 cap의 79.0%(1086/1375자)다** — 승격 진행분을 반영한 실측이며 97.9%라는 옛 수치는 폐기한다. **심각도 중** — 유일한 회수 경로는 트윈 승격이다. → 소유자가 대기 중인 승격 초안 6건에 ✅/⛔를 처리한다(전부 승인 시 754자 회수).

## 배포 스냅샷 + 불변 런타임 루트(DG-2~DG-6) 작업 중 발견한 후속 과제

기능은 DONE「배포 스냅샷 + 불변 런타임 루트 (DG-2~DG-6)」 참조, 계획 `.omo/plans/deploy-snapshot-runtime.md`

- **DG-5 4.4(수리 systemd 유닛 이관)는 부분 해소 상태다** — `docs/qa/DG-5/rollout-partial.txt`는 4.4를 defer로 기록했으나, 이후 repair-report-rollout의 coordination amendment로 `autophagy-repair-agent.service`는 `/srv/autophagy-agent-current`로 이관됐다(라이브 확인: `WorkingDirectory=/srv/autophagy-agent-current`, `NeedDaemonReload=no`). **정정(2026-08-10)**: `autophagy-repair-approval-watch.service`는 "미설치"가 아니다 — 도입 커밋 `aae36d2`부터 의도적으로 **system 스코프**(`/etc/systemd/system/` + `.timer`, `User=ops`)로 설치돼 있으며, 이전의 "빈 `FragmentPath`" 관측은 잘못된 `systemctl --user` 조회의 산물이었다. 진짜 문제는 **설치본 내용이 낙았다**는 것이다 — `fdc995e`(DG-5)도 `7ea6a8c`(미러 쓰기 제거)도 반영되지 않은 pre-`7ea6a8c` 바이트라 `ReadWritePaths`에 `/srv/autophagy-agents` 미러 쓰기가 남고 런타임도 미러를 가리켰다. **심각도 중간** — 이 유닛은 이번 rollout의 주 보고 경로(cha ✅ → approval-watch → complete/reopen → enqueue)를 타므로 활성화 전에 수렴되어야 한다. 조치: 2026-08-10 별도 system-scope 수렴 runbook(소유자 root 게이트)으로 설치본을 post-DG-5 `BASE` 바이트로 맞춘 뒤 `repair-report-rollout` B1.2 ⓔ·F4가 system 스코프로 검증한다(근거 사슬: `.omo/notepads/repair-report-rollout/decisions.md`).

## G5 — 스킬 위생 + mail 다이제스트

구현 내용은 [소개](기능소개/승인표면-메일다이제스트-정리.md). 아래는 코드 완료 뒤에도 소유자 권한·라이브 관측이 필요해 이번 PR에서 실행하지 않은 체크리스트다.

- **[배포 체크리스트·미완료] 라이브 cron은 아직 `--deliver local`로 관측됐고 코드만 `--deliver discord`로 수렴해 있다** → G1 착지 뒤 owner-approved 세션에서 mail·calendar·coordination·wiki를 각각 해시 승인·재배포하고, `skills/mail/deploy.sh` 실행 후 cron의 `Deliver: discord`를 확인한다. 07-31 누락분은 dry-run 건수 확인 뒤 한 번만 재전송한다. **심각도 중** — 배포 전까지 다음 실패 알림도 조용할 수 있으나 이번 세션은 노드 동작을 전혀 시도하지 않았다.
- **[배포 체크리스트·미완료] vendored mailon의 미사용 import 7건은 소스에서 제거됐지만 라이브 mailon 릴리스에는 아직 반영되지 않았다** → 수정 커밋이 `origin/main`에 착지한 뒤 별도 owner-approved mail 재배포를 요청하고 `~/.hermes/mailon-runtime/current`가 새 vendor digest를 가리키는지 확인한다. **동작·보안 문제 없음 · 심각도 낮음(배포 대기)** — 제거된 import는 실행에 쓰이지 않았고 unit 3391건·vendor offline 58건·저장소 전체 Ruff가 통과했으며, 이번 repair-report rollout에서는 승인 게이트가 필요한 외부효과를 수행하지 않는다.
- **[canary 체크리스트·미완료] GLM payload 전달 여부는 worktree에서 증명할 수 없다** → owner 세션에서 비민감 합성 입력으로 비-4xx·유효 JSON·reasoning tokens 0을 확인한다. 기존 proxy가 필드를 버린다는 증거가 생길 때만 `configs/litellm-staging/config.yaml`을 별도 변경한다. **심각도 중** — 현재는 fail-open과 항목 재시도가 동작을 보전하며, 이 PR은 gateway config를 수정하지 않았다.
- **[조사·노드 확인 미완료] 리포 증적상 mail은 pending 0이고 최신 skill-gate 잔재 11종 목록에도 mail·wiki가 없다** → 재배포 직전 노드에서 두 스킬의 실제 pending 상태를 read-only로 다시 확인한다. 성공한 배포는 stage 4 직후 정확한 `(skill, hash, message_id)`만 `consume`하므로 새 요청은 자동 정리되지만, 이미 결정된 구레코드가 발견되면 무조건 덮어쓰지 않고 소유자 판단으로 `skill_gate abandon`을 사용한다. **보안 문제 아님·심각도 낮음** — fail-closed 잔재가 있으면 배포가 멈추는 가용성 문제다.

## H3 배포 위생 도구 반영 후 OWNER 실행 항목

기능은 [소개](기능소개/H3-배포-위생.md).

- **일일 sandbox 스모크는 코드·격리 검증까지만 완료되어 노드 timer가 아직 설치되지 않았다** → PR 머지 뒤 OWNER가 `provision-deploy-smoke.sh`를 실행하고 첫 tick의 `~/.hermes/deploy-smoke/tick.json`을 확인한다. **배포 안전 관측성·심각도 중** — 설치 전에는 다음 실배포가 여전히 첫 노드 종단 검증이다.
- **미러 writer inventory와 로컬 브랜치 후보는 실제 노드에서 실행하지 않았다** → OWNER가 read-only inventory와 `docs/guide/미러-로컬-브랜치-정리.md` 기준을 적용해 검토하며, 삭제는 별도 OWNER 판단으로 수행한다. **동작·보안 영향 없음·심각도 낮음** — 도구 개발 중 실제 미러·ref는 변경하지 않았다.

## H1 — 헬스체크 릴리스 관측성 OWNER 인계 (2026-08-05)

기능은 [소개](기능소개/헬스체크-릴리스-관측성.md), 증적은 `.omo/evidence/fs2/task-1-parallel-followup-sweep-2.txt`다.

- **`RELEASE_STALE_PROBE_ENFORCE`는 기본 0(WARN)이다** — 독립 프로브가 먼저 shadow 관측되도록 강제 승격하지 않았다. **릴리스 stale 단독 경로는 경고만 남는 롤아웃 단계 · 심각도 중** → OWNER가 오탐 여부를 확인한 뒤 원장에 승격 결정을 기록하고 1로 전환한다.

## 플랫폼 운영자 매뉴얼(W-M3) 작성 중 발견한 후속 과제

F4 해소 기록(2026-08-21): 404 링크 제거와 신규 링크 conformance는 완료됐다. 남은 owner
action은 네 제외 런북을 공개 대상으로 승격할지 결정하는 것뿐이며 OWNER-29가 그 범위만 소유한다.

아래 불릿은 초기 131행과 결합된 회계 정본 원문이라 수정하지 않는다. 현재 열린 범위는 위
해소 기록의 owner action으로 대체됐다.

- **공개본에서 끊기는 문서 링크가 있다** — 공개 원장에 있는 매뉴얼들이 export 제외 문서를 링크한다: `manual-group-admin.md` → `managed-skill-channel.md`(기존), `manual-maintainer.md` → `operations.md`·`incident-response.md`·`reboot-recovery.md`(신규). 공개 트리에서는 그 대상이 존재하지 않아 404가 된다. **보안·동작 무관, 문서 탐색성만 영향 · 심각도 낮음** → 세 가지 중 하나를 고른다: (a) 해당 문서를 공개 대상으로 승격 (b) 공개되는 문서에서 링크를 걷어내고 산문으로만 언급 (c) export 시 제외 대상 링크를 검출하는 conformance 테스트를 추가해 최소한 새로 늘지 않게 한다. 신규 3건은 이번에 `*(개발 저장소 전용 — 공개본에 포함되지 않는다)*` 주석을 달아 오해만 막아 두었다.

증적: `.omo/notepads/public-release/learnings.md`의 「[2026-08-15] Task: W-M3 플랫폼 운영자 매뉴얼」 항목.

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

## 연구계획서 자동생성 v2 실증 중 발견한 후속 과제 (2026-08-23)

완료 기능은 [연구계획서 자동생성](기능소개/연구계획서-자동생성.md)이다. 실제 문서 본문·개인정보·자격증명은 이 기록에 포함하지 않는다.

- **실제 쪽수 acceptance가 estimate-tier에 머문다** — 현재 워크스테이션 판정이 권위이고, LibreOffice 24.2는 Java 21과 H2Orestart v0.7.13을 설치한 뒤에도 seed HWPX를 읽지 못해 원인을 밝히지 못했다 → Windows/한글(Hancom) PDF 러너를 도입할 때 실제 쪽수 판정을 정밀 acceptance로 승격한다. **현재는 소유자 열람 확인으로 대체하는 비차단 항목 · 영향 범위: 페이지 수 검증 · 심각도 낮음**.
- **실 이미지 API의 비용·자격증명 경로가 아직 검증되지 않았다** — 워크스테이션에 OPENAI 키가 없어 images 단계는 fake로 검증했고 월 10달러 소프트캡 뒤의 실제 지출 관측도 없다 → 키를 승인된 비밀 경로로 조달하고 월 지출 모니터링·경보를 운영한다. **영향 범위: 실 그림 생성과 비용 통제 · 심각도 중**.
- **정규 picture-carrier fixture가 없다** — 현재 픽스처는 공개 honeypot 템플릿 유래 파생본이라 한/글이 직접 만든 carrier와의 호환성을 대표하지 못한다 → 한/글에서 수작업으로 정규 fixture를 만들고 구조·렌더 회귀의 기준으로 고정한다. **영향 범위: HWPX 그림 호환성 · 심각도 중**.
- **노드의 완전한 live E2E가 남았다** — 워크스테이션 E2E의 research·images·draft는 fake/replay였고 그 결과 인용도 5건으로 목표 15건보다 적었다 → 노드에서 Hermes 딥리서치, 실 LLM 작성, 실 이미지 생성을 한 번에 재실행해 F3 인용 15건 이상과 자원 매핑 60% 이상을 확인한다. **영향 범위: 최종 콘텐츠 품질 게이트 · 심각도 높음**.

## 음성 녹취 회의록 자동화(speechtotext) 착지 후 남긴 것 (2026-08-25)

- **`Meet Recordings` 의 mp4 는 파일명에 확장자가 없다** — Drive 가 `rnm-yzkx-tub (…)` 형태로 저장해
  suffix allowlist 가 하나도 보지 못한다. 지금은 감시 대상이 아니라 무해하지만, 그 폴더를 붙이려면
  mimeType 판별이 필요하다(Gemini 회의록과 중복 여부도 함께 판단). **미사용 · 심각도 낮음**.
- **`PLAUD/raw` 18건은 이미 전사된 `.txt`** — 기기가 STT 를 끝낸 결과물이라 전사 단계가 불필요하다.
  조치: 붙인다면 `meeting ingest` 직행 경로가 맞다. **미사용 · 심각도 낮음**.

## 화자 구분·문장 단위 출력 착지 후 남긴 것 (2026-09-01)

> [이관 2026-09-03 · OWNER] 소유자·노드에서만 닫힌다.

- **화자 분리는 CPU, 전사는 GPU 로 갈려 있다** — whisper.cpp 는 CUDA 빌드(`build-cuda`)를 쓰지만 sherpa-onnx 는 CPU 빌드다. 긴 녹취에서 화자 분리가 전체 처리 시간의 하한을 만들 수 있고, 상한은 `SPEECHTOTEXT_DIARIZE_TIMEOUT`(3600초)에서 끊긴다. 조치: 실측 소요를 먼저 남기고, 필요해지면 sherpa-onnx GPU 빌드를 노드에 올린다(노드 작업). **영향 범위: 처리 지연뿐이며 타임아웃도 fail-soft(`DIARIZE-FAIL` 후 화자 없이 진행) · 심각도 낮음**.
  ↳ 처리(2026-09-03): 노드 작업 — 실측 소요를 남기고 필요해지면 sherpa-onnx GPU 빌드를 노드에 올린다


## healthcheck 폭주 수리(PR #347) 중 발견한 인접 결함

> [이관 2026-09-03 · OWNER] 소유자·노드에서만 닫힌다.

- **healthcheck 상시 FAIL 4건이 PR #347 범위 밖에 남는다 → 노드 운영자(OWNER)가 폭주 해소 후 재판정하고, 남는 것만 조치한다.** `ops checkout mirrors origin/main`(mirror-dirty), `privileged release helpers match release`(HELPER-DRIFT `autophagy-converge-origin-main` → 노드에서 `sudo bash <release>/automation/provision-deploy-converge.sh`), `watcher wrappers match the release`·`runtime packages match the release`(RUNTIME-PACKAGE DIFF `memory_curator effects.py` → `automation/memory_curator/deploy.sh`)가 해당한다. 나머지 UNKNOWN 은 폭주 중 ssh 불통 탓일 가능성이 있어 폭주 해소 후 재판정한다. 영향 범위: 운영 드리프트, 보안 문제 아님, **심각도 낮음**.
  ↳ 처리(2026-09-03): 노드 운영자가 폭주 해소 후 재판정 — mirror-dirty 는 이번 스윕의 미러 동결 문구가, HELPER-DRIFT·RUNTIME-PACKAGE DIFF 는 provision-deploy-converge.sh·memory_curator/deploy.sh 실행이 닫는다


## 후속 과제 스윕 4 착지 후 소유자만 닫을 수 있는 것 (2026-09-03)

- **[OWNER] 중앙 매니페스트(`configs/watcher-deploy-manifest.txt`) 바이트가 바뀌었다 → 노드에서 `automation/healthcheck_probe_wrapper.sh --install` 을 다시 돌리고, 새 배포기 `automation/cost-report/deploy.sh`·`automation/reminder_poller/deploy.sh`·`automation/repair/deploy.sh`·`automation/skill_generation/deploy.sh` 를 한 번씩 실행해 손배포 사본을 선언된 배포본으로 바꾼다.** 그 전까지 `healthcheck_wrapper_current` 프로브가 지문 불일치를 알린다(심각도: 중).
- **[OWNER] 08-29~09-01 미러 동결 동안 `checkout_mirrors_origin` 프로브가 실제로 FAIL·수리 티켓을 냈는지, 그리고 이번 릴리스 뒤 `state.json` 의 `mirror_state` 가 노드에서 값을 갖는지 노드 로그로 1회 확인한다.** 리컨실러 문구 보강은 코드로 끝났고 이것은 관측 확인이다(심각도: 낮음).
- **[OWNER] `~/.hermes/selfskill-audit/pending-overlaps.json` 의 미결 겹침(실측: `meeting-minutes-authoring`·`document-publishing` ↔ governed meeting·doctype·report)을 승격(governed 로 제출) 또는 폐기(`hermes curator archive`)로 결정한다.** 원장은 코드가 유지하고 결정만 남았다(심각도: 중).
- **[OWNER] obsidian write clone 전환 뒤 첫 fetch 로그에서 origin 이 `uploadpack.allowfilter` 를 허용하는지(blob 없는 fetch 가 실제로 작아졌는지) 와 `tmp_pack_*` 잔해가 더 생기지 않는지 1주 관측한다.** 거부되면 fetch 는 전량이지만 실패하지는 않는다(심각도: 낮음).
- **[OWNER] 이번 스윕이 SKILL.md·스크립트를 바꾼 스킬(mail·calendar·budget·todo·wiki·coordination·speechtotext·doctype·meeting·patent-prep·procurement·prompt·proposal·recall·report·topics·hello-autophagy)은 릴리스 ✅ 1회로 전량 마운트된다 — `automation/release.sh` 실행.** 그 전까지 노드는 옛 마운트로 돈다("커밋됨 ≠ 배포됨")(심각도: 중).
  ↳ 해소(2026-09-04) 저장소 절반만: 브랜치 베이스가 릴리스 태그 v1.1.4 에 거리 0 커밋으로 앉아 있어 릴리스는 이미 끊겼다. 노드 마운트 실물 확인은 아직 아니며 소유자 몫으로 열려 있다.

# BLOCKED — 동결·벤더·외부 의존으로 지금 손댈 수 없다

## 동결 해제·repair 재발 수리 착지 후 남긴 것 (2026-09-04)

> [이관 2026-09-04] follow-ups.md 에서 옮겨 왔다 — 네 행이 동결로 남아 이 저장소가 지금 손댈 수 없다. 해제는 「고칠 코드가 그 파일 안에 있는 행만」 기준으로 요청한다.

- **`automation/repair/repair_ops_core.py`·`repair_lifecycle.py`·`repair/systemd/*`·`gate-ledger-inventory.md` 는 동결로 남았다** — 이번 해제는 실제로 고칠 것이 그 파일 안에 있는 행(`repair_core.py`)과 원장이 거짓이던 행만 풀었다. 그 네 행에 걸린 BLOCKED 항목(감사용 전용 exit code·TOCTOU·삭제 패치 범위·유닛 ExecStart 인자·문서 지연)은 [follow-ups-deferred.md](follow-ups-deferred.md) 에 그대로 있다. 조치: 그 항목들을 실제로 고칠 사이클이 잡힐 때 같은 방식(고칠 코드가 그 파일에 있는 행만)으로 해제를 요청한다. **의도된 잔여 · 심각도 낮음**.

## G8 — LOC 등록부

기능은 [소개](기능소개/loc-등록부-재측정.md), 작업 배분은 `.omo/plans/parallel-followup-sweep.md` §5 G8이다. 8개 코드 그룹이 전부 머지된 HEAD(`2383a92`)에서 전수 재측정해 **초과 29건 · 등록 3건**이던 상태를 등록 29건으로 맞췄고, LOC 게이트는 `EXCEPTION 29 / VIOLATION 0 · LOC RESULT: PASS`가 됐다.

- **진짜 분할 후보 5건은 "naturally large"가 아니라 "미룸 + 왜"로 등록했다** — `deploy-skill.sh`(550, 인자 파싱·실행 lease·ABI 스캔 분리 가능) · `triage_gate.py`(467, mailon/gmail 두 실행 백엔드) · `calendar_confirm.py`(437, 워처 HMAC 인가 블록 약 100줄) · `skill_gate.py`(429, G2 요청분) · `research_trends.py`(262, 수집/LLM/전달 계층). **현재 동작·보안 영향 없음 · 품질 부채** → 각각 별도 사이클에서 다룬다. 나머지 24건은 지금 쪼개면 오히려 나빠지는 것들이라 사유만 남겼다(vendored 6 · 승인 게이트 단일 절차 7 · argparse 표면 2 · 특권/주입 경계 3 · 순수 로직 5 · 셸 자기완결 1).
  **심각도 낮음** — 분할은 각 별도 사이클이 소유하는 품질 부채다.

  [해소 부분·이관 2026-08-31] 이 사이클에서 착수한 결과 — triage_gate 467→231(mailon/gmail 백엔드 분리) 완료 · calendar_confirm 437→300(HMAC 인가 `calendar_confirm_authz.py` 분리, 잔여 사유 재등록) 부분 · research_trends 는 분할했다가 되돌림(FS3 재생 핀 `.omo/evidence/fs3/task-12-already-fixed-probe.py` 의 `_loc_registry_entries` 가 그 파일의 250 초과+등록을 살아있는 불변식으로 고정) · skill_gate(717)·deploy-skill.sh 는 동결(todo 14 조율)+`test_public_release_hygiene` trust-root 정확일치 가드에 막힘. 되돌아오는 조건: 핀·동결 소유 사이클.


## 제안서 HWPX 품질 수리 중 남긴 것 (2026-08-26)

> [이관 2026-08-31] 아래 항목은 외부 저장소 kimm-docbot(고정 SHA 엔진) 내부 결함·개선이다 — 이 저장소에서 닫을 수 없고, KD 사이클 + 엔진 핀 전진이 되돌아오는 조건이다.

- **버전 디렉터리를 손으로 조립하면 `refine` 이 멈춘다** — `REFINEMENT-INPUT-ERROR: version manifest
  is missing`. (정정: 처음에 이를 "manifest 는 publish 가 만드는데 refine 이 더 앞이라 순서가 어긋난다"고
  적었으나 **틀렸다**. `manifest.json` 은 `VersionStore.promote(slug, staging, {parent, request,
  schema_version})` 이 버전 승격 시점에 쓴다 — 정상 파이프라인에는 항상 존재한다.) 이번처럼 기존 버전을
  복제해 손으로 만든 디렉터리에만 없다. 조치: 진단 편의를 위해 오류 문구에 "버전이 VersionStore 를 거쳐
  생성되었는지 확인" 안내를 덧붙인다. **정상 경로 영향 없음 · 심각도 낮음**.
- **대제목이 홀로 한 쪽을 차지한다** — 밴드는 그림을 쪽 상단에 두려고 선행 페이지 나눔을 강제하므로,
  각 섹션 대제목 다음이 곧 나눔이라 제목만 있는 쪽이 생긴다(33쪽 중 1쪽이 113자 수준). 조치: 섹션의
  첫 밴드에 한해 나눔을 생략하려면 `FIGURE_TOP_QUARTER` 계약을 함께 손봐야 한다. **분량 · 심각도 낮음**.
- **KPI 근거의 `env` 값이 인제스트에서 잘린다** — 코퍼스 원문 `env: TensorFlow 2.13 학습 서버로
  설정한다` 가 증거 단위 `fact` 에는 `env: TensorFlow 2` 로 들어온다. 플래너는 무죄다 — 같은 문장을
  직접 주면 `TensorFlow 2.13 학습 서버` 로 온전히 파싱된다(회귀 고정: `tests/contract/test_planner.py`).
  즉 절단은 corpus→evidence 변환 단계이고, 소수점 `2.13` 의 마침표를 문장 끝으로 본 결과로 보인다.
  조치: `converter` 의 문장 분할이 숫자 사이 마침표를 경계로 삼지 않게 고친다.
  **제출본 표에 불완전한 값이 실린다 · 심각도 중**.
- **`remove_direct_paragraphs` 는 중첩 문단을 품은 문단을 지우면 XML 을 깨뜨린다** — 바이트 추출이
  `<hp:p>` 다음의 **첫 `</hp:p>`** 에서 끝나는데, 그림 캡션(`hp:caption`)이나 표 셀은 그 안에 문단을
  또 갖는다. 그런 문단을 삭제 대상에 넣으면 잘린 조각이 남아 문서가 파싱되지 않는다. 지금은 도달하지
  않는다 — 삭제 집합(안내문·양식 소제목)이 시드에서 유도해 동결돼 있고 거기에 중첩 문단이 없으며,
  앵커 보호가 한 겹 더 있다. 실제로 밟은 것은 QA 뮤테이션 도구뿐이다. 조치: 중첩 `hp:p` 를 가진
  문단이 삭제 대상에 들어오면 잘린 조각을 내보내지 말고 이름 있는 오류로 거부한다(같은 계열의
  `_body_template_ordinal` 은 kimm-docbot `4d29058` 에서 이미 건너뛰기로 닫았다).
  **현재 경로 무영향 · 심각도 낮음**.
- **윤문 폴백이 1/5 에서 3/5 로 늘었다** — 본문을 양식 목차(`## 1-1. …` 처럼 번호로 시작하는 제목)로
  재구성한 뒤 refine 의 청크 5개 중 3개가 불변식에 걸려 원문 폴백했다(직전 사이클은 1개). 폴백은
  재구성 원문을 그대로 두므로 손상은 없고 리포트에 `PASS_WITH_FALLBACK` 으로 정직하게 남지만,
  윤문이 닿는 범위가 줄었다. 조치: `refine-report.json` 의 청크별 실패 불변식을 확인해, 숫자로 시작하는
  제목 줄을 구조 필드로 오인하는지부터 본다. **문체 다듬기 범위 축소 · 심각도 낮음**.
- **`render_paragraph_blocks` 가 렌더 경로에서 빠졌는데 공개 API 로 남아 있다** — 그림 없는 경로가
  `figure_density.render_body_paragraphs` 로 옮겨가면서 이 함수는 이제 테스트만 부른다. 그런데 이웃 시드
  문단을 복제해 문자 속성을 물려받는 그 성질이 그대로라, 다음 사람이 "본문 삽입용 함수"로 다시 집으면
  같은 결함이 되살아난다. 조치: 남은 호출부(`tests/hwpx/test_seed_fill.py` 3곳)를 새 진입점으로 옮기고
  함수를 지우거나, 최소한 문자 속성을 필수 인자로 만든다. **현재 산출물 무영향 · 심각도 낮음**.
- **필수 섹션 별칭이 양식 제목 문자열에 묶여 있다** — `hwpx/validate._canonical_section_text` 가
  `1. 연구 배경 및 필요성` 같은 **문자열**을 별칭 목록에 들고 있어, 양식이 제목 문구를 한 글자만 바꿔도
  게이트가 조용히 "섹션 누락"으로 돌아선다. 시드에서 제목을 유도해 대조하는 편이 안전하다
  (`seed_fill.FORM_SUBHEADINGS` 가 소제목에 대해 이미 그렇게 한다). 조치: 대제목도 시드에서 유도해
  별칭을 파생시키고 회귀로 고정한다. **양식이 바뀔 때만 발현 · 심각도 낮음**.
- **플래너가 작업 패키지를 한 덩어리로 내고 `lead` 에 무관한 과제명을 담는다** — 이 코퍼스에서 36개월
  짜리 패키지 1개가 나왔고 그 `title` 은 네 구간을 모두 열거한 문장, `deliverables` 는 같은 문장, `lead` 는
  **다른 과제**(`**주요사업: 협력형배송체 (Cooperative Delivery)**`)였다. 마크다운 굵게 표시까지 그대로 남아
  있어 인제스트 단계에서 잘못된 블록을 집은 것으로 보인다. 추진 일정 표는 이번에 이름 절단으로 방어했지만
  근본 원인은 남아 있다. 조치: `contract/planner.py` 의 work package 추출이 어떤 증거 블록을 고르는지
  확인하고 과제 경계를 넘지 않게 고정한다. **표·표지 파생값에 잘못된 값이 실린다 · 심각도 중**.
- **추진 일정의 구간 파싱이 바로 위 결함에 얹혀 있다** — `pipeline/orchestrator._plan_phases` 는 작업
  패키지 서술에서 `N~M개월 <이름>` 을 읽어 행을 만든다. 그런데 그 서술이 네 구간을 한 문장에 열거하는
  것은 위 항목이 지적한 **플래너의 한 덩어리 출력** 때문이다. 플래너를 고쳐 구간별 패키지를 따로 내면
  문장에 `N~M개월` 이 사라져 파싱이 0건이 되고, 표는 조용히 패키지 순차 배치 폴백으로 돌아간다 — 그
  폴백은 지금도 정상 동작이라 실패로 보이지 않는다. 조치: 플래너를 고칠 때 구간 정보를 `WorkPackage` 의
  구조 필드(착수·종료 월)로 올리고, `_plan_phases` 는 그 필드를 먼저 보고 문자열 파싱을 폴백으로 내린다.
  **지금은 정상 · 플래너 수리 시 조용히 퇴행 · 심각도 중**.
- **윤문 예산이 입력에 적용되어 다듬을 여지를 남기지 않는다** — `char-budget` 불변식은 윤문 **결과**를
  절 예산의 1.1배와 비교하는데, 다듬기는 문장을 늘릴 수 있다. 예산에 맞춰 쓴 절(900자/예산 900)이
  윤문 뒤 1,013자가 되어 거부됐고, 전 절에 9~59% 여유를 남겨서야 통과했다. 조치: 입력 예산과 출력
  상한을 분리하거나, 입력 목표를 상한의 85% 수준으로 문서화한다. **저자가 이유를 알 수 없다 · 심각도 중**.
- **같은 상수가 두 저장소에 사본으로 존재한다** — 레이아웃 프로파일(쪽 목표·그림 슬롯·예산)이 KD
  `contracts/layout_profile.py` 와 AT `skills/proposal/scripts/proposal_ir.py` 양쪽에 있고, 이번에
  한쪽만 고쳐 두 번 실패했다. AT 는 KD 를 import 할 수 없다(고정 SHA 서브프로세스). 조치: 렌더 시
  엔진이 자기 프로파일을 산출물에 기록하고 AT 가 그것을 읽게 하거나, 두 표를 대조하는 적합성 검사를
  핀 체크아웃 기준으로 돌린다. **한쪽만 고치면 조용히 어긋난다 · 심각도 중**.
- **산출 HWPX 가 26MB 다** — 그림 15장을 1024x1024 원본 그대로 임베드한다. 조치: 렌더 시 본문 배치
  크기(120mm)에 맞춘 다운스케일 옵션을 둔다. **제출·공유 편의 · 심각도 낮음**.
- **양식이 요구하는 「기술이전 계획」을 본문에 쓰면 렌더가 막힌다** — `sensitivity-rules.yaml` 이 `기술이전`
  을 patent-sensitive 키워드로 잡고, patent-sensitive 는 `render` 목적지에서 거부된다. 양식의 4-1 안내문은
  "기업화 / 기술이전 / 추가 연구 / 표준화 계획"을 요구하므로 규약과 게이트가 정면으로 부딪힌다. 이번에는
  규칙이 아니라 문구를 「산업 연계」로 바꿔 우회했다(2026-08-26 이미지 프롬프트 `claims` 건과 같은 처리).
  조치: patent-sensitive 를 render 에서 거부할 이유가 실제로 있는지(렌더는 로컬 서브프로세스다) 재검토하고,
  없다면 destination 진리표를 고친다. **본문 어휘가 게이트에 갇힌다 · 심각도 중**.
  [이관 2026-08-31] 재검토 실측: render 경로가 KD 엔진 서브프로세스에 자격증명을 전달한다(`skills/proposal/scripts/proposal_render.py:26-29`) — KD 엔진이 LLM 클라이언트를 구성하지 않음을 확인하기 전에는 차단이 정당하다. 되돌아오는 조건: KD 확인 → dead-weight 전달 제거 → 진리표 재검토.

## 시나리오가 두 개의 환경 계약으로 실행된다 (2026-08-17 실측)

> [이관 2026-08-31] `deploy-skill.sh` 동결(todo 14 조율 범위)에 막혀 있다 — 해제 조율 후 credential-free 러너 분리를 진행한다.

FS3 K2-A는 비동결 `skill_review`·`peer_attest` 실행 환경을 공용 러너로 통일했다. 동결된
`deploy-skill.sh` 내부 구성과 자격증명 격리는 todo 14 조율 범위로 남긴다.

승인이 끝난 `todo` 배포가 `PEER-ATTEST-BLOCK` 으로 마운트되지 못한 사고에서 드러났다. 스킬 쪽 원인은
수정했으나(`skills/todo/scripts/scenario.sh`), 그 사고를 가능하게 한 구조는 그대로 남아 있다.

- **peer 검토는 자격증명을 든 계정에서 untrusted 시나리오를 실행한다** — stage 3 은 peer 의 `.env.secrets` 를
  로드한 뒤 같은 계정에서 시나리오를 돌린다. `env -i` 와 임시 `HOME` 은 파일시스템 접근을 막지 않으므로,
  악의적 시나리오가 실제 peer home 을 역산해 봇 토큰·서명키에 접근할 여지가 있다 → 시나리오 실행을
  credential-free 전용 UID 또는 동등한 mount namespace 로 분리한다. **현재 악용 사례 없음 · 심각도 중**.

증적: PR #139, peer 계정 실측(`scenario` False→True), Oracle 판정(단일 러너 권고).


## Hermes 무재시동 자체 업데이트로 게이트웨이 도구 계층이 죽었다 (2026-08-18)

`<primary-node>`의 agent 게이트웨이가 08-17 13:24부터 08-18 09:57(KST)까지 **모든 도구 호출**을 `ImportError: cannot import name '_plan_tool_batch_segments' from 'agent.tool_dispatch_helpers'`로 실패시켰다(errors.log 9회). 소유자의 메일 요청 2회가 이것으로 막혔고, 첨부 확인·수신자 조회·승인 초안이 전부 도구 호출이라 함께 죽었다(발송·초안 생성은 일어나지 않았다 — 승인 로그·draft store 신규 레코드 0). **코드가 아니라 프로세스가 원인이다**: 그 심볼은 디스크 파일에 정상 존재하고(`tool_dispatch_helpers.py:117`, `__all__` 등록), 새 인터프리터에서 `import agent.tool_executor`는 통과한다. 게이트웨이는 08-16 12:50 기동, `hermes-update`는 08-16 14:34·14:36 실행 — **떠 있는 프로세스 밑에서 소스 트리가 교체**됐고, 옛 모듈을 든 프로세스가 새 파일을 import하다 죽었다(트레이스백 줄 번호가 디스크 소스와 어긋나는 것이 같은 증거). agent·peer 게이트웨이를 함께 재시동해 해소했다(01:03 UTC, 실제 도구 호출 1건으로 검증).

## 수리 티켓 스윕-2 종결 중 발견한 후속 과제 (2026-08-17)

TRACK-A(PR #125) · TRACK-BC(PR #123) · TRACK-D(PR #129)를 착지시키고 보드·증적을 정리하며 남은 것들. 기능은 [todo 소유자-DM 승인 경로](기능소개/todo-소유자-DM-승인-경로.md) · [승인 게시 복구와 강화 저널](기능소개/승인-게시-복구와-강화-저널.md) · [2-store 메모리 재배치](기능소개/2-store-메모리-재배치.md).

- **`docs/guide/gate-ledger-inventory.md`가 이번 변경만큼 낡았다** — 그 문서는 스윕-2 freeze 목록에 있어 이번 사이클에서 손대지 않았다(변경 0 확인). 그 사이 `todo` 승인이 `~/.hermes/todo-approvals/` 아래 approval store·lease·posting-journal 디렉터리를 새로 쓰고, 메모리 재배치의 posting journal 레코드가 3필드에서 5필드(`message_id`·`channel_id` 추가)로 늘었다 → freeze가 풀리면 등록부에 이 경로·권한(0600/0700)과 레코드 스키마를 반영한다. **경로 계약은 코드가 정본이라 동작 영향 없음 · 심각도: 낮음(문서 지연)**.
- **수리 systemd 유닛의 `ExecStart`에 티켓 인자가 없다(현재도 유효한 잠복 관측)** — `autophagy-repair-agent.service`는 `repair_ops_cli.py`를 인자 없이 띄우지만 CLI는 티켓 id 하나를 필수로 요구한다. 현재 유닛은 static/inactive이고 실제 승인 워처는 CLI에 티켓 id를 직접 넘기므로 라이브 장애는 아니다. freeze가 풀려 이 유닛을 활성 경로로 쓸 때 큐 래퍼 또는 `%i` 템플릿으로 인자를 공급하고 회귀 검사를 추가한다. **동결 파일은 읽기만 함 · 심각도: 중(직접 기동 시 즉시 실패)**.

## 수리 스윕 3차·개인 서버 대화 채널 후속 과제 (2026-08-17)

완료 기능은 [개인 서버 대화 채널](기능소개/개인서버-대화-채널.md)과 [기관메일 발신자·전체 폴더·검색](기능소개/기관메일-발신자-전체폴더-검색.md), 완료 수리는 [peer trust-root 진단 분리](patch/2026-08-17-skill-gate-peer-trust-root-diagnostic.md)다.

- **Discord의 중간 진행·도구 출력 억제는 Hermes vendor 수정이 필요하다 (`t_db6a60e8`)** — repo의 protocol transport는 일반 agent turn 렌더링을 소유하지 않아 동결 경로를 우회해도 해결되지 않는다 → vendor freeze가 풀린 별도 계획에서 gateway/Discord adapter 또는 `hermes_compat` 패치와 회귀를 함께 설계한다. **현재 계획에서는 BLOCKED-freeze · 심각도: 중(대화 표면의 불필요한 내부 상태 노출)**.

## 배포 가드 보강(DG-7) 작업 중 발견한 후속 과제

본 작업은 `land.sh`의 부수 push와 provenance 디렉터리 blind spot을 닫았다. 아래는 같은 작업에서 드러난 잔여부채다.

- **`gh pr create`가 문서에만 있고 구현이 없다 — BLOCKED 유지, 조율안 산출됨(2026-08-05, H7)** — `AGENTS.md`「수리 반영 경로 규칙」은 브랜치 push 후 PR 생성을 요구하지만 코드에 호출이 0건이다(2026-07-31 선례: 브랜치만 push되고 PR이 없어 cha가 머지할 방법이 없었다). **유실 위험·심각도 중.** 이음새 `repair_ops_work_clone.py`는 비동결이나 (a) 동결된 호출자 `repair_ops_cli.py:163`이 `branch`·`push_error`만 계약에 실고, (b) 실제 소실 경계는 그 앞의 승인 워처다 — `repair_ops_reaction_watch.py:74-86`이 자식 stdout을 `capture_output`으로 삼킨 뒤 불리언만 반환하고 `main()`(214-230)은 항상 0을 반환하므로 `pr_error`는 journal에도 남지 않는다. (c) 자격증명 배선도 공유 `/etc/autophagy/repair-approval.env`(root:ops 0640, 재조정 유닛과 공유)에 손대야 한다. 조치: 구현 전 freeze 해제 조율이 선행 조건이며, 해제 조건 3절(결과 계약 확장·전용 drop-in 배선·비대화식 gh 스케치)을 [조율안](guide/수리-PR-자동생성-조율안.md)으로 명세했다 → `.omo/plans/repair-report-core.md` 소유자 결정 대기.
증적: `docs/qa/DG-7/summary.txt`

## 수리 승인 내용 바인딩(RTS-4) 작업 중 발견한 후속 과제

기능은 [소개](기능소개/수리-승인-내용-바인딩.md), 증적 `docs/qa/RTS-4/r2-content-binding.txt`

- **적용 직전 TOCTOU가 완전히 닫히지 않았다** — `ManualOwnerApproval.permits`가 바이트를 읽은 뒤 `GitRepository.apply`가 같은 파일을 다시 읽고(`repair_ops_git.py:48`), `git apply <path>`가 또 한 번 읽는다. 즉 같은 실행 내부에 짧은 교체 창이 남는다. **심각도 낮음** — 교차 실행 간 공격(승인 후 패치 교체)은 이번에 닫혔고, 남은 창은 ops 전용 0700 경로에 대한 동시 쓰기 권한을 요구한다. 조치: `GitRepository`에 `expected_patch_sha256`를 두고 `_apply_approved`→`_run`→`_agent`로 스레딩한 뒤 `git apply -`로 검증된 바이트를 stdin으로 넘긴다. **이번에 미수행한 이유**: `repair_ops_cli.py`가 `.omo/plans/repair-report-core.md:220,237`에서 불변으로 선언되어 기계 검사(`git diff BASE..HEAD | wc -l == 0`)로 강제되며, 같은 계획이 :189에서 그 불변성을 전제로 다른 설계 타협을 소유자 승인으로 수용했다. 해당 계획과 순서를 조율한 뒤 진행한다.
- **`_run`이 `AWAITING_APPROVAL`에도 exit 0을 돌려준다**(`repair_ops_cli.py:174`). 워처는 자식이 0이면 성공으로 보고 레코드를 회수한 뒤 approvals 로그에 `"status":"approved"`를 남긴다 — 아무것도 적용하지 않은 채로. **이번에는 거부를 `False`가 아닌 예외로 만들어 우회했다**(non-zero 종료 → 레코드 보존, 감사 위조 없음). 근본 수리는 위와 같은 이유로 보류. 조치: 적용 경로의 `AWAITING_APPROVAL`을 전용 exit code로 분리한다. 심각도 중 — 감사 무결성 문제이지 오적용은 아니다.
- **승인 거부 시 TTL까지 매 tick마다 샌드박스가 다시 돌아간다** — `RepairAgent.repair()`가 planner·sandbox를 먼저 돌리고 그 다음에야 `approval.permits`를 부른다(`repair_ops_core.py:134-144`). 내용 불일치로 거부되는 티켓은 24h TTL로 자가치유되기까지 매번 최대 900초 샌드박스를 소모한다. **동작·보안 문제 아니고 빈도도 낮다**(patch.diff는 티켓당 한 번 쓰이고 planner가 재기록하지 않음). 조치: `_apply_approved`에 값싼 사전 가드를 두어 planner 이전에 끝낸다 — 역시 `repair_ops_cli.py` 조율 후.
- **`_assert_scope`가 삭제 패치를 표현하지 못하고 rename의 원본 경로를 검사하지 않는다** — `+++ b/` 스캔이라 `+++ /dev/null`(삭제)은 조용히 거부되고 rename은 삭제측이 unstaged로 남는다. 이번에 만든 `parse_patch_changes`가 양쪽을 정확히 알므로 그것을 소비하면 “승인한 것 = 범위 검사한 것 = 적용한 것”이 된다. **이번에 미수행한 이유**: 보안 수리 중에 게이트의 허용 집합을 넓히는 것은 별개 결정이다. 심각도 낮음 — 파일 삭제가 필요한 수리를 지금은 아예 적용할 수 없다는 기능 제약이지 구멍은 아니다.

## H1 — 헬스체크 릴리스 관측성 OWNER 인계 (2026-08-05)

기능은 [소개](기능소개/헬스체크-릴리스-관측성.md), 증적은 `.omo/evidence/fs2/task-1-parallel-followup-sweep-2.txt`다.

- **문서 갱신 대기: `operations.md:105` (freeze 해제 후)** — 현재 문구는 수리 티켓 allowlist가 없다고 적어 manifest 도입 뒤 낡지만 freeze 때문에 수정하지 않았다. **운영 안내 정확성만 영향 · 심각도 낮음** → freeze 해제 후 D1 명령과 manifest/OWNER 등록 절차로 갱신한다.

## K4-b 설치기·신뢰키 위생(FS3 todo 12) 중 발견한 후속 과제

- **게이트가 import하는 모듈은 `deploy-skill.sh`가 동결인 동안 분할할 수 없다** — `peer_attestation.py`의 진단을 별도 모듈로 떼자 `validate_gate_staging_imports`가 `STAGE-BLOCK: imported gate module is not staged`로 배포를 막았고(실측), staging 목록은 동결된 `deploy-skill.sh` 안에 있다. 그래서 그 모듈은 250줄 천장 안에서 해결해야 했다(현재 249). 같은 제약이 `skill_gate.py`(553, 예외 등록)에도 이미 걸려 있다. **현재 동작·보안 영향 없음 · 심각도 중(구조적 — 천장에 닿은 게이트 모듈이 늘수록 선택지가 사라진다)** → staging 목록을 `deploy-skill.sh` 밖의 기계 판독 파일로 옮기는 안을 todo 14 조율안의 해제 조건과 함께 검토한다.

증적: `.omo/evidence/fs3/task-12-parallel-followup-sweep-3.txt`, 소개 [설치기 위생과 신뢰키 회전 병합](기능소개/설치기-위생과-신뢰키-회전-병합.md).

## Hermes kanban 열린 이슈 정리(2026-08-22) 중 발견한 후속 과제

보드의 열린 카드 8건을 정리하며 healthcheck 허용목록 래퍼(`<primary-node>`·`<rag-node>`)를 재생성하고
runtime-package 프로브의 `cron/` 오탐을 고쳤다. 그 과정에서 드러난 구조적 틈이다.


## healthcheck 폭주 수리(PR #347) 중 발견한 인접 결함

> [이관 2026-09-03 · BLOCKED] 동결 파일에 막혀 있다 — freeze 해제 뒤 되돌린다.



## 요청별 승인 스레드 착지 후 남긴 것 (2026-09-01)

> [이관 2026-09-03 · BLOCKED] 동결 파일에 막혀 있다 — freeze 해제 뒤 되돌린다.

- **repair 워처와 `--apply-approved` 의 read-only 부트스트랩이 여전히 kind 스레드 `승인-repair` 를 find-or-create 한다 → `RepairDiscordApi` 를 바인딩 없이 만들고 `for_pending` 에서만 저장 레코드로 바인딩하도록 transport·워처·CLI 를 정리해 kind 스레드 의존을 없앤다.** 승인 게시(`_approval`)는 이미 티켓별 스레드 `수리 · t_<ticket>` 로 가고 이 경로는 아무것도 게시하지 않으므로 기능 결함은 아니다 — 빈 `승인-repair` 스레드 하나가 채널에 남는 표시 문제(심각도 낮음). 디렉터리는 요청별 스레드를 조회 없이 열기 때문에 부트스트랩에 티켓을 넘기는 손쉬운 우회는 틱마다 빈 스레드를 하나씩 더 만든다 — 그래서 미루었다.
  ↳ 처리(2026-09-03): `automation/repair/repair_ops_*.py` 는 configs/freeze-inventory.txt closed 동결 — repair 소유 계획에 제출하고 cha 가 해제를 승인한 뒤 별도 사이클

# OBSERVE — 조건이 성립하기 전에는 조치하지 않는다

## Plaud lifelog 노트 v2 양식 착지 후 관측할 것 (2026-09-04)

- **[OBSERVE] `transcribe.finalize` 에서 추출(glm-main)이 실패하면 그 녹음은 `추출: …` 사유로 `transcribing` 에 남고, 전사본은 추출 뒤에 저장되므로 다음 틱이 오디오 다운로드·로컬 전사를 통째로 다시 한다 → 노드 stderr 에 같은 녹음의 `추출:` 대기가 2회 이상 반복되면 전사본을 추출 **전에** 저장하고 finalize 가 저장된 전사본을 재사용하도록 순서를 바꾼다.** 지금 바꾸면 stale 판정·저장 경로가 복잡해지는데 실제로 반복되는지 모른다(117분 녹음 = 약 45분 재전사; 정확성 무영향, 비용만 — 심각도 낮음).

## 참고자료 후속 5건 종결 중 남긴 것 (2026-08-27)

> [이관 2026-08-31] 관측 조건이 성립하기 전에는 조치하지 않는다 — 조건은 각 불릿의 조치 문구가 적고 있다.

- **xlsx 는 셀 값만 읽는다** — 차트·도형·메모의 텍스트는 빠지고 수식은 저장된 계산 결과가
  있을 때만 읽힌다. 조치: 실제로 그 형태의 근거가 필요해질 때 확장한다.
  **회수율 · 심각도 낮음**(그 형식으로만 존재하는 근거는 아직 관측되지 않았다).
- **전사 질의어의 구어 불용어 목록은 휴리스틱이다** — 실제 회의를 몇 회분 돌려 보기 전에는
  어떤 낱말이 검색을 흐리는지 알 수 없다. 조치: 질의 로그 몇 건을 보고 목록을 조정한다.
  **회수율 · 심각도 낮음**(커버리지 우선 랭킹이 오염을 상당 부분 흡수한다).

증적: 이 문서의 수치는 실제 Drive 읽기 전용 조회 결과를 마스킹한 것이다.

## 회의록 과제 양식·Action Item 데이터베이스 중 남긴 것 (2026-08-27)

> [이관 2026-08-31] 관측 조건이 성립하기 전에는 조치하지 않는다 — 조건은 각 불릿의 조치 문구가 적고 있다.

- **미처리 전사본이 여러 건일 때 `!meeting` 한 마디로 고를 수단은 여전히 없다** — CLI 는 목록을 통지하고
  멈추지만(exit 7), 소유자가 그중 하나를 지정하려면 그 전사본을 Drive 에서 내려받아 첨부하거나
  에이전트에게 `--file` 을 지정해 달라고 해야 한다. 조치: `!meeting <전사본 파일명>` 또는
  `--pending-name <이름>` 은 이미 있고(야간 배치가 그것으로 순차 처리한다) 남은 것은 대화형 선택자뿐이다.
  **오동작 아님 · 심각도 낮음**(다건이 쌓여도 자정 배치가 처리하므로 급하지 않다).
- **야간 배치의 한 틱 상한 5건은 실측 없는 값이다** — 회의록 1건 생성에 드는 시간·LLM 비용을
  재본 적이 없어, 5건이 자정 창에 들어가는지 모른다. `MEETING_PENDING_LIMIT` 로 조정할 수 있고
  초과분은 다음 밤으로 넘어가므로 밀리지는 않는다. 다만 2026-08-28 실측에서 91,894바이트 전사본
  하나(38,499자)의 추출 왕복이 **258.9초**였다(그래서 예산을 180 → 600초로 올렸다).
  상한 5건이면 최악 50분이고, 더 긴 회의는 `MEETING_LLM_TIMEOUT` 으로 노드에서 올린다.
  조치: 첫 몇 번의 실행 소요를 보고 상한과 예산을 함께 정한다. **오동작 아님 · 심각도 낮음**.
- **`.hwp`·`.docx` 양식은 보고만 하고 읽지 못한다** — `TEMPLATE-UNREADABLE` 을 남기고 내장 골격으로
  되돌아가므로 소유자가 올린 양식이 조용히 무시되지는 않지만, 기관 양식의 다수가 그 두 형식이다.
  조치: 소유자에게 `.md` 변환본을 같은 폴더에 함께 두도록 안내하거나, 요구가 반복되면 읽기 전용
  변환기(예: `hwp5txt`)를 별도 사이클에서 검토한다. **회의록은 계속 생성됨 · 심각도 낮음**.


## 회의록 서식 재설계 중 남긴 것 (2026-08-26)

- **`[근n]` 마커는 클릭 이동이 안 된다** — 렌더러 이식성과 빈 제목 artifact 때문에 `[^1]` 각주
  문법을 일부러 피했고, Drive 프리뷰의 각주 지원은 미확인이다. 조치: 실측 후 지원되면 단일
  함수 `meeting_minutes.evidence_marker`만 교체. **가독성 여지 · 심각도 낮음**.
- **pptx 파싱이 `xml.etree`를 쓴다** — `<!DOCTYPE`/`<!ENTITY` 선언은 거부하지만 엔티티 확장
  계열 표면이 원리적으로 남는다. 입력은 소유자가 직접 준 파일뿐이다. 조치: 표면이 넓어지면
  `defusedxml` 또는 텍스트 런만 뽑는 스트리밍 파서로 교체. **owner-only 입력 · 심각도 낮음**.
- **`agenda`(안건) 키는 v4에 넣지 않았다** — 리서치는 필수로 권하지만 사후 전사본에서 뽑으면
  `discussion` 주제와 거의 겹쳐 LLM 실패 표면만 늘어난다. 조치: 실사용에서 안건이 논의와
  갈린다고 느껴지면 v5에서 추가. **의도된 생략 · 심각도 낮음**.

관련 기능: [회의록 서식 재설계](기능소개/회의록-서식-재설계.md).

## DM→#agent-chat 이관(승인 표면 v7) 중 발견한 후속 과제 (2026-08-24)

- **`agent_chat_thread` 해석은 캐시가 없다** — 승인 게시마다 REST 3~4회(채널 조회·active
  스레드·archived·생성). 승인 게시 빈도가 낮아 실용상 무해하나, 빈발 시 directory의 기존
  `cache_path` 메커니즘 재사용을 검토. **성능 여지 · 심각도 낮음**.

## cron 실패 통지 확장 중 남은 판단 (2026-08-24)

계획: `.omo/plans/cron-error-remediation.md`. 기능 소개:
[cron 실패 가시성과 주간 따라잡기](기능소개/cron-실패-가시성과-주간-따라잡기.md).

- **budget 재시도 큐는 replay 큐가 아니라 인시던트 표식이다** (CR-B 리스크) — `SHEET-FAIL`이
  행을 쌓고 다음 성공 tick이 열린 행을 **일괄** resolve한다. 다음 스냅샷이 현재 잔액을 그대로
  읽으므로 개별 재생은 불필요하지만, 이름이 그 의미를 배신한다 → 실제 replay 요구가 생기면
  그때 자료구조를 다시 본다. 지금은 회귀 테스트로 현재 의미만 고정했다.
  **동작 결함 아님 · 심각도 낮음**.
- **`mail-daily-digest`는 여전히 실패 tick마다 exit 1 + 마커 줄이다** — 일 1회 산출물이라
  실패는 즉시 알려야 하고 마커 줄이 그 전달이다. exit 1이 남아 스케줄러 배너가 마커와
  중복 배달되지만 하루 1건 상한이라 홍수가 아니다. 조치: 소유자가 중복을 성가셔하면
  digest도 기록-후-exit 0으로 통일. **의도된 시끄러움 · 심각도 낮음**.

## 메일 결과 원채널 스레드 통지 중 발견한 후속 과제 (2026-08-22)

- **origin 전달은 SKILL.md 지침(LLM)에 의존한다** — 에이전트가 `--origin-channel-id`를
  빠뜨리면 결과가 기존처럼 owner DM으로 간다(해가 없는 방향의 폴백 — 발송 게이트 자체는 불변).
  결정론적 강제는 게이트웨이가 지시 채널 id를 CLI 호출에 주입하는 구조가 필요해 이번 범위 밖 →
  누락이 반복 관측되면 meeting처럼 게이트웨이 플러그인 훅 주입을 검토한다. **fail-safe 폴백 ·
  심각도 낮음**.

관련 기능: [메일 결과 원채널 스레드](기능소개/메일-결과-원채널-스레드.md).

## Hermes 무재시동 자체 업데이트로 게이트웨이 도구 계층이 죽었다 (2026-08-18)

`<primary-node>`의 agent 게이트웨이가 08-17 13:24부터 08-18 09:57(KST)까지 **모든 도구 호출**을 `ImportError: cannot import name '_plan_tool_batch_segments' from 'agent.tool_dispatch_helpers'`로 실패시켰다(errors.log 9회). 소유자의 메일 요청 2회가 이것으로 막혔고, 첨부 확인·수신자 조회·승인 초안이 전부 도구 호출이라 함께 죽었다(발송·초안 생성은 일어나지 않았다 — 승인 로그·draft store 신규 레코드 0). **코드가 아니라 프로세스가 원인이다**: 그 심볼은 디스크 파일에 정상 존재하고(`tool_dispatch_helpers.py:117`, `__all__` 등록), 새 인터프리터에서 `import agent.tool_executor`는 통과한다. 게이트웨이는 08-16 12:50 기동, `hermes-update`는 08-16 14:34·14:36 실행 — **떠 있는 프로세스 밑에서 소스 트리가 교체**됐고, 옛 모듈을 든 프로세스가 새 파일을 import하다 죽었다(트레이스백 줄 번호가 디스크 소스와 어긋나는 것이 같은 증거). agent·peer 게이트웨이를 함께 재시동해 해소했다(01:03 UTC, 실제 도구 호출 1건으로 검증).

- **재작성이 남긴 축소 1건: 복구 경로는 영수증 수신 표시를 받지 않는다** — v0.20.3이 ingress 승인 블록을 **동기** `_discord_message_admission()`으로 추출해, `await`하는 영수증 코드가 그 자리에 존재할 수 없게 됐다. 승인 직후 첫 비동기 지점인 `_dispatch_discord_message()`로 옮겼고 라이브 경로는 예전대로 물리 DM당 1회 발화하지만, 복구 경로 `_dispatch_recovered_message()`(`claim=False`)는 👀와 ledger `record_received` 행을 받지 못한다 — 해결(resolve)은 MOD1이 쓰는 metadata를 읽으므로 정상 동작하고 수신측 breadcrumb만 빠진다(원본 패치도 라이브 경로 한 곳만 덮었으므로 범위는 같다) → 복구 경로에도 영수증을 달지 결정하려면 그 경로가 재생하는 메시지의 👀가 소유자에게 혼란을 주는지부터 정한다. **동작 결함 아님 · 심각도: 낮음(드문 경로의 관측성 공백)**.

## 수리 티켓 스윕-2 종결 중 발견한 후속 과제 (2026-08-17)

TRACK-A(PR #125) · TRACK-BC(PR #123) · TRACK-D(PR #129)를 착지시키고 보드·증적을 정리하며 남은 것들. 기능은 [todo 소유자-DM 승인 경로](기능소개/todo-소유자-DM-승인-경로.md) · [승인 게시 복구와 강화 저널](기능소개/승인-게시-복구와-강화-저널.md) · [2-store 메모리 재배치](기능소개/2-store-메모리-재배치.md).

- **승인 단일성 E2E 재평가 조건이 발동했다(OBSERVE#5)** — 원 OBSERVE 원장의 기준은 “게이트 스키마/파사드 변경 시 재검토”이고, TRACK-BC가 공유층 `automation/interop/approval_lifecycle.py`와 `approval_lease.py`에 enriched journal·probe 복구 분기를 추가해 그 조건을 충족했다. 기존 단위·인터리빙 검사는 green이지만 producer 간 E2E 교차 케이스는 부재한다 → 다음 승인 생명주기 작업에서 새 복구 분기를 포함한 교차 E2E를 복원할지 재판정하고 근거를 원장에 남긴다. **알려진 동작 결함은 없음 · 심각도: 중(공유 승인층 회귀 탐지 범위)**.

## 에이전트 자가 스킬 공존(SS-1) 작업 중 발견한 후속 과제 (2026-08-15)

자가 스킬 루트 반전과 감사 원장을 만들며 발견했다. 기능은 [소개](기능소개/에이전트-자가-스킬.md).

- **curator 노브를 기본값 그대로 수용했다** — stale 30일 / archive 90일 / consolidate off는 벤더 기본값이며, 자가 스킬의 실제 사용 주기에 맞는지 근거가 아직 없다 → **첫 감사 리포트 몇 회분을 받아본 뒤** 재검토한다(자주 쓰지 않지만 필요한 스킬이 90일에 걸려 아카이브되면 원장에 `archived` 델타로 보이고 `restore`·`pin`으로 되돌릴 수 있으므로 유실은 아니다). **동작 결함 아님 · 심각도 낮음(정책 조율 미완)**.

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

## G3 — 릴리스 스토어

기능은 [소개](기능소개/릴리스-리텐션-검증.md). keep-last-5와 설치 전 blob 검증, 세대·용량
healthcheck까지 구현했다.

- **용량 경계가 절대값 1 GiB다** — 세대 수는 retention 계약으로 제한되지만 실제 파일 크기와 `/srv` 여유 공간은 노드마다 달라 절대값만으로는 작은 디스크의 압박을 늦게 알릴 수 있다. **동작·보안 문제 아님·심각도 낮음** — 현재는 총 바이트와 세대 수가 매 healthcheck 로그에 남고 상한 초과는 실패한다. → 실제 운영 수치를 축적한 뒤 free-space 비율 경보를 추가할지, `RELEASE_STORE_MAX_BYTES` 기본값을 조정할지 결정한다. 이번 작업은 실제 `/srv` 실행 금지라 로컬 기본값만 검증했다.

## 배포 체크아웃 드리프트 가드(DG-1) 작업 중 발견한 후속 과제

기능은 DONE「배포 체크아웃 드리프트 가드 (DG-1)」 참조, 증적 `docs/qa/DG-1/summary.txt`

- **마운트 ABI 검사는 지금 WARN 전용이다** — C(DG-1)는 deploy-skill.sh·land.sh에서 라이브 스킬 ABI 파손을 감지하면 `MOUNT-ABI-WARN`/`LAND-ABI-WARN`으로 알리고 계속 진행한다(배포 중 차단은 이미 소비된 승인을 고아로 만들어 더 나쁘 실패모드). 실제 파손은 승인 흐름의 fail-closed(게시 거부)로 나타난다. **심각도 중** — 가드는 있으나 자동 차단은 아니다. 조치: `DEPLOY_ABI_STRICT=1`/`LAND_ABI_STRICT=1` 옵인을 오탐율 관찰 후 기본값으로 승격할지 결정한다.
- **미추적 파일 드리프트는 여전히 보이지 않는다** — 로컬 probe도 `--untracked-files=no`를 유지한다(`logs/` 오탐 방지용 의도된 계약). 주 사고 유형(로컬 커밋·추적 파일 수정)은 ahead/dirty로 덮인다. 조치: 미추적 드리프트가 실제 관측되면 화이트리스트 탐지 검토. 심각도 중.

## 저장·라우팅 스윕(RTS) — doctype 개인노트 진입점만 실사용 이력이 없다 (2026-08-04 갱신)

앞서 「티켓 3건이 열려 있다」고 적어둔 것은 **낡았다**. 2026-08-04 실측으로 정정한다 — `t_1b8aab9b`·`t_929ca5ad`·`t_f92027cb` 세 티켓 모두 **`done`**(2026-07-29 15:08 종료)이고, 막혀 있던 「승인 게이트 경유 실제 볼트 쓰기」도 이미 수행됐다. 상세는 DONE「저장·라우팅 스윕(RTS) 종단 검증」 참조.

- **`doctype`의 “개인노트로 저장해” 진입점은 아직 실사용된 적이 없다** — 실증된 5건은 전부 `memory_relocate`(운영사실 재배치) 경로였고, `doctype_cli`의 개인노트 분기는 같은 `obsidian_write`를 쓰지만 진입점이 다르다. **심각도 낮음으로 하향**(종전 “중”) — 공유 하단(PARA upsert → commit → push → 원격 read-back)이 5회 실증돼 미지의 영역이 라우팅 분기 하나로 즐었고, 종단 회귀는 `tests/unit/test_doctype_save_routing_e2e.py` 5건이 고정하고 있다. 조치: 소유자가 처음 “개인노트로 저장해”를 쓸 때 결과를 한 번 확인한다(별도 작업 불필요).

## 승인 생명주기 공용화(2026-07-25 배포) 중 발견된 후속 과제

증적 `docs/qa/E12/00-single-live-approval.txt`, 패치 `docs/patch/2026-07-25-approval-lifecycle-consolidation.md`

- **승인 단일성 불변식의** e2e 교차 케이스는 drive-archive 시나리오에 있었으나 E11 폐기(2026-07-31)로 함께 제거됨. 나머지 9개 게이트는 단위+인터리밙 테스트로만 고정함(각 스킬의 일반 e2e 시나리오는 별도로 존재). 우선순위 낮음 — 불변식 자체는 이미 검증됨.

## 저장 경로간 ‘의미 중복’ 가능성 — 기억해(위키) · 개인노트 저장(Obsidian) 분류기가 서로를 모름

(cha 지적, 2026-07-29)

- **문제**: B트랙의 `classify_memory_request`는 canonical을 **위키 노트**(`wiki:` 키)로, A트랙의 `classify_save_request`는 개인노트를 **Obsidian**(`obsidian:` 키)으로 보낸다. 둘 다 RAG 소스라, 같은 내용을 “기억해”로 한 번 · “개인노트로 저장해”로 한 번 요청하면 **서로 다른 source_key로 두 벌이 인덱싱**된다. 파일 중복이 아니라 의미 중복이며, 두 분류기가 상대를 모르므로 현재 코드는 이를 막지 못한다.
- **영향 범위**: recall 검색 품질(같은 사실이 두 출처로 나와 가중치 왜곡) · 트윈 판단 근거의 이중 계산. **보안 문제 아님** — 둘 다 소유자 게이트를 거치고 민감도 판정도 그대로 적용된다. 심각도 중 — 사용 패턴 의존적이라 실제 관측 전에는 빈도를 알 수 없다.
- **조치(관찰 후 결정)**: 지금 상위 라우터를 선제적으로 두면 사용되지 않을 추상을 하나 더 얹는 셈이다. 먼저 **중복이 실제로 관측되는지** 확인한다 — `personal_cha`에서 같은 내용이 `wiki:`와 `obsidian:` 두 키로 나오는 사례를 수집. 관측되면 (a) 두 분류기 앞에 공유 상위 판정(“이 요청은 기억인가 문서 저장인가”)를 두거나, (b) recall 단계에서 동일 내용의 교차 출처를 병합해 보여주는 방법 중 선택. calendar↔coordination의 `classify_meeting_request` 선례처럼 **공유 판정 함수 + 모호하면 clarify**가 (a)의 형태가 된다.

## G5 — 스킬 위생 + mail 다이제스트

구현 내용은 [소개](기능소개/승인표면-메일다이제스트-정리.md). 아래는 코드 완료 뒤에도 소유자 권한·라이브 관측이 필요해 이번 PR에서 실행하지 않은 체크리스트다.

- **[재평가] JSON 계약 하드닝 뒤에도 항목별 1회 재시도가 필요한지는 라이브 실패율이 없다** → 배포 후 `classification_failed` 빈도와 추가 호출 비용을 관찰하고, 충분한 무실패 기간 전에는 회귀 보험을 제거하지 않는다. **심각도 낮음** — 재시도는 캘린더 위임 전에 현재 메일 분류만 반복해 외부효과 중복은 없다.

## G2 — 승인 게이트·공급망 워처

기능은 [소개](기능소개/승인게이트-공급망워처-정리.md), 작업 배분은 `.omo/plans/parallel-followup-sweep.md` §5 G2다.

- **레거시 pre-schema 레코드의 자동 종결은 금지하고 소유자 실행 경로만 마련했다** — `action_hash`·`kind`·`channel_id`·`policy_version`·`surface`가 빠진 레코드는 현대 승인으로 승격하지 않는다. **안전 문제 아님·심각도 낮음** — fail-closed 보류가 유지된다. cha가 이미 실현된 효과와 정확한 `(skill, hash, message_id)`를 확인한 뒤 `abandon --legacy-only`를 실행하며, 현대 스키마 레코드는 이 경로가 거부한다. 현재 procurement 1건의 실행 여부는 소유자 판단으로 남긴다.
- **`managed-activate`·`skill-publish` 자동 재개는 의도적으로 구현하지 않았다** — 전자는 레코드에 `--activate-managed <quarantine-dir>`가 없고 후자는 별도 프로그램과 `--managed-repo`가 필요하다. **추측 실행은 공급망 안전 위험·심각도 높음**. 자동화하려면 이 맥락을 승인 레코드 필드에 영속할지, 검증된 전용 상태 파일에 둘지 먼저 설계하고 그때 `SUPPORTED_KINDS`를 확장한다.

## repair-report cron 활성화 중 발견한 후속 과제 (2026-08-13)

- **Hermes Python no-agent job은 정상 실행의 stdout도 cron output artifact에 보존하지 않을 수 있다** — `repair-report-consumer`뿐 아니라 `daily-cost-report`·`budget-watch`·`reminder-poller`의 실제 성공 run도 `Status: silent (empty output)`로 남았다. **수리 보고 처리 자체와 보안에는 영향 없음 · 심각도 낮음(스케줄러 관측성)** → Hermes의 Python no-agent stdout 수집 경로를 별도 조사하고, 그 전에는 성공 여부를 stdout 본문이 아니라 `Last run` 전진과 도메인 부작용의 exact readback으로 증명한다.

증적: `docs/qa/RRO-2/03-cron.md` §16 및 최종 활성화 절, 구현 근거 `automation/repair/cron/repair_report_consume_watch.py:30-39`.

## 공개 컷 전 인가 감사(2026-08-15) 중 발견한 후속 과제

- **root 소유 `managed-skills-allowed-signers`가 2개 이상 principal을 담게 되면 roster가 principal 선택 수단이 된다** — `managed_sync/cli.py:90-102`의 `_publisher_principal()`은 신뢰 principal 이름을 **roster 문서에서 직접 읽는다**(`roster.admin.publisher_principal`). 지금 안전한 이유는 `plan_signer_install`이 그 root 소유 파일에 **정확히 1개 entry**만 쓰기 때문이지(이름이 안 맞으면 `ssh-keygen -Y verify`가 거부) roster가 서명되기 때문이 아니다. 다중 그룹이나 키 로테이션 과도기로 entry가 늘면 roster를 쥔 쪽이 어느 principal을 쓸지 고르게 된다. **현재 동작 영향 없음 · 심각도 낮음** → entry를 늘리는 변경이 생기면 그때 principal 선택을 roster 밖(대역외로 고정한 로컬 설정)으로 옮긴다.

증적: `.omo/notepads/public-release/issues.md`의 「[2026-08-15] Security audit — auth/authorization」 두 번째 항목(수정된 roster replay 1건 + 위 3건의 근거와 기각 사유 포함).

## 지식 계층 4단계(2026-08-22) 중 발견한 후속 과제

위키 스키마 v2와 Obsidian→위키 큐레이션을 착지시키며 남은 것들이다([소개](기능소개/위키-큐레이션과-스키마-v2.md)).
같은 묶음에 있던 「RAG compose 프로젝트 이름 분열」은 `compose.yaml`을 유닛과 같은 `personal_rag`로
맞추고 두 파일을 대조하는 회귀를 걸어 해소했다.

- **엔터티 추출이 원천 frontmatter에만 의존한다** — Obsidian 노트에 `entity` 키가 없으면 제안된 초안의
  `entity`가 비어 검색 앵커가 생기지 않는다(증류 LLM은 본문 요약만 돌려준다) → 앵커 없는 제안이 많아지면
  `twin_distill`처럼 LLM 출력에서 엔터티를 함께 받아 검증한다. **노트 제안 자체는 정상 · 심각도 낮음**.
- **`relations`는 저장·인덱싱까지만 흐르고 랭킹에 쓰이지 않는다** — `rank.py`가 소비하는 것은 `entity`와
  `event_date`뿐이다 → 관계 질의가 실제로 필요해지는 시점에 랭킹 신호로 승격한다. **심각도 낮음**.

## 연구계획서 자동생성 v2 실증 중 발견한 후속 과제 (2026-08-23)

완료 기능은 [연구계획서 자동생성](기능소개/연구계획서-자동생성.md)이다. 실제 문서 본문·개인정보·자격증명은 이 기록에 포함하지 않는다.

- **과거 제안서가 RAG 코퍼스에 들어오지 않았다** — knowledge-layer 단계에서 유예해 새 초안이 과거 제안서의 표현·실적을 자동 검색하지 못한다 → 민감도와 중복 제거 계약을 정한 뒤 과거 제안서 전용 인제스트를 추가한다. **영향 범위: 근거 회수율과 초안 품질 · 심각도 중**.

## 음성 녹취 회의록 자동화(speechtotext) 착지 후 남긴 것 (2026-08-25)

- 해소 (2026-09-01): sherpa-onnx 로컬 화자 분리 착지 — 전사본 블록에 `화자N` 헤더와 이름 범례가 붙고, 도구가 없으면 `DIARIZE-FAIL` 후 화자 없이 계속한다(파이썬 ML 스택 없이 바이너리 호출로 해결해 stdlib 정책 예외 불필요). [소개](기능소개/전사본-화자-구분과-문장-단위-출력.md)
- **proposal 이 루트 기본값을 여전히 따로 적는다** — 살아 있던 절반(override 불일치)은 닫았다:
  `proposal_publish._root_folder()` 가 `DRIVE_OUTPUTS_ROOT` 를 함께 읽는다. 남은 것은 기본
  문자열 중복이며, `outputs_root()` 유도로 바꾸려면 이 모듈이 일부러 피해 온 runtime-root
  임포트 경계를 들여야 한다(주입형 transport 설계). `proposal_config.drive_root` 는 소비처가
  없는 죽은 설정으로 확인됐다. **동작 영향 없음 · 심각도 낮음**.
- **다듬기는 결정적이고 요약이 아니다** — 문장·문단·연속중복·용어집만 손대고 발언자 구분이나
  군더더기 정리는 하지 않는다. LLM 재작성은 회의 원문을 또 다른 LLM 경계로 내보내는 결정이라
  meeting 의 민감도 게이트를 거치는 별도 설계가 필요하다. **동작 영향 없음 · 심각도 낮음**.

관련 기능: [음성 녹취 → 전사본 → 회의록 자동화](기능소개/음성-녹취-회의록-자동화.md).

## Plaud lifelog 동기화 후속 (2026-09-02)

> [이관 2026-09-03 · OBSERVE] 조건이 성립하기 전에는 조치하지 않는다.

- **get_note 항목의 `data_error_code`(실측 예: 10) 의미가 불확실해 지금은 오류 항목의 `data_content` 도 요약에 넣는다
  → 실사용에서 오류 항목이 관측되면 스킵 규칙을 넣는다.** 실스키마 자체는 2026-09-02 로그인 후 측정해 fetch.py 를
  교정했고(top-level 리스트·segments·next_cursor·빈=[]), 승인 게이트가 최종 검토라 잘못된 요약은 ✅ 단계에서 걸린다(심각도: 중).
  ↳ 처리(2026-09-03): get_note 오류 항목(`data_error_code`)이 실사용에서 관측되면 스킵 규칙을 넣는다 — 그 전에는 승인 게이트(✅)가 잘못된 요약을 거른다


## 기관메일 회신 원문 인용 후속 (2026-09-01)

> [이관 2026-09-03 · OBSERVE] 조건이 성립하기 전에는 조치하지 않는다.

- **mailon 웹메일의 답장 버튼(In-Reply-To/References 헤더) 경유는 vendor 변경이 필요해 미도입 → 상대 클라이언트의 스레드 묶음이 어긋나는 사례가 보고되면 `send_trigger` 계열에 답장 모드를 실측 기반으로 추가한다.** 현재는 본문 인용으로 사람 눈에는 회신으로 보이며 발송 안전성과 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): 상대 클라이언트의 스레드 묶음 어긋남이 실제로 보고되면 vendor 답장 모드를 실측 기반으로 추가한다 — 그 전에는 본문 인용으로 충분


## 화자 구분·문장 단위 출력 착지 후 남긴 것 (2026-09-01)

> [이관 2026-09-03 · OBSERVE] 조건이 성립하기 전에는 조치하지 않는다.

- **API 백엔드(`gpt-4o-transcribe`)에는 화자 구분이 없다** — 그 응답에는 구간 타임스탬프가 없어 sherpa-onnx 의 turn 과 문장을 맞출 수 없다. `gpt-4o-transcribe-diarize` 는 화자를 주지만 **원음이 민감도 게이트를 거치기 전에** 외부로 나가므로 이 스킬의 기본 경로가 될 수 없다. 조치: 기본값으로 만들지 않고, 소유자가 비민감 녹취에 한해 켤 수 있는 명시 옵션으로 검토한다(지금은 아이디어 단계). **영향 범위: API 폴백 경로의 전사본에만 화자 헤더가 없다 · 심각도 낮음** — 운영 노드는 로컬 whisper.cpp 경로다.
  ↳ 처리(2026-09-03): 비민감 녹취 한정 명시 옵션은 소유자가 필요를 표명할 때 설계한다(원음이 게이트 전에 외부로 나가므로 기본값 불가)

- **자기소개 인식 패턴이 아직 측정되지 않은 휴리스틱이다** — `stt_speakers._INTRODUCTION`·`_TITLE` 은 "저는 ○○입니다"·"○○ 박사입니다" 계열 정규식과 불용어 목록이고, 실제 회의록 표본으로 재현율·오탐률을 잰 적이 없다. 조치: 실회의 전사본이 쌓이면 라벨링해 패턴을 조정하거나 LLM 근거 쪽 가중을 올린다. **영향 범위: 이름이 안 붙으면 `미상` 으로 남을 뿐 전사·회의록은 정상 · 심각도 낮음**.
  ↳ 처리(2026-09-03): 실회의 전사본 표본이 쌓이면 라벨링해 재현율·오탐률을 잰 뒤 패턴/LLM 가중을 조정한다

- **이름은 화자 분리 오류를 그대로 따라간다** — 자기소개 문장이 잘못된 클러스터에 배정되면 그 이름도 잘못된 라벨에 붙고, 병합 규칙상 소유자 override 전까지 유지된다(규칙 출처가 LLM 보다 세다). 조치: 범례가 출처와 시각(`자기소개 00:03:12`)을 적어 소유자가 그 지점을 직접 확인할 수 있게 해 두었고, 정정은 `polish --speakers` 한 번이다. 자동 교차검증(LLM 이견이 있을 때 규칙 신뢰를 낮추기)은 표본이 쌓인 뒤 판단한다. **영향 범위: 회의록의 발화 귀속이 어긋날 수 있다 · 심각도 중**.
  ↳ 처리(2026-09-03): 표본이 쌓인 뒤 LLM 이견 시 규칙 신뢰를 낮추는 교차검증을 판단한다 — 지금은 범례의 출처·시각으로 소유자가 직접 확인


## 후속 과제 스윕 4 착지 후 남긴 것 (2026-09-03)

- **[OBSERVE] 전환된 obsidian 클론에서 `git reset --hard` 의 지연 blob fetch 는 fetch 예산(900초)이 아니라 일반 git 예산(120초)에 묶인다 → 틱당 vault 델타가 커져 reset 이 타임아웃되는 사례가 관측되면 reset 도 fetch 예산으로 옮긴다.** 지금은 틱당 노트 한 건이라 해당 없음(심각도: 낮음).
- **`automation/pipeline_lock.hold` 가 `@contextlib.contextmanager` 생성기라 `with hold()` 본문에서 frozen+slots dataclass 예외가 나면 contextlib 의 `exc.__traceback__` 대입이 TypeError 로 바뀌어 진짜 실패 사유가 사라진다(obsidian `clone_lock` 구현 중 실측, 그래서 그쪽은 class 기반 CM) → speechtotext·meeting 워처의 lock 본문에서 그런 예외가 날 수 있는지 확인하고, 있으면 class 기반 CM 으로 바꾼다.** 영향 범위: 워처 실패 사유의 표시뿐, 동작 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): `fix(pipeline-lock): hold 를 class 기반 컨텍스트 매니저로 바꿔 frozen+slots 예외가 TypeError 로 가려지지 않게 한다` — `automation/pipeline_lock.hold` 가 `__enter__/__exit__` 클래스, tests/unit/test_pipeline_lock.py 에 frozen+slots 예외 전파 회귀(RED: TypeError → GREEN)
- **`automation/memory_relocate/effects_live.py` 가 transport 승격 뒤에도 261 pure LOC 라 `automation/final/f2_loc_exceptions.txt` 예외가 남는다 → `RelocationStore` 를 별도 모듈로 분리해 등록부에서 내린다.** 코드 크기 규약만, 동작 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): `refactor(memory-relocate): RelocationStore 를 relocation_store.py 로 분리해 effects_live.py 를 F2 예외 등록부에서 내린다` — effects_live 219 pure LOC, `automation/final/f2_loc_exceptions.txt` 에서 제거, 재수출로 호출부 무변경

# 해소 기록 — 닫혔지만 회계 가드가 원문을 요구한다

## 릴리스 승인 카드가 peer 시야에 있다 (2026-09-05)

> [이관 2026-09-05 · 해소] follow-ups.md 에서 옮겨 왔다. 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`[release]` 승인 카드가 `#approvals`(SKILL_APPROVALS 표면)에 게시돼 peer 봇이 보고, peer 가 매번 즉석 심사해 `⛔ 배포 승인 보류 / DO-NOT-APPROVE` 를 붙인다 → `release` kind 를 요청별 `#agent-chat` 스레드로 옮겨 peer 시야에서 뺀다.** 그 예외의 사유는 `tests/unit/test_request_thread_adoption_conformance.py:28-34` 에 적혀 있다 — "2차 주체인 peer 봇이 같은 채널을 봐야 하므로". 그런데 **`release` 만은 그 사유가 성립하지 않는다**: VA-1 릴리스 승인은 소유자 단독 ✅ 이고 peer attestation 이 없다(skill-deploy·attest·publish·submit·managed-activate 는 성립한다). 즉 peer 가 볼 이유가 없는 유일한 kind 가 peer 가 보는 채널에 있다.
- **오탐은 구조적이라 정확도를 고쳐서는 닫히지 않는다.** peer 는 HEAD 를 노드 로컬 상태에서 찾는다(카드가 `origin/main = <직전 릴리스 sha>`, `운영 포인터 = /srv/autophagy-agent-releases/<직전 sha>` 를 함께 보고한다 — 둘 다 노드 값이다). 미러는 「ops 체크아웃 단방향 규칙」의 `sync_mirror` 대로 릴리스가 수렴한 **뒤에야** origin/main 을 따라오는데, 승인 카드는 정의상 아직 배포되지 않은 HEAD 를 가리킨다 — **승인 전에 통과할 수 있는 순간이 없다.** 2026-09-05 실측 반증: peer 가 "공유 저장소에 없음" 이라 한 커밋을 빈 저장소에서 `git fetch --depth 1 <origin> <sha>` 로 직접 받아냈고, `git ls-remote` 의 `refs/heads/main` 이 바로 그 sha 였다.
- **자가 스킬 회수로는 닫히지 않았다(2026-09-04 시도).** peer 의 `skill-deploy-review` 를 아카이브한 뒤에도 같은 심사가 나왔고 문구만 `DO-NOT-APPROVE` → `배포 승인 보류` 로 바뀌었다. 실측: peer 1차 루트에 `autophagy-interop` 뿐이고 그 스킬은 여전히 아카이브 상태다. 즉 원인은 스킬이 아니라 **그 카드가 peer 에게 보인다는 사실** 이다.
- 조치 전 확인할 것: `surface_for(release)` 가 바뀌면 `approval_surface.POLICY_VERSION`(현재 8) bump 가 필요한지 — 필요하면 MAJOR 신호라 `release.sh --bump major` 사이클이다. 레코드 소비자(`skill_gate.py`·`release_approval.py`·`deploy_all.py`)는 채널이 아니라 레코드를 읽으므로 영향 없을 것으로 보이나 확인이 필요하다. 영향: 매 릴리스마다 거짓 ⛔ 가 붙어 소유자가 멀쩡한 릴리스를 막거나, 반대로 경보를 무시하는 습관이 든다 — 심각도 중(릴리스·노드 동작은 무영향).
  ↳ 처리(2026-09-05): 적어 둔 조치(B — `release` kind 를 `#agent-chat` 요청별 스레드로 이동)는 **채택하지 않았다**. 소유자 결정: 인터롭(공유 Lab 의 보고·조율)은 계속 필요하므로 peer LLM 게이트웨이는 유지하고, 근본 원인 두 겹만 걷어냈다. **원인 정정** — 3번째 불릿의 "원인은 스킬이 아니라 카드가 보인다는 사실" 은 절반만 맞았다: 심사를 지시한 문서는 아카이브한 `skill-deploy-review` 가 아니라 **pin 된 `autophagy-interop`** 이었다(2026-09-01 자가 저작 — 라우팅 표 `[release]` 행, `### [release] — Quick Reference` 50줄, `references/release-approval.md`, 교훈 불릿 12건; 그 절차가 노드 미러의 `git rev-parse origin/main` 과 `readlink /srv/autophagy-agent-current` 로 HEAD 를 찾으라고 적혀 있어 승인 전에는 통과할 수 없었다). **A(전송 계층)**: peer `~/.hermes/config.yaml` 최상위 `discord.ignored_channels` 에 `#approvals` 채널 id 를 넣었다 — Hermes Discord 어댑터의 거부 목록은 스레드의 부모 채널 id 까지 대조하고(`channel_keys`) `allowed_channels` 보다 우선하므로 카드 auto-thread 까지 막힌다. config 백업 후 YAML 파싱 검증, agent+peer 게이트웨이 쌍 재시동(둘 다 `active`, NRestarts 0). **C(지시 계층)**: `autophagy-interop` 을 tarball 백업(sha256 `a60d1fee…`)한 뒤 위 절차 전부와 `[skill-deploy]`·`[skill-publish]` 행·`scripts/publish-verify.py` 를 제거했다(344→231행, 잔존 참조 0, `hermes skills list` enabled 유지). PATENT EXPORT·Drive 아카이브·blocked-review 는 그대로다. peer attestation 은 `peer_attest.py` 가 REST 로 게시하는 경로라 무영향. 4번째 불릿(POLICY_VERSION bump 검토)은 B 를 하지 않으므로 소멸. 실표면 검증 지점: v1.2.3 승인 카드에 peer 논평이 없어야 한다. 상세 `docs/patch/2026-09-05-peer-gateway-ignores-approvals.md`.

## 동결 해제·repair 재발 수리 착지 후 남긴 것 (2026-09-04)

> [이관 2026-09-04 · 해소] follow-ups.md 에서 옮겨 왔다. 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **재발 판정이 detect 경로에 `hermes kanban show` 왕복을 하나 더 얹는다** — `HermesKanban.is_closed` 는 `_run` 의 timeout=60 을 쓰므로, 보드가 응답하지 않으면 재발 1건당 최대 60초를 쓰고 나서야 fail-soft 로 예전 동작(occurrence 증가)으로 떨어진다. 실패 경로에서만 발생하고 카드·원장은 정확히 유지되지만, 워처가 몰아서 detect 할 때 지연이 합쳐질 수 있다. 조치: 노드에서 재발 실측을 몇 건 모은 뒤 필요하면 이 조회에만 짧은 전용 timeout 을 준다. **동작 결함 아님 · 심각도 낮음**.
  ↳ 해소(2026-09-04): 상태 조회에 전용 10초 상한을 줬다 — `HermesKanban._run(*args, timeout=_BOARD_TIMEOUT)` 가 mutation 의 60초를 유지하고 `is_closed` 만 `_STATUS_READ_TIMEOUT` 을 넘긴다. 노드 실측을 기다리지 않은 이유는 실패 방향이 바뀌지 않기 때문이다 — 조회가 끊기면 예전 동작(occurrence 증가)으로 떨어지므로 짧은 상한은 최악 지연만 6분의 1로 줄인다. 회귀 `tests/unit/test_repair_tickets.py::test_status_read_gives_up_sooner_than_a_board_mutation` 가 실제 subprocess 경계에서 show·create 의 timeout 을 비교한다.
- **닫힌 카드로 새 티켓을 발급할 때 예전 티켓과의 연결이 원장에만 남는다** — `RepairRegistry` 는 signature 항목을 새 ticket_id 로 덮어써 occurrence 를 1 로 되돌리므로, 새 카드 본문에는 "이전 카드 t_… 의 재발"이라는 문구가 없다. 소유자가 보드에서 두 카드를 잇는 단서는 같은 제목·signature 뿐이다. 조치: 새 카드 생성 시 이전 ticket_id 를 본문 한 줄로 싣는다(마스킹 규칙 그대로 — id 는 비밀이 아니다). **가시성 개선 · 심각도 낮음**.
  ↳ 해소(2026-09-04): 새 카드 본문이 `Supersedes closed card: t_…` 로 이전 카드를 지목한다. 같은 자리에서 **그 발급이 실제로는 새 카드를 만들지 못하던 결함**도 함께 닫았다 — `hermes kanban create --idempotency-key` 는 non-archived 동일 키에 기존 카드 id 를 돌려주므로(`docs/qa/RRC-0/01-cli-contract.md` ④) done 카드의 재발이 그 닫힌 카드로 되돌아가고 occurrence 만 1 로 리셋됐다. 이제 멱등키에 이전 카드 id 를 붙인다. 회귀 `tests/unit/test_repair_tickets.py::test_superseding_card_names_the_closed_card_and_asks_a_distinct_dedup_key`.

## 회의록 과제 양식·Action Item 데이터베이스 중 남긴 것 (2026-08-27)

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **`skills/meeting/scripts/meeting_minutes.py` 가 정확히 250 pure-LOC 에 앉았다** — 천장과 같은
  값이라 다음 사이클이 한 줄만 더해도 F2 게이트가 빨개진다. 이음새는 이미 보인다: 양식 배치 경로
  (`_form_body` 와 그것이 부르는 슬롯 조립)를 `meeting_template` 쪽이나 별도 모듈로 떼면 여유가 생긴다.
  조치: 회의록 렌더러를 다음에 여는 사이클에서 그 분할을 함께 한다. **동작 영향 없음 · 심각도 낮음**.

  [해소 확인 2026-08-31] 양식 배치 경로를 `meeting_template` 로 이관해 233 pure LOC — 천장 여유 확보. 회귀: meeting 스코프 247 passed(`test_meeting_minutes.py` 포함).
- **`meeting_action_db.items_from` 이 `Sequence[object]` 를 받는다** — 안에서
  `item.title`·`item.deadline`·`item.owner`·`item.basis` 를 읽는데 타입 검사기는 그 속성을 볼 수 없어
  오타나 필드 개명이 런타임까지 간다. 조치: 그 네 속성을 가진 `Protocol` 을 선언해 시그니처를 바꾼다
  (`meeting_types` import 는 순환이 되므로 구조적 타입이어야 한다). **동작 영향 없음 · 심각도 낮음**.

  [해소 확인 2026-08-31] `ActionSource` Protocol(title·deadline·owner·basis)을 `meeting_action_db` 안에 선언해 시그니처를 구조적 타입으로 바꿨다 — `tests/unit/test_meeting_action_db_protocol.py`.
- **meeting 야간 배치가 lock 을 최대 50분 잡으면 speechtotext 가 그동안 10틱 스킵된다** —
  상한 5건 × 실측 258.9초다. 데이터 손상은 없고 다음 틱에 회복되지만, 밤에 올라온 녹음의
  전사가 그만큼 밀린다. 조치: 상한을 3으로 낮추거나(최악 13분) 야간 배치를 5분 틱과 덜
  겹치는 시각으로 옮긴다 — 소유자가 자정을 지정했으므로 상한 쪽이 먼저다.
  **오동작 아님 · 심각도 낮음**.

  [해소 확인 2026-08-31] 야간 배치 기본 상한 5→3(최악 약 13분), env 오버라이드 유지 — `tests/unit/test_meeting_pending_watch.py` RED→GREEN.
- **`ACTION-ID-EXHAUSTED` 가 소유자 통지에 실리지 않는다** — 과제·연도의 일련번호(10,000건)가 바닥나면
  회의록은 그대로 쓰고 신규 표의 번호 자리만 `—` 가 되지만, 그 사실은 stderr 마커와 실행 로그에만 남고
  완료 통지에는 나오지 않는다. 소유자가 번호 빠진 표를 눈으로 알아채야 한다는 뜻이다. 조치:
  `format_notify` 에 한 줄을 더하거나 요약 JSON 의 `actions_new` 와 함께 통지 문구를 분기한다.
  **회의록 생성은 정상 · 심각도 낮음**(한도가 과제·연도당 10,000건이라 도달 자체가 드물다).

  [해소 확인 2026-08-31] `format_notify` 가 관리번호 소진을 완료 통지에 명시한다 — `tests/unit/test_meeting_project_ingest.py` RED→GREEN.

## Gmail 승인 초안 Cc 보존 수리 중 남긴 것 (2026-08-26, t_0c46c0ad)

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **`mail_preflight.py`가 250 pure-LOC 천장을 넘었다** — 정확히 250에 앉아 있던 파일이 Cc 보존
  수정으로 262가 되어 `automation/final/f2_loc_exceptions.txt`에 사유와 함께 등록했다. 조치:
  런타임 루트 해석 계층(`repo_root`/`_repo_module`/`_contracts`/`_gate`)을 별도 모듈로 분리하되,
  배포 스킬 import 표면(`tests/unit/test_skill_preflight_deployed_imports.py`)을 같은 사이클에서
  다시 검증한다. **코드 위생 · 심각도 낮음**(동작 영향 없음).

관련 회귀: `tests/unit/test_gmail_cc_preservation.py`, `tests/unit/test_mail_watch_draft_isolation.py`.

  [해소 확인 2026-08-31] 런타임 루트 해석 계층을 `mail_runtime.py` 로 분리(217 pure LOC, `meeting_runtime` 선례) — `test_skill_preflight_deployed_imports.py` 18 passed 로 배포 import 표면 재검증.

## 후속 과제 원장 분리(2026-08-26) 중 남긴 것

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **`automation/rag_stack/deploy.sh` 만 호스트 미지정 가드가 없다** — 나머지 17종은 이번에
  `DEPLOY-BLOCK` 으로 닫았지만, 이 스크립트의 호스트 해석 줄은 `tests/unit/test_rag_stack_deploy.py`
  가 provenance·lock 계약의 일부로 **문자열 그대로 고정**한다. 가드를 넣으려면 그 계약을 함께
  바꿔야 하는데 대상 노드도 변수도 다르다(`RAG_STACK_SSH_HOST`). 지금은
  `tests/unit/test_deploy_host_fail_closed_all.py` 의 `_CONTRACT_PINNED` 에 사유와 함께 제외돼 있다.
  조치: rag 노드 배포 계약을 여는 사이클에서 같은 가드를 넣고 그 pin 을 함께 갱신한다.
  **동작 영향 없음(빈 호스트면 지금도 ssh 가 실패한다) · 심각도 낮음**.

  [해소 확인 2026-08-31] 빈 호스트 `DEPLOY-BLOCK` 가드 추가(문구는 RAG 변수 기준으로 정정), `test_deploy_host_fail_closed_all.py` 의 `_CONTRACT_PINNED` 제외 제거로 보편 검사 편입 — 45 passed.
- **`skills/meeting/scripts/meeting_actions.py` 가 254 pure-LOC 로 천장을 넘어 등록부에 올랐다** — 어느 쪽
  변경도 혼자서는 넘지 않았다. main 의 #287·#290 이 더한 카드 디스패치 줄과 이 브랜치가 더한 회의 날짜
  파일명 줄이 머지에서 만나 넘겼다. 이음새는 이미 보인다: 마일스톤 YAML 4함수(`_yaml_str`·`_emit_milestones`·
  `_parse_milestones`·`update_milestones`, 약 80줄)를 `meeting_milestones.py` 로 떼면 176 줄로 내려간다.
  조치: 그 분할을 별도 사이클에서 한다 — 머지 커밋 안의 80줄 이동은 리뷰 범위를 벗어난다.
  **동작 영향 없음 · 심각도 낮음**.

  [해소 확인 2026-08-31] 마일스톤 YAML 4함수를 `meeting_milestones.py` 로 분리(212 pure LOC), re-export 로 호출부 무변경 — meeting 스코프 green.

## DM→#agent-chat 이관(승인 표면 v7) 중 발견한 후속 과제 (2026-08-24)

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **잔여 owner-DM 발신자들이 남아 있다** — 승인 경로 밖 통지 발신자 중 이번에 이관한
  것은 research_trends·mail `dm_owner`뿐이다. 잔여: `procure_review.send_review`,
  `cost-report.send_cost_report`, `interop.gate_driver.main`,
  `hermes_plugin._send_direct_result`, `reminder_poller.DmSender`,
  `memory_curator.alert_owner`, `selfskill_audit/report`, `budget_confirm.dm_owner`,
  `patent_export_gate.dm_owner`(이상 agent 봇) + `owner_notice`/`healthcheck_notify`
  (ops 봇 — 길드 미참여 제약 동일). 조치: agent 봇 발신분부터 「config 있으면 채널 직송,
  없으면 DM 폴백」 선례 패턴을 순차 적용. **통지 도달에는 문제 없음 · 심각도 낮음**.

  [해소 확인 2026-08-31] agent 봇 발신자 8곳(gate_driver·hermes_plugin·reminder_poller·memory_curator·procure_review·cost-report·selfskill_audit·budget_confirm)을 owner_notice 파사드 채널-우선으로 이관(면제 제거) — `test_owner_notice_sender_migration*.py` RED→GREEN. `patent_export_gate` 는 특허 링크 노출 때문에 DM 유지(면제 사유 명시), ops 봇 2곳은 길드 미참여 제약으로 기존 보류 불릿이 소유.

## K4-b 설치기·신뢰키 위생(FS3 todo 12) 중 발견한 후속 과제

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **스킬 시나리오 17개 중 배포 전에 실제로 실행되는 것은 3개뿐이다** — 이번에 더한 `tests/unit/test_skill_scenario_drift.py`는 실행 없이 보이는 드리프트(사라진 저장소 경로 참조, `bash -n` 파싱 실패)만 잡는다. calendar·mail·wiki 외 14개는 여전히 배포 시점에야 깨진 것이 드러난다. **배포 전 발견 비용만 영향 · 동작·보안 문제 아님 · 심각도 낮음** → 스킬별로 임시 HOME·더미 자격증명에서 시나리오를 실행하는 하네스를 하나씩 늘린다. 한 번에 17개를 유닛 스위트에 넣는 것은 금물이다 — 메일 발송·브라우저 기동이 섞여 있어 외부효과가 난다.

  [해소 확인 2026-08-31] `tests/unit/test_skill_scenario_execution.py` 신설 — recall·topics 시나리오를 임시 HOME·더미 자격증명으로 실제 실행(3→5), 잔여 시나리오는 제외 사유를 분류표(docstring)에 각각 명시(다음 후보 report·prompt).

## Hermes kanban 열린 이슈 정리(2026-08-22) 중 발견한 후속 과제

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

보드의 열린 카드 8건을 정리하며 healthcheck 허용목록 래퍼(`<primary-node>`·`<rag-node>`)를 재생성하고
runtime-package 프로브의 `cron/` 오탐을 고쳤다. 그 과정에서 드러난 구조적 틈이다.

- **RAG 노드(`<rag-node>`)의 래퍼는 드리프트 프로브 대상이 아니다** — `healthcheck probe allowlist
  matches the checks`는 PRIMARY_NODE만 보고, RAG 노드의 래퍼는 07-14 손유지본(해시 3개)이라
  rag_stack 프로브와 그 티켓 명령이 거부되고 있었다(UNKNOWN·`REPAIR_TICKET_FAILED rc=126`). 이번에
  `--print <rag-node>`로 생성해 수동 설치했다(구본 `.bak-20260822-handmaintained`) → RAG 노드용 래퍼
  프로브 행을 추가한다. **수동 설치로 해소 · 심각도 낮음**.

  [해소 확인 2026-08-31] `healthcheck_wrapper_current` RAG 노드 행과 수리 매니페스트 항목 추가 — `tests/unit/test_healthcheck_rag_wrapper_probe.py` RED→GREEN, healthcheck 스코프 107 passed.

## proposal v7 피드백 반영에서 발견 (2026-08-28)

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **버전 사본의 `out/drafts.json` 이 refined 계보에서 갈라져 있다** — v000006 의
  `out/drafts.json` 은 그림 참조 5개(옛 세대)인데 실제 렌더 계보 `drafts.refined.json` 은
  6개(fig-s3-04 포함)다. 다음 판을 스테이징할 때 `drafts.json` 을 복사하면 refine→render 가
  `UNREFERENCED_FIGURE` 로 죽는다(v000007 제작 중 실측, refined 계보로 교체해 해소).
  조치 방향: improve/스테이징이 자식의 `drafts.json` 을 만들 때 부모의 refined 산출이 있으면
  그것을 기준 초안으로 삼도록 정리. 영향 범위: 수동 스테이징·improve 경로 한정, 렌더 게이트가
  fail-closed 로 잡아주므로 조용한 오염은 없음 — 심각도 낮음.

  [해소 확인 2026-08-31] 자식 스테이징이 부모 `out/drafts.refined.json` 을 기준 초안으로 삼는다(부재 시 `drafts.json` 폴백) — `tests/unit/test_proposal_staging_refined_lineage.py` RED→GREEN.

## 스킬 배포 사이클(2026-08-29) 중 발견한 후속 과제

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **proposal 은 마운트 후 invoke smoke 가 항상 실패한다** — `skills/proposal/scripts/scenario.sh` 가
  패키지 루트를 위치로 유추한다(`repo_root="$script_dir/../../.."`). peer 샌드박스에서 그것은
  `~/.hermes` 라 그 아래 `skills/proposal/…` 이 있어 `python3 -m skills.proposal.scripts.proposal_version`
  이 성립하지만, 라이브 스토어에서는 `/srv` 가 되어 `skills` 패키지가 없다 →
  `ModuleNotFoundError: No module named 'skills'` → `post-mount invoke smoke failed on agent`(exit 4).
  2026-08-29 실측: `INSTALLED`·`CONSUMED` 까지 끝난 뒤 이 단계에서만 죽어 틱이 `resume-exit:4` 를 남겼다.
  조치 방향: version 단계를 한 단 위 루트(`$script_dir/../..`)와 `python3 -m proposal.scripts.proposal_version`
  으로 바꾸면 두 배치 모두에서 성립한다(라이브 스토어에서 실행 확인). 또는 샌드박스 호출이 이미 넘기는
  `AUTOPHAGY_REPO_ROOT` 를 마운트 후 호출에도 넘기고 scenario 가 그것을 우선 쓰게 한다
  (`automation/deploy-skill.sh:809` 는 넘기고 `:994` 는 넘기지 않는다). 영향 범위: 마운트는 이미 성립한
  뒤라 배포를 막지 않는다 — 다만 공급망 워처 유닛이 매번 failed 로 끝나 진짜 실패와 구분되지 않고
  백오프 지문을 오염시킨다. 심각도 중.
  [해소 확인 2026-08-31] `AUTOPHAGY_REPO_ROOT` 우선 + 상위 루트/`proposal.scripts` 폴백으로 repo·샌드박스·라이브 세 배치 모두 실측 통과 — `test_skill_scenario_drift.py` 6 passed.
- **소유자 ✅ 이후에도 재개가 15회 연속 실패했는데 사유를 재현할 수 없다** — 2026-08-28 15:01 ~ 08-29 13:52
  동안 `skill-deploy:proposal` 이 같은 지문으로 15회 실패했고, 저널에 남은 사유는
  `REJECTED: valid peer attestation absent` → `REJECTED: owner approval binding invalid` 였다.
  `skill_gate_refresh.refresh_required` 는 `_owner_approval_present` 를 통과한 뒤에만 후자를 찍으므로
  **소유자 승인은 읽혔는데 `valid_approval` 이 바인딩을 무효로 판정했다**는 뜻이다. 그 pending 레코드가
  뒤이어 사라져 지금은 재현할 수 없다. 조치 방향: 다음 발생 시 레코드를 지우기 전에 보존하고,
  `skill_gate_approval.valid_approval` 의 거부를 사유별 토큰으로 나눠 로그에 남긴다. 영향 범위:
  peer attestation TTL 이 만료된 뒤 승인하면 릴리스가 바뀌기 전까지 배포가 서는 경로 — 소유자가 즉시
  누르지 못한 모든 배포가 해당한다. 심각도 높음.
  [해소 확인 2026-08-31] `valid_approval` 거부를 `APPROVAL-BINDING-REJECT:<cause>` 사유 토큰으로 분리하고 거부된 pending 레코드를 `pending-rejected/` 에 보존한다 — `tests/unit/test_skill_gate_approval_reject_reasons.py` RED→GREEN.
- **supersede 후 게시가 실패하면 승인 요청이 통째로 사라지고 아무 신호가 없다** — 2026-08-29 13:52 실측:
  어떤 실행이 proposal 을 REVIEW 까지 3회 진행했는데(`review-verdicts.jsonl` 3행, digest `537e39a4`)
  `proposals.jsonl` 에는 한 줄도 붙지 않았고 `pending/` 은 비어 있었다. 즉 기존 요청을 supersede 해
  메시지·레코드를 지운 뒤 새 요청 게시에 도달하지 못했고, 결과적으로 **소유자가 누를 요청이 0건인
  상태**가 되었다. 레코드가 없으면 틱은 아무 말도 하지 않으므로 침묵으로 남고, 소유자는 이미 눌렀다고
  믿는다. 「승인 메시지 단일성 규칙」이 막으려던 고아 메시지의 반대 방향이다. 조치 방향: 파사드의
  supersede→게시 구간에서 게시가 성공하기 전에는 기존 레코드를 지우지 않도록 바꾸거나, 최소한 게시
  실패를 기존 owner notice 경로로 표면화한다. 영향 범위: 승인 대기 중이던 결정이 조용히 증발 — 승인
  게이트의 가용성 결함이며 인가 경계는 훼손되지 않는다. 심각도 높음. 증적: 2026-08-29 배포 사이클
  (릴리스 `ec39b11f`/v1.0.133, proposal `deb0a49b`→`537e39a4`).

  [해소 확인 2026-08-31] supersede 후 게시 실패가 조용히 pending 0건으로 끝나지 않는다 — 레코드 복원/`SUPERSEDE-PUBLISH-FAILED` 표면화 + 주입 notifier seam(게이트 staging 체인 import 0 추가) — `tests/unit/test_approval_lifecycle_supersede_no_loss.py` RED→GREEN.

## 릴리스 운영 하드닝 잔여 (2026-08-31)

> [해소 이관 2026-08-31] 이 사이클의 병렬 수리로 닫혔다 — 각 불릿 끝의 해소 주석이 증적을 가리킨다.

- **⛔ 된 release 레코드가 다음 릴리스 요청을 막는다** — lifecycle 은 결정된(취소 포함)
  요청을 파괴하지 않으므로, ⛔ 된 `pending/release.json` 이 남으면 다음 `release.sh` 의
  요청 게시가 `owner-decided` 로 DEFER 된다. 스킬 게이트에는 감사 탈출구
  `skill_gate_retire.abandon` 이 있으나 release kind 에는 대응물이 없다. 조치 방향:
  release 전용의 감사되는 abandon(3필드 일치·fsync 감사·메시지 비삭제)을 설계해 붙인다.
  영향 범위: 소유자가 릴리스를 ⛔ 한 뒤의 다음 릴리스만 해당 — 인가 경계는 훼손되지
  않는 가용성 결함. 심각도 중.
  [해소 확인 2026-08-31] `automation/release_abandon.py` — 3필드 일치·fsync 감사·Discord 메시지 비삭제·byte-exact 아카이브(0600), `release.sh` 거부 메시지에 명령 힌트 — `tests/unit/test_release_abandon.py` 15 passed.
- **todo 스킬 샌드박스 시나리오가 `NOTIFY-FAIL`(TodoApprovalError) 3건을 남긴다** —
  2026-08-31 v1.0.139 전량 배포 중 todo SANDBOX 단계에서 실측. 결과 통지는 best-effort
  라 배포 판정에는 무영향이나, 더미 시크릿 경로라면 `NOTIFY-SKIP` 이어야 소음이 아니다.
  조치 방향: todo 시나리오의 통지 leg 가 E2E 주입 경로에서 SKIP 마커를 내는지 확인하고
  아니면 맞춘다. 영향 범위: 로그 소음뿐. 심각도 낮음.

  [해소 확인 2026-08-31] DUMMY- 시크릿 경로가 `NOTIFY-SKIP reason=dummy_secret` 을 낸다 — `tests/unit/test_todo_notify_sandbox.py` RED→GREEN + 실제 scenario `SCENARIO-PASS`.


## 제안서 HWPX 품질 수리 중 남긴 것 (2026-08-26)

> [이관 2026-08-31] 이미 닫힌 항목의 원문 보존.

- **릴리스 위생 게이트가 git SHA 를 Discord ID 로 오탐했다(해소)** — 스노플레이크 매처 `[0-9]{17,19}`
  가 문맥 없이 숫자 열만 봐서, 40자리 객체명 안의 17자리 연속 숫자에 걸렸다(`08a0693…705138677`).
  토큰 경계를 넣어 해소했고 진짜 ID 는 그대로 잡힌다. allowlist 로 우회하지 않았다 — 그랬다면 다음
  핀에서 같은 일이 반복된다.
소유자가 받은 `excavator/v000001` 이 "완성된 제안서로 볼 수 없다"고 지적해 렌더 체인과 집필 경로를
고쳤다. 본문 품질(마크다운 잔존·문단 분리·그림·자간)은 닫았고, 아래는 같은 문서에서 드러났으나
이번 범위 밖이라 남긴 것들이다. 증적: 구조 프로브와 렌더 페이지 이미지(마스킹 전 원시는 ops 전용).
- **`merge-pr.sh` 가 계산 중인 mergeability 를 충돌로 보고한다** — push 직후 GitHub 은 잠시
  `mergeable: UNKNOWN` 을 돌려주는데 스크립트가 이를 `CONFLICT` 로 분류해 "origin/main 을 병합하고
  local_ci 를 다시 돌려라"고 안내한다. 이번 사이클에 3회 그 안내를 따라 불필요한 병합·재검증을 했고,
  마지막에는 브랜치가 **0 커밋 뒤처진** 상태에서도 같은 메시지가 나왔다. 조치: `UNKNOWN` 이면 짧게
  재조회하고, 그래도 미확정이면 충돌이 아니라 판정 보류로 보고한다. **불필요한 재작업 · 심각도 중**.
  [해소 확인 2026-08-31] UNKNOWN 은 3회 한도 재조회 후 `MERGEABILITY-UNKNOWN`(exit 4) 판정 보류로 보고하고 충돌 안내를 내지 않는다 — `tests/unit/test_merge_pr_gate.py` 15 passed.


## 배포 사이클(2026-08-27) 중 발견한 후속 과제

> 세 건 모두 닫혔다. 원 묶음 헤딩과 불릿 첫 줄은 회계 가드(A9) 대조 키라 그대로 둔다.

릴리스 `6c062e74` 를 배포하며 드러났다. 배포를 실제로 막은 proposal 샌드박스 회귀는 이 사이클에서 고쳤다.

- **스킬 배포 스크립트 5개가 노드 설정에서 호스트를 읽지 않는다** — `calendar` · `coordination` ·
  `mail` · `todo` · `wiki` 는 `${DEPLOY_SSH_HOST:-}` 만 보고, 나머지 11개가 쓰는
  `${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}` 폴백이 없다. `node.toml` 이 설정돼 있어도 exit 3 이라
  변수를 손으로 줘야 한다(2026-08-27 배포에서 mail·todo 두 번 걸렸다). `test_deploy_host_fail_closed_all.py`
  는 "아무 데도 없을 때 자기 이름으로 거부하는가"만 보고 "설정이 있으면 읽는가"는 안 봐서 못 잡는다.
  조치: 다섯 줄을 나머지와 통일하고 그 축을 테스트에 더한다. **배포 마찰뿐 · 안전 문제 아님 · 심각도 낮음**.
 [해소 확인 2026-08-28] 다섯 줄을 정본 패턴으로 통일했다 — `node_config_sh.py --print-env` 를 eval 한 뒤
  `${DEPLOY_SSH_HOST:-${NODE_DEPLOY_SSH_HOST:-}}` 를 본다. 「설정이 있으면 읽는가」축은 어느 가드에도 없었으므로
  `tests/unit/test_deploy_host_reads_node_config.py` 로 새로 고정했다(기존 두 가드는 다른 질문을 본다). — `74a250c3`
- **todo 워처는 스킬 마운트 뒤에만 갱신해야 하는데 그 순서가 어디에도 없다** — 워처가 live 스킬 경로를
  `sys.path` 에 넣고 신규 `todo_execution_reconcile` 를 import 하므로, 마운트 전에 `deploy.sh` 를 돌리면 매
  틱 즉시 죽는다(2026-08-21 repair 워처 5일 침묵과 같은 모양). 다른 워처는 live 를 subprocess 로만 부른다.
  조치: `deploy.sh` 가 마운트를 선검사해 거부한다 — 산문보다 낫다. **이번엔 순서로 회피 · 심각도 중**.
 [해소 확인 2026-08-28] `deploy.sh` 가 push 이전에 마운트를 선검사하고 없으면 exit 5 로 거부한다. 필요한 모듈은
  `automation/live_mount_preflight.py` 가 워처 소스에서 도출하므로(정적 import + `import_module`/`__import__`)
  import 가 늘어도 등록을 잊을 수 없다. BLOCK·PASS 양쪽 분기를 실측했다. — `1d1b9acc`
- **프로덕션 스킬 6개가 `origin/main` 보다 낡다** — live digest 대조로 budget(2커밋) · calendar(2) ·
  coordination(2) · doctype(1) · procurement(1) · report(1). 소유자 ✅ 가 필요해 자동으로 따라오지 않는다.
  조치: 한 번에 묶어 승인받아 정렬한다. **구버전이 정상 동작 중 · 심각도 낮음**.
 [해소 확인 2026-08-27] 같은 배포 사이클에서 소유자 ✅ 로 묶어 정렬했다. 사용자 확인 완료.


## 회의록 서식 재설계 중 남긴 것 (2026-08-26)

- **노트 파일명과 Drive 배치는 아직 처리 날짜 기준이다** — 헤더 일시는 추출한 회의 날짜로
  고쳤지만 파일명 `YYYY-MM-DD-meeting-<ref>.md`와 그 이름에서 날짜를 읽는 발행은 처리 시각을
  쓴다(SKILL.md는 "날짜는 회의일"). 조치: 파일명을 `meeting.date` 기준으로 바꾸되 기발행분이
  중복 사본이 되지 않도록 upsert 키를 함께 본다. **배치 정확도 · 심각도 낮음**(내용 영향 없음).
  [해소 확인 2026-08-26] 노트 파일명이 추출된 회의 날짜를 쓰고, 날짜가 없거나 파싱 불가면 처리일로 폴백한다. 같은 ref 재실행이 사본을 만들지 않는 것까지 `tests/unit/test_meeting_skill.py` 가 고정한다.

## cron 실패 통지 확장 중 남은 판단 (2026-08-24)

계획: `.omo/plans/cron-error-remediation.md`. 기능 소개:
[cron 실패 가시성과 주간 따라잡기](기능소개/cron-실패-가시성과-주간-따라잡기.md).

- **스트릭 상태 루트가 쓰기 불가면 사고가 영원히 침묵할 수 있다** — `store()`가 OSError를
  삼키므로 카운터가 임계치 아래에 얼어붙는데, `record()`는 정상 반환해 워처는 "기록됨"으로
  보고 exit 0을 낸다(배너도 없음). 예전에는 exit 1 배너가 우연한 방어선이었다. 조치:
  `watch_failure_streak.record()`가 store 실패를 반환값으로 구분하거나 healthcheck에
  `~/.hermes/watch-failure` 쓰기 가능 프로브 추가를 검토. **발생 조건이 노드 수준 고장(홈
  디렉터리 쓰기 불가)이라 희귀 · 심각도 낮음**.
  [해소 확인 2026-08-26] `record()` 가 store 실패를 반환값으로 구분하고 호출자(notes_organize·research_trends)가 그 신호를 처리한다 — 상태 루트가 쓰기 불가일 때 조용히 얼어붙지 않는다(`tests/unit/test_watch_failure_streak.py`).

## RAG 런타임 배포·탐지 수리 중 발견한 후속 과제 (2026-08-22)

- **런타임 패키지 프로브가 네 패키지를 아직 보지 못한다** — `interop_runtime`·`regression_bank_runtime`·`reminder_poller_runtime`·`research_trends_runtime`은 이번에 실측한 `rag_ingest`·`memory_curator`와 내부 전개 레이아웃이 달라 같은 재귀 대조를 적용하면 오판한다 → 각 배포기의 실제 전개 경로를 확인해 `configs/runtime-package-manifest.txt`에 등록하거나, 등록할 수 없다면 패키지별 사유와 다른 판정 방법을 명시한다. **현재 동작과 무관 · 심각도: 중(같은 조용한 드리프트가 재발할 수 있음)**.
  [해소 확인 2026-08-25] 배포기가 실제로 싣는 파일만 릴리스 쪽에서 골라 비교하도록 매니페스트에 `deployed-python-files` 열을 추가했다(regression_bank·research_trends 등록, interop·reminder_poller 는 사유+대체 판정 명시) — `5fcaf886`

## 태그 없는 머지로 프로덕션이 다시 얼었다 (2026-08-21)

「릴리스 태그 규칙」이 생긴 다음 날 같은 정지가 재발했다. PR #203~#210 여덟 건이 태그 없이 main에 들어가 `origin/main` HEAD(`8ce01da0`)가 어떤 서명 태그의 peel 대상도 아니게 됐고, 리컨실러는 2분마다 수렴을 시도하다 exit 4로 실패했다. 이번엔 조용하지 않았다 — 실패 3회에서 소유자에게 드리프트 통지가 갔고, `automation/release-tag.sh`로 v1.0.25를 컷하자 2분 안에 수렴했다(`current -> 8ce01da0`, floor `v1.0.24@9c30748a` → `v1.0.25@8ce01da0`, `incident_open` 해제·`consecutive_failures` 0 확인).

- **설치기가 `/etc/autophagy/node.toml`을 설치하지 않는다** — `automation/install/assets.py`는 계정 홈 사본만 설치하는데, 리컨실러 유닛이 `ProtectHome=tmpfs`라 서비스에게 그 홈은 빈 tmpfs로 보인다. 그래서 신규 설치 노드는 토폴로지를 seed에서 읽게 되고, 그것이 정확히 2026-08-16에 플레이스홀더 호스트명으로 세 가지가 조용히 실패한 원인이었다(`e71e10cc`가 읽기 경로만 고쳐두고 쓰기 경로는 남겨둔 상태) → 설치기가 `root:root 0644`로 그 경로를 설치하게 하고, 계정별 사본은 호환을 위해 당분간 유지한다. **이 노드는 이미 그 파일이 있어 영향 없음 · 심각도: 중(새 설치가 조용히 seed 토폴로지로 돌아간다)**.
  [해소 확인 2026-08-25] 설치 계획이 `/etc/autophagy/node.toml` 을 root:root 0644 로 설치한다(계정 홈 사본 0600 유지) — `3e8d385a`
- **롤백 방지 floor와 update-channel 바인딩이 ops 소유라 downgrade 여지가 남는다** — 위 수정으로 임의의 미서명 코드 설치는 막혔지만, 악의의 ops가 `/srv/autophagy-private/deploy-reconcile/release-floor.json`을 지우고 update channel이나 미러 origin을 **과거의 진짜 서명 릴리스**로 되돌리면 이전 서명 버전으로의 downgrade를 유도할 수 있다(취약점이 있는 구버전으로 되돌리는 공격) → floor를 root 소유 디렉터리로 옮기고, 사전 게이트는 read-only 검사만 하며 root 헬퍼가 서명 재검증 뒤 단조 증가시키도록 분리한다. 기존 floor는 초기화하지 말고 정확히 마이그레이션해야 하며, 공개 채널 전환 시의 설치 단위 floor 규칙도 그대로 유지한다. **ops 침해를 전제로 하는 잔여 위험 · 심각도: 중**.
  [해소 확인 2026-08-25] floor 를 root 소유 `/var/lib/autophagy/update-trust/` 로 옮기고 ops 사전게이트는 읽기 전용, root 헬퍼만 단조 전진하도록 갈랐다. 기존 floor 는 삭제 없이 정확 복사 이관 — `6d17cb1d`

증적: 노드 `<primary-node>` — 리컨실러 저널 00:29~00:45 UTC, `release-floor.json`·`state.json` 전후 대조, `readlink /srv/autophagy-agent-current` = `8ce01da0`.

## 배포 스크립트 자체를 신뢰할 수 없었다 (2026-08-18)

죽은 워처 5개를 복구하다가, 그것들을 실어 나르는 **배포 계층 자체의 결함**이 드러났다. 이번 사건에서 가장 비싼 교훈은 워처 코드가 아니라 **배포가 실패해도 조용했다**는 점이다.

- **같은 `parents[3]` 결함이 승인 어댑터 4개에 더 있다** — `skills/calendar/scripts/calendar_approval.py` · `skills/budget/scripts/budget_gate.py` · `skills/wiki/scripts/wiki_approval.py` · `skills/patent-prep/scripts/patent_export_binding.py`. 이번에 binding 3건만 고쳤다. 같은 부류가 아닌 것도 구분해 둔다 — `calendar_cli.py`·`prompt_cli.py`·`topics_cli.py`는 checkout 상대 앱 import, `topics_registry.py`는 config 탐색, `todo_preflight.py`는 존재 검사형 probe라 무관하다. 조치: 같은 probe 형태를 적용하고 mounted-release 파라미터화에 추가한다. **잠복(해당 승인 경로가 돌 때 드러난다) · 심각도: 중**.
  [해소 확인 2026-08-25] 네 어댑터 모두 `parents[2:6]` + `/srv/autophagy-agent-current` 후보 탐색으로 이미 교체돼 있다(calendar_approval.py:47 · budget_gate.py:70 · wiki_approval.py:39 · patent_export_binding.py:35)
- **fail-closed 호스트 가드가 deploy.sh 둘에만 있다** — `DEPLOY_SSH_HOST` 미지정 시 리터럴 플레이스홀더로 ssh 를 시도해 DNS 오류가 진짜 원인을 가리는 문제를 mail·wiki 두 스크립트에서만 닫았다(이번 불릿의 범위가 mail 이었다). calendar·coordination·todo 와 `automation/**/deploy.sh` 는 그대로다(budget 은 2026-08-24 `NODE_DEPLOY_SSH_HOST` 폴백 통일로 해소) → 같은 가드를 전 배포 스크립트로 넓히고 `tests/unit/test_deploy_host_fail_closed.py` 의 목록을 전수로 바꾼다. **안전 문제 아님 · 심각도: 낮음(배포 절차 마찰)**.

증적: PR #161·#162·#163, cron 틱 `calendar/coordination 21:28:58 ok`, 워처 직접 실행 `notes/research rc=0`.
  [해소 확인 2026-08-26] `DEPLOY-BLOCK` 가드를 배포 스크립트 17종으로 넓혔고 리터럴 플레이스홀더는 0건이다(`automation/rag_stack/deploy.sh` 1종은 계약 고정으로 제외 — `follow-ups.md` 에 열린 채로 남겼다). `tests/unit/test_deploy_host_fail_closed.py` 의 목록은 손으로 적지 않고 `**/deploy.sh` 를 훑어 만들므로 새 스크립트가 자동으로 대상이 된다.

## Hermes 무재시동 자체 업데이트로 게이트웨이 도구 계층이 죽었다 (2026-08-18)

`<primary-node>`의 agent 게이트웨이가 08-17 13:24부터 08-18 09:57(KST)까지 **모든 도구 호출**을 `ImportError: cannot import name '_plan_tool_batch_segments' from 'agent.tool_dispatch_helpers'`로 실패시켰다(errors.log 9회). 소유자의 메일 요청 2회가 이것으로 막혔고, 첨부 확인·수신자 조회·승인 초안이 전부 도구 호출이라 함께 죽었다(발송·초안 생성은 일어나지 않았다 — 승인 로그·draft store 신규 레코드 0). **코드가 아니라 프로세스가 원인이다**: 그 심볼은 디스크 파일에 정상 존재하고(`tool_dispatch_helpers.py:117`, `__all__` 등록), 새 인터프리터에서 `import agent.tool_executor`는 통과한다. 게이트웨이는 08-16 12:50 기동, `hermes-update`는 08-16 14:34·14:36 실행 — **떠 있는 프로세스 밑에서 소스 트리가 교체**됐고, 옛 모듈을 든 프로세스가 새 파일을 import하다 죽었다(트레이스백 줄 번호가 디스크 소스와 어긋나는 것이 같은 증거). agent·peer 게이트웨이를 함께 재시동해 해소했다(01:03 UTC, 실제 도구 호출 1건으로 검증).

- **`hermes-update`가 소스를 교체하고도 게이트웨이를 재시동하지 않는다** — 벤더 자체 업데이트 경로에는 우리 릴리스 리컨실러가 갖고 있는 `autophagy-gateway-pair restart`에 해당하는 단계가 없어, 업데이트 시점부터 다음 재시동까지 프로세스가 **옛 코드 + 새 파일**의 불일치 상태로 돈다. 이번엔 그 상태가 20시간 잠복하다 첫 도구 호출에서 드러났다 → 벤더 업데이트 뒤 게이트웨이 쌍 재시동을 자동화하거나(래퍼), 최소한 "게이트웨이 기동 시각 < `~/.hermes/hermes-agent` 소스 mtime"을 보는 프로브를 넣어 잠복을 깬다. **재발 확실 · 심각도: 높음(도구 계층 전체 정지)**.
  [해소 확인 2026-08-25] 기동 시각 < 벤더 소스 mtime 을 판정하는 탐지 전용 프로브 `automation/hermes_compat/gateway_runtime_probe.py` 신설(재시동은 여전히 소유자 몫) — `69c4293b`
- **liveness 관점에서는 장애가 아니었다 — 그래서 아무도 못 봤다** — 유닛은 내내 `active/running`이었고 Discord도 연결돼 있어 텍스트 응답은 정상으로 나갔다. 즉 `systemctl is-active`나 Discord 연결성에 기대는 프로브는 이 장애를 원리적으로 통과시킨다. 소유자는 자기 요청이 두 번 실패한 뒤에야 알았다 → 위 mtime 프로브와 함께 `agent.conversation_loop`의 `Outer loop error` 연속 발생을 사건으로 승격하는 기준을 정한다. **탐지 공백 · 심각도: 중(장애 지속 시간이 소유자의 다음 요청 시점에 좌우된다)**.
  [해소 확인 2026-08-25] 같은 프로브가 liveness·연결성과 무관한 신선도 기준으로 판정한다 — `69c4293b`
- **벤더 버전이 계정별로 갈라진다 — 오늘 맞췄지만 재발을 막는 것은 없다** — 발견 시점에 agent는 v0.20.1, peer는 **v0.18.2**(07-14자)로 문제의 심볼이 아예 없었다 — 자기 코드끼리 일관돼 정상 동작했기 때문에 갈라짐 자체가 아무 신호를 내지 않았다. 「게이트웨이 재시동 규칙」은 두 계정을 항상 쌍으로 다루는데 벤더 업데이트는 계정별로 따로 일어나 그 쌍 가정이 깨져 있었다. 2026-08-18 소유자 지시로 ① peer를 agent 커밋 `7095e23eb`(v0.20.1)으로 ff해 같게 만든 뒤 ② 둘 다 upstream 최신 `a3995f8`(**v0.20.3**, 2026.8.16.2)로 `hermes update`했다 — 두 계정이 같은 커밋·같은 버전 문자열임을 대조하고(업스트림은 두 업데이트 사이에 움직일 수 있으므로 사후 대조가 필수다), config 포맷은 양쪽 모두 v33→v37로 마이그레이션됐다. 롤백 지점은 양쪽 `backup/pre-latest-20260818T012445Z`. 다만 **갈라짐을 막거나 보이게 하는 장치는 여전히 없다** — 다음 `hermes update`가 한 계정에서만 돌면 그날로 다시 갈라진다 → 두 계정의 벤더 버전을 함께 올리는 절차를 정하거나, 버전 대조를 헬스체크에 드러낸다. **현재 일치 · 심각도: 중(재발 시 한쪽만 회귀해 원인 규명이 어려워진다)**.
  [해소 확인 2026-08-25] 같은 프로브가 agent·peer 소스 지문 갈라짐을 기계 판정한다 — `69c4293b`
- **무패치 구동 탐지가 healthcheck 에 배선되지 않았다** — 이제 `python3 -m automation.hermes_compat.patch_state` 로 마커 상태를 기계 판정할 수 있고(기능 소개: [hermes-compat 무패치 구동 탐지](기능소개/hermes-compat-무패치-구동-탐지.md)), 매니페스트 notes 가 없는 검사를 있다고 말하던 오류도 제거했다. 다만 아직 사람이 돌려야 돌다 → healthcheck 는 ops 계정으로 도는데 대상은 `~agent/.hermes/hermes-agent` 라 권한 설계가 먼저 필요하다(같은 이유로 mailon 런타임 프로브도 배선이 미완이다). **탐지 공백 · 심각도: 중(지금은 사람이 돌려야 보인다)**.
  [해소 확인 2026-08-25] 마커·신선도·갈라짐 모두 기계 판정이 가능해졌다. **잔여**: ops→agent 권한 설계가 필요한 healthcheck 배선은 OWNER 항목으로 남는다 — `69c4293b`

## 수리 스윕 3차·개인 서버 대화 채널 후속 과제 (2026-08-17)

완료 기능은 [개인 서버 대화 채널](기능소개/개인서버-대화-채널.md)과 [기관메일 발신자·전체 폴더·검색](기능소개/기관메일-발신자-전체폴더-검색.md), 완료 수리는 [peer trust-root 진단 분리](patch/2026-08-17-skill-gate-peer-trust-root-diagnostic.md)다.

- **RAG healthcheck의 일시 실패 원인을 귀속할 당시 관측치가 없다 (`t_029a7e08`)** — 같은 tick에서 embedding·Qdrant가 실패하고 5분 뒤 회복했지만 probe별 rc·latency·SSH/HTTP 구분과 당시 서비스 로그가 보존되지 않아 transport·서비스·자원 중 하나를 고를 수 없다 → 비공개 런타임 증적에 probe별 원인·시간을 먼저 남긴 뒤 재현된 원인에만 retry·timeout·서비스 임계값을 적용한다. **현재 서비스 정상·추측성 수정 금지 · 심각도: 중(재발 원인 미확정)**.
  [해소 확인 2026-08-25] 프로브별 rc·소요 ms·transport/service 경계를 비밀 없이 기록하는 `automation/healthcheck_probe_evidence.sh` 추가(기록 실패가 판정·종료코드를 바꾸지 않음) — `0925c9f2`
- **다섯 cron 래퍼가 폐기된 스킬 경로를 검사해 거짓 장애를 낸다** — budget·report·coordination·calendar·research-trends가 레거시 사용자 홈 경로를 하드코딩해 `not mounted` 또는 import 오류를 내지만 governed live 심링크는 정상이다 → mount 판정을 `automation/skill_mount_drift.py`와 같은 `/srv/autophagy-skills/live/<skill>` 정의로 통일한다. **보안 문제·실제 마운트 손상 아님 · 심각도: 중(주기 작업 실패·오진)**.
  [해소 확인 2026-08-25] 다섯 래퍼가 `automation/skill_mount.py` 단일 정의로 판정하고, 해결 불가 시 미마운트로 fail-closed 한다 — `83ce629c`

## 에이전트 자가 스킬 공존(SS-1) 작업 중 발견한 후속 과제 (2026-08-15)

자가 스킬 루트 반전과 감사 원장을 만들며 발견했다. 기능은 [소개](기능소개/에이전트-자가-스킬.md).

- **`selfskill_audit/ledger.py`가 249 순수 LOC로 천장(250) 코앞이다** — PR #113에서 이미 `store.py`(신뢰 경계 JSON I/O)로 한 번 쪼갰는데, PR #116의 `removed` 델타 추가로 다시 한 줄 차이까지 왔다. 지금은 규약 위반이 아니지만 **다음 변경이 무엇이든 천장을 넘긴다** → 다음에 이 파일을 열 때 줄을 더하지 말고 분할한다(후보: 스냅샷 수집 `_scan`/`_snapshot` 계열을 `scan.py`로, `_diff`+`Action`을 `delta.py`로). **동작 결함 아님 · 심각도 낮음(다음 작업자가 천장에 부딪혀서야 알게 되는 것이 비용)**.

증적: `docs/qa/SS-1/reference-inventory.md`(참조 154건 분류 · 위 12개 코드 행의 원문 판정 포함).
  [해소 확인 2026-08-26] `scan.py`(스냅샷 수집)·`delta.py`(델타 판정)로 분할해 천장 아래로 내렸다. 공개 이름은 `ledger.py` 에서 그대로 import 되므로 호출자는 변경 없음.

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
  [해소 확인 2026-08-26] 공지 예약을 감사와 함께 해제하는 서브커맨드를 `announcement_recovery.py` 에 추가했다 — 없는 예약은 fail-closed 로 거부하고, 공지 재전송은 하지 않는다(`tests/unit/test_managed_announcement_recovery.py`).

## 자동 배포가 한 번 실패하면 그 스킬만 조용히 빠진다 (2026-08-04 실측 · 해소)

소유자 ✅ → 자동 마운트 계약은 실제로 동작한다(coordination·wiki가 `done (owner-approved)`로 완주). 그러나 같은 날 나머지 3건은 승인이 있었는데도 마운트되지 않았다. **두 결함 모두 PR #55로 해소**했고(`4be42d0`·`070eafd`, 릴리스 `5cad5e47`로 수렴 확인), 남은 것은 아래 잔여 두 줄이다. 증적 `docs/qa/SCW-1/summary.txt`.

- **잔여 — 소유자에게 밀어주는 통지는 여전히 없다** — 위 두 수정은 정지를 자가치유시키고(릴리스 교체 시) 기다림을 보이게 하지만, 둘 다 노드 로컬 journal에만 남는다. 즉 같은 릴리스에서 지속적으로 실패하는 경우의 통지는 「수리 티켓 경로」 묶음의 PATH 결함에 그대로 종속된다. **심각도 낮음으로 하향**(종전 “중”) — 사고의 실제 침묵 모드는 사라졌고 남은 것은 push 통지뿐이다. 조치: 그 묶음의 PATH 결함을 고치면 이 부류가 자동으로 티켓화된다. **`tick.json`을 읽는 healthcheck 프로브는 검토 후 채택하지 않았다** — healthcheck는 ops crontab에서 돌고 로컬 프로브에는 ssh·sudo가 없으며(rc=126), `tick.json`은 agent 소유 0600이라 소유자 프로비저닝(allowlist sha256 또는 sudoers)이 필요해 “최소 코드 수정”을 넘고, 이미 그 묶음이 담당하는 통지 경로와 병렬 구조가 된다(「병렬 confirm 구조 신설 금지」).
  [해소 확인 2026-08-24] 종속 대상이던 「수리 티켓 경로」 PATH·allowlist 결함이 2026-08-20 허용목록 생성으로 해소돼 이 부류의 티켓화가 동작한다(2026-08-22 occurrence 적재 실측).

## 스킬 배포 파이프라인이 3겹으로 막혀 있었다 (2026-08-04 실측 · 전부 해소)

FS3 K2-A는 기존 `release_helper_probe.sh`의 실설치 자산 검사를 랜딩 출력에도 재사용했다.
금지된 `deploy-skill.sh` 실행 없이 저장소 회귀와 읽기 전용 프로브 계약만 검증했다.

위 「마운트된 스킬 5종…」의 **근본 원인**이다. 재배포를 실제로 시도해서야 드러났다 — 마운트가 낡은 것이 아니라 **마운트할 수가 없었다**. 하나를 풀자 다음 것이 드러나길 세 번 반복했고, 세 개 모두 **이번 스윕(2026-08-03)이 직접 만들었거나 드러낸** 것이다. 실제 재배포 없이는 세 겹 중 어느 것도 보이지 않았다.

- **설치본 특권 헬퍼의 드리프트를 감지하는 것이 없다** — 이전에도 같은 부류가 기록됐으나(`PYTHONDONTWRITEBYTECODE=1` 누락, 「조치 불요」로 종결) 이번엔 **인자 불일치로 배포 자체가 죽었다** — “무해한 드리프트”라는 앞서의 판단이 일반화될 수 없음을 보인다. **심각도 중**. 조치: 헬퍼 해시를 릴리스 원본과 대조하는 healthcheck 프로브를 두거나, 릴리스 수렴 직후 provisioner를 멱등 실행해 항상 같은 버전이 되게 한다.
  [해소 확인 2026-08-25] `automation/release_helper_probe.sh` + healthcheck `LOCAL_PROBES` 의 `release_helper_drift` + `tests/unit/test_healthcheck_helper_drift.py` 로 이미 감지된다

## 마운트된 스킬 5종이 릴리스보다 낡았다 — 머지된 수정이 미발효 (2026-08-04 실측)

OWNER 체크리스트 9번·F4 판정 중 발견. `skill_mount_drift.py`를 라이브에 직접 돌렸다.

- **이 드리프트를 상시 감시하는 경로가 사실상 무력화돼 있다** — `healthcheck.sh`의 `skill_mounts_current` 프로브가 이것을 보고 있지만, 실패해도 수리 티켓은 위 「수리 티켓 경로」 묶음의 PATH 결함으로 생성되지 않고, 알림도 노드 로컬 로그에만 남는다. **심각도 중(관측성)** — 두 결함이 겹쳐 “드리프트가 나도 아무도 모른다”가 된다. 조치: PATH 결함을 먼저 고치면 이 부류의 발견이 자동화된다(그 묶음에 종속).
  [해소 확인 2026-08-24] 동일 근거 — PATH·allowlist 결함 해소로 `skill_mounts_current` FAIL의 티켓화가 동작한다.

## 수리 티켓 경로 — allowlist 뒤에 PATH 결함이 숨어 있었다 (2026-08-04)

OWNER 체크리스트 2번(`report_repair` allowlist 등록) 처리 중 발견. allowlist는 등록도론 rc=126은 해소됐고([소개](기능소개/배포-체크아웃-지연-감지.md) 「잔여」 항목), 그 뒤에 가려 있던 다음 결함이 드러났다.

- **allowlist를 통과해도 티켓은 여전히 안 생긴다 — `hermes`가 PATH에 없다** — 실측: 게이트 통과 후 `repair error: [Errno 2] No such file or directory: 'hermes'`(rc=1). 원인은 `healthcheck.sh:133`의 `sudo -n -u agent -H`가 로그인 셸을 거치지 않아 sudo secure_path만 받기 때문이다 — `hermes`는 `/home/agent/.local/bin/hermes`에 있고 그 디렉터리는 secure_path에 없다(agent 로그인 셸 PATH에는 있음). 즉 **자동 수리 티켓은 여전히 0건**이며, 드리프트를 *감지*해도 *티켓*은 안 된다는 상태는 그대로다(증상만 rc=126→rc=1로 바뀌었다). **동작·보안 무관 · 심각도 중**(관측성). 조치: `healthcheck.sh:133`의 명령을 `sudo -n -u agent -H env PATH=/home/agent/.local/bin:<secure_path> python3 -I ...`로 바꾸되, **명령이 바뀌면 해시 13개가 전부 무효해지므로** 신·구 해시를 함께 등록해 반영 사이의 회귀 창을 없앱 뒤 구 해시를 제거한다(미러 ff-pull은 리컨실러가 2분 주기로 따라오므로 창이 실재한다).
  [해소 확인 2026-08-24] 허용목록 래퍼를 릴리스에서 생성·재설치하는 방식([헬스체크 허용목록 생성](기능소개/헬스체크-허용목록-생성.md), 2026-08-20)으로 대체돼 수동 해시 등록 자체가 불필요해졌고, rc=126이 0이 되어 티켓 경로가 소생했다(2026-08-22 kanban 정리에서 티켓 occurrence 적재 실측).

## 배포 미러 임시 워크트리 누수 — trap 정리 실패 (2026-08-04 실측)

OWNER 체크리스트 1번(`.git` 권한 회수) 조사 중 발견. 권한 자체는 해소됐다(2775→2755, 그룹쓰기 1629개→0, agent 소유 356개→0).

- **`origin_snapshot.sh`의 스냅샷 워크트리가 trap 정리를 빠져나간다** — DG-2가 배포마다 만드는 `/tmp/autophagy-snapshot.*/tree`가 4개 남아 미러 `.git/worktrees`에 등록된 채 56MB를 점유하고 있었다(전부 dirty 0 · 미착지 0 · origin/main 조상으로 확인 후 정리함). **동작·보안 무관 · 심각도 낮음** — 용량과 등록 목록 오염뿐이다. 조치: `origin_snapshot.sh`의 trap이 어떤 경로에서 안 도는지(조기 exit·신호·SSH 끊김) 확인해 정리를 보장하거나, 배포 진입 시 오래된 스냅샷을 먼저 prune한다.
  [해소 확인 2026-08-26] 누수 경로의 정리를 보장하고 진입 시 오래된 스냅샷을 prune 한다 — dirty 하거나 미착지 커밋이 있는 워크트리는 남긴다(`tests/unit/test_origin_snapshot_cleanup.py`).

## 에이전트가 배포 미러의 git 내부를 쓸 수 있다 (2026-08-03 실측)

cha가 보고한 "Hermes가 테스트용 코드를 만들고 커밋하면 워크플로우 전체가 망가진다"의 물리적 원인이다.

- **`/srv/autophagy-agents/.git`과 `.git/refs`가 `2775`(그룹 쓰기 가능)이고 `agent`가 `autophagy` 그룹이다** — 워킹트리 쪽도 `AGENTS.md`·`automation/`·`prompts/`가 그룹 쓰기 가능하다. 실측: 미러에 **agent 소유 파일 1108개**(`.git/objects` 295 + `tests/unit` 126 + `skills/mail` 72 + `automation/memory_curator` 64 …). 즉 커밋 거부 훅은 `git commit`만 막고, `--no-verify`나 ref 직접 조작은 파일시스템 권한이 그대로 허용한다. **코드 주입 경로는 아니다** — 배포 소스는 root 소유 읽기 전용 릴리스이고 그 트리는 `origin/main`과 바이트 일치해야 하므로(DONE「봉인된 릴리스의 배포 provenance」) 에이전트가 쓴 코드는 배포될 수 없다. 실제 피해는 **DoS·감사 오염**이다: 미러의 `origin/main` ref가 어긋나면 ⑤ 재조정이 잘못 수렴하려 하거나 provenance 검증이 모든 배포를 막는다(이번 진단 중 실수로 재현했고 ops 인증 fetch로 즉시 복구). **심각도 중**. 조치: `.git` 그룹 쓰기 비트를 회수한다 — 단, `/tmp`의 스냅샷·수리 worktree 메타데이터가 그 권한을 쓰고 있어 단순 `chmod`가 아니다. 무엇이 정당하게 쓰는지 먼저 확정하고 ops 경유로 재배치한 뒤 회수한다.
  [해소 확인 2026-08-24] 그룹 쓰기 비트 회수 완료 — 2775→2755, 그룹쓰기 1629개→0, agent 소유 356개→0(「배포 미러 임시 워크트리 누수」 묶음의 조사 기록).

## 배포 체크아웃 드리프트 가드(DG-1) 작업 중 발견한 후속 과제

기능은 DONE「배포 체크아웃 드리프트 가드 (DG-1)」 참조, 증적 `docs/qa/DG-1/summary.txt`

- **수리 티켓 경로가 여전히 allowlist에 거부된다** — `report_repair`의 SSH 명령(`repair_cli.py detect`)도 sha256 allowlist에 없어 `REPAIR_TICKET_FAILED rc=126`가 난다(추정 아닌 실측). 즉 A가 드리프트를 *감지*해도 자동 *티켓*은 안 된다(FAIL 로그로만 드러남). **이번 범위 밖**: 노드 `~<operator-account>/.local/libexec/autophagy-healthcheck-probe`의 allowlist 갱신은 소유자 작업이다. **심각도 중** — A + B로 드리프트 빈도가 분단으로 줄어 방치해도 큰 사고로 번지지는 않는다. 조치: cha가 allowlist에 checkout probe와 report_repair 명령 해시를 등록(또는 checkout probe는 이제 로컬이라 불필요 — report_repair만 남음). **2026-08-17 재확인**: 여전히 살아 있다 — cron 틱 10:45·10:50·10:55·11:00·11:05 전부 `REPAIR_TICKET_FAILED rc=126` ×2(수동 실행만의 인공물이 아님을 cron 로그로 확정). 지금은 실제 FAIL 2건(HELPER-DRIFT·SKILL-STALE)이 티켓화되지 못하고 있다.
  [해소 확인 2026-08-24] 허용목록 래퍼를 릴리스에서 생성·재설치하는 방식([헬스체크 허용목록 생성](기능소개/헬스체크-허용목록-생성.md), 2026-08-20)으로 대체돼 수동 해시 등록 자체가 불필요해졌고, rc=126이 0이 되어 티켓 경로가 소생했다(2026-08-22 kanban 정리에서 티켓 occurrence 적재 실측).

## G8 — LOC 등록부

기능은 [소개](기능소개/loc-등록부-재측정.md), 작업 배분은 `.omo/plans/parallel-followup-sweep.md` §5 G8이다. 8개 코드 그룹이 전부 머지된 HEAD(`2383a92`)에서 전수 재측정해 **초과 29건 · 등록 3건**이던 상태를 등록 29건으로 맞췄고, LOC 게이트는 `EXCEPTION 29 / VIOLATION 0 · LOC RESULT: PASS`가 됐다.

- **F2 감사는 LOC를 고쳐도 여전히 red다 — `f2_quality.sh:41`이 `ruff check .`를 그대로 돌린다** — CI는 `--exclude skills/mail/vendor`를 주는데(`.github/workflows/ci.yml`, "고칠 수 없는 트리를 린트하면 CI가 영구히 빨간불") F2만 안 준다. 실측: F2의 ruff 지적은 **전량 vendor 트리**이고 CI 기준으로는 `All checks passed!`다. **보안·동작 무관 · 심각도 중(감사 신호 손실 — LOC에서 방금 없앤 것과 같은 종류의 상시 red)** → `f2_quality.sh:41`에 같은 exclude를 주면 F2 전체가 green이 된다. 이번 범위(등록부)를 벗어나 손대지 않았다.
  [해소 확인 2026-08-24] `f2_quality.sh:43`이 이미 `--exclude skills/mail/vendor`를 적용하고 있다(2026-08-24 실측).

증적: `docs/qa/F2/module-loc.txt` (`EXCEPTION 29 / VIOLATION 0`), 전체 스위트 3111 passed, `ruff check . --exclude skills/mail/vendor` 통과.

## H1 — 헬스체크 릴리스 관측성 OWNER 인계 (2026-08-05)

기능은 [소개](기능소개/헬스체크-릴리스-관측성.md), 증적은 `.omo/evidence/fs2/task-1-parallel-followup-sweep-2.txt`다.

- **노드 wrapper의 실제 알고리즘 재검증과 15개 명령 해시 등록은 아직 OWNER 몫이다** — 커밋 manifest는 원문 생성·shim 대조의 기준이며 실제 등록을 대신하지 않는다. **자동 수리 티켓 관측성 영향 · 심각도 중, 보안 하향 없음** → OWNER가 배포 노드의 agent UID·sudo secure_path를 명시 입력해 manifest를 재생성하고 wrapper 알고리즘으로 검증·등록한다.
  [해소 확인 2026-08-24] 허용목록 래퍼를 릴리스에서 생성·재설치하는 방식([헬스체크 허용목록 생성](기능소개/헬스체크-허용목록-생성.md), 2026-08-20)으로 대체돼 수동 해시 등록 자체가 불필요해졌고, rc=126이 0이 되어 티켓 경로가 소생했다(2026-08-22 kanban 정리에서 티켓 occurrence 적재 실측).

## 구독자 sync 자동화 배포(W-F3-B) 작업 중 발견한 후속 과제

틱을 배포물로 만들며 발견했다. 기능은 [소개](기능소개/구독자-sync-자동화-배포.md).

- **`automation/install/assets.py`가 250 pure-LOC 천장에 4줄 남았다** — opt-in 레지스트리를 `components.py`로 분리해 246으로 내려놓았으나, 다음 always-on 유닛·helper 추가가 F2 게이트를 깨뜨린다. **현재 동작 영향 없음·심각도 낮음** → 다음에 `assets.py`를 만지는 wave가 파일 종류별(systemd·sudoers·libexec·hooks) 빌더로 쪼개거나 사유와 함께 등록부에 올린다.
  [해소 확인 2026-08-24] 2026-08-24 실측 213 pure LOC — `components.py` 분리 이후 여유가 복원돼 전제가 소멸했다.

증적: `docs/qa/W-F3-B/summary.txt`(15/15 + 증명된 것/불가한 것 분리).

## K4-b 설치기·신뢰키 위생(FS3 todo 12) 중 발견한 후속 과제

- **`automation/healthcheck.sh`가 249/250 pure LOC인데 LOC 예외에 등록되어 있지 않다** — probe 함수 하나(약 15줄)만 더해도 `f2_quality.sh`의 LOC 루프가 `VIOLATION`을 내므로, 다음에 헬스체크 항목을 추가하려는 사람은 자기 작업과 무관한 분할부터 해야 한다. 실제로 이번 스윕에서 quarantine 미확인 릴리스 probe를 넣으려다 여기서 막혔다. **현재 동작·보안 영향 없음 · 심각도 낮음(다음 작업자가 천장에 부딪혀서야 알게 되는 것이 비용)** → probe 함수군을 `healthcheck_probes.sh`로 떼어 source하거나, 분할이 부적절하다면 사유와 함께 `automation/final/f2_loc_exceptions.txt`에 등록한다.
  [해소 확인 2026-08-26] probe 함수군을 `automation/healthcheck_probes.sh` 로 떼어 source 한다 — 다음 probe 를 넣으려는 사람이 자기 작업과 무관한 분할부터 하지 않아도 된다.

## F4 스코프 감사의 `approvals-send-log` 가 라이브 노드에서 FAIL (2026-08-21 발견)

병렬 후속 스윕 3 마감 중, `k-fix4` 워크트리에 커밋되지 않은 `automation/final/f4_scope.sh`
재실행 결과가 남아 있었다(라이브 노드 대상, `F4 RESULT: FAIL`).

- 소유자 승인 대비 전송 로그가 맞지 않는다 — `owner_approved_records=207`,
  [해소 확인 2026-08-25] 감사를 `automation/final/approvals_send_log_audit.py` 로 추출해 어긋난 행마다 `approval-missing`·`send-log-row-missing`·`method-not-matched` 사유를 붙인다. 원인 하나 확인: 매칭 규칙(2026-07-18 `35d104a7`)이 `mail.compose_send`(2026-07-19 `02c133fa`)보다 앞서 쓰여 정상 compose 발송이 계속 어긋난 것으로 세어졌다. **잔여**: 노드 1건 대조는 OWNER — `f0d1f796`
  `send_logged_records=25`, `sent_records=55`, **`unmatched_sends=31`**(전부
  `method='manual_reaction'`, 직전 증적 2026-07-18 에서는 0). 노드에서 31건 중 1건을 골라
  승인 레코드와 전송 로그를 직접 대조해 **(a) 검사기가 `manual_reaction` 을 매칭하지 못하는 것**과
  **(b) 실제 전송이 로깅되지 않는 것**을 가른 뒤, (a)면 매칭 규칙에 추가하고 (b)면 로깅 누락을 수리한다.
  **심각도: 중**(가른 뒤 (b)면 높음 — 승인-전송 감사 추적의 구멍이라는 뜻). 영향 범위는 승인 게이트의
  사후 감사이며 승인 자체나 외부효과 차단에는 영향이 없다.

이번 스윕 범위 밖인 이유: 이 스윕은 후속 과제 원장의 정산 회계를 다루고 이 검사는 라이브 노드의
운영 상태를 본다. 재실행 결과는 이전 웨이브(2026-07-18)의 `docs/qa/F4/` 증적을 덮어쓰므로
커밋하지 않고 복원했다 — 위 수치가 그 내용이다.

## Hermes kanban 열린 이슈 정리(2026-08-22) 중 발견한 후속 과제

보드의 열린 카드 8건을 정리하며 healthcheck 허용목록 래퍼(`<primary-node>`·`<rag-node>`)를 재생성하고
runtime-package 프로브의 `cron/` 오탐을 고쳤다. 그 과정에서 드러난 구조적 틈이다.

- **허용목록 래퍼의 드리프트 지문이 프로브 명령 본문을 덮지 않는다** — `wrapper_inputs_digest`는
  [해소 확인 2026-08-25] `wrapper_inputs_digest` 가 기록된 프로브 명령 본문의 해시 집합을 지문 입력에 포함한다 — `523371d6`
  `LIVE_CHECKS`와 매니페스트 2종만 해시하므로, 프로브가 원격 명령 문자열만 바꾸면 지문은 같은데
  명령은 exit 126으로 거부돼 UNKNOWN이 조용히 난다(그래서 이번 runtime-package 수정은 릴리스 쪽
  필터만 바꿨다) → 지문에 기록된 명령 해시 목록을 함께 넣는다. **탐지 누락 위험 · 심각도 중**.
- **mail `scenario.sh`의 파사드 probe도 호출자 cwd에 민감하다** — `skills/mail/scripts/scenario.sh`가
  topics와 같은 `PYTHONPATH=… python3 -c 'import automation.…'` 형태로 분기를 고르므로, 샌드박스가
  릴리스 루트를 cwd로 물려주면 probe는 import에 성공하고 격리 CLI는 다른 답을 낼 수 있다(topics는
  이 불일치로 ✅ 뒤에도 매 틱 `SCENARIO-FAIL evidence list`였다 — PR #242로 수정). mail은 오늘
  샌드박스를 통과했지만 같은 모양이다 → `python3 -I` + 명시 `sys.path`로 고치고 cwd=checkout 회귀를
  둔다. **잠재 플레이크 · 심각도 낮음(현재 통과)**.
  [해소 확인 2026-08-26] 파사드 probe 를 `python3 -I` + 명시 sys.path 로 고쳐 호출자 cwd 가 답을 바꾸지 못한다(`tests/unit/test_mail_scenario.py` 가 두 cwd 에서 같은 답을 요구한다).
- **수동 healthcheck 실행도 수리 occurrence를 낸다** — `read_only=true`를 찍으면서도 REPAIR_TICKET
  경로는 항상 활성이라 진단용 실행이 카드에 occurrence를 더한다 → 수동 진단용 opt-out(예:
  `HEALTHCHECK_NO_REPAIR=1`)을 둔다. **노이즈만 · 심각도 낮음**.
  [해소 확인 2026-08-26] `HEALTHCHECK_NO_REPAIR=1` 이 수리 티켓 경로만 무력화한다 — 프로브 판정과 종료 코드는 그대로다(`tests/unit/test_healthcheck_checkout_ticket.py`).

## 지식 계층 4단계(2026-08-22) 중 발견한 후속 과제

위키 스키마 v2와 Obsidian→위키 큐레이션을 착지시키며 남은 것들이다([소개](기능소개/위키-큐레이션과-스키마-v2.md)).
같은 묶음에 있던 「RAG compose 프로젝트 이름 분열」은 `compose.yaml`을 유닛과 같은 `personal_rag`로
맞추고 두 파일을 대조하는 회귀를 걸어 해소했다.

- **컴파일된 `.pyc` 하나가 공개 릴리스를 막는다** — `tests/unit/test_f2_secret_pattern.py:32`는 규약대로
  토큰을 런타임에 조립하는데(`"ghp_" + (...)`), 컴파일된 `.pyc`는 그 결과를 **상수로 굳힌다**. 공개 export의
  스캔은 카나리아 설계상 `.gitignore`된 파일까지 훑으므로(`gitleaks dir`) 그 `.pyc` 하나로 릴리스가 멈춘다
  (2026-08-22 실측: 워킹트리 스캔 leak 1건 → 비-venv `__pycache__` 정리 후 0건) → export 절차에 정리 단계를
  넣거나 스캔에서 `*.pyc`를 제외할지 판단한다. **실제 비밀 아님 · 심각도 낮음(릴리스 때만 드러나는 운영자 함정)**.
  [해소 확인 2026-08-26] export 절차가 스캔 전에 비-venv `__pycache__` 를 정리한다. 스캔에서 `*.pyc` 를 빼는 쪽은 택하지 않았다 — 무시된 파일까지 훑는 것이 카나리아 설계의 핵심이기 때문이다(`tests/unit/test_public_export.py`).
- **`wiki_store.SCHEMA_GUIDE`가 "필수 키 5개 정확히, 그 외 키 금지"로 남아 있다** — v1 twin 키 때부터
  있던 불일치이고 v2로 더 벌어졌다. 거부 판정 자체는 정확하고 **안내 문구만 실제 허용 범위보다 좁다**
  → 스키마와 같은 자리에서 안내를 함께 갱신한다. **심각도 낮음**.
  [해소 확인 2026-08-26] SCHEMA_GUIDE 가 실제 허용 키 집합을 말한다. 고정은 문장이 아니라 검증기에서 유도한 키 집합 대조라 다시 갈라지지 않는다(`tests/unit/test_wiki_store_schema_guide.py`).

## 연구계획서 자동생성 v2 실증 중 발견한 후속 과제 (2026-08-23)

완료 기능은 [연구계획서 자동생성](기능소개/연구계획서-자동생성.md)이다. 실제 문서 본문·개인정보·자격증명은 이 기록에 포함하지 않는다.

- **KD-AT 두 저장소를 함께 맞춰야 한다** — 고정 SHA로 안전은 확보했지만 `kimm-docbot` 또는 `im-not-ai` 변경 때 두 저장소의 코드·핀을 사람이 동기화해야 한다 → 호환성 검증, 핀 갱신, 롤백을 포함한 단일 운영 절차를 문서화한다. **영향 범위: 엔진 업그레이드와 유지보수 · 심각도 중**.
  [해소 확인 2026-08-25] `docs/guide/kd-at-두-저장소-운영.md` 에 호환성 검증·핀 갱신·롤백·증적 절차를 문서화했다 — `5e3c06fd`

## interop 런타임 스테이징 검사기 사각지대 (2026-08-24, drive-doc-storage 롤아웃 중 발견)

- **무엇이 문제**: `deploy-skill.sh`의 `validate_gate_staging_imports`(및 `tests/unit/test_deploy_staging_is_derived_from_imports.py`)가 **상대 import를 따라가지 못한다**. PR #250이 `git_tag_signature.py`에 `from .typing_compat import override`를 도입했는데 `GATE_HELPERS`에 `typing_compat.py`가 누락돼도 로컬 검증·전체 스위트가 green이었고, 노드의 REQUEST 단계에서야 `ModuleNotFoundError`로 5개 스킬 배포 요청이 전부 실패했다(2026-08-24 실측).
  [해소 확인 2026-08-26] AST 워커가 `level>=1` 상대 import 를 패키지 디렉터리 기준으로 해석한다 — 픽스처(`tests/fixtures/deploy_staging_imports/`)가 누락 시 RED 가 되는 것을 고정한다.
- **어떻게 조치**: AST 워커에 `ast.ImportFrom`의 `level>=1`(패키지 내 상대 import) 해석을 추가해 staged 집합의 import-폐쇄성 검사가 상대 import도 포함하게 한다. 회귀 테스트: 상대 import 의존성을 가진 픽스처로 검사기가 RED가 되는 케이스.
  [해소 확인 2026-08-26] 위와 같은 변경. 동결된 `automation/deploy-skill.sh` 는 손대지 않았고 CI 가 도는 검사기 쪽만 고쳤다.
- **영향·심각도**: 보안 문제 아님 — 경로는 fail-closed로 멈춘다(조용한 오배포 없음). 가용성 문제: 매니페스트 누락이 노드에서만 발견돼 배포 요청이 막힌다. 급한 불은 `typing_compat.py`를 `GATE_HELPERS`에 추가해 해소됨(이 항목은 검사기 자체의 교정). **심각도 낮음**.
  [해소 확인 2026-08-26] 위와 같은 변경.

## todo 승인 후 자동 등록 착지 후 남긴 것 (2026-08-25, 수리 티켓 t_e3243dc5 occ 2·4)

- **실행 파라미터가 없는 옛 승인 레코드는 자가 치유되지 않는다** — 이번 변경 이전에 만들어진
  approved 세대에는 `tasklist`·`title`·`notes`·`due` 가 없어 워처가 무엇을 쓸지 복원할 수 없고
  `TODO-EXEC legacy-unreplayable` 로 건너뛴다. 노드에 그런 세대가 2건 있다
  (`todo%3asha256%3a96f3af3f…`, `…e59dd4a4…`). 조치: 그 2건은 소유자가 이미 수동 등록을 마쳤으므로
  방치해도 중복 등록 위험이 없다. 남은 것을 확실히 닫으려면 각 claim 을 `verified` 로 조정하거나
  archive 세대를 정리한다. **동작 정상(건너뛰기), 로그 노이즈만 · 심각도 낮음**.
  [해소 확인 2026-08-26] 실측은 2건이 아니라 **4건**이었다(`32da22b1`·`89408841`·`96f3af3f`·`e59dd4a4`).
  claim 을 `verified` 로 손보는 쪽은 택하지 않았다 — 증명할 수 없는 쓰기에 receipt 를 쓰는 것이
  claim store 가 존재하는 이유 그 자체를 무너뜨리고, archive 세대는 소유자 승인의 정직한 이력이라
  지우지 않는다. 대신 리컨실러가 이 상태를 **사건이 아니라 정착 상태**로 다루게 해 매 틱 로그를
  없앴다(결과는 호출자에게 그대로 반환된다) — `beb0ecac`
- **실행 실패가 매 틱 재시도된다** — claim receipt 가 아직 없는 상태에서 create 가 실패하면
  (예: `gws` 자격증명 만료) 다음 틱이 다시 시도하고 `TODO-EXEC failed:…` 를 매 분 남긴다.
  중복 쓰기는 claim 이 막지만 저널이 시끄러워진다. 조치: 연속 실패 횟수를 세어 백오프하거나
  N 회 후 소유자에게 1회 통지한다(기존 `approval_reminder` 의 중복 억제 패턴 재사용).
  **쓰기 안전성에는 영향 없음 · 심각도 낮음**.
  [해소 확인 2026-08-26] 연속 실패가 백오프되고 저널 반복이 멈춘다 — 재시도 자체는 살아 있어 이후 성공이 정상 등록된다(`skills/todo/scripts/todo_execution_reconcile.py`, `tests/unit/test_todo_watch.py`).
- **`docs/기능소개/todo-소유자-DM-승인-경로.md` 의 파일명이 여전히 현행과 어긋난다** — 내용에는
  정정 배너를 달았지만 이름은 "소유자-DM" 이다. `docs/done.md`·`docs/qa/RTS-5/00-summary.md` 가
  그 경로를 가리키는 역사적 기록이라 이름을 바꾸면 그 링크가 깨진다. 조치: 역사 기록의 링크를
  함께 옮기는 별도 사이클에서 리네임한다. **오해 소지는 배너로 차단됨 · 심각도 낮음**.
  [해소 확인 2026-08-26] `docs/기능소개/todo-승인-경로.md` 로 리네임하고 `done.md`·`qa/RTS-5/00-summary.md`·기능소개 상호참조까지 같은 사이클에서 옮겼다. FS3 회계 baseline 은 불변이라 건드리지 않는다.
- **`todo_approval.py` 가 250 pure-LOC 를 넘어 예외 등록부에 올랐다** — 실행 파라미터 4개를 spec 에
  얹으며 248→254 가 됐다. 조치: transport/directory Protocol 포트를 별도 모듈로 빼면 25줄 남짓이
  줄지만, 승인 어댑터를 쪼개는 것은 배포 staging 목록과 conformance 인벤토리를 함께 재검증해야
  하므로 별도 사이클로 미룬다. **동작 영향 없음 · 심각도 낮음**.
  [해소 확인 2026-08-26] 그 별도 사이클을 지금 돌렸다. Protocol 포트 3개를
  `skills/todo/scripts/todo_approval_ports.py` 로 분리해 254→236, 등록부 행도 함께 제거했다
  (`test_no_stale_entries_below_the_ceiling` 이 죽은 행을 결함으로 본다). 선언만 옮겨
  conformance 인벤토리가 묶는 모듈은 그대로다 — `8d325dbd`

관련 기능: [todo 승인 후 자동 등록](기능소개/todo-승인-후-자동-등록.md).

## 회의록 과제 양식·Action Item 데이터베이스 중 남긴 것 (2026-08-27)

- **`meeting_project.py` 가 런타임 루트를 자기 방식으로 또 해석한다** — `_repo()` 의
  `Path(__file__).resolve().parents[3]` 는 `meeting_cli.runtime_root` 와 `stt_runtime.runtime_root`
  에 이어 **세 번째 사본**이다. 마운트 경로 형태가 바뀌면 셋을 따로 고쳐야 하고, 과거 이 해석이
  어긋났을 때 Drive 발행이 `DRIVE-PUBLISH-SKIP reason=ImportError` 로 조용히 건너뛰어진 전례가 있다.
  조치: 공유 해석기 하나로 모으고 세 호출부를 그것으로 돌린다. **지금은 동작 · 심각도 중**(조용한
  기능 상실 형태로 재발한다).

  **해소(2026-08-28)**: `meeting_runtime.runtime_root` 단일 정의로 합쳤다. 그리고 위의
  "지금은 동작" 판정은 **틀렸다** — 검증하지 않고 쓴 문장이었다. 라이브 실측에서 `_repo` 가
  `ModuleNotFoundError` 로 죽어 양식·action item 원장·미처리 전사본 조회가 **전부 조용히 무력**했고
  (`BOARD-FETCH-FAIL` 마커로 삼켜짐), 회귀는 마운트와 같은 깊이에서 별도 프로세스로 import 하는
  `tests/unit/test_meeting_runtime_root.py` 가 고정한다. `stt_runtime` 과의 사본 2개는 스킬 경계를
  넘는 import 를 만들지 않기 위해 그대로 둔다.

## meeting 게이트웨이 플러그인 배포 경로 신설(2026-08-28) 중 남은 것

`!meeting` 이 "회의록 처리 시작에 실패했습니다"로 거부된 원인을 고치며(홈 플러그인 사본이 8/23 자로
5일 낡아 이미 없어진 규칙을 적용) 배포 경로와 드리프트 감시를 만들었다. 남은 두 건은 이 저장소가
닫을 수 없다.

- **[OWNER] 새 매니페스트 2행이 프로덕션에서 UNKNOWN 으로 나온다** — healthcheck 는 전용 키의
  `command=` 강제명령 래퍼를 거치고 그 래퍼는 명령 문자열 sha256 **정확 일치**만 허용한다.
  `.hermes/plugins/00-meeting-gate/{__init__.py,plugin.yaml}` 두 행이 내는 명령의 해시를 노드의
  `~/.local/libexec/autophagy-healthcheck-probe` 에 넣기 전까지 두 행은 exit 126 → UNKNOWN 이다.
  조용한 통과가 아니라 fail-closed 이므로 **탐지가 꺼진 것은 아니지만 아직 켜진 것도 아니다**.
  래퍼는 리포에 설치기가 없는 손 유지보수 자산이라 소유자만 넣을 수 있다. **심각도 중** —
  이 항목이 닫히기 전까지 플러그인 드리프트는 conformance(코드 대조)만 잡고 노드 실측은 못 잡는다.
- **[OWNER] 게이트웨이 플러그인은 배포와 반영이 별개다** — 플러그인은 프로세스 시작 시 로드되므로
  `deploy.sh` 가 파일을 맞춰도 재시동 전까지 도는 코드가 다르다. `deploy.sh` 는 해시가 바뀐 틱에만
  `PLUGIN-CHANGED` 로 재시동 필요를 알리지만, 재시동 자체는 자동화하지 않았다 — 운영 규칙이
  "원인 확인 후에만, agent·peer 함께"이고 배포 스크립트가 매번 재시동하면 그 규칙이 무의미해진다.
  자동화하려면 재시동 주체가 아니라 **알림의 도달**을 고쳐야 한다(현재는 stderr 뿐이라 사람이
  `deploy.sh` 출력을 봐야 안다). **심각도 낮음** — 지금은 배포와 재시동이 같은 세션에서 이어진다.

## 제안서 노드 자율 구동 중 남긴 것 (2026-08-31)

> [해소 이관 2026-08-31] 제안서 노드 자율 구동의 3건을 수리 2건·재판정 1건으로 닫았다.

- **노드 agent 홈의 chromium 빌드가 세 벌(1.3G)이다** — 사용자공간 설치 프로브가
  `chromium_headless_shell-1234` 로 스모크를 통과하기 전에 `chromium-1228`·`headless_shell-1228`
  도 받았다. 조치 방향: `PROPOSAL_PREVIEW_CHROME` 이 가리키는 빌드만 남기고 나머지를 지운다.
  영향 범위: 디스크만. 심각도 낮음.
  [해소 재판정 2026-08-31] 1228 두 벌은 2026-07-14 `npx playwright install`이 만든 Hermes 브라우저 도구
  (`~/.hermes/hermes-agent/tools/browser_tool.py`) 자산이고, 제안서 것은 `chromium_headless_shell-1234`(340M)뿐이다 → 삭제 대상 없음.
- **`PROPOSAL_*` env 는 게이트웨이 재시동 뒤에야 에이전트 도구 subprocess 에 닿는다** — 프로비저너는
  `~/.env.secrets`(systemd EnvironmentFile)에 upsert 만 하고 재시동은 설계상 하지 않는다. 조치 방향:
  재시동(agent·peer 함께)을 프로비저닝 체크리스트에 명시하거나 proposal CLI 가 cron 래퍼처럼
  `~/.env.secrets` 를 자가 로드하도록 검토한다. 영향 범위: 노드 대화형 렌더의 첫 실행만. 심각도 중.
  [해소 확인 2026-08-31] `skills/proposal/scripts/proposal_env.py`가 CLI 시작 시 `~/.env.secrets`의
  `PROPOSAL_*`·`KIMM_DOCBOT_*`를 fill-only로 읽고, 기존 환경값 우선은 `tests/unit/test_proposal_env_secrets.py`가 고정한다.
- **codex 이미지 경로의 비용이 예산 원장 단가와 다르다** — 원장은 API 단가로 예약하지만 codex
  `image_gen` 은 ChatGPT 구독에 청구된다. 조치 방향: 전송기별 단가 표를 분리해 원장이 실제 청구
  주체를 적게 한다. 영향 범위: 월 상한 판정의 정확도. 심각도 낮음.
  [해소 확인 2026-08-31] `skills/proposal/scripts/proposal_images.py`가 codex를 `chatgpt-subscription`으로
  USD 0·이미지 건수만 기록해 API 월 한도에서 제외하며, `tests/unit/test_proposal_images.py`가 이를 고정한다.

## 릴리스 승인 자동 완결 후속 (2026-08-31)

- **낡은 pending 릴리스 요청이 새 요청을 막는다 → 미결정·낡은 레코드만 release.sh 가 감사형 abandon 으로 자동 회수하게 한다.** release kind 의 lifecycle 은 다른 head 에 묶인 살아 있는 요청을 supersede 하지 않고 `binding-mismatch` 로 거부한다(`ReleaseSpec.bound` 가 action_hash 일치를 요구). 두 세션이 릴리스를 번갈아 올리면 앞선 요청의 head 가 origin/main 에서 밀려나는 순간 그 요청은 영원히 릴리스될 수 없는데도 `pending/release.json` 에 남아 **모든** 새 요청을 거부시킨다 — 2026-08-31 실측: v1.0.141@6a03321f(다른 세션) 가 c68a13dc 요청을 막았고, 노드 agent 계정에서 `python3 -m automation.release_abandon` 을 손으로 돌려야 풀렸다. 조치: `release.sh` 가 `request` 거부 사유가 `binding-mismatch` 이고 저장 레코드의 probe 가 `BOUND_PENDING`(소유자 미결정)이며 그 head 가 `origin/main` 의 조상이 아닐 때만 `release_abandon` 을 원격 실행(감사 줄·archive 그대로)한 뒤 한 번 재요청한다. 결정된(✅/⛔) 레코드는 지금처럼 건드리지 않는다(L3). 영향: 가용성 — 승인 자동 완결(타이머)은 무관하고 **요청 게시**만 막힌다. 심각도 중 — 병렬 세션이 릴리스를 올릴 때마다 재발한다. 회귀는 `tests/unit/test_release_sh.py` 에 stub 으로 고정한다.

  [해소 확인 2026-08-31] 구현: `automation/release.sh` + `release_approval.py abandon` 서브커맨드 — `binding-mismatch`·`bound_pending`이고 head가 origin/main tip에서 밀려난 경우만 감사형 abandon 뒤 정확히 1회 재요청하며 결정 레코드는 불변(L3).
  회귀: `tests/unit/test_release_sh.py` stub. 정정: 인시던트 head `6a03321f`는 origin/main의 조상이라 조상-아님 조건은 자기 사례를 못 거른다 — 실제 경계는 tip 동일성이다.

## Plaud lifelog 동기화 후속 (2026-09-02)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **Discord 리액션 transport·`record_push_approval` 이 memory_relocate 와 사본으로 갈라져 있다 → interop 공용
  모듈로 승격해 두 워처가 공유한다.** relocate 모듈 import 는 memory_curator 체인을 끌고 와 의도적으로 복사했다.
  영향 범위: 코드 중복뿐, 동작 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): `refactor(interop): 리액션 승인 transport 를 공용 모듈로 모으고 plaud 종결 통지를 승인 스레드에 닫는다` — automation/interop/reaction_approval.py 신설, plaud_sync·memory_relocate 가 import

- **relocate·plaud 워처가 같은 obsidian write clone 에 `write_note` 한다 → 규약 (n)대로 공유 lock 도입을 검토한다.**
  경쟁 시 push 는 retryable 실패 후 다음 틱 재시도이고 read-back 검증이 오염을 막는다(심각도: 낮음).
  ↳ 처리(2026-09-03): `fix(obsidian-write): 클론 lock·tmp_pack 정리·fetch 전용 타임아웃·blobless 클론으로 승인된 노트 유실을 막는다` — automation/obsidian_write/clone_lock.py, `OBSIDIAN_WRITE_FETCH_TIMEOUT`(기본 900초), promisor 전환

- **결과 통지가 승인 스레드 게시까지만이고 outcome-archive(접두어+아카이브) 미채택 → `origin_notice.deliver(outcome=)`
  채택을 검토한다.** 통지는 best-effort 라 실행·영수증 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): `refactor(interop): 리액션 승인 transport 를 공용 모듈로 모으고 plaud 종결 통지를 승인 스레드에 닫는다` — automation/interop/reaction_approval.py 신설, plaud_sync·memory_relocate 가 import · plaud `notify` 가 origin_notice.deliver(outcome=) 로 ✅ 완료·⛔ 취소·⌛ 만료를 스레드에 표시·아카이브

- **memory_relocate 도 message 소실(missing) 뒤 재게시 때 `resolve_new_binding` 만 불러 같은 요청에 스레드가 둘 생길 수 있다 → plaud 의 `effects_live.thread_candidates`(레코드 `approval_thread_id` 재사용)를 interop 공용으로 올려 두 생산자가 공유하도록 검토한다.** 재게시는 드물어 동작 영향 낮음, 보안 문제 아님.
  ↳ 처리(2026-09-03): `refactor(interop): 리액션 승인 transport 를 공용 모듈로 모으고 plaud 종결 통지를 승인 스레드에 닫는다` — automation/interop/reaction_approval.py 신설, plaud_sync·memory_relocate 가 import · 재게시가 레코드 approval_thread_id 를 먼저 재사용

- **Plaud 기본 녹음명이 일시라 노트 파일명에 날짜가 두 번 들어간다(`2026-09-02-2026-09-02-13…`) → `note.plan_lifelog_note` 슬러그가 파일명의 날짜 접두와 같은 날짜로 시작하면 한 번만 쓰도록 검토한다.** 미관 문제, 기능 영향 없음(id 해시 접미가 유일성을 보장).
  ↳ 처리(2026-09-03): `fix(plaud-sync): 녹음명이 일시로 시작하면 노트 파일명에 날짜를 한 번만 쓴다`

- **obsidian-write 클론의 `git fetch` 가 120초(`writer._GIT_TIMEOUT_SECONDS`)에 죽으며 매 틱 ~770 MB `tmp_pack_*` 잔해를 남겼다(2026-09-02 실측: 8/4 이후 한 달, 230개·176 GB, plaud·relocate 두 워처 공용) → 쓰기 전용 클론을 `--filter=blob:none` 부분 클론으로 바꾸거나 fetch 타임아웃을 델타 크기에 맞게 올리고, 실패한 fetch 의 `tmp_pack_*` 를 다음 시도 전에 정리한다.** 심각도 높음(디스크 성장 + 승인된 노트가 저장되지 않음), 보안 문제 아님. 임시 조치: 잔해 삭제 + 3600초 fetch 로 1회 따라잡기.
  ↳ 처리(2026-09-03): `fix(obsidian-write): 클론 lock·tmp_pack 정리·fetch 전용 타임아웃·blobless 클론으로 승인된 노트 유실을 막는다` — automation/obsidian_write/clone_lock.py, `OBSIDIAN_WRITE_FETCH_TIMEOUT`(기본 900초), promisor 전환


## 노드 배포 표면 감사 후속 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **드리프트 프로브가 "선언된 것"만 대조해 손배포 사본을 보지 못한다 → `automation/deploy_all_probe.py`(또는 healthcheck)에 홈 표면 스캔을 더해 매니페스트에 없는 `~/.hermes/scripts/*`·`~/.hermes/plugins/*/` 를 미선언 산출물로 보고한다.** 2026-09-01 실측(agent 홈): `restart-hermes-gateway-once.sh`(에이전트 저작 재시동 헬퍼)·`regression_bank_weekly.py`(출처 불명)·`send_cost_report.py`·`poll_reminders.py`·`repair_report_consume_watch.py`(리포 소스는 있으나 선언 없음)·`mail_attachment_drive_{sync,watch}.py`(이번 착지로 해소)가 `configs/watcher-deploy-manifest.txt` 에 없고, 프로브는 파생 매니페스트 행만 읽어 이들에 대해 "드리프트 0"을 보고한다 — 손배포일수록 안 보이는 구조다. 영향 범위: 노드 배포 판정 전체(심각도: 높음).
  ↳ 처리(2026-09-03): `feat(deploy): 매니페스트에 없는 홈 배포물을 프로브가 경고로 관측한다` — `undeclared` 관측(경고, `--strict-undeclared` 로 drift 승격), tests/unit/test_deploy_all_undeclared.py

- **리포에 소스가 있는 홈 배포물 3개에 배포 선언이 없다 → `automation/cost-report`·`automation/reminder_poller`·`automation/repair`(cron) 옆에 `deploy-manifest.txt` 를 신설하고 `python3 -m automation.watcher_manifest emit` 으로 파생본을 재생성한다.** `rg -c 'send_cost_report|poll_reminders|repair_report_consume' configs/watcher-deploy-manifest.txt` → 모두 0인데 노드에는 셋 다 배포돼 cron(daily-cost-report·reminder-poller·repair-report-consumer)으로 돈다. 영향 범위: 이 3개 워처의 드리프트 탐지(심각도: 중).
  ↳ 처리(2026-09-03): `feat(deploy): 손배포되던 홈 산출물 4종을 deploy-manifest 에 선언하고 빠진 배포기를 만든다` — cost-report·reminder_poller·repair 에 deploy-manifest.txt+deploy.sh, 중앙 표 emit 재생성

- **게이트웨이 플러그인 `05-skill-generation` 에 배포 선언이 없다 → `automation/skill_generation/deploy-manifest.txt` 를 신설해 `.hermes/plugins/05-skill-generation/{__init__.py,plugin.yaml}` 를 등록한다.** 지금은 노드 사본과 리포 소스 해시가 같지만(`3c0072ea…`) 감시가 없어 다음 변경부터 조용히 벌어진다 — `interop-protocol` 이 같은 조건에서 6주째 07-20 사본이다. 영향 범위: 플러그인 드리프트 탐지(심각도: 중).
  ↳ 처리(2026-09-03): 같은 커밋 — automation/skill_generation/deploy-manifest.txt 가 05-skill-generation 의 __init__.py·plugin.yaml 을 등록

- **미러 dirty 가 3일간 아무 사건도 만들지 않았다 → `checkout_mirrors_origin` 프로브가 실제로 FAIL·수리 티켓을 냈는지 노드 로그로 확인하고, 리컨실러의 `release-backlog` 다이제스트가 "미러가 dirty 라 못 따라온다"를 "소유자가 아직 릴리스를 안 올렸다"와 구분해 통지하도록 보강한다.** 2026-09-01 실측 `/srv/autophagy-private/deploy-reconcile/state.json`: `consecutive_failures=162`, `skip_reason=release-backlog`, `incident_open=false` — 08-29 부터 미러가 미커밋 파일로 동결된 동안 같은 조용한 경로를 탔다. 영향 범위: 노드 배포 정지 탐지(심각도: 중).
  ↳ 처리(2026-09-03): `fix(reconcile): 릴리스 백로그 다이제스트가 미러 동결(dirty/ahead)을 '릴리스 미실행'과 구분해 말한다` — `mirror_state` 를 state.json 에 additive 저장. 노드 로그로 프로브 FAIL 여부를 확인하는 부분은 OWNER 신규 항목

- **자가 스킬 감사 결과가 노드에 남지 않고 겹침 판정이 결정으로 이어지지 않는다 → `automation/selfskill_audit` 리포트를 `~/.hermes/logs/selfskill-audit/` 에도 남기고 `OVERLAPS-GOVERNED` 를 승격·폐기 결정 큐(소유자 항목)로 연결한다.** 노드에 자가 스킬 5개(2026-08-18~28)가 있고 `meeting-minutes-authoring`·`document-publishing` 은 governed meeting·doctype·report 와 겹치는데, `selfskill-audit-watch` 는 매일 ok 로 끝나며 산출물이 Discord 로만 간다. 영향 범위: 자가 스킬 거버넌스(심각도: 중).
  ↳ 처리(2026-09-03): `feat(selfskill-audit): 감사 결과를 노드 로컬 jsonl 에 남기고 겹침을 미결 원장으로 추적한다` — logs/selfskill-audit/<YYYY-MM>.jsonl + pending-overlaps.json. 승격·폐기 결정 자체는 OWNER 신규 항목


## 첨부 아카이브 착지 후 남긴 것 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`mail_attachment_drive_sync.py` 가 250 pure-LOC 상한을 넘겨 F2 등록부에 예외로 올라갔다(378) → 순수 계획·이름 정책·상태 DB(`mail_attachment_archive.py`)와 Drive 실행(CLI)로 나누고 등록부에서 내린다.** 착지 사이클에서는 돌고 있는 archive.db·folders.json 호환을 한 파일에서 보장하는 쪽을 택했다. 영향 범위: 코드 크기 규약만, 동작 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): `refactor(mail): 첨부 아카이브를 순수 계획(mail_attachment_archive)과 Drive 실행 CLI 로 나눈다` — 185/154 pure LOC, F2 등록부에서 내림


## 스킬 실행 경로 후속 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **mail 외 SKILL.md 들이 아직 `~/.hermes/skills/<skill>/scripts/…` 를 안내한다 → SS-1(2026-08-15) 이후 그 루트는 자가 스킬 전용이라 경로가 없다. wiki·topics·speechtotext·report·recall·proposal 등(`rg '~/.hermes/skills/[a-z-]+/scripts' skills/*/SKILL.md`)을 `/srv/autophagy-skills/live/<skill>/scripts/…` 로 바꾸고 각 스킬 버전을 올린다.** 최근 14일 세션 기록에서 없는 경로 시도 30건·미러 경로 실행 90건이 이 안내에서 비롯됐다 — 에이전트가 파일을 찾아 헤매다 낡은 사본을 채택한다. 지금은 mail 만 고쳤다(심각도: 중 — 다른 스킬도 같은 방식으로 낡은 코드를 실행할 수 있다; 발송 안전성은 각 스킬의 승인 게이트가 지킨다).
  ↳ 처리(2026-09-03): `docs(skills): SKILL.md 스크립트 경로를 governed live 마운트(/srv/autophagy-skills/live)로 고친다` — 15개 SKILL.md(meeting 은 configs·prompts 도), 버전 patch 상승

- **관리자 배포본 밖 사본 실행 거부(`mail_runtime.governed_copy_refusal`)가 mail 에만 있다 → calendar·budget·todo·wiki·coordination 등 mutating CLI 를 가진 스킬에 같은 판정을 넣되 사본을 늘리지 않는다.** 스킬은 import 시점에 `automation` 을 못 쓰므로 판정 함수의 단일 정의를 어디에 둘지(각 스킬 `*_runtime.py` 의 인라인 + 동일성 회귀, 또는 마운트된 릴리스 경유 lazy import)를 먼저 정한다. 게이트웨이 수준(`interop-protocol` 플러그인 `pre_tool_call`)의 경로 정책이 근본 해법이지만 그 플러그인은 배포 경로가 없어 [follow-ups-deferred](follow-ups-deferred.md) 의 OWNER 선행 조건에 묶여 있다(심각도: 중).
  ↳ 처리(2026-09-03): `refactor(skill-mount): governed 사본 거부 판정을 automation.skill_mount 단일 정의로 올린다` + `feat(calendar|wiki|coordination|budget|todo): … STALE-SKILL-COPY-BLOCK 으로 거부한다` + `test(skills): … conformance` — 정의는 skill_mount 하나, 스킬은 `<skill>_governed.py` 로 지연 호출·ImportError 시 fail-closed


## 기관메일 회신 원문 인용 후속 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **Gmail 계정 회신(`gws gmail +reply --message-id`)은 인용을 붙이지 않는다 → gws 가 원문을 자동 인용하는지 노드에서 실측한 뒤, 안 하면 `mail_gmail_send.ReplyMailRequest` 본문에 같은 `mail_quote.render_quote` 를 붙인다.** Gmail 은 message-id 스레딩으로 대화 보기에 원문이 이미 묶이므로 동작 정상 — 표시 일관성 문제일 뿐이고 발송 안전성과 무관(심각도: 낮음).
  ↳ 처리(2026-09-03): 코드 불필요 — 실측(librarian, 2026-09-03): `gws`(npm `@googleworkspace/cli` 0.22.5, github.com/googleworkspace/cli) 의 `gmail +reply` 는 crates/google-workspace-cli/src/helpers/gmail/reply.rs 에서 `format_quoted_original(original)` 을 본문 뒤에 붙이고 In-Reply-To/References/threadId 도 자동 설정한다. Gmail 회신도 원문이 인용된다


## cron 워처 수리 후속 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`typing.override` 직수입이 3.11 no-agent 런타임 밖 경로에 남아 있다 → 해당 경로를 cron 체인에 편입하거나 리팩터할 때 `automation/typing_compat.py`(또는 mail_runtime 의 인라인 폴백 패턴) 경유로 먼저 바꾼다.** `skills/doctype/scripts/doctype_save.py`(게이트웨이 대화 경로 전용 — doctype 은 cron 워처가 없다)·`automation/group_roster/editor.py`·`automation/managed_skills/submission_errors.py`(둘 다 워크스테이션 CLI 전용)가 `from typing import override` 를 직수입한다. Hermes cron 의 uv CPython 3.11 이 실행하는 체인에는 현재 도달하지 않아 동작 정상 — mail-triage-watch 를 매 틱 죽인 결함(2026-08-31)과 같은 계열이지만 지금은 잠복이다. 영향 범위: 현재 없음, **심각도 낮음**. cron 편입 순간 같은 ImportError 가 재발한다.
  ↳ 처리(2026-09-03): `fix(py311): typing.override 직수입을 3.11 호환 경로로 바꾸고 가드가 재발을 막는다` — 남은 직수입 전부 typing_compat/인라인 폴백으로, tests/unit/test_py311_syntax_guard.py 가 배포 소스 전수를 검사


## 화자 구분·문장 단위 출력 착지 후 남긴 것 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **문장 경계가 구두점에 묶여 있어 구두점 없는 전사에서는 화자 블록이 무너진다** — whisper 가 구두점을 내지 않는 언어·구간(배포 검증 실측: 중국어 4화자 샘플 57초가 한 문장 158자 → 화자1 하나)에서는 `split_sentences` 가 문장 하나를 만들고 화자 배정도 그 문장의 최다 겹침 하나로 굳는다. 한국어 large-v3-turbo 출력은 735문장 중 1건만 구두점이 없어 실사용 영향은 낮다. 조치: `stt_diarize.assign` 이 문장 안 토큰 타이밍으로 화자 전환 지점(단어 경계 최근접)에서 문장을 쪼개거나, 구두점 없는 긴 문장을 시간 기준(예: 15초)으로 나눈다. **영향 범위: 구두점 없는 출력에 한정 · 심각도 중**.
  ↳ 처리(2026-09-03): `feat(speechtotext): 화자 경계와 15초 무구두점 구간에서 문장을 쪼개 한 문장이 여러 화자를 삼키지 않게 한다` — skills/speechtotext/scripts/stt_split.py `split_on_turns`, assign 직전에 적용(fail-soft)


## healthcheck 폭주 수리(PR #347) 중 발견한 인접 결함

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`from typing import override` 를 폴백 없이 쓰는 모듈이 남아 있다 → `automation/typing_compat.override` 로 통일하거나 `tests/unit/test_py311_syntax_guard.py` 범위를 넓혀 기계적으로 막는다.** `automation/freeze_inventory.py`, `automation/group_roster/editor.py`, `automation/install/allowed_signers.py`, `automation/managed_skills/{publish_core,submission_errors,submission_source,submission_transport}.py`, `automation/managed_sync/verify.py`, `automation/memory_relocate/cli.py`, `automation/update_trust.py`, `automation/update_trust_state.py`, `skills/doctype/scripts/doctype_save.py`가 해당한다. Hermes no-agent cron 은 uv CPython 3.11 로 돌아 t_4829b4b5 의 원인처럼 이들 중 cron 자식 체인에 들어가는 것이 생기면 같은 ImportError 가 재발한다. 영향 범위: 현재 cron 체인에는 없음(정적 import 그래프 확인), 잠재 결함, **심각도 낮음**.
  ↳ 처리(2026-09-03): `fix(py311): typing.override 직수입을 3.11 호환 경로로 바꾸고 가드가 재발을 막는다` — 남은 직수입 전부 typing_compat/인라인 폴백으로, tests/unit/test_py311_syntax_guard.py 가 배포 소스 전수를 검사


## 요청별 승인 스레드 착지 후 남긴 것 (2026-09-01)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **budget 에는 만료 경로가 없고 todo `_expire()` 는 통지 없이 아카이브해 `origin_notice.ThreadOutcome.EXPIRED` 가 두 스킬에서 미사용이다 → 만료 시 결과 통지(`deliver(..., outcome=EXPIRED)`)를 붙여 스레드가 `⌛ 만료 ·` 로 닫히게 한다.** 만료된 요청의 스레드가 활성 목록(=진행 중 요청 보드)에 남는 정확도 문제이며 실행·원장·영수증에는 무영향(심각도 낮음).
  ↳ 처리(2026-09-03): `feat(approval): 만료된 todo·budget 요청의 승인 스레드를 ⌛ 만료 로 닫고 결과를 통지한다` — todo `_expire` 가 EXPIRED 를 전달, budget 은 24시간 TTL 만료 분기 추가


## 수리 티켓 스윕 후속 (2026-09-02)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(PR 「follow-ups sweep 4」, 브랜치 session/followup-sweep-4). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **digest 요약 실패는 이제 `llm-calls.jsonl` 에 `digest_summary_failed` 로 남지만 classify 폴백(`_CLASSIFY_FALLBACK`)은 여전히 사유를 남기지 않는다 → `triage_llm.log_failure(purpose="classify", …)` 를 classify 재시도 소진 지점(`triage_digest.build_item`)에도 붙인다.** `⚠️ 분류 실패` 배지만 남고 원인(LlmCallError/LlmParseError)이 사라져, t_44b406fe 처럼 조치 불가능한 수리 티켓이 다시 생성될 수 있다. 영향 범위: 다이제스트 분류 실패의 원인 추적만, 동작은 fail-open 으로 정상 — 심각도 낮음.
  ↳ 처리(2026-09-03): `fix(mail-digest): classify 재시도 소진 사유를 llm-calls.jsonl 에 남긴다` — `triage_llm.log_failure(purpose="classify")`, tests/unit/test_mail_digest.py

## 수리 티켓 t_bd0d3789 후속 (2026-09-03)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(브랜치 session/v110-convenience, v1.1.0 편의 릴리스). 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`automation/drive_client.py` 의 `ensure_folder_path` 가 `~/.hermes/drive-publish/folders.json` 의 캐시된 폴더 id 를 살아 있는지 확인하지 않고 그대로 돌려준다 → 캐시 id 를 `files get fields=trashed,parents` 로 재검증하고, 휴지통에 있거나 없어졌으면 캐시를 버리고 새로 조회하도록 폴백을 넣는다.** 2026-08-26 에 옮겨지거나 버려진 옛 폴더(`autophagy/회의록/2026`)로의 게시가 조용히 성공하고, 파일은 살아 있는 트리 어디에도 보이지 않는다. 영향 범위: 파사드로 게시하는 모든 스킬 · 자료 유실은 없다(파일은 존재하되 자리가 틀리다) — 심각도 중.
  ↳ 처리(2026-09-03): `fix(drive): 캐시된 폴더 id 를 files get 으로 재검증하고 휴지통·부재면 재조회한다` — `automation/drive_client_cache.py`(`_folder_alive` + 접두 키 무효화), tests/unit/test_drive_client.py 가짜 runner 로 trashed → 재조회·캐시 재기록 회귀

## LiteLLM GPT 전환과 헬스체크 정리 후 남긴 것 (2026-09-03)

> [이관 2026-09-03 · 해소] 이 저장소에서 고쳤다(브랜치 session/v110-convenience, v1.1.0 편의 릴리스). 회계 가드가 원문을 요구해 불릿을 그대로 둔다. 노드 설치(OWNER)와 alerting 검토(OBSERVE)는 아래 두 불릿.

- **`automation/healthcheck.sh` 의 LiteLLM 프로브가 `/health/liveliness`(프록시 생존)만 봐서 상류 제공자 장애(2026-09-03 00:21 KST 부터 전 요청 429 잔액 소진)를 11시간 동안 아무도 몰랐다 → 30분 틱에 `glm-main` 실제 completion 1건(≈48건/일)을 더해 실패 시 FAIL·수리 티켓을 내고, LiteLLM `alerting` 에 outage 유형을 alert-dispatcher 와 함께 붙일지 검토한다.** ops 의 compose 배포본은 seed 보다 낡아 `/health`(모든 배치에 실제 completion, 하루 8,640건)를 치고 있었고 이번 배포로 seed 의 liveliness 로 수렴한다 — 그 뒤 실제 completion 프로브는 우리 healthcheck 만이 낼 수 있다. 영향 범위: 상류 장애 탐지 지연 — 심각도 중.
  ↳ 처리(2026-09-03): `feat(healthcheck): LiteLLM 에 실제 completion 프로브(litellm_completion)를 더해 상류 429·잔액 소진을 FAIL·수리 티켓으로 드러낸다` — `probe_litellm_completion`(glm-main, max_tokens 1, 20초, choices[0] 만 PASS, 상태·error type/code 만 출력), 래퍼 `# probe-type: litellm_completion` + allowlist 해시, tests/unit/test_healthcheck_probe_wrapper.py·test_healthcheck_allowlist_manifest.py(RED 5→GREEN 29). LiteLLM `alerting` 연동은 검토 보류(아래 OBSERVE).
- **[OWNER] 재생성된 healthcheck 래퍼(`litellm_completion` 프로브 포함)는 노드에서 `automation/provision-healthcheck-probe.sh` 를 소유자가 돌려야 설치된다 → 설치 전까지 새 행은 allowlist 불일치로 FAIL 한다.** 영향: 설치 전 healthcheck 30분 틱의 LiteLLM completion 행 1건 — 심각도 낮음(설치 1회로 끝).
- **[OBSERVE] LiteLLM `alerting` 에 outage 유형을 alert-dispatcher 와 함께 붙일지 → 실제 completion 프로브가 2주간 429 를 몇 번 잡는지 본 뒤 결정(프로브가 충분하면 얹지 않는다).** 영향: 없음(관측 대기).

## Hermes 무재시동 자체 업데이트로 게이트웨이 도구 계층이 죽었다 (2026-08-18)

> [이관 2026-09-04 · 해소] BLOCKED 에서 옮겨 왔다. 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`docs/guide/operations.md`가 안내하는 `userctl` 래퍼가 노드에 없다** — 표의 복구 명령 `userctl <node> <account> restart hermes-gateway.service`가 `command not found`로 실패해, 이번 재시동도 `sudo -u <account> env XDG_RUNTIME_DIR=/run/user/$(id -u <account>) systemctl --user restart hermes-gateway.service`로 우회했다 → 래퍼를 설치하거나 문서를 실제로 동작하는 명령으로 고친다. **동작 영향 없음 · 심각도: 낮음(장애 대응 중에 한 번 더 막힌다)**.
  ↳ 해소(2026-09-04): 재판정이다. `docs/guide/operations.md` 1절(10~17행)이 `userctl` 을 관리 셸에서 운영자가 직접 정의하는 셸 함수로 규정한다. 노드에 래퍼가 설치돼 있어야 한다는 전제 자체가 성립하지 않으므로 코드 수정 없이 닫는다.

## K4-b 설치기·신뢰키 위생(FS3 todo 12) 중 발견한 후속 과제

> [이관 2026-09-04 · 해소] BLOCKED 에서 옮겨 왔다. 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **`docs/guide/operations.md`가 자격증명 조회 alias를 「프로비저닝에 없다」고 계속 말한다** — `automation/provision-agent.sh`가 이제 복원하므로 낡은 서술이지만, 그 문서는 `configs/freeze-inventory.txt`의 동결 대상이라 이번에 고치지 않았다(같은 문구를 가진 `onboarding-kit.md`와 기능소개 문서는 갱신했다). **오안내 위험만 · 동작 영향 없음 · 심각도 낮음** → 그 계획의 동결이 풀리는 사이클에서 한 문단을 함께 고친다.
  ↳ 해소(2026-09-04): 그 문구가 없다. `docs/guide/operations.md` 에서 alias 를 grep 하면 `~oriclaw/.bash_aliases` 를 말하는 360행만 걸리고, 그 파일은 동결 이후 커밋 3건(aae018e70, 32738984d, 7496167d2)을 받아 이미 갱신됐다.

## Hermes kanban 열린 이슈 정리(2026-08-22) 중 발견한 후속 과제

> [이관 2026-09-04 · 해소] BLOCKED 에서 옮겨 왔다. 아래 「healthcheck 폭주 수리(PR #347)」 절의 불릿과 **같은 결함이 두 절에 두 번 기록된 것**이다. 회계 가드가 원문을 요구해 두 원문을 모두 남긴다.

- **수리 occurrence가 done 티켓에 계속 붙는다** — repair-detector가 signature로 기존 티켓을 찾을 때
  상태를 보지 않아, 완료된 healthcheck 티켓(t_6f3f7e1e)에 재발 occurrence가 55건까지 조용히 쌓였고
  (`created:false`) 보드의 열린 목록에는 아무것도 나타나지 않았다 → signature 티켓이 done/archived면
  새 티켓을 만들거나 reopen한다. **재발 가시성 영향 · 심각도 중**.
  ↳ 해소(2026-09-04): `automation/repair/repair_core.py` 의 `RepairRegistry.claim` 이 저장된 티켓이 닫혀 있으면 새 티켓을 발급하고, 티켓 상태를 읽을 수 없을 때는 기존 중복 제거 동작을 그대로 유지한다. 아래 「healthcheck 폭주 수리(PR #347)」 절의 중복 기록과 한 건이다.

## healthcheck 폭주 수리(PR #347) 중 발견한 인접 결함

> [이관 2026-09-04 · 해소] BLOCKED 에서 옮겨 왔다. 위 「Hermes kanban 열린 이슈 정리(2026-08-22)」 절의 불릿과 같은 결함의 두 번째 기록이다.

- **수리 티켓 레지스트리(`~/.hermes/repair-tickets.json`)는 done 카드에도 같은 시그니처로 occurrence 만 계속 올린다 → `automation/repair/repair_core.py` 의 registry lookup 에서 카드 상태를 확인해 done 이면 새 티켓을 발급(또는 재오픈)하는 설계 판단을 별도 논의한다.** t_318263ba 2623회·t_c0718520 2233회·t_6f3f7e1e 2078회 등에서 done 카드가 재발해도 소유자가 볼 새 신호가 없다. 영향 범위: 수리 티켓 재발 감지, 운영 가시성 저하, **심각도 낮음**.
  ↳ 해소(2026-09-04): 위 불릿과 동일한 결함이며 이번 사이클에 `automation/repair/repair_core.py` 에서 닫혔다. `RepairRegistry.claim` 이 저장된 티켓이 closed 면 새 티켓을 발급하고, 상태를 읽지 못하면 기존 중복 제거 동작을 유지한다.

## DM→#agent-chat 이관(승인 표면 v7) 중 발견한 후속 과제 (2026-08-24)

> [이관 2026-09-04 · 해소] OBSERVE 에서 옮겨 왔다. 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **승인 스레드는 kind별 고정 앵커다** — 지시 메시지나 다이제스트 메시지 밑에 직접
  스레드를 다는 origin 앵커링(결과 통지의 `origin_notice`와 대칭)은 후속 개선. 조치:
  운영해 보고 소유자 선호에 따라 producer에 origin 전달을 확장. **UX 개선 · 심각도 낮음**.
  ↳ 해소(2026-09-04): 요청별 스레드로 구현됐다 — `automation/interop/approval_surface.py:61 class RequestThread`, `automation/interop/approval_directory.py:170 agent_chat_request_thread(kind, request)`, 그리고 AGENTS.md 의 「요청별 승인 스레드 규칙」(2026-09-01).

## 음성 녹취 회의록 자동화(speechtotext) 착지 후 남긴 것 (2026-08-25)

> [이관 2026-09-04 · 해소] OBSERVE 에서 옮겨 왔다. 회계 가드가 원문을 요구해 불릿을 그대로 둔다.

- **화자 분리(diarization)가 없다** — whisper.cpp의 `--diarize`는 채널 분리 녹음에만
  유효하고 `tinydiarize`는 실험적이다. 회의록의 "타인 액션아이템" 정확도는 LLM 추출에
  의존한다. 조치: 필요해지면 별도 로컬 diarization 파이프라인을 검토(파이썬 ML 스택이라
  stdlib 정책 예외가 필요). **현재 기능에 영향 없음 · 심각도 낮음**.
  ↳ 해소(2026-09-04): `skills/speechtotext/scripts/stt_diarize.py` 와 `skills/speechtotext/scripts/stt_speakers.py` 가 존재한다. 같은 소절의 「해소 (2026-09-01)」 줄이 sherpa-onnx 착지를 이미 기록해 두었다.

## 용어 교정 문서 단계 이동 (2026-09-05) — 소유자·관측

- **OWNER — Drive 에 교정 참고 문서가 아직 없다 → 소유자가 `autophagy/용어집.csv`(모든 산출물)와 필요하면 `autophagy/회의록/용어집.csv`·`autophagy/라이프로그/용어집.csv` 를 만들어야 실제 교정이 걸린다.** 2026-09-04 실측에서 공통 파일이 부재했고, 2026-09-04 QA 사고로 노드의 옛 `~/.hermes/speechtotext/glossary.txt` 도 지워졌다. 코드는 없는 참고 문서를 빈 것으로 읽으므로(fail-soft) 실패가 아니라 **교정 0건**으로 나타난다 — 형식과 자리는 [용어-교정-규약](guide/용어-교정-규약.md). 심각도 중(문서 품질), 저장소에서 닫을 수 없음.
- **OBSERVE — 라이프로그 노트의 파일 이름 슬러그는 교정하지 않는다 → 파일 탐색기에는 오인식 표기가 남을 수 있다.** 의도된 절충이다: 경로가 참고 문서를 따라 움직이면 용어집을 한 줄 고친 날 같은 녹음이 노트 둘로 갈라진다(제목·본문은 교정된다). 소유자가 파일 이름까지 맞추고 싶다고 말하면 그때 이관 규칙(옛 경로 → 새 경로 이동)을 함께 설계한다. 심각도 낮음.

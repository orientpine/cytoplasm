---
name: proposal
description: "개인 제안서 워크스페이스에서 섹션 Kanban·초안·인간 기여분·취합·Codex 최종 검토를 안전하게 관리한다. W5-4."
version: 2.2.3
author: autophagy-agents
license: MIT
metadata:
  hermes:
    tags: [Proposal, Private-Workspace, Kanban, Sensitivity-Gate, Codex-Review]
prerequisites:
  commands: [python3, hermes]
---

# proposal — 개인 제안서 작성 워크스페이스

변경 명령은 `/srv/autophagy-skills/live/proposal/scripts/`에서만 실행하며, 오래된 사본은 STALE-SKILL-COPY-BLOCK으로 거부한다.

모든 본문은 `~/proposals/<slug>/`(0700)과 그 하위 0600 파일에만 둔다. `PROPOSAL_STATUS_ROOT`는
본문 없이 `slug`, 섹션 키/제목/상태, Kanban 카드 ID만 가진 상태 메타데이터 위치다. 운영에서 repo
메타 worktree를 쓸 때만 이 환경변수로 지정한다. 기본 `~/.hermes/proposal-status/`도 0700이다.

## 절대 규칙

1. `draft --brief-file`은 전체 제안서와 브리프를 결정적 민감도 게이트로 먼저 검사한다. 적중하면
   `openai-codex/gpt-5.4`만 사용하며 GLM을 호출하지 않는다.
2. `review`는 `hermes -z --provider openai-codex -m gpt-5.4 -t todo` **1회만** 실행한다. 검토는
   취합본에 저장하고, `PROPOSAL_DM_TARGET` 또는 `~/.hermes/proposal/config.json`의 `dm_target`으로
   cha에게 DM한다. 재검토 대신 사람이 검토 결과를 직접 반영한다.
3. 타인 기여분은 사람이 전달한 `--file` 또는 `--text`만 `contribute`로 섹션에 접는다. 웹/외부
   문서 수집은 **리서치 단계에서만** 허용하며 세 조건을 모두 만족해야 한다. (a) 수집물은
   `inputs/SYNTHESIS.md`의 `## Verified Claims` 표로만 유입되고 각 행에 출처 URL이 반드시 있다
   (`| claim | CONFIRMED | https://... |`). (b) 그 결과가 `corpus/*.md`로 내려갈 때 KD
   `corpus-lint` 게이트를 통과해야 하고 exit 3이면 파이프라인이 멈춘다. (c) 사람이 준
   `--file/--text` 밖의 본문 직접 붙여넣기는 단계와 무관하게 계속 금지다. `--with-evidence`의
   읽기 전용 개인 지식 조회는 수집이 아니며 아래 지식 파사드 규약(파사드 경유)만 따른다.
4. 섹션 카드는 전용 `proposal-<slug>` 보드에 `needs_input` 사유가 있는 `blocked`로 만든다. 이는
   실제 인간 입력 대기 상태이며, Ready 주차 용도가 아니다. 초안이 생기면 Ready로 옮기지 않고
   직접 완료해 디스패처 LLM 워커를 만들지 않는다.
5. 제안서 본문·검토 코멘트·외부 기여분을 repo, `docs/qa`, 공개 채널에 붙이지 않는다. 경로·SHA256·
   구조 assert만 증적으로 남긴다.

## v2 파이프라인 계약

버전 루트는 `~/proposals/<slug>/versions/vNNNNNN/`이고, 아래 8단계는 이 순서로만 돈다.

| 단계 | 입력 | 출력 경로 | 게이트/비고 |
| --- | --- | --- | --- |
| research | 주제·브리프 요청 | `inputs/RESEARCH_BRIEF.md`, `inputs/SYNTHESIS.md` | 웹 수집 허용 구간. `## Verified Claims` 행마다 출처 URL 필수 |
| corpus | `inputs/SYNTHESIS.md` | `corpus/*.md` | KD `corpus-lint` 통과 필수, exit 3이면 차단 |
| images | corpus, 도해 지시 | `images/*.png`, `figures.json` | 프롬프트에 `no text, no labels, no numerals`, 캡션은 `그림 N. …`. 렌더 시 그림은 문단 중앙 정렬로 최대 142.9mm(엔진 캡 40,500 HWPUNIT)까지 표시된다. 전송기는 `PROPOSAL_IMAGE_TRANSPORT=fake\|live\|codex`이며, `codex`는 Codex CLI OAuth 세션의 내장 `image_gen`으로 생성하므로 OpenAI API 키가 필요 없다. 지출 원장은 전송기별 청구 주체를 기록해 `live`는 `openai-api` USD를 예약하고, `codex`는 `chatgpt-subscription` 건수·USD 0으로 기록하며 `openai-api`만 `PROPOSAL_IMAGE_MONTHLY_CAP_USD`에 센다 |
| draft | corpus + figures | `out/drafts.json` | KD `kimm-docbot draft` |
| refine | `out/drafts.json` | 변경 시 `out/drafts.refined.json`, 항상 `out/refine-report.json` | Codex 윤문, markdown 단계, **렌더 이전**. 결정론 전처리로 그림-주어 문장(`[[FIG:x]]은 …를 나타낸다`)을 주장+괄호 인용(`…를 개발한다 ([[FIG:x]]).`)으로 재작성하고 건수를 `figure_citation_recasts`에 기록. 무변경·호스트 불가 시 refined 파일을 만들지 않고 사유 기록 |
| render | `out/drafts.refined.json` | `out/proposal.hwpx`, `out/proposal.hwpx.traceability.md` | KD `kimm-docbot render`, 고정 SHA, `--profile 30-page\|10-page`. 근거 추적성(Coverage)은 본문이 아니라 사이드카 md 로만 나간다. `tables.json`에 `kind: "gantt"` 표(행: `[연차, 꼭지, 시작월, 종료월]`, 월은 연차 안 1..12)가 있으면 추진 내용 표를 전 연차로 채운다 — 연차마다 꼭지 정확히 8개, 마지막 연차 종료 전까지 비는 달이 없어야 하며 위반은 렌더 중단 |
| publish | `out/proposal.hwpx` | Drive `autophagy/제안서/<YYYY>/`, `manifest.json`, `publish-receipt.json` | 게시 수신증 보관 |
| version | 게시 결과 | `HEAD`, `changelog.json`, `CHANGELOG.md` | 다음 판은 `improve --since vN`으로 v_{n+1} |

윤문이 렌더 앞이라는 순서 자체가 계약이다. refine을 render 뒤로 미루면 다듬은 문장이 산출
HWPX에 들어가지 못하고, 그 시점에는 고칠 표면이 바이너리뿐이라 되돌릴 방법이 없다.
`refine-report.json`의 `refined`, `no_op_detected`, `changed_sentence_count`, `source_equals_output`,
`rules_applied`, `failure_reason`을 확인하면 실제 변경 여부를 본문을 열지 않고 판정할 수 있다.
호스트를 호출하지 못했거나 결과가 원문과 같으면 `refined=false`이며, 원문 사본을
`drafts.refined.json`으로 만들지 않는다. render는 이 명시적 skip 리포트를 확인한 뒤 원본을 사용한다.

docbot 엔진은 고정 SHA 서브프로세스(`PROPOSAL_DOCBOT_PIN=382f1a60a49a0f2f2e6abe21dcced9b6c011358b`)로만 부른다. 핀 없이 실행하지 않는다.
핀은 저장소가 아니라 런타임 설정값이다 — 엔진을 올리면 노드의 `PROPOSAL_DOCBOT_PIN` 도 같이 올려야 하고,
어긋나면 `ENGINE-PIN-BLOCK` 으로 렌더가 닫힌다.
이 핀부터 렌더는 기록 직전 모든 `Contents/section*.xml` 에서 `hp:linesegarray`(한/글 라인 레이아웃 캐시)를 버리고,
하나라도 살아남으면 `validate` 가 산출을 거부한다 — 문단을 지우거나 밴드를 끼우면 그 뒤 문단이 전부 다른 쪽으로 밀려
캐시가 낡고, 한/글이 그 옛 좌표를 믿어 글줄이 뭉치고 자간이 무시되기 때문이다.
소유자 비공개 노트는 외부 호스트에 닿지 않는다. 인용·렌더에 들어가는 근거는 PUBLIC뿐이고,
비공개 근거는 구성 판단에만 쓴다.

## Commands

CLI는 시작 시 `~/.env.secrets`의 `PROPOSAL_*`·`KIMM_DOCBOT_*`를 fill-only로 읽으며, 기존 환경값이 우선한다.

```bash
# 섹션 구조와 실제 Hermes Kanban 카드 생성
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py create \
  --slug <kebab-slug> --title "제안서 제목" \
  --section need:필요성 --section approach:추진전략 --section impact:기대효과

# 섹션 현황과 추가
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py sections --slug <kebab-slug>
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py section-add \
  --slug <kebab-slug> --key budget --title 예산계획

# 사람이 준 본문을 섹션 초안으로 저장하거나, brief 기반 초안을 생성
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py draft \
  --slug <kebab-slug> --section need --file <local-file>
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py draft \
  --slug <kebab-slug> --section approach --brief-file <local-file> --with-evidence

# 생성 전에 원문을 노출하지 않는 팩 요약 또는 파사드 렌더 출처를 미리 확인
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py evidence \
  --slug <kebab-slug> --section approach --brief-file <local-file> --json

# 사람이 전달한 자료만 관련 섹션에 취합
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py contribute \
  --slug <kebab-slug> --section approach --source collaborator --file <local-file>

# 취합: 누락 섹션은 표지+리마인더를 출력하고 실패하지 않음
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py assemble --slug <kebab-slug>

# 최종 Codex 검토 1회와 cha DM
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py review --slug <kebab-slug>

# 최종 HWPX를 페이지별 PNG로 펼쳐 에이전트가 직접 육안 검토
python3 /srv/autophagy-skills/live/proposal/scripts/proposal_cli.py visual-review \
  --slug <kebab-slug> --json
```

## 페이지 시각 검토

`visual-review`는 현재 불변 버전의 `out/proposal.hwpx`를 직접 읽어 페이지별 PNG와 PDF를 만든다.
결과는 원본 버전 안이 아니라
`~/.hermes/proposal/visual-reviews/<slug>/<version>/<hwpx-sha256>/`에 둔다. 같은 바이트는
재사용하므로 v10 같은 발행본을 수정하지 않는다.

시인성이나 가독성을 평가할 때는 XML 수치·문단 길이·쪽수만으로 완료를 주장하지 않는다.
`visual-review --json`이 돌려준 `pages`를 전부 직접 열어 제목 고립, 그림만 있는 쪽, 표 머리글
고립, 과도한 공백, 본문·그림 밀도 편차를 확인한다. 이 미리보기는 양식 판형·글꼴·들여쓰기·표·
그림 크기·캡션·떠있는 그림의 후속 본문 채우기를 재현하는 QA 표면이며, 한/글 정밀 렌더러나
제출용 PDF를 대신하지 않는다.

## 지식 근거

근거 조회는 opt-in `--with-evidence`일 때만 [`지식 계층 규약`](../../docs/guide/지식-계층-규약.md)의
읽기 전용 `automation.knowledge` 파사드를 경유한다. proposal은 RAG/wiki/Obsidian을 직접 검색하거나
검색 임계값을 바꾸지 않는다. 초안의 `### 근거` 각주와 취합본 말미의 `## 근거 목록`은 모두
`render_citations`가 만든 단일 출처 형식을 쓰며, 팩 밖 인용은 생성 직후 제거한다. 팩은 섹션 옆
`*.evidence.json`(0600)에 보관한다.

관련 근거가 없으면 초안 머리에 "근거 없음"을 명시하고 소유자의 과거·노트에 관한 사실 주장을
근거 있는 것처럼 쓰지 않는다. 계층 조회가 불가능하면 "근거 수집 불가"를 표시하되 생성은 계속하며
재시도하거나 자체 검색으로 우회하지 않는다. patent-sensitive 근거와 센티널 content는 GLM에 보내지
않고 기존 Codex 전용 민감도 경로를 사용한다.

## Sandbox

`scripts/scenario.sh`은 더미 시크릿과 임시 0700 워크스페이스만 사용한다. Kanban과 DM을 비활성화한
상태로 섹션 생성·인간 기여분 취합·전체/누락 취합·상태 메타 무본문과
`KNOWLEDGE_FAKE_PACK` 기반 오프라인 근거 초안·각주·사이드카를 검증한다.

## Drive 게시 (최종본)
취합 산출물은 `DRIVE_PUBLISH_ENABLED=1`일 때 assemble 직후 cha 본인 Drive의 `autophagy/제안서/<YYYY>/<YYYY-MM-DD>_<slug>.<확장자>`에 리뷰·기록용으로 자동 업로드된다(초안 제외, 게이트 없음). 날짜는 **최초 발행일로 고정**되어 재취합해도 사본이 늘지 않는다. `assemble --companion <경로>`로 명시 지정한 동반 자료(예: 이미지 프롬프트 원본)가 있으면 산출물과 companion이 `<YYYY-MM-DD>_<slug>/` 번들 폴더에 함께 저장되며, companion은 **원본 파일명을 그대로** 유지한다. 자동 발견·일괄 업로드는 금지다. 발행은 공용 파사드 `automation.drive_outputs`만 쓴다. 상세: `docs/guide/drive-publish.md`.

v2 `publish` 서브커맨드는 리뷰 아티팩트 관례에 따라 승인 없이 검증된 `DriveClient` 경로(owner-only 권한 검사 + SHA-256 재다운로드 대조)를 사용한다. 공유·권한 변경·알림은 이 스킬의 범위 밖이며 반드시 external-effect gate를 거쳐야 한다.

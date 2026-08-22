---
name: proposal
description: "개인 제안서 워크스페이스에서 섹션 Kanban·초안·인간 기여분·취합·Codex 최종 검토를 안전하게 관리한다. W5-4."
version: 1.1.0
author: autophagy-agents
license: MIT
metadata:
  hermes:
    tags: [Proposal, Private-Workspace, Kanban, Sensitivity-Gate, Codex-Review]
prerequisites:
  commands: [python3, hermes]
---

# proposal — 개인 제안서 작성 워크스페이스

모든 본문은 `~/proposals/<slug>/`(0700)과 그 하위 0600 파일에만 둔다. `PROPOSAL_STATUS_ROOT`는
본문 없이 `slug`, 섹션 키/제목/상태, Kanban 카드 ID만 가진 상태 메타데이터 위치다. 운영에서 repo
메타 worktree를 쓸 때만 이 환경변수로 지정한다. 기본 `~/.hermes/proposal-status/`도 0700이다.

## 절대 규칙

1. `draft --brief-file`은 전체 제안서와 브리프를 결정적 민감도 게이트로 먼저 검사한다. 적중하면
   `openai-codex/gpt-5.4`만 사용하며 GLM을 호출하지 않는다.
2. `review`는 `hermes -z --provider openai-codex -m gpt-5.4 -t todo` **1회만** 실행한다. 검토는
   취합본에 저장하고, `PROPOSAL_DM_TARGET` 또는 `~/.hermes/proposal/config.json`의 `dm_target`으로
   cha에게 DM한다. 재검토 대신 사람이 검토 결과를 직접 반영한다.
3. 타인 기여분은 사람이 전달한 `--file` 또는 `--text`만 `contribute`로 섹션에 접는다. URL/외부
   문서 자동 수집은 이 스킬에 없다. `--with-evidence`의 읽기 전용 개인 지식 조회는 타인 기여분
   수집이 아니며 아래 지식 파사드 규약만 따른다.
4. 섹션 카드는 전용 `proposal-<slug>` 보드에 `needs_input` 사유가 있는 `blocked`로 만든다. 이는
   실제 인간 입력 대기 상태이며, Ready 주차 용도가 아니다. 초안이 생기면 Ready로 옮기지 않고
   직접 완료해 디스패처 LLM 워커를 만들지 않는다.
5. 제안서 본문·검토 코멘트·외부 기여분을 repo, `docs/qa`, 공개 채널에 붙이지 않는다. 경로·SHA256·
   구조 assert만 증적으로 남긴다.

## Commands

```bash
# 섹션 구조와 실제 Hermes Kanban 카드 생성
python3 ~/.hermes/skills/proposal/scripts/proposal_cli.py create \
  --slug <kebab-slug> --title "제안서 제목" \
  --section need:필요성 --section approach:추진전략 --section impact:기대효과

# 섹션 현황과 추가
python3 ~/.hermes/skills/proposal/scripts/proposal_cli.py sections --slug <kebab-slug>
python3 ~/.hermes/skills/proposal/scripts/proposal_cli.py section-add \
  --slug <kebab-slug> --key budget --title 예산계획

# 사람이 준 본문을 섹션 초안으로 저장하거나, brief 기반 초안을 생성
python3 ~/.hermes/skills/proposal/scripts/proposal_cli.py draft \
  --slug <kebab-slug> --section need --file <local-file>
python3 ~/.hermes/skills/proposal/scripts/proposal_cli.py draft \
  --slug <kebab-slug> --section approach --brief-file <local-file> --with-evidence

# 생성 전에 원문을 노출하지 않는 팩 요약 또는 파사드 렌더 출처를 미리 확인
python3 ~/.hermes/skills/proposal/scripts/proposal_cli.py evidence \
  --slug <kebab-slug> --section approach --brief-file <local-file> --json

# 사람이 전달한 자료만 관련 섹션에 취합
python3 ~/.hermes/skills/proposal/scripts/proposal_cli.py contribute \
  --slug <kebab-slug> --section approach --source collaborator --file <local-file>

# 취합: 누락 섹션은 표지+리마인더를 출력하고 실패하지 않음
python3 ~/.hermes/skills/proposal/scripts/proposal_cli.py assemble --slug <kebab-slug>

# 최종 Codex 검토 1회와 cha DM
python3 ~/.hermes/skills/proposal/scripts/proposal_cli.py review --slug <kebab-slug>
```

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
최종 산출물은 `DRIVE_PUBLISH_ENABLED=1`일 때 cha 본인 Drive의 `Autophagy 산출물/proposal/<YYYY-MM>/`에 생성 즉시 자동 업로드된다(초안 제외, 리뷰용, 게이트 없음). 공통 vendored 헬퍼 `scripts/drive_publish.py` 사용. 루트=`DRIVE_OUTPUTS_ROOT`, 기간=`DRIVE_PUBLISH_PERIOD`. 상세: `docs/guide/drive-publish.md`.

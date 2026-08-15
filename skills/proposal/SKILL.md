---
name: proposal
description: "개인 제안서 워크스페이스에서 섹션 Kanban·초안·인간 기여분·취합·Codex 최종 검토를 안전하게 관리한다. W5-4."
version: 1.0.1
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
   문서 자동 수집은 이 스킬에 없다.
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
  --slug <kebab-slug> --section approach --brief-file <local-file>

# 사람이 전달한 자료만 관련 섹션에 취합
python3 ~/.hermes/skills/proposal/scripts/proposal_cli.py contribute \
  --slug <kebab-slug> --section approach --source collaborator --file <local-file>

# 취합: 누락 섹션은 표지+리마인더를 출력하고 실패하지 않음
python3 ~/.hermes/skills/proposal/scripts/proposal_cli.py assemble --slug <kebab-slug>

# 최종 Codex 검토 1회와 cha DM
python3 ~/.hermes/skills/proposal/scripts/proposal_cli.py review --slug <kebab-slug>
```

## Sandbox

`scripts/scenario.sh`은 더미 시크릿과 임시 0700 워크스페이스만 사용한다. Kanban과 DM을 비활성화한
상태로 섹션 생성·인간 기여분 취합·전체/누락 취합·상태 메타 무본문을 검증한다.

## Drive 게시 (최종본)
최종 산출물은 `DRIVE_PUBLISH_ENABLED=1`일 때 cha 본인 Drive의 `Autophagy 산출물/proposal/<YYYY-MM>/`에 생성 즉시 자동 업로드된다(초안 제외, 리뷰용, 게이트 없음). 공통 vendored 헬퍼 `scripts/drive_publish.py` 사용. 루트=`DRIVE_OUTPUTS_ROOT`, 기간=`DRIVE_PUBLISH_PERIOD`. 상세: `docs/guide/drive-publish.md`.

---
name: report
description: "개인 노트를 민감도 게이트 뒤 보고서·reveal.js 슬라이드·발표 대본으로 생성한다. 모든 산출물은 agent 전용 outputs에만 저장한다. W5-3."
version: 1.0.1
license: MIT
metadata:
  hermes:
    tags: [Private-Notes, Report, RevealJS, Sensitivity-Gate]
prerequisites:
  commands: [python3, hermes]
---

# report — 노트 → 보고서 → 슬라이드 → 대본

모든 입력은 `~/notes/`(700)에서만 읽고, 모든 산출물은 `~/outputs/`(700)에만
쓴다. 공유(Drive/repo 이동)는 자동으로 하지 않으며, cha의 명시적 공유 결정 후에만
별도 작업으로 수행한다.

## 절대 규칙

1. `!report`는 선택된 **모든** 노트를 결정적 민감도 게이트로 먼저 검사한다.
   `patent-sensitive` 적중 시 GLM을 절대 호출하지 말고 `openai-codex/gpt-5.4`만 쓴다.
2. 노트·보고서·슬라이드·대본 본문을 Discord 공개 채널, repo 또는 docs/qa에 붙이지
   않는다. CLI 출력의 경로·provider·건수만 응답에 사용한다.
3. `!slides`, `!script`는 이미 `~/outputs/`에 있는 보고서에서만 파생 산출물을 만든다.
   Drive/repo 이동 명령은 이 스킬에 없다.

## Commands

```bash
# !report [선택 키워드] — 최근 노트에서 보고서 초안 생성
python3 ~/.hermes/skills/report/scripts/report_cli.py report \
  [--query "키워드"] [--limit 12] [--title "보고서 제목"]

# !slides <report-path> — reveal.js HTML 생성
python3 ~/.hermes/skills/report/scripts/report_cli.py slides --report <~/outputs/report-*.md>

# !script <report-path> [slides-path] — 발표 대본 생성
python3 ~/.hermes/skills/report/scripts/report_cli.py script \
  --report <~/outputs/report-*.md> [--slides <~/outputs/slides-*.html>]
```

`!report`가 `자료 부족`을 반환하면 노트를 먼저 추가하거나 `--query`를 넓힌다.
민감 적중의 provider 결과만 DM으로 알려 주고, 원문·매칭어·본문은 표시하지 않는다.

## Sandbox

`scripts/scenario.sh`은 더미 시크릿과 임시 노트만 써서 보고서·슬라이드·대본의 구조,
빈 노트 처리, 민감 입력의 codex 전용 라우팅을 검증한다.

## Drive 게시 (최종본)
최종 산출물은 `DRIVE_PUBLISH_ENABLED=1`일 때 cha 본인 Drive의 `Autophagy 산출물/report/<YYYY-MM>/`에 생성 즉시 자동 업로드된다(초안 제외, 리뷰용, 게이트 없음). 공통 vendored 헬퍼 `scripts/drive_publish.py` 사용. 루트=`DRIVE_OUTPUTS_ROOT`, 기간=`DRIVE_PUBLISH_PERIOD`. 상세: `docs/guide/drive-publish.md`.

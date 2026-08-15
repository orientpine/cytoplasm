# 서류 종류 라이브러리 사용 안내

`doctype`는 Autophagy-Agent와의 **대화 중** 예시 서류를 등록·개선하고, 등록된 종류로 새 초안을 만드는
기능이다. 문서 주제가 구매/용역/사유서/보고서/제안서인지와 무관하게, 문서를 서류 종류로 등록하거나
개선하는 요청은 doctype이 처리한다. 문서 본문은 private store 밖에 저장하거나 Git·QA·공개 채널·채팅
결과에 노출하지 않는다.

## 1. 새 서류 종류 등록 — 문서 첨부 + `~로 등록` 또는 `스킬화`

Autophagy-Agent 대화에 `.docx`, `.hwpx`, `.md`, `.txt` 중 하나를 첨부하고 예를 들어
`업체추천사유서로 등록` 또는 `용역지시서 스킬화`라고 요청한다. 에이전트는 gateway가 받은 첨부 경로로
`doctype_cli.py register-from-example --name … --example …`을 실행한다.

같은 이름이 이미 등록되어 있으면 새로 저장하지 않고 `refine --name … --approved …`를 실행해 불변
버전을 추가한다. `.hwp` 또는 지원하지 않는 컨테이너는 `CONVERSION-REQUEST`로 거부되므로 HWPX 변환본을
첨부해야 한다.

## 2. 기존 종류 개선 — 승인 문서 첨부 + `개선` 또는 `refine`

승인된 개선본을 첨부하고 `업체추천사유서 개선` 또는 `업체추천사유서 refine`이라고 요청한다. 에이전트는
`doctype_cli.py refine --name … --approved … [--note …]`만 실행해 기존 종류에 새 private example과
불변 버전을 더한다. 존재하지 않는 이름은 fail-closed로 거부한다.

## 3. 새 초안 작성 — 등록된 종류 + 사실값

`업체추천사유서 작성: 업체명=…; 사업명=…`처럼 등록된 종류와 사실값을 준다. 에이전트는 사실값을 private
JSON에만 두고 `doctype_cli.py draft --name … --inputs-json … --out <private-path>`을 실행한다. narrative
섹션은 Codex가 작성하며, 초안 검토·승인과 제출은 cha가 직접 수행한다. 이 기능은 외부 제출·메일 발송을
하지 않는다.

## 4. 결과와 보안

에이전트는 CLI 실행 뒤 `show --name …`으로 private 본문 없는 메타데이터만 확인하고
**name/version/mode/fields**만 응답한다. 문서 본문을 채팅에 붙여 넣지 않는다. 예시·승인본·초안은
`DOCTYPE_PRIVATE_ROOT`의 0700/0600 private store에만 보관한다. 모든 요지 추출과 한국어 서술 초안은
`openai-codex/gpt-5.4`를 사용하고, 민감도 게이트는 GLM 경로를 fail-closed로 막는다.

# 과제비 원장 Sheet 설정 템플릿

> **목적**: W4-3(과제비 변경 감지·`!budget` 조회)에 필요한 Google Sheet 좌표 형식을 설명한다.
> 운영 규칙·스키마 상세: [`docs/guide/과제비-운영.md`](../docs/guide/과제비-운영.md).
> 실제 식별자는 repo에 기록하지 않고 런타임 환경의 `BUDGET_SHEET_ID`로만 주입한다.

## 좌표 (기계 소비용 — 키 이름 변경 금지)

| key | value |
|---|---|
| `budget_sheet_id` | `<spreadsheet-id>` |
| `budget_sheet_url` | `https://docs.google.com/spreadsheets/d/<spreadsheet-id>/edit` |
| `balance_tab` | `항목별 잔액` |
| `balance_header_range` | `항목별 잔액!A6:E6` |
| `balance_header_expected` | `항목,예산,집행액,잔액,최종수정` |
| `balance_data_start_row` | `7` |
| `ledger_tab` | `지출 이력` |
| `memo_tab` | `수동 메모` |
| `owner` | `<owner>` — 값 수정은 오너만, 에이전트는 읽기 전용 |

내부 `sheetId`(batchUpdate용): 항목별 잔액=`<balance-tab-id>`, 지출 이력=`<ledger-tab-id>`, 수동 메모=`<memo-tab-id>`.

## W4-3 소비 규칙

- 30분 주기 스냅샷 diff는 `balance_tab` 전체를 읽되, **`balance_data_start_row`(7행) 이후만** 비교한다 (1~4행은 운영 규칙 문구, 5행 공백, 6행 헤더).
- diff 전 `balance_header_range`가 `balance_header_expected`와 일치하는지 검증. 불일치 → diff 중단 + 오류 보고 (보정 절차: `docs/qa/W0-10/02-schema-validation.txt`).
- 인증: W0-6 gws OAuth 재사용 (`spreadsheets` 스코프 포함). 자격증명은 repo 밖(`~/.config/gws/`)에만 존재.

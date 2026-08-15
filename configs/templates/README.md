# 구매/용역 서류 템플릿 — registry 정책 (W4-4)

procurement 스킬(`skills/procurement/`)은 최초 양식을 **한 번 등록**하고 이후의 모든
초안 생성에서 동일한 저장 원본과 분석 맵을 재사용한다. 이 문서는 지원 형식 및 실양식
handoff 정책이다. 실제 양식과 값은 절대 repo에 커밋하지 않는다.

## 지원 형식 / 판별 방법

| 형식 | 판별 (컨테이너 검사) | 생성/검증 엔진 | 상태 |
|---|---|---|---|
| `.docx` | zip 멤버 `word/document.xml` | stdlib ZIP/XML: unpack → same-format run coalesce → replace → rezip → XML validate | ✅ 지원 |
| `.xlsx` | zip 멤버 `xl/workbook.xml` | openpyxl | ✅ 지원 |
| `.hwpx` | zip 멤버 `Contents/section0.xml` 또는 `mimetype`=`application/hwp+zip` | vendored honeypot `form_mapper` + `slot_filler` + namespace fix | ✅ 지원 |
| `.hwp` (바이너리) | OLE2/HWP3 매직 또는 `.hwp` 확장자 | — | ❌ 변환 요청 |
| 기타 | 비-zip 또는 지원 목록 밖 zip | — | ❌ 변환 요청 |

- 판별은 확장자가 아니라 **컨테이너 내용**(zip 멤버/매직 바이트) 기준이다. `.hwp`로
  이름만 바뀐 zip도 거부한다.
- 바이너리 `.hwp` 수신 시 `CONVERSION-REQUEST`(exit 3)를 내고 한글에서
  `다른 이름으로 저장 → HWPX(표준 문서)`한 사본을 요청한다. 이 경로의 생성 시도는 0건이다.
- DOCX/XLSX는 `{{품목}}` `{{금액}}` `{{업체}}` `{{일자}}` placeholder를 지원한다.
  Word token이 run 경계로 분리되어도 DOCX 엔진이 동일 formatting run을 합쳐 찾는다.
- HWPX는 기존 `{{}}`도 호환하지만, 실양식의 라벨(예: `품목`)과 인접한 빈 표 셀,
  `<hp:t/>`, 공백, 단일 기호를 그대로 유지해도 된다. 등록 시 deterministic mapper가
  위치/라벨을 `form_map.json`으로 저장한다. `일자`는 미답변 시 오늘(KST) 자동 입력한다.

## registry 구조

`~/.hermes/procurement/templates/<name>/` (directory 0700)에 아래를 저장한다.

| 파일 | 내용 | 권한 |
|---|---|---|
| `template.<format>` | cha가 최초 DM으로 준 원본 양식 | 0600 |
| `form_map.json` | HWPX slot address + deterministic semantic heuristic | 0600 |
| `placeholders.json` | DOCX/XLSX placeholder manifest | 0600 |
| `meta.json` | format, source SHA-256, created KST, field list | 0600 |

`register --name po_form --template <file>`은 기존 이름을 거부한다. 최초 양식을
명시적으로 교체할 경우만 `--force`를 쓴다. `templates-list`와 `templates-show`는 이름,
형식, field key만 노출하며 실제 양식 내용은 출력하지 않는다.

## 실양식 handoff ([USER] — cha)

1. 실제 구매/용역 양식 파일(.docx/.xlsx/.hwpx 권장)을 **Autophagy-Agent DM**으로 보낸다.
   바이너리 .hwp면 agent가 변환 사본을 요청한다.
2. agent는 `register --name <재사용-이름> --template <수신파일>`을 **한 번** 실행한다.
   HWPX는 `form_map.json`, DOCX/XLSX는 `placeholders.json`, 모든 형식은 원본 SHA-256와
   함께 보관한다.
3. 이후 cha는 같은 양식을 다시 보내지 않는다. agent는 `templates-show`의 필드를 DM으로
   확인하고 `generate --template-name <이름> --fields-json …`으로 저장 원본을 반복
   사용한 뒤 생성본만 검토 DM한다.
4. **제출은 항상 cha 본인이 한다** — 스킬은 문서를 외부 기관에 제출하지 않는다.

## 합성 증명

`skills/procurement/scripts/make_fixtures.py`는 실양식 대신 run-split DOCX와 라벨+빈
테이블 셀 HWPX를 만든다. `scripts/scenario.sh`와 unit test는 HWPX/DOCX 모두
register → store → reuse → fill → verify 및 HWPX ZIP timestamp 정규화 후 결정성을
오프라인으로 검증한다.

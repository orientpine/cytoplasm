# LOC 등록부 재측정 (G8)

## 무엇을

F2 품질 감사의 250 pure-LOC 예외 등록부(`automation/final/f2_loc_exceptions.txt`)를 **병렬 후속 스윕 8개 코드 그룹이 전부 머지된 시점의 HEAD에서 전수 재측정**해 다시 맞췄다. 초과 파일은 29개, 등록은 3개뿐이었다 → 26건을 각각의 사유와 함께 등록해 26건을 없앴다. 함께 `tests/unit/test_f2_loc_exceptions.py`를 넣어 등록부가 실제 측정과 **양방향**으로 어긋나면 CI에서 잡히게 했다.

## 왜

등록부는 "고치지 않아도 된다"가 아니라 **"왜 지금 안 고치는가"**를 남기는 자리다. 그런데 스윕이 진행되는 동안 각 그룹이 자기 파일에 코드를 더하면서 임계값을 넘는 파일이 계속 움직였고(보드 기재 19 → 계획 문서 27 → 실측 29), 등록부는 3개에 멈춰 있었다. 그래서 게이트가 **상시 red**였다 — 항상 실패하는 게이트는 진짜 위반이 섞여도 구분되지 않아 신호를 잃는다.

여기에 두 가지 구멍이 더 있었다.

- **게이트는 한쪽 방향만 본다.** 등록됐지만 이미 250 이하로 줄어든 죽은 항목은 영영 조용하다. 죽은 줄이 쌓이면 다음 사람이 그 줄을 근거로 멀쩡히 쪼갤 수 있는 파일을 그냥 둔다.
- **게이트는 CI에서 돌지 않는다.** `.github/workflows/ci.yml`은 ruff와 `pytest tests/unit`만 돌린다. 등록부가 어긋나도 PR에서는 아무 신호가 없고, 누군가 노드에서 F2 감사를 돌릴 때에야 드러난다.

두 구멍 모두 새 유닛 테스트가 덮는다.

## 사용 시나리오

- **정상**: `bash automation/final/f2_quality.sh` → `docs/qa/F2/module-loc.txt`에 `EXCEPTION` 29줄·`VIOLATION` 0줄, `LOC RESULT: PASS`.
- **새 초과(막힌다)**: 어떤 `.py`/`.sh`가 250 pure-LOC를 넘으면 `VIOLATION`으로 잡히고 `test_every_over_limit_module_is_registered`가 CI에서 먼저 실패한다. 쪼개거나, 왜 지금 안 쪼개는지를 한 줄로 등록한다.
- **죽은 등록(막힌다)**: 어떤 파일을 250 이하로 리팩터하면 `test_no_stale_entries_below_the_ceiling`이 "이제 예외가 필요 없다 — 지운다"고 실패한다. 등록부는 이렇게 **의도적으로만** 늘고 준다.

## 이번에 어떻게 판단했나

기계적으로 한 줄씩 붙이지 않고 파일마다 실제 내용을 보고 분류했다.

| 분류 | 건 | 사유의 성격 |
|---|---|---|
| vendored (`skills/mail/vendor/**`) | 6 | upstream 무수정 vendoring 규약상 LOC를 이유로 한 재구성이 금지 |
| 셸 | 5 (기존 3 + 신규 2) | 파이프라인 순서가 안전 계약 / 샌드박스에 통째로 복사돼 실행되는 자기완결 스크립트 |
| 승인 게이트 | 7 | 「병렬 confirm 구조 신설 금지」— 쪼개면 게이트마다 자기 render/resolve가 생긴다 |
| argparse CLI 표면 | 2 | LOC가 로직이 아니라 서브커맨드 수에 비례 |
| 특권·주입 경계 | 3 | root가 읽는 코드 표면 / 대부분이 exact-preimage 리터럴 |
| 순수 로직·빌더 | 5 | 부수효과 0 또는 공유 판정 복제 위험 |
| 배치 파이프라인 | 1 | 주 1회·비대화형이라 위험 낮음, 계층 분리는 후보 |

이 중 **진짜 분할 후보 5건**(`deploy-skill.sh` 550, `triage_gate.py` 467, `calendar_confirm.py` 437, `skill_gate.py` 429, `research_trends.py` 262)은 "naturally large"가 아니라 **"별도 사이클로 미룸 + 왜"**로 적었다. 나머지는 지금 쪼개면 오히려 나빠지는 것들이다.

## 관련

- 등록부 `automation/final/f2_loc_exceptions.txt` · 게이트 `automation/final/f2_quality.sh:51-68` · 회귀 `tests/unit/test_f2_loc_exceptions.py`
- 선행 정리: [F2 LOC 등록부 정리](loc-등록부-정리.md) · 작업 배분: `.omo/plans/parallel-followup-sweep.md` §5 G8
- 증적 `docs/qa/F2/module-loc.txt` · 승인·배포 대상 없음(감사 스크립트와 등록부만 바뀐다)

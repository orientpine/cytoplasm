# 아무리 긴 녹음도 손실은 그 구간에만 갇힌다 (구간 전사 · 격리 · 재개)

## 무엇을

로컬 전사(whisper.cpp)를 **녹음 전체 1회 실행**에서 **구간별 실행**으로 바꿨다. 기본 15분 창(겹침 15초)으로
나눠 구간마다 whisper 프로세스를 돌리고, 각 구간의 JSON 을 **무손실로 디코드**해 합친다. 어떤 구간이
어떤 이유로 실패하든 그 구간만 격리되고 자리에는 표식이 들어가며, 나머지 구간은 그대로 전사본에 도달한다.
성공한 구간은 `~/.hermes/speechtotext/windows/<키>/` 에 남아 **다시 돌리면 이어서** 한다.

**반복 의심 구간은 삭제 대신 접어서 보존한다(Q6(b)).** 회의 녹음의 한 창이 반복 점유율 상한을 넘으면 기존 실패 표식 아래에 시각·`repetition=…` 사유를 적은 HTML `<details>` 접힘을 붙인다. 예를 들어 61분 회의 중 15분이 반복 의심으로 격리돼도, 전사본에서 펼치면 그 창이 소유한 문장 원문을 읽을 수 있다. 재다듬기·화자 이름 반영은 접힘 안의 줄을 바꾸지 않으며, meeting은 접힌 본문을 LLM 추출 입력에서만 빼고 민감도 검사와 부록 원문에는 남긴다. JSON 파싱 불가·전사 필드 부재 등은 이전처럼 표식만 남고, 라이프로그(`SPEECHTOTEXT_ALLOW_INCOMPLETE=1`)는 기존 비접힘 동작을 유지한다. 원본 payload의 격리 저장과 `WHISPER-WINDOW-QUARANTINED` 로그도 유지한다.

## 왜

2026-09-04 사고: 61분짜리 Plaud 녹음의 whisper JSON 에 불완전 UTF-8 한글 바이트가 섞였고,
`stt_local.transcribe` 의 `json.loads(payload.read_text(encoding="utf-8"))` 가 `UnicodeDecodeError` 를 냈다.
그 예외는 `except (OSError, json.JSONDecodeError)` 에 걸리지 않아 그대로 탈출했고 — **바이트 2개 때문에
61분 전체가 사라졌다**. 2회 실패 후 클라우드 폴백이 요약도 전문도 없는 649 B 노트를 게시했다.

예외 절을 넓히는 것으로는 부족하다. 구조가 "전부 아니면 전무"인 한, 다음에는 분 118 의 타임아웃이나
디코더 붕괴가 같은 일을 한다. 그래서 **손실의 단위를 녹음에서 구간으로 내렸다**.

## 어떻게

1. **구간 계획** — `stt_window.plan_windows`: stride = 창 − 겹침, 마지막 구간은 끝까지, 창보다 짧은 녹음은
   구간 1개(예전과 같은 단일 실행). 창은 `SPEECHTOTEXT_WINDOW_MS`(기본 900000)·`SPEECHTOTEXT_WINDOW_OVERLAP_MS`(기본 15000).
2. **구간 실행** — `stt_window_run`: 정규화된 wav 하나를 `-ot <ms> -d <ms>` 로 구간만 읽는다(슬라이스 파일 0,
   whisper 가 **절대 오프셋**을 그대로 보고하므로 시간축 보정도 필요 없다). 예산도 구간 단위다.
3. **무손실 디코드** — `stt_window.decode_payload`: strict utf-8 로 읽고 실패하면 `errors="replace"` 로 다시 읽어
   **대체 문자 수를 함께 돌려준다**. `WHISPER-WINDOW-REPAIRED index=n replaced=k` 한 줄이 stderr 에 남는다.
4. **구간 격리** — 파싱 불가·rc≠0·타임아웃·반복 붕괴는 그 구간만 `WHISPER-WINDOW-QUARANTINED` 로 빠지고
   원본 payload 는 `quarantine/` 에 보존되며, 본문 자리에는
   `[전사 실패 구간 00:03:30–00:05:30 — 이 구간만 비어 있고 나머지는 그대로입니다].` 가 들어간다.
5. **재개** — 성공한 구간은 오디오 sha256·모델·구간 계획으로 키를 만든 캐시에 남고, 재실행은 잃은 구간만
   다시 돌린다. 전부 성공하면 캐시는 지워진다. 캐시 루트가 git 체크아웃 안이면 fail-closed 로 거부한다.
6. **부분 보존** — 커버리지·반복 거부가 나기 **전에** 여기까지의 전사본을 파일로 남기고 거부 메시지가 그 경로를 말한다.

## 사용 시나리오

- **정상(라이프로그)**: 2시간 녹음이 들어오면 8구간으로 전사되고, 3번 구간의 JSON 이 깨져도 나머지 7구간이
  노트 `## 전문` 에 들어간다. plaud 자식 env 는 `SPEECHTOTEXT_ALLOW_INCOMPLETE=1` 이라 표식이 붙은 부분
  전사본을 그대로 받는다 — 라이프로그는 회의록이 아니고, **표식 있는 부분 전사본이 빈 노트보다 낫다**.
- **실측(사고 원본 61분)**: 구간 0·1 에서 실제로 불완전 UTF-8 이 나왔고(`replaced=2`), 예전이라면 전량 소실이던
  자리에서 문자 2개만 대체된 채 전사가 계속됐다.
- **회의록 경로**: 잘린 전사본을 회의록으로 넘기지 않는 기존 거부(exit 8)는 그대로다. 다만 이제 거부 메시지가
  부분 전사본 경로를 함께 알려준다.
- **클라우드 폴백**: 요약과 전문이 **둘 다** 비면 노트를 게시하지 않고 백오프로 보류한다.
  클라우드는 매 틱 재확인하며 총 실패 상한(기본 5회)에도 비어 있으면 폐기한다.
  [전사 정책 게이트](plaud-전사-정책-게이트.md) 참조. 빈 노트가 vault 에 들어가는 경로는 닫혔다.

## 관련

- 티켓 t_4e3d6630 · 코드 `skills/speechtotext/scripts/{stt_window,stt_window_run,stt_window_store,stt_local}.py`,
  `automation/plaud_sync/{transcribe,transcribe_live,fetch}.py`
- 회귀 `tests/unit/test_stt_window.py`, `tests/unit/test_speechtotext_skill.py`, `tests/unit/test_plaud_sync_transcribe.py`
- 승인 게이트는 그대로다 — 전사는 노드 안에서 끝나고, vault 쓰기는 여전히 소유자 ✅ 다.

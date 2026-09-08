# 격리 STT 엔진 계약

Python 3.12/3.13, Linux x86_64/aarch64 전용 배치 CLI다. 메인 트리에 서드파티를 설치하지 않는다.
`bash provision.sh "$STT_ENGINES_VENV"` 또는 환경변수 `STT_ENGINES_VENV`로 절대 경로를 준다.
`HF_TOKEN`은 호출자가 환경으로 전달한다. 이 서비스는 비밀 파일을 읽거나 토큰을 복사하지 않는다.
venv·HF_HOME·UV_CACHE_DIR는 체크아웃 밖이어야 한다. 설치는 `uv sync --frozen --no-editable`이다.

정렬 전용 격리 venv의 `faster-whisper`와 `ctranslate2`는 WhisperX의 전이 의존성이며, ASR 경로는 `whisper.cpp` 그대로다 — 메인 트리 import는 0개다.

## CLI (todo 18/19 소비자용)

- `stt-engines diarize --wav <path> [--num-speakers N | --min N --max N] --mode exclusive|regular`
  - PCM16, 16000 Hz, mono WAV만 받는다. 수는 양의 정수이고 범위 양쪽을 함께 준다.
  - 기본 모드는 exclusive다. stdout은 `0.031 -- 3.456 speaker_00` 같은 줄뿐이다.
  - 단위는 초, 소수 3자리, 화자 번호는 관측 순서대로 0부터 매긴다. regular는 중첩 턴을 허용한다.
  - `stt_diarize.parse_output`을 그대로 사용할 수 있다. 사용자 화자명은 출력·셸에 전달하지 않는다.
- `stt-engines align --wav <path> --segments <json-file>`
  - 입력은 `[{"text":"가7!","start":0.0,"end":1.0}]`이다. 시각은 초이고 WAV 범위 안이어야 한다.
  - 출력은 **문자 단위** `[{"text":"가","start_ms":100,"end_ms":300,"score":0.9}, ...]`이다.
  - 입력 순서·문자(공백 포함)를 보존한다. 조합/어절 시각은 호출자가 집계한다.
  - 한국어 모델은 `kresnik/wav2vec2-large-xlsr-korean`으로 고정한다.
  - 어휘 밖 문자와 실패 문자는 세 수치 모두 JSON null이다. 최근접 시각 보간은 하지 않는다.
  - WhisperX가 문자열을 누락하거나 바꾸면 해당 입력 세그먼트 전체를 null로 돌린다.
    숫자·기호·모델명의 null은 정상이며 호출자는 토큰 시각으로 폴백한다.
- `stt-engines prepare --engine diarize|align`: 오디오 없이 해당 모델·정렬 보조 리소스를 캐시한다.
  provision은 두 엔진을 차례로 준비한다. stdout은 비어 있다.

진단은 stderr에만 쓴다(프로비저닝 CUDA 확인은 stdout). 실패 시 결과 stdout은 비운다.

| 종료 코드 | 의미 |
|---|---|
| 0 | 정상 완료. CPU도 정상이며 `STT-ENGINES-CPU-ONLY`로 명시한다 |
| 1 | 런타임/캐시/모델 실패, `STT-ENGINES-ERROR <예외유형>` |
| 2 | 입력/설정 오류 |
| 4 | `STT-ENGINES-NO-TOKEN`; 토큰 검사 전 네트워크·모델 import·자식 실행 없음 |
| 5 | `STT-ENGINES-GATED https://huggingface.co/<조직>/<모델>`; 소유자 동의 후 명시적 재실행 |
| 124 | 전체 실행 제한 초과, `STT-ENGINES-TIMEOUT` |
| 130 | SIGINT/SIGTERM 취소, `STT-ENGINES-CANCELLED` |

인자 오류는 토큰 검사보다 먼저 거부한다. 토큰은 오프라인 실행에서도 필수다.
게이트 URL은 HF 예외 응답에서 하위 모델 단위로 추출하며 쿼리·예외 원문·토큰은 출력하지 않는다.
진단 로그 원문은 민감 정보 보호를 위해 버리고 실패 유형/센티널을 재발행한다.
`STT_ENGINES_DEBUG=1`을 명시한 실패 실행만 전체 예외 체인·트레이스백을
`$HF_HOME/../stt-engines-debug/<시각>-<임의값>.log`에 0600으로 기록한다
(HF_HOME 미설정 시 `~/.cache/stt-engines/stt-engines-debug/`). stderr에는 기존 센티널에
파일의 절대 경로 한 줄만 추가한다. 체크아웃 내부·그쪽으로 향한 심링크는 거부하며,
덤프 저장 실패는 원문 없이 `STT-ENGINES-DEBUG-FAILED <예외유형>`으로 알린다.
변수 미설정·1 이외 값은 기존 출력과 동일하며 파일을 만들지 않는다.
덤프에는 토큰·서명 URL이 있을 수 있다. 원문을 공유하지 말고 필요한 예외 유형·메시지·프레임만
비밀을 마스킹해 공유한 뒤 파일을 삭제한다.

## 다운로드·캐시·CPU 정책

- `HF_HOME` 기본값은 `~/.cache/stt-engines/huggingface`다. 코드/의존성/모델 정책별 세대 아래
  엔진별로 잠근 뒤 `pending`에서 준비하고 성공한 경우만 `ready`로 원자적 승격한다.
- `ready`를 다음 프로세스가 사용할 때는 import **전** `HF_HUB_OFFLINE=1`을 설정한다.
  최초 성공 시 받아 둔 HF snapshot/ref를 재사용한다. 원격 main 변경은 자동 반영하지 않는다.
  의존성/모델 변경 시 `CACHE_VERSION`을 올린다. 불완전/깨진 ready는 온라인 자동 복구하지 않는다.
  새 HF_HOME을 지정하여 명시적으로 다시 준비한다.
- 첫 실행에서 `HF_HUB_OFFLINE=1`을 지정했는데 ready가 없으면 실패한다. 오프라인에서
  NLTK 리소스가 없을 때도 네트워크 폴백하지 않는다.
- 다운로드/추론 전체 제한은 `STT_ENGINES_TIMEOUT_SECONDS`(기본 3600, 1..86400초),
  HF 각 요청은 30초다. HF 0.36 전송의 backoff/스트리밍 재시도와 Xet/hf_transfer를 끈다.
  호출자는 실패 후 명시적으로 다시 실행한다. 음성은 네트워크로 전송하지 않는다.
- 취소/시간 초과는 자식 프로세스 그룹을 강제 종료하고 회수한다. 회수 중 반복 신호는 무시한다.
  SIGKILL로 남은 pending은 다음 실행이 단독 잠금을 잡은 후 제거한다. ready는 보존한다.
- 캐시 잠금 경쟁은 대기하지 않고 실패한다. 모델 다운로드·추론의 자동 재시도는 없다.
- cu128의 torch 2.8.0 aarch64 휠이 lock에서 거부되어 CPU 인덱스로 고정했다.
  CUDA 인덱스 선언은 보존했지만 현재 source는 CPU다. GB10/Blackwell 실동작은 **미검증**이다.
- aarch64 torchcodec은 torch 2.8 ABI 지원이 미검증이다. 이 CLI는 stdlib WAV→메모리 waveform을
  pyannote에 주고 numpy 오디오를 WhisperX에 주므로 torchcodec/ffmpeg 파일 디코딩에 의존하지 않는다.
- CPU 긴 녹음 제한은 **러너 소유**다. provision 기본 `STT_ENGINES_CPU_MAX_MS=600000`을
  호출자 서비스 환경에도 설정한다(스크립트 export는 부모 셸에 남지 않는다). 상한 초과(예: 11분)
  또는 길이 미상(`duration_ms=0`)인 녹음의 분리·정렬 후보는 러너가
  `DIARIZE-SKIP reason=cpu-only`로 건너뛴다. ASR 후보는 그대로 실행하며, CUDA 복구 시
  이 변수를 지우면 길이 제한을 해제한다. CLI 자체는 길이로 성공을 가장하지 않는다.

실제 gated 모델 다운로드, 노드 provision rc 0, GB10 추론/정렬, CPU 러너 skip은 todo 23 및
다른 러너 태스크의 acceptance다. 여기의 계약 테스트는 실제 모델 품질을 검증하지 않는다.

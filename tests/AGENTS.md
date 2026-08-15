# tests/ — 단위 + E2E

루트에서 실행: `pytest tests/unit` (루트 pytest 설정 없음 — 기본값 + `.pytest_cache`).
`configs/rag/*` 서비스는 자체 `pyproject.toml`의 pytest를 별도로 가진다(`tests/**`는 S101 제외).

## 구조
```
tests/
├── unit/                # 순수 로직·게이트·상태머신 단위 테스트 (56+)
└── e2e/
    ├── drivers/         # 게이트·피어·훅 경계를 구동하는 드라이버
    ├── scenarios/       # 종단 시나리오 스크립트
    └── fixtures/        # 고정 입력 (예: w2-personal-memory)
```

## e2e 규약
- scenario YAML(`scenarios/*.yaml`)은 `cases`/`expect` 스키마. driver/actor가 `OBS-JSON` 한 줄을 출력하고 `drivers/judge_expectations.py`가 exact-equality로 판정.
- fixture는 `e2e/fixtures/<시나리오>/`에 시나리오 전용 자산(입력 md + 기록된 LLM 응답 JSON 등).
- `conftest.py` 없음 — unit은 `tmp_path`/`monkeypatch`/stub 실행파일/env override 패턴을 직접 사용.

## 규칙
- **버그 수리는 RED→GREEN 회귀 고정 선행.** 특히 no-agent cron의 자식 subprocess 자격증명 전파는
  회귀로 못박는다 — 선례: `test_calendar_confirm_watch_subprocess.py`,
  `test_coordination_confirm_watch_subprocess.py` (부모 env에 토큰 없고 accessor만 해석 가능한 상황에서 자식이 토큰을 받는지 검증).
- 게이트/보안 경계(`external_effect_gate`, 승인 판정, deadlock/재협상)는 mutation 로직 변경 시 반드시 단위 테스트 동반.
- 테스트는 실시크릿·실외부효과를 유발하지 않는다 — 격리(`E2E_TEST_MODE`/DUMMY 시크릿/서명 주입) 경로만 사용.

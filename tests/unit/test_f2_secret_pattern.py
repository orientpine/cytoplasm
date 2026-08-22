"""F2 시크릿 스캔 패턴은 진짜 키만 잡고 `task-` 파일명에는 걸리지 않아야 한다.

`automation/final/f2_quality.sh` 의 `secret_pattern` 은 `sk-[[:alnum:]_-]{20,}` 를
앵커 없이 쓰고 있었다. 그런데 `sk-` 는 **`task-` 의 부분문자열**이라, 20자 이상 이어지는
`task-10-green-unresolved-target-result` 같은 이름이 그대로 OpenAI 키 모양으로 읽혔다.

측정(2026-08-21, `085d0d04`): 추적 트리에서 109건이 매칭됐고 그중 108건이
`.omo/evidence/fs3/**` 의 증적 파일명, 1건이 그 경로를 문자열로 든 소스였다. 진짜 시크릿
모양은 0건이다. 스윕 직전(`14d5678e~1`)에는 0건이었으므로 이 회귀는 증적 파일명이
들어오면서 생겼다.

상시 red 인 시크릿 스캐너는 없는 것보다 나쁘다 — 진짜 유출이 섞여도 구분되지 않는다.
그래서 저장소 규약도 「토큰 모양 문자열을 주석/문서에도 쓰지 말라 — secret-scan 오탐이
배포를 막는다」를 안티패턴으로 못박고 있다. 여기서는 그 판정을 양방향으로 고정한다:
경계에 붙은 진짜 키는 계속 잡히고, `task-` 오탐은 잡히지 않는다.

`f2_quality.sh` 는 CI 에서 돌지 않으므로(ci.yml 은 ruff 와 `pytest tests/unit` 만
돌린다) 이 테스트가 유일한 상시 신호다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_GATE: Final = _REPO / "automation" / "final" / "f2_quality.sh"

#: 리터럴로 적으면 이 파일 자체가 시크릿 스캐너·gitleaks 의 표적이 되므로 런타임에 조립한다.
_FAKE_OPENAI_KEY: Final = "sk-" + ("a1b2c3d4e5" * 3)
_FAKE_GH_TOKEN: Final = "ghp_" + ("A1b2C3d4E5" * 4)


def _secret_pattern() -> str:
    """게이트 스크립트에서 실제로 쓰이는 패턴을 추출한다(사본을 만들지 않는다)."""
    for line in _GATE.read_text(encoding="utf-8").splitlines():
        if line.startswith("secret_pattern="):
            return line.split("=", 1)[1].strip("'")
    raise AssertionError(f"secret_pattern 정의를 찾지 못했다: {_GATE}")


def _matches(pattern: str, text: str) -> bool:
    """`git grep -E` 와 같은 POSIX ERE 의미로 판정한다."""
    completed = subprocess.run(
        ["grep", "-qE", pattern],
        input=text,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def test_real_secret_shapes_still_match() -> None:
    pattern = _secret_pattern()
    for text in (
        _FAKE_OPENAI_KEY,
        f"OPENAI_API_KEY={_FAKE_OPENAI_KEY}",
        f"key: {_FAKE_OPENAI_KEY}",
        f'"{_FAKE_OPENAI_KEY}"',
        _FAKE_GH_TOKEN,
    ):
        assert _matches(pattern, text), f"진짜 시크릿 모양을 놓쳤다: {text[:16]}..."


def test_task_filenames_are_not_flagged() -> None:
    pattern = _secret_pattern()
    for text in (
        ".omo/evidence/fs3/task-10-green-unresolved-target-result.txt",
        "python3 .omo/evidence/fs3/task-12-already-fixed-probe.py 29054bdc",
        ".omo/evidence/fs3/red/task-12-scenario-drift-probe.py",
        "task-11-parallel-followup-sweep-3.txt",
    ):
        assert not _matches(pattern, text), f"`task-` 오탐이 되살아났다: {text}"


def test_tracked_tree_has_no_secret_shaped_content() -> None:
    """게이트가 실제 트리에서 통과하는지 — 오탐이 남아 있으면 여기서 드러난다."""
    completed = subprocess.run(
        [
            "git",
            "grep",
            "-nEI",
            _secret_pattern(),
            "--",
            ":!docs/qa/**",
            ":!*.md",
            ":!tests/**",
        ],
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=False,
        env={"GIT_MASTER": "1", "PATH": "/usr/bin:/bin", "HOME": str(_REPO)},
    )
    assert completed.returncode == 1, (
        "추적 트리에 시크릿 모양 내용이 있다(또는 grep 이 실패했다):\n"
        f"exit={completed.returncode}\n{completed.stdout[:2000]}"
    )


def test_pattern_keeps_boundary_anchor() -> None:
    """앵커를 다시 떼면 위 두 판정이 조용히 뒤집히므로 문자열 자체를 고정한다."""
    pattern = _secret_pattern()
    assert "sk-" in pattern, "OpenAI 키 접두 검사가 사라졌다"
    assert "[^[:alnum:]]" in pattern, "경계 앵커가 사라져 `task-` 오탐이 되돌아온다"

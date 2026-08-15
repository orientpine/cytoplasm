"""LOC 예외 등록부는 **양방향**으로 실제 측정과 맞아야 한다.

`automation/final/f2_quality.sh:51-68` 이 추적된 `automation/**`·`skills/**` 의 모든
`.py`/`.sh` 를 250 pure-LOC 기준으로 재고, 초과분에 등록부 항목이 없으면 VIOLATION 을
낸다. 그 게이트는 **한쪽 방향만** 본다 — 등록됐지만 이미 250 이하로 줄어든 죽은 항목은
영영 조용하다. 죽은 항목이 쌓이면 "왜 지금 안 고치는가"라는 등록부의 목적이 사라지고,
다음 사람이 그 줄을 근거로 멀쩡히 분할 가능한 파일을 그냥 둔다.

그리고 게이트 자체는 CI 에서 돌지 않는다 — `.github/workflows/ci.yml` 은 ruff 와
`pytest tests/unit` 만 돌린다. 즉 등록부가 어긋나도 PR 에서는 아무 신호가 없고,
누군가 노드에서 F2 감사를 돌릴 때에야 드러난다. 그래서 같은 판정을 여기서 고정한다.

배경(2026-08-04, G8): 병렬 후속 스윕의 8개 코드 그룹이 머지되며 초과 파일이 19→29 로
움직였는데 등록부는 3개뿐이라 LOC 게이트가 상시 red 였다. 게이트가 항상 실패하면 진짜
위반이 섞여도 구분되지 않아 신호를 잃는다.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Final

_REPO: Final = Path(__file__).resolve().parents[2]
_REGISTRY: Final = _REPO / "automation" / "final" / "f2_loc_exceptions.txt"
_GATE: Final = _REPO / "automation" / "final" / "f2_quality.sh"

#: f2_quality.sh:59 의 임계값
_CEILING: Final = 250
#: f2_quality.sh:60 의 `grep -F "$path | "` 가 요구하는 구분자
_SEPARATOR: Final = " | "


def _pure_loc(path: Path) -> int:
    """f2_quality.sh:57 의 awk 와 같은 셈: 공백 줄과 주석 전용 줄을 뺀 나머지."""
    count = 0
    for line in path.read_text(encoding="utf-8", errors="surrogateescape").splitlines():
        stripped = line.lstrip(" \t")
        if not stripped or stripped.startswith("#"):
            continue
        count += 1
    return count


def _tracked_candidates() -> tuple[Path, ...]:
    """게이트가 재는 것과 같은 집합: 추적된 automation/**·skills/** 의 .py/.sh."""
    listing = subprocess.run(
        ("git", "ls-files"),
        cwd=_REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return tuple(
        Path(entry)
        for entry in listing
        if entry.startswith(("automation/", "skills/")) and entry.endswith((".py", ".sh"))
    )


def _registry_entries() -> tuple[tuple[str, str], ...]:
    entries: list[tuple[str, str]] = []
    for line in _REGISTRY.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        path, separator, reason = line.partition(_SEPARATOR)
        assert separator, f"등록부 줄에 ' | ' 구분자가 없다: {line!r}"
        entries.append((path, reason))
    return tuple(entries)


def _violations() -> dict[str, int]:
    return {
        str(candidate): loc
        for candidate in _tracked_candidates()
        if (loc := _pure_loc(_REPO / candidate)) > _CEILING
    }


def test_gate_constants_still_match_the_registry_contract() -> None:
    """게이트가 임계값이나 구분자를 바꾸면 이 테스트의 전제가 무너진다."""
    gate = _GATE.read_text(encoding="utf-8")
    assert f"pure_loc > {_CEILING}" in gate
    assert f'grep -F "$path{_SEPARATOR}"' in gate


def test_every_entry_names_a_tracked_file_with_a_reason() -> None:
    for path, reason in _registry_entries():
        assert (_REPO / path).is_file(), f"등록부가 없는 파일을 가리킨다: {path}"
        assert path in {str(candidate) for candidate in _tracked_candidates()}, (
            f"게이트가 재지 않는 경로다(추적 automation/**·skills/** 의 .py/.sh 아님): {path}"
        )
        assert reason.strip(), f"사유 없는 등록은 허용하지 않는다: {path}"


def test_no_duplicate_entries() -> None:
    paths = [path for path, _ in _registry_entries()]
    assert len(paths) == len(set(paths)), "같은 경로가 두 번 등록됐다"


def test_every_over_limit_module_is_registered() -> None:
    """게이트와 같은 방향 — 미등록 초과분이 있으면 F2 가 red 가 된다."""
    registered = {path for path, _ in _registry_entries()}
    unregistered = {path: loc for path, loc in _violations().items() if path not in registered}
    assert not unregistered, (
        "250 pure-LOC 를 넘었는데 등록부에 없다. 분할하거나, 왜 지금 안 고치는지 사유와 함께 "
        f"automation/final/f2_loc_exceptions.txt 에 등록한다: {unregistered}"
    )


def test_no_stale_entries_below_the_ceiling() -> None:
    """게이트가 못 보는 반대 방향 — 250 이하로 줄었으면 그 줄은 지운다."""
    violations = _violations()
    stale = [path for path, _ in _registry_entries() if path not in violations]
    assert not stale, (
        "이제 250 pure-LOC 이하라 예외가 필요 없다 — 등록부에서 지운다: " f"{stale}"
    )

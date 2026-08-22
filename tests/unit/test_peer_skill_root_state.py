r"""아카이브된 스킬은 충돌이 아니다 — 안 그러면 가드가 안내한 해법이 막다른 길이 된다.

`deploy-skill.sh` 의 SS-1 가드는 peer 스킬 루트에 동명 디렉터리가 있으면 배포를 멈추고,
메시지로 `hermes curator archive <name>` 을 안내한다. 그런데 그 명령은 디렉터리를
**`.archive/<name>` 으로 옮길 뿐**이고, 중첩 검사가 `root.glob("*/" + name)` 이라
pathlib 이 숨김 디렉터리도 매칭해 옮겨진 사본이 다시 잡혔다 — 판정이 `foreign` 에서
`foreign` 으로 그대로여서 **안내대로 해도 영영 풀리지 않았다**(2026-08-20 실측: prompt
배포가 archive 이후에도 같은 코드로 차단).

여기서는 셸에 박힌 그 파이썬 블록을 **소스에서 직접 뽑아** 돌린다 — 로직을 테스트에
베껴 적으면 그 사본이 갈라져 실제 가드가 아닌 것을 검증하게 된다.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DEPLOY_SKILL = _REPO / "automation" / "deploy-skill.sh"


def _classifier() -> Path:
    source = _DEPLOY_SKILL.read_text(encoding="utf-8")
    body = source.split("peer_skill_root_state() {", 1)[1].split("<<'PY'", 1)[1]
    body = body.split("\nPY\n", 1)[0].replace('\\"', '"').replace("\\$", "$")
    program = Path(tempfile.mkdtemp()) / "classify.py"
    _ = program.write_text(body, encoding="utf-8")
    return program


def _classify(build, name: str = "prompt") -> str:
    root = Path(tempfile.mkdtemp())
    build(root, name)
    return subprocess.run(
        ("python3", str(_classifier()), str(root), name),
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()


def _write_skill(directory: Path, *, signed: bool) -> None:
    directory.mkdir(parents=True)
    marker = "author: autophagy-agents\n" if signed else ""
    _ = (directory / "SKILL.md").write_text(f"---\nname: x\n{marker}---\n", encoding="utf-8")


def test_an_archived_skill_does_not_block_the_deploy() -> None:
    """가드가 안내한 해법(`hermes curator archive`)이 실제로 통하는지."""
    verdict = _classify(lambda root, name: _write_skill(root / ".archive" / name, signed=False))

    assert verdict == "absent", "아카이브된 사본이 여전히 배포를 막는다 — 안내가 막다른 길이 된다"


def test_our_own_staging_residue_is_overwritten() -> None:
    verdict = _classify(lambda root, name: _write_skill(root / name, signed=True))

    assert verdict == "staging-residue"


def test_an_unsigned_copy_still_blocks() -> None:
    """가드를 약화하지 않는다 — 우리 것이라고 말하지 않는 사본은 그대로 차단."""
    verdict = _classify(lambda root, name: _write_skill(root / name, signed=False))

    assert verdict == "foreign"


def test_a_real_nested_self_skill_still_blocks() -> None:
    """Hermes 는 자가 스킬을 카테고리 디렉터리 아래에 둔다 — 그건 여전히 충돌이다."""
    verdict = _classify(lambda root, name: _write_skill(root / "category" / name, signed=False))

    assert verdict == "foreign"

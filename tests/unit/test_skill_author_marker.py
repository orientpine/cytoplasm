r"""모든 스킬은 `author: autophagy-agents` 마커를 들고 있어야 한다.

peer 계정의 스킬 루트는 배포 샌드박스 staging 과 같은 공간이라, 지난 실행의 사본이 남는다.
`deploy-skill.sh` 의 SS-1 가드는 그 잔여물을 **우리 것**으로 알아볼 때만 덮어쓰고, 판단
근거가 바로 이 마커다(`peer_skill_root_state`: `.usage.json` 이 agent-created 라고 하거나
frontmatter 에 마커가 없으면 `foreign` → fail-closed 차단).

2026-08-20 실측: `~/.hermes/skills/prompt` 가 리포의 `skills/prompt` 와 **바이트 동일**한
우리 자신의 잔여 사본이었는데, `skills/prompt/SKILL.md` 에 마커가 없어 `foreign` 으로 분류돼
`SELF-SKILL-COLLISION-BLOCK` 이 났다. 그때 17개 중 8개가 마커를 갖고 있지 않았고 — 잔여
사본이 있는 것이 `prompt` 하나였을 뿐 나머지 7개도 같은 지뢰였다.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_MARKER = "author: autophagy-agents"


def _frontmatter(skill_md: Path) -> list[str]:
    lines = skill_md.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return []
    end = next((i for i in range(1, len(lines)) if lines[i] == "---"), 0)
    return lines[1:end]


def test_every_skill_declares_our_authorship() -> None:
    skills = sorted(p for p in (_REPO / "skills").iterdir() if (p / "SKILL.md").is_file())
    missing = [p.name for p in skills if _MARKER not in _frontmatter(p / "SKILL.md")]

    assert skills, "스킬을 하나도 찾지 못했다 — 경로 유도가 깨졌다"
    assert not missing, (
        "이 스킬들은 peer 루트에 자기 잔여 사본이 남는 순간 배포가 fail-closed 로 막힌다 — "
        f"SKILL.md frontmatter 에 '{_MARKER}' 를 넣어라: {missing}"
    )

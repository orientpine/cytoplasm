"""The release-wide deploy must reuse the one release approval for every skill."""
from __future__ import annotations

from pathlib import Path


def test_deploy_all_passes_the_release_approval_to_skill_deploys() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "automation" / "deploy_all.sh"
    ).read_text(encoding="utf-8")

    assert (
        '"$repo_root/automation/deploy-skill.sh" "$arg" --release-approval'
        in source
    )


def test_deploy_all_actions_loop_reads_on_a_private_fd() -> None:
    """ssh inside the deployers drains fd 0, so the loop must not live there.

    2026-08-31 실측: `done <<< "$actions"` 로 actions 를 stdin 에 실었더니 첫
    deploy-skill 안의 ssh 가 남은 action 줄을 전부 삼켜 매 실행 한 건만 배포됐다.
    """
    source = (
        Path(__file__).resolve().parents[2] / "automation" / "deploy_all.sh"
    ).read_text(encoding="utf-8")

    assert "read -r -u 9 tag kind arg" in source
    assert 'done 9<<< "$actions"' in source
    assert 'done <<< "$actions"' not in source

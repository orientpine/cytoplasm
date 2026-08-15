from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

from automation.skill_review import main, skill_digest


def _skill(tmp_path: Path, name: str = "reviewable") -> Path:
    skill_dir = tmp_path / name
    scripts = skill_dir / "scripts"
    scripts.mkdir(parents=True)
    _ = (skill_dir / "SKILL.md").write_text(
        f'---\nname: {name}\ndescription: "A deterministic test skill for review checks."\n---\n',
        encoding="utf-8",
    )
    scenario = scripts / "scenario.sh"
    _ = scenario.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n[[ \"${AUTOPHAGY_DEMO_SECRET:-}\" == DUMMY-* ]]\nprintf 'SCENARIO-PASS\\n'\n",
        encoding="utf-8",
    )
    scenario.chmod(0o700)
    return skill_dir


def _review(skill_dir: Path) -> int:
    digest = skill_digest(skill_dir)
    return main(
        [
            "review",
            "--skill",
            skill_dir.name,
            "--skill-dir",
            str(skill_dir),
            "--hash",
            digest,
        ]
    )


def _deploy_skill_digest(skill_dir: Path) -> str:
    command = (
        "cd \"$1\" && find . -type f -not -path '*/__pycache__/*' "
        "-not -name '*.pyc' -not -name '*.pyo' | LC_ALL=C sort | "
        "xargs sha256sum | sha256sum | cut -d' ' -f1"
    )
    completed = subprocess.run(
        ["bash", "-c", command, "skill-digest", str(skill_dir)],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def test_check_when_exact_pass_verdict_exists_then_allows_and_keeps_private_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a legitimate skill and an isolated verdict ledger.
    skill_dir = _skill(tmp_path)
    verdicts = tmp_path / "gate" / "review-verdicts.jsonl"
    monkeypatch.setenv("REVIEW_VERDICTS_PATH", str(verdicts))

    # When: the deterministic review records a PASS for the skill's current hash.
    assert _review(skill_dir) == 0

    # Then: only that exact hash is authorized and the ledger is mode 600.
    assert main(["check", "--skill", skill_dir.name, "--hash", skill_digest(skill_dir)]) == 0
    assert stat.S_IMODE(verdicts.stat().st_mode) == 0o600


def test_skill_digest_when_python_cache_exists_then_is_source_only_and_matches_deploy_formula(
    tmp_path: Path,
) -> None:
    # Given: a skill with a clean source digest.
    skill_dir = _skill(tmp_path)
    clean_digest = skill_digest(skill_dir)
    cache = skill_dir / "scripts" / "__pycache__"
    cache.mkdir()
    _ = (cache / "scenario.cpython-313.pyc").write_bytes(b"volatile bytecode")
    _ = (skill_dir / "legacy.pyo").write_bytes(b"volatile optimized bytecode")

    # When: Python cache artifacts appear in the tree.
    cached_digest = skill_digest(skill_dir)

    # Then: neither reviewer nor deploy formula binds those volatile artifacts.
    assert cached_digest == clean_digest
    assert cached_digest == _deploy_skill_digest(skill_dir)


def test_check_when_fail_verdict_is_newest_then_blocks_mount(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a skill whose frontmatter cannot pass review.
    skill_dir = _skill(tmp_path)
    _ = (skill_dir / "SKILL.md").write_text("---\nname: reviewable\n---\n", encoding="utf-8")
    verdicts = tmp_path / "review-verdicts.jsonl"
    monkeypatch.setenv("REVIEW_VERDICTS_PATH", str(verdicts))

    # When: review records FAIL.
    assert _review(skill_dir) == 1

    # Then: a matching hash still cannot authorize mount.
    assert main(["check", "--skill", skill_dir.name, "--hash", skill_digest(skill_dir)]) == 1


def test_check_when_no_verdict_exists_then_blocks_mount(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: no ledger has been created.
    monkeypatch.setenv("REVIEW_VERDICTS_PATH", str(tmp_path / "absent.jsonl"))

    # When / Then: fail closed rather than treating absence as approval.
    assert main(["check", "--skill", "reviewable", "--hash", "a" * 64]) == 1


def test_check_when_pass_is_for_a_different_hash_then_blocks_mount(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: version A has a recorded PASS.
    skill_dir = _skill(tmp_path)
    verdicts = tmp_path / "review-verdicts.jsonl"
    monkeypatch.setenv("REVIEW_VERDICTS_PATH", str(verdicts))
    assert _review(skill_dir) == 0

    # When: a different version hash is offered for mount.
    different_hash = "b" * 64

    # Then: the PASS for version A does not authorize version B.
    assert main(["check", "--skill", skill_dir.name, "--hash", different_hash]) == 1


def test_review_when_skill_contains_openai_secret_shape_then_records_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a otherwise-valid skill with a token-shaped literal.
    skill_dir = _skill(tmp_path)
    _ = (skill_dir / "token.txt").write_text("sk-live-secret-value-12345", encoding="utf-8")
    verdicts = tmp_path / "review-verdicts.jsonl"
    monkeypatch.setenv("REVIEW_VERDICTS_PATH", str(verdicts))

    # When: review scans every skill file.
    result = _review(skill_dir)

    # Then: it records a FAIL, never an authorizing PASS.
    assert result == 1
    assert main(["check", "--skill", skill_dir.name, "--hash", skill_digest(skill_dir)]) == 1


def test_plain_word_after_bot_is_not_a_secret(tmp_path: Path) -> None:
    # Given: a harmless comment with a natural-language word after "bot".
    skill_dir = _skill(tmp_path)
    _ = (skill_dir / "comment.md").write_text("# Discord bot attachment ceiling\n", encoding="utf-8")

    # When: the secret scanner examines the directory.
    result = _review(skill_dir)

    # Then: prose is not treated as a token-shaped secret.
    assert result == 0


def test_realistic_bot_token_still_detected(tmp_path: Path) -> None:
    # Given: a Discord-style bot authorization token.
    skill_dir = _skill(tmp_path)
    _ = (skill_dir / "authorization.txt").write_text(
        "Authorization: Bot MTA2NzYzO.GhIjKl.mn-opqrstuvwx12345\n",
        encoding="utf-8",
    )

    # When: the secret scanner examines the directory.
    result = _review(skill_dir)

    # Then: token-shaped credentials still fail the scan.
    assert result == 1


def test_review_when_skill_directory_is_relative_then_runs_its_scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: a legitimate skill addressed relatively from the deploy working directory.
    skill_dir = _skill(tmp_path)
    verdicts = tmp_path / "review-verdicts.jsonl"
    monkeypatch.setenv("REVIEW_VERDICTS_PATH", str(verdicts))
    monkeypatch.chdir(tmp_path)
    relative_skill_dir = Path(skill_dir.name)

    # When: review executes the scenario without pre-captured sandbox output.
    result = main(
        [
            "review",
            "--skill",
            relative_skill_dir.name,
            "--skill-dir",
            str(relative_skill_dir),
            "--hash",
            skill_digest(relative_skill_dir),
        ]
    )

    # Then: the relative path resolves once and the verdict is PASS.
    assert result == 0

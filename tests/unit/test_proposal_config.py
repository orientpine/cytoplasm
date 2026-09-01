from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "proposal" / "scripts"))

from proposal_config import ConfigError, ProposalConfig, load_config, main, preflight  # noqa: E402


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "docbot"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-m",
            "x",
            "-q",
        ],
        check=True,
    )
    seed = root / "resource" / "(주제1) R&D 연구계획서 양식.hwpx"
    seed.parent.mkdir()
    seed.write_bytes(b"seed")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-m",
            "seed",
            "-q",
        ],
        check=True,
    )
    pin = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    digest = hashlib.sha256(seed.read_bytes()).hexdigest()
    return root, pin, digest


def _env(root: Path, pin: str, digest: str) -> dict[str, str]:
    return {
        "PROPOSAL_DOCBOT_ROOT": str(root),
        "PROPOSAL_DOCBOT_PIN": pin,
        "PROPOSAL_SEED_SHA256": digest,
    }


def test_pin_mismatch_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _, digest = _repo(tmp_path)
    monkeypatch.setenv("PROPOSAL_DOCBOT_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_DOCBOT_PIN", "0" * 40)
    monkeypatch.setenv("PROPOSAL_SEED_SHA256", digest)
    with pytest.raises(SystemExit) as exc:
        main(["--preflight"])
    assert exc.value.code == 4 and "ENGINE-PIN-BLOCK" in capsys.readouterr().err


def test_last_character_pin_mismatch_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, pin, digest = _repo(tmp_path)
    last = format((int(pin[-1], 16) + 1) % 16, "x")
    near_pin = pin[:-1] + last
    monkeypatch.setenv("PROPOSAL_DOCBOT_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_DOCBOT_PIN", near_pin)
    monkeypatch.setenv("PROPOSAL_SEED_SHA256", digest)
    with pytest.raises(SystemExit) as exc:
        main(["--preflight"])
    assert exc.value.code == 4
    stderr = capsys.readouterr().err
    assert "ENGINE-PIN-BLOCK" in stderr and "pin-mismatch" in stderr


def test_dirty_worktree_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, pin, digest = _repo(tmp_path)
    (root / "dirty").write_text("x")
    monkeypatch.setenv("PROPOSAL_DOCBOT_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_DOCBOT_PIN", pin)
    monkeypatch.setenv("PROPOSAL_SEED_SHA256", digest)
    with pytest.raises(SystemExit) as exc:
        main(["--preflight"])
    assert exc.value.code == 4 and "ENGINE-PIN-BLOCK" in capsys.readouterr().err


def test_happy_path(tmp_path: Path) -> None:
    root, pin, digest = _repo(tmp_path)
    cfg = load_config(_env(root, pin, digest))
    report = preflight(cfg)
    assert isinstance(cfg, ProposalConfig) and report.ok


def test_defaults_and_invalid_values() -> None:
    cfg = load_config({"PROPOSAL_DOCBOT_PIN": "a" * 40})
    # resource/rule.md asks for "10 페이지 내외"; the skill must not spend a
    # generation budget on thirty pages unless the owner asks for them.
    assert cfg.profile == "10-page"
    with pytest.raises(ConfigError):
        load_config({"PROPOSAL_DOCBOT_PIN": "a" * 39})
    with pytest.raises(ConfigError):
        load_config({"PROPOSAL_DOCBOT_PIN": "a" * 40, "PROPOSAL_PROFILE": "bad"})

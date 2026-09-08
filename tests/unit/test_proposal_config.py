from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills" / "proposal" / "scripts"))

import proposal_config  # noqa: E402
from proposal_config import (  # noqa: E402
    ConfigError,
    ProposalConfig,
    load_config,
    main,
    preflight,
)

SEED_NAME = "(주제1) R&D 연구계획서 양식.hwpx"


def test_config_declares_no_external_engine_checkout() -> None:
    cfg = load_config({})

    for retired in ("docbot_root", "docbot_pin", "seed_hwpx_relpath", "seed_sha256"):
        assert not hasattr(cfg, retired), f"{retired} outlived the external checkout"


@pytest.mark.skipif(
    not (Path(__file__).resolve().parents[2] / "skills" / "proposal" / "engine").is_dir(),
    reason="skills/proposal/engine is manifest-excluded from the public export; this contract holds only where the engine ships",
)
def test_preflight_passes_on_the_engine_this_repository_ships() -> None:
    report = preflight(load_config({}))

    assert report.ok, report.reasons
    assert Path(report.engine_root).is_dir()
    assert report.seed_sha256 is not None and len(report.seed_sha256) == 64


def test_missing_engine_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(proposal_config, "ENGINE_ROOT", tmp_path / "absent")

    with pytest.raises(SystemExit) as raised:
        main(["--preflight"])

    assert raised.value.code == 4
    stderr = capsys.readouterr().err
    assert "ENGINE-BLOCK" in stderr and "engine-missing" in stderr


def test_missing_seed_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = tmp_path / "engine"
    (engine / "hwpx").mkdir(parents=True)
    _ = (engine / "hwpx" / "writer.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(proposal_config, "ENGINE_ROOT", engine)

    with pytest.raises(SystemExit) as raised:
        main(["--preflight"])

    assert raised.value.code == 4
    stderr = capsys.readouterr().err
    assert "ENGINE-BLOCK" in stderr and "seed-missing" in stderr


def test_engine_directory_without_sources_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = tmp_path / "engine"
    seed = engine / "resource" / SEED_NAME
    seed.parent.mkdir(parents=True)
    _ = seed.write_bytes(b"seed")
    monkeypatch.setattr(proposal_config, "ENGINE_ROOT", engine)

    report = preflight(load_config({}))

    assert not report.ok
    assert "engine-sources-missing" in report.reasons


def test_defaults_and_invalid_values() -> None:
    cfg = load_config({})

    # resource/rule.md asks for "10 페이지 내외"; the skill must not spend a
    # generation budget on thirty pages unless the owner asks for them.
    assert isinstance(cfg, ProposalConfig) and cfg.profile == "10-page"
    with pytest.raises(ConfigError):
        _ = load_config({"PROPOSAL_PROFILE": "bad"})
    with pytest.raises(ConfigError):
        _ = load_config({"PROPOSAL_IMAGE_MONTHLY_CAP_USD": "-1"})

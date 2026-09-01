"""Codex OAuth and browser checks for proposal preflight."""

from __future__ import annotations

from pathlib import Path

from skills.proposal.scripts import proposal_preflight


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _environment(tmp_path: Path) -> dict[str, str]:
    docbot = tmp_path / "docbot"
    docbot.mkdir()
    carrier = docbot / "picture-carrier.bin"
    carrier.write_bytes(b"carrier")
    return {
        "HOME": str(tmp_path / "home"),
        "PATH": str(tmp_path / "bin"),
        "PROPOSAL_DOCBOT_ROOT": str(docbot),
        "PROPOSAL_PICTURE_CARRIER": carrier.name,
    }


def test_codex_oauth_unblocks_images_without_an_image_api_key(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    bin_dir = Path(environment["PATH"])
    bin_dir.mkdir()
    _executable(bin_dir / "codex")
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")
    environment.update(
        CODEX_HOME=str(codex_home),
        PROPOSAL_IMAGE_TRANSPORT="codex",
    )

    report = proposal_preflight.collect_report(environment)

    assert report["checks"]["codex"] == "present"
    assert report["checks"]["codex-auth"] == "present"
    assert report["stages"]["images"] == "present"


def test_missing_codex_auth_blocks_codex_image_transport(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    bin_dir = Path(environment["PATH"])
    bin_dir.mkdir()
    _executable(bin_dir / "codex")
    environment.update(
        CODEX_HOME=str(tmp_path / "codex"),
        PROPOSAL_IMAGE_TRANSPORT="codex",
    )

    report = proposal_preflight.collect_report(environment)

    assert report["checks"]["codex-auth"] == "absent"
    assert report["stages"]["images"] == "blocked"


def test_chromium_on_path_controls_visual_review_stage(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    bin_dir = Path(environment["PATH"])
    bin_dir.mkdir()
    _executable(bin_dir / "chromium")

    report = proposal_preflight.collect_report(environment)

    assert report["checks"]["chrome"] == "present"
    assert report["stages"]["visual-review"] == "present"

    empty_bin_dir = tmp_path / "empty-bin"
    empty_bin_dir.mkdir()
    environment["PATH"] = str(empty_bin_dir)
    report = proposal_preflight.collect_report(environment)

    assert report["checks"]["chrome"] == "absent"
    assert report["stages"]["visual-review"] == "blocked"


def test_configured_preview_chrome_overrides_path(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    empty_bin_dir = tmp_path / "empty-bin"
    empty_bin_dir.mkdir()
    environment.update(
        PATH=str(empty_bin_dir),
        PROPOSAL_PREVIEW_CHROME=str(_executable(tmp_path / "preview-chrome")),
    )

    report = proposal_preflight.collect_report(environment)

    assert report["checks"]["chrome"] == "present"
    assert report["stages"]["visual-review"] == "present"

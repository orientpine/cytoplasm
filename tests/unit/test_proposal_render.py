from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from skills.proposal.scripts import proposal_render
from skills.proposal.scripts.proposal_config import PreflightReport, ProposalConfig
from skills.proposal.scripts.proposal_ir import FigureSpec, figures_to_json
from skills.proposal.scripts.proposal_version import Staging, VersionStore


class FakeRunner:
    def __init__(self, *, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        out = Path(argv[argv.index("--out") + 1])
        if self.returncode == 0:
            out.write_bytes(b"rendered hwpx")
        return subprocess.CompletedProcess(argv, self.returncode, "", self.stderr)


def _config(tmp_path: Path, *, pin: str = "a" * 40) -> ProposalConfig:
    docbot = tmp_path / "docbot"
    docbot.mkdir(exist_ok=True)
    return ProposalConfig(
        docbot_root=docbot,
        docbot_pin=pin,
        profile="30-page",
        image_model="gpt-image-2",
        image_monthly_cap_usd=10,
        refine_pin="b" * 40,
        drive_root="outputs",
        state_root=tmp_path / "state",
    )


def _version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, bytes]:
    root = tmp_path / "proposals"
    store = VersionStore(root)
    staging = store.begin("demo", hashlib.sha256(str(root).encode()).hexdigest())
    assert isinstance(staging, Staging)
    version = store.promote(
        "demo",
        staging,
        {"parent": None, "request": {"profile": "30-page"}, "schema_version": 1},
    )
    path = root / "demo" / "versions" / version
    png = b"png bytes"
    figure = FigureSpec(
        "fig-s1-01",
        "s1",
        ("public:claim-1",),
        "public diagram",
        "caption",
        hashlib.sha256(png).hexdigest(),
        0,
    )
    (path / "figures.json").write_text(figures_to_json((figure,)), encoding="utf-8")
    (path / "images" / "fig-s1-01.png").write_bytes(png)
    drafts = {"sections": [{"section_id": "s1", "body": "public research body"}]}
    (path / "out" / "drafts.json").write_text(json.dumps(drafts), encoding="utf-8")
    (path / "out" / "drafts.refined.json").write_text(json.dumps(drafts), encoding="utf-8")
    (path / "out" / "drafts.json.planspec.json").write_bytes(b"planspec sidecar")
    (path / "out" / "drafts.json.pms.json").write_bytes(b"pms sidecar")
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    return path, png


def _allow_preflight(monkeypatch: pytest.MonkeyPatch, pin: str = "a" * 40) -> None:
    monkeypatch.setattr(
        proposal_render,
        "preflight",
        lambda _cfg: PreflightReport(pin, pin, True, "c" * 64, True, ()),
    )
    monkeypatch.setattr(proposal_render, "_read_engine_head", lambda _root: pin)


def test_pin_mismatch_exits_four_before_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _version(tmp_path, monkeypatch)
    cfg = _config(tmp_path)
    monkeypatch.setattr(
        proposal_render,
        "preflight",
        lambda _cfg: PreflightReport("b" * 40, "a" * 40, True, None, False, ("pin-mismatch",)),
    )
    runner = FakeRunner()

    with pytest.raises(SystemExit) as raised:
        proposal_render.run_render("demo", runner=runner, config=cfg)

    assert raised.value.code == 4
    assert runner.calls == []


def test_missing_figure_exits_five_and_lists_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    (version / "images" / "fig-s1-01.png").unlink()
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)
    runner = FakeRunner()

    with pytest.raises(SystemExit) as raised:
        proposal_render.run_render("demo", runner=runner, config=cfg)

    assert raised.value.code == 5
    assert "fig-s1-01" in capsys.readouterr().err
    assert runner.calls == []


def test_happy_path_uses_exact_argv_explicit_env_and_updates_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)
    ambient = os.environ
    for name in (
        "KIMM_DOCBOT_LLM_API_KEY",
        "KIMM_DOCBOT_LLM_BACKEND",
        "UV_CACHE_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "credential-value")
    runner = FakeRunner()

    result = proposal_render.run_render("demo", mode="live", runner=runner, config=cfg)

    expected = [
        "uv",
        "run",
        "kimm-docbot",
        "render",
        "--drafts",
        str(version / "out" / "drafts.refined.json"),
        "--corpus",
        str(version / "corpus"),
        "--profile",
        "30-page",
        "--images",
        str(version / "images"),
        "--figures",
        str(version / "figures.json"),
        "--tables",
        str(version / "tables.json"),
        "--out",
        str(version / "out" / "proposal.hwpx"),
        "--mode",
        "live",
    ]
    argv, kwargs = runner.calls[0]
    assert argv == expected
    assert kwargs["cwd"] == cfg.docbot_root
    assert kwargs["timeout"] == 600
    assert kwargs["capture_output"] is True and kwargs["text"] is True
    assert kwargs["env"] is not ambient
    assert kwargs["env"] == {
        "ANTHROPIC_API_KEY": "credential-value",
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/local/bin:/usr/bin",
    }
    digest = hashlib.sha256(b"rendered hwpx").hexdigest()
    assert result.hwpx_sha256 == digest
    assert result.engine_sha == cfg.docbot_pin
    manifest = json.loads((version / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["hwpx_sha256"] == digest
    assert manifest["engine_sha"] == cfg.docbot_pin
    assert manifest["profile"] == "30-page"
    assert manifest["refined"] is True
    assert manifest["draft_preview"] is False


def test_engine_written_outputs_are_private_under_permissive_umask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)

    def writing_runner(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        output = Path(argv[argv.index("--out") + 1])
        output.unlink(missing_ok=True)
        output.write_bytes(b"rendered hwpx")
        sidecar = output.parent / "engine-sidecar.json"
        sidecar.write_text("{}\n", encoding="utf-8")
        output.chmod(0o664)
        sidecar.chmod(0o664)
        return subprocess.CompletedProcess(argv, 0, "", "")

    previous_umask = os.umask(0o022)
    try:
        proposal_render.run_render("demo", runner=writing_runner, config=cfg)
    finally:
        os.umask(previous_umask)

    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o600
        for path in (version / "out").rglob("*")
        if path.is_file()
    )


def test_refined_drafts_provision_sidecars_before_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)
    expected = {
        ".planspec.json": b"planspec sidecar",
        ".pms.json": b"pms sidecar",
    }

    def checking_runner(
        argv: list[str], **_kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        drafts_path = Path(argv[argv.index("--drafts") + 1])
        for suffix, content in expected.items():
            assert Path(f"{drafts_path}{suffix}").read_bytes() == content
        Path(argv[argv.index("--out") + 1]).write_bytes(b"rendered hwpx")
        return subprocess.CompletedProcess(argv, 0, "", "")

    proposal_render.run_render("demo", runner=checking_runner, config=cfg)

    for suffix, content in expected.items():
        assert (version / "out" / f"drafts.refined.json{suffix}").read_bytes() == content


def test_refined_drafts_fail_closed_when_source_sidecar_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    (version / "out" / "drafts.json.pms.json").unlink()
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)
    runner = FakeRunner()

    with pytest.raises(
        proposal_render.RenderInputError,
        match="refined drafts sidecar source is missing: drafts.json.pms.json",
    ):
        proposal_render.run_render("demo", runner=runner, config=cfg)

    assert runner.calls == []
    assert not (version / "out" / "drafts.refined.json.planspec.json").exists()


def test_raw_drafts_require_legitimate_refine_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    (version / "out" / "drafts.refined.json").unlink()
    (version / "out" / "refine-report.json").write_text(
        json.dumps({"refined": False, "reason": "no-non-glm-host"}), encoding="utf-8"
    )
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)
    runner = FakeRunner()

    result = proposal_render.run_render("demo", runner=runner, config=cfg)

    argv, _ = runner.calls[0]
    assert argv[argv.index("--drafts") + 1] == str(version / "out" / "drafts.json")
    assert result.refined is False
    manifest = json.loads((version / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["refined"] is False


def test_missing_refined_drafts_without_skip_record_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    (version / "out" / "drafts.refined.json").unlink()
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)
    runner = FakeRunner()

    with pytest.raises(proposal_render.RenderInputError, match="refined drafts"):
        proposal_render.run_render("demo", runner=runner, config=cfg)

    assert runner.calls == []


def test_allow_missing_figures_marks_draft_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    (version / "images" / "fig-s1-01.png").unlink()
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)

    result = proposal_render.run_render(
        "demo", allow_missing_figures=True, runner=FakeRunner(), config=cfg
    )

    assert result.draft_preview is True
    manifest = json.loads((version / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["draft_preview"] is True


def test_engine_pin_is_rechecked_after_runner_before_manifest_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)
    original_manifest = (version / "manifest.json").read_bytes()
    head = [cfg.docbot_pin]
    delegate = FakeRunner()

    def moving_head_runner(
        argv: list[str], **kwargs: Any
    ) -> subprocess.CompletedProcess[str]:
        completed = delegate(argv, **kwargs)
        head[0] = "d" * 40
        return completed

    with pytest.raises(
        proposal_render.RenderProcessError, match="engine HEAD changed during render"
    ):
        proposal_render.run_render(
            "demo",
            runner=moving_head_runner,
            config=cfg,
            head_reader=lambda _root: head[0],
        )

    assert (version / "manifest.json").read_bytes() == original_manifest


def test_last_character_engine_pin_change_does_not_write_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)
    original_manifest = (version / "manifest.json").read_bytes()
    near_pin = cfg.docbot_pin[:-1] + "b"

    with pytest.raises(proposal_render.RenderProcessError, match="engine HEAD changed during render"):
        proposal_render.run_render(
            "demo",
            runner=FakeRunner(),
            config=cfg,
            head_reader=lambda _root: near_pin,
        )

    assert (version / "manifest.json").read_bytes() == original_manifest


def test_runner_failure_is_explicit_and_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _version(tmp_path, monkeypatch)
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)
    runner = FakeRunner(returncode=1, stderr="engine exploded\nmore detail")

    with pytest.raises(proposal_render.RenderProcessError, match="engine exploded"):
        proposal_render.run_render("demo", runner=runner, config=cfg)

    assert len(runner.calls) == 1


def test_timeout_is_explicit_failure_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _version(tmp_path, monkeypatch)
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)
    calls = 0

    def timeout_runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    with pytest.raises(proposal_render.RenderProcessError, match="timed out"):
        proposal_render.run_render("demo", runner=timeout_runner, config=cfg)

    assert calls == 1


def test_malformed_figures_is_clean_input_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    (version / "figures.json").write_text("{broken", encoding="utf-8")
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)

    with pytest.raises(proposal_render.RenderInputError, match="figures.json"):
        proposal_render.run_render("demo", runner=FakeRunner(), config=cfg)


def test_cover_manifest_is_passed_to_the_engine_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)
    cover = version / "cover.json"
    _ = cover.write_text(json.dumps({"classification": "기계·로봇"}), encoding="utf-8")
    runner = FakeRunner()

    _ = proposal_render.run_render("demo", runner=runner, config=cfg)

    argv, _ = runner.calls[0]
    assert "--cover" in argv, "cover.json exists but the engine was never told about it"
    assert argv[argv.index("--cover") + 1] == str(cover)


def test_cover_flag_is_absent_when_no_cover_manifest_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version, _ = _version(tmp_path, monkeypatch)
    _ = version
    cfg = _config(tmp_path)
    _allow_preflight(monkeypatch)
    runner = FakeRunner()

    _ = proposal_render.run_render("demo", runner=runner, config=cfg)

    argv, _ = runner.calls[0]
    assert "--cover" not in argv

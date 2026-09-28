"""codex 0.144 가 갤러리에 남기는 실제 파일 이름으로 그림을 되찾는다.

기존 `test_proposal_images_codex.py` 는 옛 이름(`ig_*.png`)만 쓴다. 2026-09-28 노드에서는
codex 가 `generated_images/<session>/exec-<uuid>.png` 로 저장해 전송기가 생성된 그림 6장을
모두 버렸다. 같은 스텁 방식으로 새 이름과 실패 사유 출력을 따로 고정한다.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from skills.proposal.scripts import proposal_images, proposal_images_codex
from skills.proposal.scripts.proposal_ir import FigureSpec, figures_to_json
from skills.proposal.scripts.proposal_version import Staging, VersionStore


def _codex_stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    codex_home = tmp_path / "codexhome"
    (codex_home / "generated_images").mkdir(parents=True)
    script = bin_dir / "codex"
    _ = script.write_text("#!/usr/bin/env bash\nset -u\n" + body, encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("PROPOSAL_IMAGE_CODEX_BIN", raising=False)


def test_a_png_saved_under_the_current_codex_gallery_name_is_recovered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    png = proposal_images.fake_png("gallery")
    source = tmp_path / "source.png"
    _ = source.write_bytes(png)
    _codex_stub(
        monkeypatch,
        tmp_path,
        'mkdir -p "$CODEX_HOME/generated_images/01a0e899"\n'
        f"cp '{source}' \"$CODEX_HOME/generated_images/01a0e899/exec-1f58a92d.png\"\n"
        "exit 0\n",
    )

    result = proposal_images_codex.codex_transport("a folded surface", "gpt-image-2", {}, 30.0)

    assert result == png


def test_the_images_command_names_why_each_figure_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "proposals"
    store = VersionStore(root)
    staging = store.begin("demo", hashlib.sha256(b"demo").hexdigest())
    assert isinstance(staging, Staging)
    version = root / "demo" / "versions" / store.promote(
        "demo", staging, {"parent": None, "schema_version": 1}
    )
    figure = FigureSpec(
        "fig-s0-01", "0", ("public:c",), "a lattice\nno text, no labels, no numerals",
        "caption", "", 0,
    )
    _ = (version / "figures.json").write_text(figures_to_json((figure,)), encoding="utf-8")
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("PROPOSAL_IMAGE_TRANSPORT", "codex")
    _codex_stub(monkeypatch, tmp_path, 'printf "quota exhausted\\n" >&2\nexit 2\n')

    rc = proposal_images.main(["--slug", "demo", "--json"])

    captured = capsys.readouterr()
    assert rc == proposal_images.IMAGE_PARTIAL_EXIT
    assert json.loads(captured.out)["missing"] == ["fig-s0-01"]
    assert "IMAGE-MISSING fig-s0-01:" in captured.err
    assert "quota exhausted" in captured.err


def test_a_clean_run_that_skipped_the_image_tool_is_tried_once_more(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    png = proposal_images.fake_png("retry")
    source = tmp_path / "source.png"
    _ = source.write_bytes(png)
    calls = tmp_path / "calls"
    _codex_stub(
        monkeypatch,
        tmp_path,
        f"echo run >> '{calls}'\n"
        f'if [ "$(wc -l < \'{calls}\')" -ge 2 ]; then\n'
        '  mkdir -p "$CODEX_HOME/generated_images/s2"\n'
        f"  cp '{source}' \"$CODEX_HOME/generated_images/s2/exec-2.png\"\n"
        "fi\n"
        "exit 0\n",
    )

    result = proposal_images_codex.codex_transport("a folded surface", "gpt-image-2", {}, 30.0)

    assert result == png
    assert calls.read_text(encoding="utf-8").count("run") == 2


def test_a_failed_run_is_not_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = tmp_path / "calls"
    _codex_stub(monkeypatch, tmp_path, f"echo run >> '{calls}'\nexit 3\n")

    with pytest.raises(proposal_images.ImageGenerationError):
        _ = proposal_images_codex.codex_transport("a folded surface", "gpt-image-2", {}, 30.0)

    assert calls.read_text(encoding="utf-8").count("run") == 1

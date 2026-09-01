"""Codex-CLI 이미지 전송기는 실제 서브프로세스 경계로만 검증한다.

이 전송기의 계약은 "우리가 부른 명령줄"과 "코덱스가 남긴 파일"이 전부다. subprocess 를
가짜로 바꾸면 그 두 가지가 모두 사라져서, argv 오타나 산출 파일 위치 변경 같은 실제
실패를 한 건도 잡지 못한다. 그래서 여기서는 PATH 앞에 놓은 `codex` 스텁 실행 파일을
진짜로 실행시키고, 스텁이 기록한 argv 와 스텁이 남긴 PNG 로만 판정한다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from skills.proposal.scripts import proposal_images, proposal_images_codex
from skills.proposal.scripts.proposal_images import (
    ImageGenerationError,
    ImageInputError,
)


def _stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> Path:
    """PATH 맨 앞에 `codex` 스텁을 깔고 그 스텁이 argv 를 적는 파일을 돌려준다."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    codex_home = tmp_path / "codexhome"
    (codex_home / "generated_images").mkdir(parents=True, exist_ok=True)
    argv_file = tmp_path / "argv.txt"
    script = bin_dir / "codex"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        # NUL separated because the instruction argument itself spans lines.
        f"printf '%s\\0' \"$@\" > '{argv_file}'\n"
        'target=""\n'
        'previous=""\n'
        'for argument in "$@"; do\n'
        '  if [ "$previous" = "-C" ]; then target="$argument"; fi\n'
        '  previous="$argument"\n'
        "done\n" + body,
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.delenv("PROPOSAL_IMAGE_QUALITY", raising=False)
    monkeypatch.delenv("PROPOSAL_IMAGE_CODEX_BIN", raising=False)
    return argv_file


def _argv(argv_file: Path) -> list[str]:
    return argv_file.read_bytes().decode("utf-8").split("\0")[:-1]


def _source_png(tmp_path: Path, marker: str) -> tuple[Path, bytes]:
    png = proposal_images.fake_png(marker)
    source = tmp_path / f"{marker}.png"
    source.write_bytes(png)
    return source, png


def test_codex_transport_returns_the_png_codex_wrote_into_the_work_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    source, png = _source_png(tmp_path, "x")
    argv_file = _stub(
        monkeypatch,
        tmp_path,
        f"cp '{source}' \"$target/figure.png\"\n"
        'printf "%s\\n" "$target/figure.png"\n'
        "exit 0\n",
    )

    # When
    result = proposal_images_codex.codex_transport(
        "a lattice of nodes", "gpt-image-2", {"size": "512x512"}, 30.0
    )

    # Then
    assert result == png
    argv = _argv(argv_file)
    assert argv[0] == "exec"
    assert "--skip-git-repo-check" in argv
    assert argv[argv.index("-s") + 1] == "workspace-write"
    directory = argv[argv.index("-C") + 1]
    instruction = argv[1]
    assert f"{directory}/figure.png" in instruction
    assert "image_gen" in instruction
    assert "a lattice of nodes" in instruction
    assert "512x512" in instruction
    assert "medium" in instruction


def test_codex_transport_falls_back_to_the_codex_home_generated_images(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    source, png = _source_png(tmp_path, "fallback")
    _ = _stub(
        monkeypatch,
        tmp_path,
        'mkdir -p "$CODEX_HOME/generated_images/s1"\n'
        f"cp '{source}' \"$CODEX_HOME/generated_images/s1/ig_1.png\"\n"
        "exit 0\n",
    )

    # When
    result = proposal_images_codex.codex_transport(
        "a folded surface", "gpt-image-2", {}, 30.0
    )

    # Then
    assert result == png


def test_codex_transport_reports_the_failed_run_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    _ = _stub(monkeypatch, tmp_path, 'printf "auth required\\n" >&2\nexit 3\n')

    # When
    with pytest.raises(ImageGenerationError) as failure:
        _ = proposal_images_codex.codex_transport(
            "a folded surface", "gpt-image-2", {}, 30.0
        )

    # Then
    assert "auth required" in str(failure.value)
    assert "3" in str(failure.value)


def test_selected_transport_routes_codex_and_still_rejects_unknown_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    monkeypatch.setenv("PROPOSAL_IMAGE_TRANSPORT", "codex")

    # When
    selected = proposal_images._selected_transport()

    # Then
    assert selected is proposal_images_codex.codex_transport
    monkeypatch.setenv("PROPOSAL_IMAGE_TRANSPORT", "bogus")
    with pytest.raises(ImageInputError, match="fake, live or codex"):
        _ = proposal_images._selected_transport()


def test_invalid_quality_is_refused_before_codex_is_executed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Given
    argv_file = _stub(monkeypatch, tmp_path, "exit 0\n")
    monkeypatch.setenv("PROPOSAL_IMAGE_QUALITY", "bogus")

    # When
    with pytest.raises(ImageInputError, match="PROPOSAL_IMAGE_QUALITY"):
        _ = proposal_images_codex.codex_transport(
            "a folded surface", "gpt-image-2", {}, 30.0
        )

    # Then
    assert not argv_file.exists()

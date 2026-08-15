from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "recall" / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import recall_cli  # noqa: E402

_GLM_CFG = "model:\n  default: glm-main\n  provider: custom:litellm\n"
_SOL_CFG = (
    "model:\n"
    "  default: gpt-5.6-sol\n"
    "  provider: openai-codex\n"
    "fallback_providers:\n"
    "  - provider: custom:litellm\n"
    "    model: glm-main\n"
)


def _hermes_cfg(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "hermes-config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _cli_env(tmp_path: Path, rows_path: Path, hermes_cfg: Path) -> dict[str, str]:
    return {
        "PATH": os.environ["PATH"],
        "RECALL_FAKE_RESULTS": str(rows_path),
        "RECALL_LOG_DIR": str(tmp_path / "logs"),
        "RECALL_HERMES_CONFIG": str(hermes_cfg),
    }


def test_cli_excludes_sensitive_rows_and_keeps_missing_sensitivity_visible(
    tmp_path: Path,
) -> None:
    rows_path = tmp_path / "rows.json"
    rows_path.write_text(
        json.dumps(
            [
                {
                    "score": 0.61,
                    "source": "wiki:공개.md#c0000",
                    "content": "배양기 공개 메모",
                    "metadata": {"source_type": "wiki", "path": "공개.md"},
                },
                {
                    "score": 0.61,
                    "source": "wiki:기본.md#c0000",
                    "content": "배양기 기본 메모",
                    "metadata": {"source_type": "wiki", "path": "기본.md"},
                },
                {
                    "score": 0.61,
                    "source": "wiki:민감.md#c0000",
                    "content": "배양기 민감 원문",
                    "metadata": {
                        "source_type": "wiki",
                        "path": "민감.md",
                        "sensitivity": "patent-sensitive",
                    },
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "recall_cli.py"), "search", "배양기"],
        env=_cli_env(tmp_path, rows_path, _hermes_cfg(tmp_path, _GLM_CFG)),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "공개.md" in proc.stdout
    assert "기본.md" in proc.stdout
    assert "민감.md" not in proc.stdout
    assert "민감 원문" not in proc.stdout
    assert proc.stdout.count("1건은 민감 분류로 제외") == 1


def test_cli_all_sensitive_rows_emit_no_result_rows(tmp_path: Path) -> None:
    rows_path = tmp_path / "rows.json"
    rows_path.write_text(
        json.dumps(
            [
                {
                    "score": 0.61,
                    "source": "wiki:민감-a.md#c0000",
                    "content": "특허 민감 원문 A",
                    "metadata": {"sensitivity": "patent-sensitive"},
                },
                {
                    "score": 0.61,
                    "source": "wiki:민감-b.md#c0000",
                    "content": "특허 민감 원문 B",
                    "metadata": {"sensitivity": "patent-sensitive"},
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "recall_cli.py"), "search", "특허", "--json"],
        env=_cli_env(tmp_path, rows_path, _hermes_cfg(tmp_path, _GLM_CFG)),
        capture_output=True,
        text=True,
        check=False,
    )

    payload = json.loads(proc.stdout)
    assert proc.returncode == 0
    assert payload["status"] == "no_memory"
    assert payload["results"] == []
    assert proc.stderr == "2건은 민감 분류로 제외\n"
    assert "민감-a.md" not in proc.stdout + proc.stderr
    assert "민감-b.md" not in proc.stdout + proc.stderr


def test_cli_has_no_caller_facing_reinclusion_flag(monkeypatch) -> None:
    """v2 contract: inclusion is decided ONLY by the deterministic model-route
    guard — there must be no CLI flag or argparse surface a caller can use to
    force sensitive rows into the output."""
    captured: dict[str, str | int | float | bool | None] = {}

    def capture_args(args: argparse.Namespace) -> int:
        captured.update(vars(args))
        return 0

    monkeypatch.setattr(recall_cli, "run_search", capture_args)
    assert recall_cli.main(["search", "질문"]) == 0
    assert not any("sensitive" in name.lower() for name in captured)


def test_parse_primary_model_reads_top_level_model_block() -> None:
    model, provider = recall_cli._parse_primary_model(_SOL_CFG)
    assert (model, provider) == ("gpt-5.6-sol", "openai-codex")
    model, provider = recall_cli._parse_primary_model(_GLM_CFG)
    assert (model, provider) == ("glm-main", "custom:litellm")
    # fallback entries must never leak into the primary parse
    assert "glm" not in recall_cli._parse_primary_model(_SOL_CFG)[0]


def test_route_guard_fails_closed(tmp_path: Path, monkeypatch) -> None:
    # missing config file => exclude
    monkeypatch.setenv("RECALL_HERMES_CONFIG", str(tmp_path / "absent.yaml"))
    assert recall_cli._primary_route_is_glm_free() is False
    # glm primary => exclude
    monkeypatch.setenv(
        "RECALL_HERMES_CONFIG", str(_hermes_cfg(tmp_path, _GLM_CFG))
    )
    assert recall_cli._primary_route_is_glm_free() is False
    # model block missing keys => exclude
    empty = tmp_path / "empty.yaml"
    empty.write_text("agent:\n  max_turns: 60\n", encoding="utf-8")
    monkeypatch.setenv("RECALL_HERMES_CONFIG", str(empty))
    assert recall_cli._primary_route_is_glm_free() is False


def test_sol_primary_releases_sensitive_rows_with_sentinel(tmp_path: Path) -> None:
    rows_path = tmp_path / "rows.json"
    rows_path.write_text(
        json.dumps(
            [
                {
                    "score": 0.61,
                    "source": "wiki:공개.md#c0000",
                    "content": "배양기 공개 메모",
                    "metadata": {"source_type": "wiki", "path": "공개.md"},
                },
                {
                    "score": 0.61,
                    "source": "wiki:민감.md#c0000",
                    "content": "배양기 특허 원문",
                    "metadata": {
                        "source_type": "wiki",
                        "path": "민감.md",
                        "sensitivity": "patent-sensitive",
                    },
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "recall_cli.py"), "search", "배양기"],
        env=_cli_env(tmp_path, rows_path, _hermes_cfg(tmp_path, _SOL_CFG)),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "민감.md" in proc.stdout
    assert recall_cli.SENSITIVE_MARKER in proc.stdout
    assert "1건 patent-sensitive 포함" in proc.stdout
    assert "민감 분류로 제외" not in proc.stdout
    # non-sensitive rows must NOT carry the sentinel
    assert not any(
        recall_cli.SENSITIVE_MARKER in line and "공개" in line
        for line in proc.stdout.splitlines()
    )


def test_sentinel_matches_litellm_gateway_guard() -> None:
    callbacks = (
        Path(__file__).resolve().parents[2]
        / "configs"
        / "litellm-staging"
        / "custom_callbacks.py"
    ).read_text(encoding="utf-8")
    match = re.search(r"PATENT_SENTINEL = \"(.+?)\"", callbacks)
    assert match is not None
    assert match.group(1) == recall_cli.SENSITIVE_MARKER

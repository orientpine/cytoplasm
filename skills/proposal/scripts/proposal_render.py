"""Render the current proposal version through the pinned kimm-docbot checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol, cast

from .proposal_config import ConfigError, ProposalConfig, load_config, preflight
from .proposal_ir import FigureSpec, figures_from_json
from .proposal_route_guard import RouteRefused, assert_route_allowed
from .proposal_version import VersionError, VersionStore

_RENDER_TIMEOUT_SECONDS = 600
_DRAFT_SIDECAR_SUFFIXES = (".planspec.json", ".pms.json")
_CHILD_ENV_NAMES = (
    "ANTHROPIC_API_KEY",
    "HOME",
    "KIMM_DOCBOT_LLM_API_KEY",
    "KIMM_DOCBOT_LLM_BACKEND",
    "PATH",
    "UV_CACHE_DIR",
)


class RenderError(RuntimeError):
    """Rendering could not produce a verified artifact."""


class RenderInputError(RenderError):
    """The current proposal version is not renderable."""


class RenderProcessError(RenderError):
    """The isolated rendering process failed."""


@dataclass(frozen=True, slots=True)
class RenderResult:
    hwpx_path: Path
    hwpx_sha256: str
    engine_sha: str
    profile: str
    refined: bool
    draft_preview: bool


class Runner(Protocol):
    def __call__(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]: ...


def _run_subprocess(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    capture_output: bool,
    text: bool,
    timeout: int,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
        check=check,
    )


def _read_figures(path: Path) -> tuple[FigureSpec, ...]:
    if path.is_symlink() or not path.is_file():
        raise RenderInputError("figures.json is missing")
    try:
        figures = figures_from_json(path.read_text(encoding="utf-8"))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RenderInputError("figures.json is invalid") from error
    if not figures:
        raise RenderInputError("figures.json must contain at least one figure")
    if len({figure.figure_id for figure in figures}) != len(figures):
        raise RenderInputError("figures.json contains duplicate figure ids")
    return figures


def _missing_figures(version_path: Path, figures: Sequence[FigureSpec]) -> tuple[str, ...]:
    missing: list[str] = []
    for figure in figures:
        image = version_path / "images" / f"{figure.figure_id}.png"
        if image.is_symlink() or not image.is_file():
            missing.append(figure.figure_id)
            continue
        actual = hashlib.sha256(image.read_bytes()).hexdigest()
        if not figure.png_sha256 or actual != figure.png_sha256:
            missing.append(figure.figure_id)
    return tuple(missing)


def _read_json_object(path: Path, description: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise RenderInputError(f"{description} is missing")
    try:
        value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise RenderInputError(f"{description} is invalid") from error
    if not isinstance(value, dict):
        raise RenderInputError(f"{description} is invalid")
    return cast(dict[str, object], value)


def _drafts_path(version_path: Path) -> tuple[Path, bool]:
    refined = version_path / "out" / "drafts.refined.json"
    if refined.is_symlink():
        raise RenderInputError("refined drafts must be a regular file")
    if refined.is_file():
        return refined, True

    report_path = version_path / "out" / "refine-report.json"
    try:
        report = _read_json_object(report_path, "refine skip record")
    except RenderInputError as error:
        raise RenderInputError(
            "refined drafts are missing without a legitimate refine skip record"
        ) from error
    reason = report.get("reason")
    if report.get("refined") is not False or not isinstance(reason, str) or not reason:
        raise RenderInputError(
            "refined drafts are missing without a legitimate refine skip record"
        )
    drafts = version_path / "out" / "drafts.json"
    if drafts.is_symlink() or not drafts.is_file():
        raise RenderInputError("drafts.json is missing after refinement was skipped")
    return drafts, False


def _provision_refined_sidecars(drafts_path: Path, refined: bool) -> None:
    if not refined:
        return
    source_drafts = drafts_path.with_name("drafts.json")
    pending: list[tuple[Path, Path]] = []
    for suffix in _DRAFT_SIDECAR_SUFFIXES:
        destination = Path(f"{drafts_path}{suffix}")
        if destination.is_symlink() or destination.exists() and not destination.is_file():
            raise RenderInputError(f"refined drafts sidecar is invalid: {destination.name}")
        if destination.is_file():
            continue
        source = Path(f"{source_drafts}{suffix}")
        if source.is_symlink() or not source.is_file():
            raise RenderInputError(
                f"refined drafts sidecar source is missing: {source.name}"
            )
        pending.append((source, destination))
    for source, destination in pending:
        try:
            _ = shutil.copyfile(source, destination)
        except OSError as error:
            raise RenderInputError(
                f"could not provision refined drafts sidecar: {destination.name}"
            ) from error


def _body_payload(path: Path) -> str:
    document = _read_json_object(path, "drafts bundle")
    sections = document.get("sections")
    if not isinstance(sections, list) or not sections:
        raise RenderInputError("drafts bundle sections are invalid")
    bodies: list[str] = []
    for section in cast(list[object], sections):
        if not isinstance(section, dict):
            raise RenderInputError("drafts bundle section is invalid")
        body = cast(dict[object, object], section).get("body")
        if not isinstance(body, str) or not body.strip():
            raise RenderInputError("drafts bundle section body is invalid")
        bodies.append(body)
    return "\n\n".join(bodies)


def _child_environment(values: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if values is None else values
    return {name: source[name] for name in _CHILD_ENV_NAMES if name in source}


def _force_private_output_modes(out: Path) -> None:
    try:
        for path in out.rglob("*"):
            if not path.is_symlink() and path.is_file():
                path.chmod(0o600)
    except OSError as error:
        raise RenderProcessError("could not secure kimm-docbot outputs") from error


def _read_engine_head(docbot_root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(docbot_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise RenderProcessError(f"could not re-read engine HEAD: {error}") from error
    head = completed.stdout.strip()
    if completed.returncode != 0 or not head:
        detail = (completed.stderr or "git rev-parse failed").strip()[:500]
        raise RenderProcessError(f"could not re-read engine HEAD: {detail}")
    return head


def _update_manifest(
    version_path: Path,
    *,
    hwpx_sha256: str,
    engine_sha: str,
    profile: str,
    refined: bool,
    draft_preview: bool,
) -> None:
    manifest_path = version_path / "manifest.json"
    manifest = _read_json_object(manifest_path, "version manifest")
    manifest.update(
        {
            "draft_preview": draft_preview,
            "engine_sha": engine_sha,
            "hwpx_sha256": hwpx_sha256,
            "profile": profile,
            "refined": refined,
        }
    )
    forbidden = {"timestamp", "created_at", "updated_at", "created", "updated"}
    if forbidden.intersection(manifest):
        raise RenderInputError("version manifest contains forbidden timestamps")
    content = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".manifest.json.", dir=version_path)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            _ = stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest_path)
        manifest_path.chmod(0o600)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def run_render(
    slug: str,
    *,
    mode: str = "replay",
    profile: str | None = None,
    allow_missing_figures: bool = False,
    runner: Runner = _run_subprocess,
    config: ProposalConfig | None = None,
    head_reader: Callable[[Path], str] | None = None,
) -> RenderResult:
    """Render the current version after all fail-closed boundary checks pass."""
    if mode not in {"live", "replay"}:
        raise RenderInputError("mode must be live or replay")
    try:
        cfg = load_config() if config is None else config
    except ConfigError as error:
        print(f"CONFIG-ERROR: {error}", file=sys.stderr)
        raise SystemExit(4) from error
    selected_profile = cfg.profile if profile is None else profile
    if selected_profile not in {"30-page", "10-page"}:
        raise RenderInputError("profile must be 30-page or 10-page")

    report = preflight(cfg)
    if not report.ok or report.head_sha is None:
        print(f"ENGINE-PIN-BLOCK: {', '.join(report.reasons)}", file=sys.stderr)
        raise SystemExit(4)

    store = VersionStore.from_environment()
    head = store.head(slug)
    if head is None:
        raise RenderInputError("proposal has no current version")
    version_path = store.resolve_slug_dir(slug) / "versions" / head
    figures_path = version_path / "figures.json"
    figures = _read_figures(figures_path)
    missing = _missing_figures(version_path, figures)
    if missing and not allow_missing_figures:
        print(f"MISSING-FIGURES: {', '.join(missing)}", file=sys.stderr)
        raise SystemExit(5)

    drafts_path, refined = _drafts_path(version_path)
    body_payload = _body_payload(drafts_path)
    _ = assert_route_allowed(body_payload, "render")
    _provision_refined_sidecars(drafts_path, refined)

    output_path = version_path / "out" / "proposal.hwpx"
    argv = [
        "uv",
        "run",
        "kimm-docbot",
        "render",
        "--drafts",
        str(drafts_path),
        "--corpus",
        str(version_path / "corpus"),
        "--profile",
        selected_profile,
        "--images",
        str(version_path / "images"),
        "--figures",
        str(figures_path),
        "--tables",
        str(version_path / "tables.json"),
        "--out",
        str(output_path),
        "--mode",
        mode,
    ]
    cover_path = version_path / "cover.json"
    if not cover_path.is_symlink() and cover_path.is_file():
        argv += ["--cover", str(cover_path)]
    try:
        completed = runner(
            argv,
            cwd=cfg.docbot_root,
            env=_child_environment(),
            capture_output=True,
            text=True,
            timeout=_RENDER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RenderProcessError(
            f"kimm-docbot render timed out after {_RENDER_TIMEOUT_SECONDS}s"
        ) from error
    except OSError as error:
        raise RenderProcessError(f"kimm-docbot render could not start: {error}") from error
    _force_private_output_modes(version_path / "out")
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = (stderr or stdout or "no process output")[:500]
        raise RenderProcessError(
            f"kimm-docbot render failed rc={completed.returncode}: {detail}"
        )
    if output_path.is_symlink() or not output_path.is_file():
        raise RenderProcessError("kimm-docbot reported success without an HWPX output")

    hwpx_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
    read_head = _read_engine_head if head_reader is None else head_reader
    engine_sha = read_head(cfg.docbot_root)
    if engine_sha != cfg.docbot_pin:
        raise RenderProcessError(
            f"engine HEAD changed during render: expected {cfg.docbot_pin}, found {engine_sha}"
        )
    draft_preview = allow_missing_figures
    _update_manifest(
        version_path,
        hwpx_sha256=hwpx_sha256,
        engine_sha=engine_sha,
        profile=selected_profile,
        refined=refined,
        draft_preview=draft_preview,
    )
    return RenderResult(
        output_path,
        hwpx_sha256,
        engine_sha,
        selected_profile,
        refined,
        draft_preview,
    )


def command(args: argparse.Namespace, *, runner: Runner = _run_subprocess) -> int:
    """Execute the proposal CLI render subcommand."""
    try:
        result = run_render(
            cast(str, args.slug),
            mode=cast(str, args.mode),
            profile=cast(str | None, args.profile),
            allow_missing_figures=cast(bool, args.allow_missing_figures),
            runner=runner,
        )
    except (RenderError, RouteRefused, VersionError) as error:
        print(f"PROPOSAL-RENDER-ERROR: {error}", file=sys.stderr)
        return 1
    payload = asdict(result)
    payload["hwpx_path"] = str(result.hwpx_path)
    if cast(bool, args.json):
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        message = (
            f"PROPOSAL-RENDERED path={result.hwpx_path} sha256={result.hwpx_sha256} "
            f"engine={result.engine_sha}"
        )
        print(message)
    return 0


__all__ = [
    "RenderError",
    "RenderInputError",
    "RenderProcessError",
    "RenderResult",
    "command",
    "run_render",
]

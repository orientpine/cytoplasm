from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import override

from automation.drive_client import DriveClientError, DriveClient
from automation.interop.external_effect_gate import ApprovalContext
from automation.obsidian_write import (
    ObsidianWriteConfig,
    ObsidianWriteError,
    load_config,
    plan_note,
    write_note,
)
from skills.doctype.scripts import doctype_extract
from skills.doctype.scripts.doctype_routing import SaveRoute

_DEFAULT_DRIVE_CACHE = Path.home() / ".hermes" / "doctype" / "drive-folders.json"
_DEFAULT_DRIVE_ROOT = "Autophagy 산출물"
_OBSIDIAN_CONFIG_ENV = "OBSIDIAN_WRITE_CONFIG"


@dataclass(frozen=True, slots=True)
class DocumentSaveError(Exception):
    destination: str
    detail: str

    @override
    def __str__(self) -> str:
        return f"{self.destination} save failed: {self.detail}"


GitRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class SaveAdapters:
    obsidian_config: ObsidianWriteConfig | None
    drive_client: DriveClient | None
    drive_folder_parts: tuple[str, ...]
    git_runner: GitRunner = subprocess.run
    approval_context: ApprovalContext | None = None


def adapters_from_environment(route: SaveRoute) -> SaveAdapters:
    obsidian_config = _obsidian_config() if "obsidian" in route.destinations else None
    drive_client = _drive_client() if "drive" in route.destinations else None
    return SaveAdapters(obsidian_config, drive_client, _drive_folder_parts())


def save_from_environment(artifact: Path, route: SaveRoute) -> None:
    save_artifact(artifact, route, adapters_from_environment(route))


def save_artifact(artifact: Path, route: SaveRoute, adapters: SaveAdapters) -> None:
    """Execute every selected adapter or surface one explicit failed destination."""
    if "obsidian" in route.destinations:
        _save_to_obsidian(artifact, adapters)
    if "drive" in route.destinations:
        _save_to_drive(artifact, adapters)


def _obsidian_config() -> ObsidianWriteConfig:
    configured_path = os.environ.get(_OBSIDIAN_CONFIG_ENV)
    return load_config(Path(configured_path).expanduser()) if configured_path else load_config()


def _drive_client() -> DriveClient:
    cache_value = os.environ.get("DRIVE_DOCTYPE_CACHE")
    cache_path = Path(cache_value).expanduser() if cache_value else _DEFAULT_DRIVE_CACHE
    return DriveClient(os.environ.get("DRIVE_GWS_BIN", "gws"), cache_path)


def _drive_folder_parts() -> tuple[str, ...]:
    root = os.environ.get("DRIVE_DOCTYPE_ROOT", _DEFAULT_DRIVE_ROOT).strip()
    if not root:
        raise DocumentSaveError("drive", "DRIVE_DOCTYPE_ROOT is empty")
    return (root, "doctype")


def _save_to_obsidian(artifact: Path, adapters: SaveAdapters) -> None:
    config = adapters.obsidian_config
    if config is None:
        raise DocumentSaveError("obsidian", "adapter configuration is missing")
    try:
        source = doctype_extract.read_document(artifact)
        plan = plan_note(artifact.stem, source.text, institutional=False, bucket_hint="resource")
        _ = write_note(
            plan,
            config,
            adapters.git_runner,
            approval_context=adapters.approval_context,
        )
    except (ObsidianWriteError, doctype_extract.ExtractionError, OSError) as error:
        raise DocumentSaveError("obsidian", str(error)) from error


def _save_to_drive(artifact: Path, adapters: SaveAdapters) -> None:
    drive = adapters.drive_client
    if drive is None:
        raise DocumentSaveError("drive", "adapter configuration is missing")
    try:
        parent_id = drive.ensure_folder_path(adapters.drive_folder_parts)
        result = drive.upsert_file(artifact, artifact.name, parent_id)
        drive.verify_owner_only(result["id"])
        _ = drive.download_and_verify(result["id"], artifact)
    except (DriveClientError, OSError) as error:
        raise DocumentSaveError("drive", str(error)) from error

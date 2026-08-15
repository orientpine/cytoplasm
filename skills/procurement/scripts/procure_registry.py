"""Persistent provide-once/reuse-forever procurement template registry."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Final

from skills.procurement.scripts import procure_core as core
from skills.procurement.scripts import procure_docx, procure_hwpx

NAME: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class RegistryError(RuntimeError):
    """Registry name, storage, or metadata error."""


@dataclass(frozen=True, slots=True)
class RegisteredTemplate:
    """Loaded stored template and its deterministic analysis artifact."""

    name: str
    format: str
    template: Path
    analysis: Path
    fields: tuple[str, ...]


def root() -> Path:
    """Return the private runtime store, overridable only for offline tests."""
    return Path(os.environ.get("PROCURE_TEMPLATE_DIR", "~/.hermes/procurement/templates")).expanduser()


def _name(name: str) -> str:
    if not NAME.fullmatch(name):
        raise RegistryError("template name must use 1-64 letters, digits, dot, underscore, or hyphen")
    return name


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _analysis(preflight: core.Preflight, template: Path) -> tuple[str, tuple[str, ...]]:
    match preflight.format:
        case "hwpx":
            form_map = procure_hwpx.analyze(template)
            path = template.parent / "form_map.json"
            procure_hwpx.write_map(form_map, path)
            return path.name, procure_hwpx.field_names(form_map)
        case "docx":
            manifest = procure_docx.manifest(template)
            path = template.parent / "placeholders.json"
            _write_json(path, manifest)
            fields = manifest["placeholders"]
            return path.name, tuple(str(field) for field in fields) if isinstance(fields, list) else ()
        case "xlsx":
            fields = core.extract_placeholders(preflight, template.read_bytes())
            path = template.parent / "placeholders.json"
            _write_json(path, {"format": "xlsx", "placeholders": list(fields)})
            return path.name, fields
        case unexpected:
            raise AssertionError(f"unsupported preflight format: {unexpected}")


def register(name: str, template: Path, force: bool = False) -> RegisteredTemplate:
    """Preflight, analyze, and privately store one real template for reuse."""
    safe_name = _name(name)
    preflight = core.preflight_path(template)
    directory = root() / safe_name
    if directory.exists():
        if not force:
            raise RegistryError(f"template already exists: {safe_name} (use --force to replace it)")
        shutil.rmtree(directory)
    directory.mkdir(parents=True, mode=0o700)
    directory.chmod(0o700)
    stored = directory / f"template.{preflight.format}"
    shutil.copyfile(template, stored)
    stored.chmod(0o600)
    analysis_name, fields = _analysis(preflight, stored)
    source_sha256 = hashlib.sha256(template.read_bytes()).hexdigest()
    _write_json(directory / "meta.json", {
        "name": safe_name, "format": preflight.format, "template_file": stored.name,
        "analysis_file": analysis_name, "fields": list(fields), "source_sha256": source_sha256,
        "created_kst": datetime.now(tz=core.KST).isoformat(timespec="seconds"),
    })
    return load(safe_name)


def _metadata(name: str) -> tuple[Path, dict[str, object]]:
    directory = root() / _name(name)
    meta_path = directory / "meta.json"
    if not meta_path.is_file():
        raise RegistryError(f"template not found: {name}")
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RegistryError(f"template metadata is invalid: {name}") from error
    if not isinstance(raw, dict):
        raise RegistryError(f"template metadata is invalid: {name}")
    return directory, raw


def load(name: str) -> RegisteredTemplate:
    """Load paths and field keys for a previously registered template name."""
    directory, meta = _metadata(name)
    format_name = meta.get("format")
    template_name = meta.get("template_file")
    analysis_name = meta.get("analysis_file")
    fields = meta.get("fields")
    if not isinstance(format_name, str):
        raise RegistryError(f"template metadata is incomplete: {name}")
    if not isinstance(template_name, str):
        raise RegistryError(f"template metadata is incomplete: {name}")
    if not isinstance(analysis_name, str):
        raise RegistryError(f"template metadata is incomplete: {name}")
    if not isinstance(fields, list):
        raise RegistryError(f"template metadata is incomplete: {name}")
    template, analysis = directory / template_name, directory / analysis_name
    if not template.is_file() or not analysis.is_file():
        raise RegistryError(f"template store is incomplete: {name}")
    return RegisteredTemplate(name, format_name, template, analysis, tuple(str(field) for field in fields))


def list_templates() -> tuple[RegisteredTemplate, ...]:
    """List valid registry entries without exposing template content."""
    store = root()
    if not store.exists():
        return ()
    return tuple(load(path.name) for path in sorted(store.iterdir()) if path.is_dir() and (path / "meta.json").is_file())

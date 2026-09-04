"""Drive folder-cache validation and path resolution."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path


FolderFinder = Callable[[str, str], str | None]
FolderCreator = Callable[[str, str], str]
FolderAlive = Callable[[str], bool]
_NOT_FOUND = re.compile(r"notFound|not found|\b404\b", re.IGNORECASE)


def _folder_alive(file_id: str, *, gws_bin: str, run: Callable[[list[str]], object]) -> bool:
    try:
        result = run(
            [gws_bin, "drive", "files", "get", "--params",
             json.dumps({"fileId": file_id, "fields": "id,trashed,parents"})]
        )
    except Exception as error:  # noqa: BLE001 - gws failures surface as one error type
        # Only a definite not-found kills the cache entry: a transport hiccup must not
        # make the caller create a duplicate folder beside the one that still exists.
        return not _NOT_FOUND.search(str(error))
    return not (isinstance(result, dict) and result.get("trashed") is True)


def _load_cache(folder_cache: Path) -> dict[str, str]:
    try:
        data = json.loads(folder_cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(key): str(value) for key, value in data.items()} if isinstance(data, dict) else {}


def _save_cache(folder_cache: Path, cache: dict[str, str]) -> None:
    folder_cache.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder_cache.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    folder_cache.chmod(0o600)


def _drop_cached_path(cache: dict[str, str], key: str) -> None:
    prefix = f"{key}/"
    for cached_key in tuple(cache):
        if cached_key == key or cached_key.startswith(prefix):
            del cache[cached_key]


def ensure_folder_path(
    parts: tuple[str, ...],
    *,
    folder_cache: Path,
    find_folder: FolderFinder,
    create_folder: FolderCreator,
    folder_alive: FolderAlive,
) -> str:
    cache = _load_cache(folder_cache)
    parent = "root"
    walked: list[str] = []
    changed = False
    for name in parts:
        walked.append(name)
        key = "/".join(walked)
        cached = cache.get(key)
        if cached and folder_alive(cached):
            parent = cached
            continue
        if cached:
            _drop_cached_path(cache, key)
            changed = True
        resolved = find_folder(name, parent) or create_folder(name, parent)
        cache[key] = resolved
        parent = resolved
        changed = True
    if changed:
        _save_cache(folder_cache, cache)
    return parent

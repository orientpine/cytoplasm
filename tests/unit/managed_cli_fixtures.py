from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Final, TypeAlias

import pytest

from automation.group_roster.parser import ROSTER_ENV

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonMapping: TypeAlias = dict[str, JsonValue]

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SEED_PATH: Final = REPO_ROOT / "configs" / "managed-sync.default.json"
PRINCIPAL: Final = "publisher-testlab@autophagy"
REQUIRED_KEYS: Final = frozenset(
    {
        "remote_url",
        "publisher",
        "allowed_signers",
        "mirror_dir",
        "ssh_key_path",
        "quarantine_dir",
        "state_path",
        "skills",
    }
)
_SIGNING_PUBLIC_KEY: Final = (
    "ssh-ed25519"
    " AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g"
    " roster-test-admin"
)
_JSON_LOADS: Final[Callable[..., JsonValue]] = json.loads


def digest(letter: str) -> str:
    return letter * 64


def config_payload(tmp_path: Path) -> JsonMapping:
    return {
        "remote_url": "ssh://feed.example/managed-skills.git",
        "publisher": "cha",
        "allowed_signers": str(tmp_path / "allowed_signers"),
        "mirror_dir": str(tmp_path / "mirror"),
        "ssh_key_path": str(tmp_path / "feed_key"),
        "quarantine_dir": str(tmp_path / "quarantine"),
        "state_path": str(tmp_path / "state.json"),
        "skills": {"managed-demo": {"opt_in": True, "pin": None}},
    }


def config_skills(payload: JsonMapping) -> JsonMapping:
    skills = payload["skills"]
    assert isinstance(skills, dict)
    return skills


def roster_text(principal: str = PRINCIPAL) -> str:
    return (
        "schema: 1\n"
        "group_id: testlab\n"
        "admin:\n"
        "  name: Test Admin\n"
        '  discord_user_id: "2001"\n'
        f"  publisher_principal: {principal}\n"
        f"  signing_public_key: {_SIGNING_PUBLIC_KEY}\n"
        "members: []\n"
    )


def install_roster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
) -> Path:
    roster_path = tmp_path / "roster.yaml"
    _ = roster_path.write_text(text, encoding="utf-8")
    monkeypatch.setenv(ROSTER_ENV, str(roster_path))
    return roster_path


def install_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: JsonMapping,
) -> Path:
    config_path = tmp_path / "config.json"
    _ = config_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("MANAGED_SYNC_CONFIG", str(config_path))
    _ = install_roster(tmp_path, monkeypatch, roster_text())
    return config_path


def load_json_mapping(path: Path) -> JsonMapping:
    payload = _JSON_LOADS(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def stage_release(quarantine_dir: Path, skill: str, sequence: int, digest_value: str) -> Path:
    release = quarantine_dir / skill / digest_value
    (release / skill).mkdir(parents=True)
    provenance: JsonMapping = {
        "publisher": "cha",
        "sequence": sequence,
        "tag": f"{skill}/v{sequence}",
        "verified_at": "2026-07-24T00:00:00+00:00",
    }
    _ = (release / "provenance.json").write_text(
        json.dumps(provenance, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return release

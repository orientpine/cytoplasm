from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from automation.managed_skills import publisher_config

_REPO_ROOT: Final = Path(__file__).resolve().parents[2]
_SEED_PATH: Final = _REPO_ROOT / "configs" / "managed-publisher.default.json"
_ROSTER_SEED_PATH: Final = _REPO_ROOT / "configs" / "roster.example.yaml"
_REQUIRED_KEYS: Final = frozenset({"publisher", "publisher_principal"})


def _payload() -> dict[str, Any]:
    return {"publisher": "testlab", "publisher_principal": "publisher-testlab@autophagy"}


def _install(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "publisher.json"
    _ = path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_publisher_identity_when_config_is_valid_then_returns_declared_identity(
    tmp_path: Path,
) -> None:
    # Given: a publisher-side runtime config naming a non-cha publisher.
    path = _install(tmp_path, _payload())

    # When: the publish tool resolves its own identity.
    identity = publisher_config.load_publisher_identity(path)

    # Then: both the manifest name and the signing principal come from that config.
    assert identity == publisher_config.PublisherIdentity(
        publisher="testlab", publisher_principal="publisher-testlab@autophagy"
    )


def test_load_publisher_identity_when_config_is_absent_then_fails_closed_naming_path(
    tmp_path: Path,
) -> None:
    # Given: no publisher config at all (an unconfigured install).
    missing = tmp_path / "publisher.json"

    # When/Then: publishing is refused — there is no default publisher identity.
    with pytest.raises(publisher_config.PublisherConfigError, match=str(missing)):
        _ = publisher_config.load_publisher_identity(missing)


def test_load_publisher_identity_when_config_is_not_json_then_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "publisher.json"
    _ = path.write_text("not-json{{{", encoding="utf-8")

    with pytest.raises(publisher_config.PublisherConfigError, match="not valid JSON"):
        _ = publisher_config.load_publisher_identity(path)


def test_load_publisher_identity_when_config_is_not_an_object_then_fails_closed(
    tmp_path: Path,
) -> None:
    path = _install(tmp_path, ["publisher-testlab@autophagy"])

    with pytest.raises(publisher_config.PublisherConfigError, match="JSON object"):
        _ = publisher_config.load_publisher_identity(path)


@pytest.mark.parametrize("key", sorted(_REQUIRED_KEYS))
def test_load_publisher_identity_when_required_key_is_missing_then_names_the_key(
    tmp_path: Path, key: str
) -> None:
    payload = _payload()
    del payload[key]
    path = _install(tmp_path, payload)

    with pytest.raises(publisher_config.PublisherConfigError, match=f"missing required key: {key}"):
        _ = publisher_config.load_publisher_identity(path)


def test_load_publisher_identity_when_unknown_key_is_present_then_names_the_key(
    tmp_path: Path,
) -> None:
    path = _install(tmp_path, {**_payload(), "signing_key": "/tmp/key"})

    with pytest.raises(
        publisher_config.PublisherConfigError, match="unknown config key: signing_key"
    ):
        _ = publisher_config.load_publisher_identity(path)


@pytest.mark.parametrize(
    ("key", "value"),
    (
        ("publisher", 7),
        ("publisher", ""),
        ("publisher", "Testlab"),
        ("publisher", "test lab"),
        ("publisher_principal", None),
        ("publisher_principal", ""),
        ("publisher_principal", "testlab@autophagy"),
        ("publisher_principal", "publisher-TESTLAB@autophagy"),
    ),
)
def test_load_publisher_identity_when_value_is_invalid_then_names_the_key(
    tmp_path: Path, key: str, value: object
) -> None:
    path = _install(tmp_path, {**_payload(), key: value})

    with pytest.raises(publisher_config.PublisherConfigError, match=f"invalid value for key: {key}"):
        _ = publisher_config.load_publisher_identity(path)


def test_config_path_when_environment_overrides_then_uses_that_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    override = tmp_path / "elsewhere.json"
    monkeypatch.setenv(publisher_config.CONFIG_ENV, str(override))

    assert publisher_config.config_path() == override


def test_config_path_when_environment_is_unset_then_uses_runtime_default_outside_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(publisher_config.CONFIG_ENV, raising=False)

    resolved = publisher_config.config_path()

    assert resolved == publisher_config.DEFAULT_CONFIG_PATH.expanduser()
    assert not resolved.is_relative_to(_REPO_ROOT)


def test_publisher_seed_when_parsed_then_is_a_valid_placeholder_identity() -> None:
    # Given: the tracked seed operators copy into their runtime config location.
    payload: object = json.loads(_SEED_PATH.read_text(encoding="utf-8"))

    # When/Then: it parses under the same fail-closed loader and carries only example values.
    assert isinstance(payload, dict)
    assert frozenset(payload) == _REQUIRED_KEYS
    identity = publisher_config.load_publisher_identity(_SEED_PATH)
    roster_seed: object = yaml.safe_load(_ROSTER_SEED_PATH.read_text(encoding="utf-8"))
    assert isinstance(roster_seed, dict)
    admin: object = roster_seed["admin"]
    assert isinstance(admin, dict)
    assert identity.publisher_principal == admin["publisher_principal"]

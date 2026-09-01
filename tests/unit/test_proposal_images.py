from __future__ import annotations

import hashlib
import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from skills.proposal.scripts import proposal_images
from skills.proposal.scripts.proposal_images import (
    ImageBudgetBlocked,
    ImageError,
    ImageGenerationError,
    ImageResult,
)
from skills.proposal.scripts.proposal_ir import FigureSpec, figures_to_json
from skills.proposal.scripts.proposal_route_guard import RouteRefused
from skills.proposal.scripts.proposal_version import Staging, VersionStore


class RecordingTransport:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, str], float]] = []
        self.failing = failing or set()

    def __call__(
        self, prompt: str, model: str, params: dict[str, str], timeout: float
    ) -> bytes:
        self.calls.append((prompt, model, params, timeout))
        for marker in self.failing:
            if marker in prompt:
                raise ImageGenerationError("injected transport failure")
        return proposal_images.fake_png(prompt) + b"test-padding" * 128


def _figures(
    count: int = 15, *, prompt_prefix: str = "public concept"
) -> tuple[FigureSpec, ...]:
    return tuple(
        FigureSpec(
            f"fig-s1-{index:02d}",
            "s1",
            (f"public:claim-{index}",),
            f"{prompt_prefix} slot-{index}",
            f"caption {index}",
            "",
            index - 1,
        )
        for index in range(1, count + 1)
    )


def _version(root: Path, figures: tuple[FigureSpec, ...]) -> Path:
    store = VersionStore(root)
    staging = store.begin("demo", hashlib.sha256(str(root).encode()).hexdigest())
    assert isinstance(staging, Staging)
    version = store.promote(
        "demo", staging, {"parent": None, "request": {}, "schema_version": 1}
    )
    version_path = root / "demo" / "versions" / version
    figures_path = version_path / "figures.json"
    figures_path.write_text(figures_to_json(figures), encoding="utf-8")
    figures_path.chmod(0o600)
    return version_path


def _run(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    state: Path,
    transport: RecordingTransport,
) -> ImageResult:
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_STATE_ROOT", str(state))
    monkeypatch.setenv("PROPOSAL_IMAGE_MONTHLY_CAP_USD", "10")
    return proposal_images.generate_images("demo", transport=transport)


def test_valid_target_survives_cache_loss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, state = tmp_path / "root", tmp_path / "state"
    version = _version(root, _figures(1))
    _run(monkeypatch, root, state, RecordingTransport())
    target = version / "images" / "fig-s1-01.png"
    cache = proposal_images._cache_path(
        state, "gpt-image-2", {"size": "1024x1024"},
        "public concept slot-1\nno text, no labels, no numerals",
    )
    target_mtime = target.stat().st_mtime_ns
    ledger_path = state / "image_spend.json"
    ledger = ledger_path.read_bytes()
    cache.unlink()

    transport = RecordingTransport()
    result = _run(monkeypatch, root, state, transport)

    assert transport.calls == []
    assert result.missing == ()
    assert target.stat().st_mtime_ns == target_mtime
    assert ledger_path.read_bytes() == ledger


def test_corrupt_target_regenerates_with_cache_intact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, state = tmp_path / "root", tmp_path / "state"
    version = _version(root, _figures(1))
    _run(monkeypatch, root, state, RecordingTransport())
    target = version / "images" / "fig-s1-01.png"
    target.write_bytes(b"corrupt")

    transport = RecordingTransport()
    result = _run(monkeypatch, root, state, transport)

    assert len(transport.calls) == 1
    assert result.missing == ()
    assert proposal_images._valid_png_sha(target) == result.images[0][1]


def test_failed_slots_resume_without_regenerating_successes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, state = tmp_path / "root", tmp_path / "state"
    _version(root, _figures())
    failing = RecordingTransport({"slot-5\n", "slot-6\n", "slot-7\n"})

    first = _run(monkeypatch, root, state, failing)
    assert [item.figure_id for item in first.missing] == [
        "fig-s1-05",
        "fig-s1-06",
        "fig-s1-07",
    ]
    assert len(first.images) == 12
    successful = {
        path.name: (path.stat().st_mtime_ns, sha) for path, sha in first.images
    }

    resumed = RecordingTransport()
    second = _run(monkeypatch, root, state, resumed)

    assert len(resumed.calls) == 3
    assert second.missing == ()
    assert len(second.images) == 15
    assert successful == {
        path.name: (path.stat().st_mtime_ns, sha)
        for path, sha in second.images
        if path.name in successful
    }


def test_codex_subscription_images_do_not_consume_api_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, state = tmp_path / "root", tmp_path / "state"
    _version(root, _figures(1))
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_STATE_ROOT", str(state))
    monkeypatch.setenv("PROPOSAL_IMAGE_TRANSPORT", "codex")
    monkeypatch.setenv("PROPOSAL_IMAGE_MONTHLY_CAP_USD", "0")

    result = proposal_images.generate_images("demo", transport=RecordingTransport())

    month = datetime.now(UTC).strftime("%Y-%m")
    ledger = json.loads((state / "image_spend.json").read_text(encoding="utf-8"))
    assert len(result.images) == 1
    assert ledger[month]["chatgpt-subscription"] == {"usd": 0.0, "images": 1}
    assert "openai-api" not in ledger[month]

    live_root, live_state = tmp_path / "live-root", tmp_path / "live-state"
    _version(live_root, _figures(1))
    monkeypatch.setenv("PROPOSAL_ROOT", str(live_root))
    monkeypatch.setenv("PROPOSAL_STATE_ROOT", str(live_state))
    monkeypatch.setenv("PROPOSAL_IMAGE_TRANSPORT", "live")
    with pytest.raises(ImageBudgetBlocked):
        proposal_images.generate_images("demo", transport=RecordingTransport())


def test_legacy_flat_ledger_is_migrated_on_live_reservation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, state = tmp_path / "root", tmp_path / "state"
    _version(root, _figures(1))
    month = datetime.now(UTC).strftime("%Y-%m")
    state.mkdir()
    (state / "image_spend.json").write_text(
        json.dumps({month: 0.04}), encoding="utf-8"
    )
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_STATE_ROOT", str(state))
    monkeypatch.setenv("PROPOSAL_IMAGE_TRANSPORT", "live")
    monkeypatch.setenv("PROPOSAL_IMAGE_MONTHLY_CAP_USD", "10")

    proposal_images.generate_images("demo", transport=RecordingTransport())

    ledger = json.loads((state / "image_spend.json").read_text(encoding="utf-8"))
    assert ledger[month]["openai-api"]["usd"] == pytest.approx(0.08)
    assert ledger[month]["openai-api"]["images"] == 1


def test_malformed_nested_ledger_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "image_spend.json"
    path.write_text(
        json.dumps({"2026-08": {"openai-api": {"usd": -1}}}),
        encoding="utf-8",
    )

    with pytest.raises(ImageError, match="image spend ledger is invalid"):
        proposal_images._read_ledger(path)


def test_zero_budget_blocks_before_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "root"
    _version(root, _figures(1))
    transport = RecordingTransport()
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("PROPOSAL_IMAGE_MONTHLY_CAP_USD", "0")

    assert proposal_images.main(["--slug", "demo", "--json"], transport=transport) == 6
    assert transport.calls == []
    assert "IMAGE-BUDGET-BLOCK" in capsys.readouterr().err


def test_patent_sensitive_prompt_is_refused_before_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    _version(root, _figures(1, prompt_prefix="patent invention concept"))
    transport = RecordingTransport()

    with pytest.raises(RouteRefused):
        _run(monkeypatch, root, tmp_path / "state", transport)
    assert transport.calls == []


def test_figures_json_records_prompt_model_sha_and_private_modes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, state = tmp_path / "root", tmp_path / "state"
    version = _version(root, _figures(2))
    result = _run(monkeypatch, root, state, RecordingTransport())
    records = json.loads((version / "figures.json").read_text(encoding="utf-8"))

    assert len(result.images) == 2
    for record in records:
        assert record["model"] == "gpt-image-2"
        assert (
            record["png_sha256"]
            == hashlib.sha256(
                (version / "images" / f"{record['figure_id']}.png").read_bytes()
            ).hexdigest()
        )
        assert record["prompt"].endswith("no text, no labels, no numerals")
    private_files = [version / "figures.json", *(path for path, _sha in result.images)]
    private_files.extend(path for path in state.rglob("*") if path.is_file())
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in private_files)
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o700
        for path in [
            state,
            version / "images",
            *(p for p in state.rglob("*") if p.is_dir()),
        ]
    )


def test_fake_transport_requires_explicit_test_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    _version(root, _figures(1))
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("PROPOSAL_IMAGE_TRANSPORT", "fake")

    with pytest.raises(ImageGenerationError, match="test-only"):
        proposal_images.generate_images("demo")

    monkeypatch.setenv("AUTOPHAGY_DEMO_SECRET", "DUMMY-proposal-images")
    result = proposal_images.generate_images("demo")
    assert len(result.images) == 1


def test_undersized_png_from_injected_live_transport_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    _version(root, _figures(1))

    def tiny_transport(
        prompt: str, model: str, params: dict[str, str], timeout: float
    ) -> bytes:
        del model, params, timeout
        return proposal_images.fake_png(prompt)

    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("PROPOSAL_IMAGE_MONTHLY_CAP_USD", "10")
    with pytest.raises(ImageGenerationError, match="abnormally small"):
        proposal_images.generate_images("demo", transport=tiny_transport)


def test_duplicate_png_payloads_from_injected_live_transport_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    _version(root, _figures(2))
    payload = proposal_images.fake_png("constant") + b"x" * 2048

    def duplicate_transport(
        prompt: str, model: str, params: dict[str, str], timeout: float
    ) -> bytes:
        del prompt, model, params, timeout
        return payload

    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("PROPOSAL_IMAGE_MONTHLY_CAP_USD", "10")
    with pytest.raises(ImageGenerationError, match="duplicate image payload"):
        proposal_images.generate_images("demo", transport=duplicate_transport)


def test_timeout_is_positive_and_passed_to_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "root"
    _version(root, _figures(1))
    monkeypatch.setenv("PROPOSAL_IMAGE_TIMEOUT_SECONDS", "7.5")
    transport = RecordingTransport()

    _run(monkeypatch, root, tmp_path / "state", transport)

    assert transport.calls[0][3] == 7.5


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [
            {
                "figure_id": "fig-s1-01",
                "section_id": "s1",
                "source_claim_ids": [],
                "prompt": "public concept",
                "caption": "caption",
                "png_sha256": "",
                "band_index": 0,
                "surprise": True,
            }
        ],
    ],
)
def test_malformed_figures_fail_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "root"
    version = _version(root, _figures(1))
    (version / "figures.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_STATE_ROOT", str(tmp_path / "state"))

    assert proposal_images.main(["--slug", "demo", "--json"]) != 0
    assert "IMAGE-INPUT-ERROR" in capsys.readouterr().err


def test_partial_cli_output_names_missing_slots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "root"
    _version(root, _figures(3))
    monkeypatch.setenv("PROPOSAL_ROOT", str(root))
    monkeypatch.setenv("PROPOSAL_STATE_ROOT", str(tmp_path / "state"))
    transport = RecordingTransport({"slot-2\n"})

    assert proposal_images.main(["--slug", "demo", "--json"], transport=transport) == 5
    payload = json.loads(capsys.readouterr().out)
    assert payload["missing"] == ["fig-s1-02"]
    assert len(payload["images"]) == 2


def test_live_transport_request_matches_the_image_api_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64
    import urllib.request

    payload_png = proposal_images.fake_png("live") + b"live-padding" * 128
    captured: dict[str, object] = {}

    class _Response:
        def __enter__(self) -> "_Response":
            return self

        def __exit__(self, *_: object) -> bool:
            return False

        def read(self) -> bytes:
            body = {"data": [{"b64_json": base64.b64encode(payload_png).decode("ascii")}]}
            return json.dumps(body).encode("utf-8")

    def _fake_urlopen(request: urllib.request.Request, timeout: float = 0.0) -> _Response:
        captured["body"] = json.loads(bytes(request.data or b"").decode("utf-8"))
        captured["url"] = request.full_url
        return _Response()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    result = proposal_images._live_transport(
        "public concept", "gpt-image-2", {"size": "1024x1024"}, 30.0
    )

    assert result == payload_png
    body = captured["body"]
    assert isinstance(body, dict)
    assert "response_format" not in body, (
        f"gpt-image models reject response_format; request body was {body!r}"
    )
    assert body["model"] == "gpt-image-2"
    assert body["size"] == "1024x1024"

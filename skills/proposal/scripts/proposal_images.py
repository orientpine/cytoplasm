"""Generate guarded proposal figure PNGs with private caching and budget accounting."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import struct
import sys
import tempfile
import urllib.error
import urllib.request
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, Protocol, cast

from .proposal_ir import FigureSpec, figures_from_json, figures_to_json
from .proposal_route_guard import RouteRefused, assert_route_allowed
from .proposal_version import VersionError, VersionStore


DEFAULT_MODEL: Final = "gpt-image-2"
DEFAULT_SIZE: Final = "1024x1024"
DEFAULT_TIMEOUT_SECONDS: Final = 180.0
# Conservative reservation used because the image endpoint does not return billed cost.
ESTIMATED_COST_USD_PER_IMAGE: Final = 0.04
TRANSPORT_BILLING: Final[Mapping[str, tuple[str, float]]] = {
    "live": ("openai-api", ESTIMATED_COST_USD_PER_IMAGE),
    "fake": ("openai-api", ESTIMATED_COST_USD_PER_IMAGE),
    "codex": ("chatgpt-subscription", 0.0),
}
IMAGE_BUDGET_BLOCK_EXIT: Final = 6
IMAGE_PARTIAL_EXIT: Final = 5
PROMPT_RULE: Final = "no text, no labels, no numerals"
MIN_LIVE_PNG_BYTES: Final = 1024
_FIGURE_ID: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_INPUT_FIELDS: Final = frozenset(
    {
        "band_index",
        "caption",
        "figure_id",
        "model",
        "png_sha256",
        "prompt",
        "section_id",
        "source_claim_ids",
    }
)


class ImageError(RuntimeError):
    """The image generation contract could not be completed."""


class ImageInputError(ImageError):
    """The current version has no valid figure plan."""


class ImageGenerationError(ImageError):
    """One image transport call failed."""


class ImageArtifactError(ImageGenerationError):
    """A transport returned an unusable or duplicated production artifact."""


class ImageBudgetBlocked(ImageError):
    """The next image would exceed the configured monthly soft cap."""


class ImageTransport(Protocol):
    def __call__(
        self, prompt: str, model: str, params: dict[str, str], timeout: float
    ) -> bytes: ...


@dataclass(frozen=True, slots=True)
class MissingImage:
    figure_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ImageResult:
    images: tuple[tuple[Path, str], ...]
    missing: tuple[MissingImage, ...]


@dataclass(frozen=True, slots=True)
class _Plan:
    index: int
    figure: FigureSpec
    prompt: str


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise ImageError(f"symlink directory is not allowed: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir() or path.is_symlink():
        raise ImageError(f"private directory is invalid: {path}")
    path.chmod(0o700)


def _atomic_write(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise ImageError(f"refusing to replace symlink: {path}")
    _private_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def fake_png(prompt: str) -> bytes:
    """Return a deterministic valid one-pixel RGBA PNG for offline tests."""
    color = hashlib.sha256(prompt.encode("utf-8")).digest()[:4]
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)),
            _png_chunk(b"IDAT", zlib.compress(b"\x00" + color)),
            _png_chunk(b"IEND", b""),
        )
    )


def _fake_transport(
    prompt: str, model: str, params: dict[str, str], timeout: float
) -> bytes:
    del model, params
    if timeout <= 0:
        raise ImageGenerationError("image timeout must be positive")
    return fake_png(prompt)


def _live_transport(
    prompt: str, model: str, params: dict[str, str], timeout: float
) -> bytes:
    key_name = os.environ.get("PROPOSAL_IMAGE_API_KEY_ENV", "OPENAI_API_KEY")
    api_key = os.environ.get(key_name, "").strip()
    if not api_key:
        raise ImageGenerationError(f"image API credential is absent: {key_name}")
    payload = json.dumps(
        {"model": model, "prompt": prompt, **params},
        separators=(",", ":"),
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            document = cast(object, json.loads(response.read().decode("utf-8")))
        if not isinstance(document, dict):
            raise ImageGenerationError("image API response is not an object")
        data = document.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise ImageGenerationError("image API response has no image")
        encoded = data[0].get("b64_json")
        if not isinstance(encoded, str):
            raise ImageGenerationError("image API response has no PNG payload")
        png = base64.b64decode(encoded, validate=True)
    except (
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise ImageGenerationError(f"image API request failed: {error}") from error
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ImageGenerationError("image API returned a non-PNG payload")
    return png


def _billing_party() -> tuple[str, float]:
    selected = os.environ.get("PROPOSAL_IMAGE_TRANSPORT", "live").strip().lower()
    try:
        return TRANSPORT_BILLING[selected]
    except KeyError as error:
        raise ImageInputError(
            "PROPOSAL_IMAGE_TRANSPORT must be fake, live or codex"
        ) from error


def _selected_transport() -> ImageTransport:
    selected = os.environ.get("PROPOSAL_IMAGE_TRANSPORT", "live").strip().lower()
    if selected == "fake":
        test_secret = os.environ.get("AUTOPHAGY_" + "DEMO_SECRET", "")
        if not test_secret.startswith("DUMMY-"):
            raise ImageGenerationError(
                "fake image transport is test-only and requires an isolated "
                + "DUMMY- test secret"
            )
        return _fake_transport
    if selected == "live":
        return _live_transport
    if selected == "codex":
        # Imported here because the Codex transport imports this module's errors.
        from .proposal_images_codex import codex_transport

        return codex_transport
    raise ImageInputError("PROPOSAL_IMAGE_TRANSPORT must be fake, live or codex")


def _load_figures(path: Path) -> tuple[list[dict[str, object]], tuple[FigureSpec, ...]]:
    if path.is_symlink() or not path.is_file():
        raise ImageInputError("figures.json is missing")
    try:
        raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise ImageInputError("figures.json is invalid") from error
    if not isinstance(raw, list) or not raw:
        raise ImageInputError("figures.json must contain at least one figure")
    records: list[dict[str, object]] = []
    required = _INPUT_FIELDS - {"model"}
    for value in raw:
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            raise ImageInputError("figure record is invalid")
        record = cast(dict[str, object], value)
        if set(record) - _INPUT_FIELDS or not required.issubset(record):
            raise ImageInputError("figure record fields are invalid")
        records.append(dict(record))
    try:
        figures = figures_from_json(json.dumps(records, ensure_ascii=False))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ImageInputError("figure record is invalid") from error
    identifiers: set[str] = set()
    for record, figure in zip(records, figures, strict=True):
        if (
            not isinstance(figure.figure_id, str)
            or _FIGURE_ID.fullmatch(figure.figure_id) is None
            or figure.figure_id in identifiers
            or not isinstance(figure.section_id, str)
            or not figure.section_id
            or not isinstance(figure.prompt, str)
            or not figure.prompt.strip()
            or not isinstance(figure.caption, str)
            or not isinstance(figure.band_index, int)
            or isinstance(figure.band_index, bool)
            or not all(isinstance(item, str) for item in figure.source_claim_ids)
            or not isinstance(figure.png_sha256, str)
            or bool(figure.png_sha256)
            and _SHA256.fullmatch(figure.png_sha256) is None
            or "model" in record
            and not isinstance(record["model"], str)
        ):
            raise ImageInputError("figure record values are invalid")
        identifiers.add(figure.figure_id)
    return records, figures


def _final_prompt(prompt: str) -> str:
    stripped = prompt.strip()
    return stripped if stripped.endswith(PROMPT_RULE) else f"{stripped}\n{PROMPT_RULE}"


def _cache_path(
    state_root: Path, model: str, params: dict[str, str], prompt: str
) -> Path:
    model_key = hashlib.sha256(model.encode("utf-8")).hexdigest()
    params_text = json.dumps(params, sort_keys=True, separators=(",", ":"))
    params_key = hashlib.sha256(params_text.encode("utf-8")).hexdigest()
    prompt_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return (
        state_root / "image_cache" / model_key / params_key / prompt_key / "image.png"
    )


def _valid_png_sha(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    content = path.read_bytes()
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return hashlib.sha256(content).hexdigest()


def _assert_artifact_quality(
    png: bytes,
    figure_id: str,
    seen_sha256: dict[str, str],
    *,
    allow_test_stub: bool,
) -> str:
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ImageArtifactError("transport returned a non-PNG payload")
    if not allow_test_stub and len(png) < MIN_LIVE_PNG_BYTES:
        raise ImageArtifactError(
            f"image payload for {figure_id} is abnormally small ({len(png)} bytes)"
        )
    png_sha = hashlib.sha256(png).hexdigest()
    duplicate = seen_sha256.get(png_sha)
    if not allow_test_stub and duplicate is not None:
        raise ImageArtifactError(
            f"duplicate image payload for {figure_id} matches {duplicate}"
        )
    seen_sha256[png_sha] = figure_id
    return png_sha


def _read_ledger(path: Path) -> dict[str, dict[str, dict[str, float | int]]]:
    if not path.exists():
        return {}
    if path.is_symlink() or not path.is_file():
        raise ImageError("image spend ledger is invalid")
    try:
        raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise ImageError("image spend ledger is invalid") from error
    if not isinstance(raw, dict):
        raise ImageError("image spend ledger is invalid")
    ledger: dict[str, dict[str, dict[str, float | int]]] = {}
    for month, parties in raw.items():
        if not isinstance(month, str):
            raise ImageError("image spend ledger is invalid")
        if isinstance(parties, (int, float)) and not isinstance(parties, bool):
            amount = float(parties)
            if amount < 0 or not math.isfinite(amount):
                raise ImageError("image spend ledger is invalid")
            # Legacy months never counted images; preserve their USD exactly.
            ledger[month] = {"openai-api": {"usd": amount, "images": 0}}
            continue
        if not isinstance(parties, dict):
            raise ImageError("image spend ledger is invalid")
        normalized: dict[str, dict[str, float | int]] = {}
        for party, slot in parties.items():
            if (
                not isinstance(party, str)
                or not isinstance(slot, dict)
                or set(slot) != {"usd", "images"}
            ):
                raise ImageError("image spend ledger is invalid")
            usd = slot["usd"]
            images = slot["images"]
            if (
                not isinstance(usd, (int, float))
                or isinstance(usd, bool)
                or not isinstance(images, int)
                or isinstance(images, bool)
            ):
                raise ImageError("image spend ledger is invalid")
            amount = float(usd)
            if amount < 0 or not math.isfinite(amount) or images < 0:
                raise ImageError("image spend ledger is invalid")
            normalized[party] = {"usd": amount, "images": images}
        ledger[month] = normalized
    return ledger


def _reserve_budget(
    state_root: Path, cap: float
) -> tuple[Path, str, str, dict[str, float | int]]:
    ledger_path = state_root / "image_spend.json"
    ledger = _read_ledger(ledger_path)
    month = datetime.now(UTC).strftime("%Y-%m")
    party, charge = _billing_party()
    month_ledger = ledger.setdefault(month, {})
    slot = month_ledger.setdefault(party, {"usd": 0.0, "images": 0})
    reserved = float(slot["usd"]) + charge
    if party == "openai-api" and reserved > cap + 1e-12:
        raise ImageBudgetBlocked(
            f"estimated monthly spend ${reserved:.2f} exceeds ${cap:.2f}"
        )
    reservation = {"usd": round(reserved, 8), "images": int(slot["images"]) + 1}
    month_ledger[party] = reservation
    _atomic_write(
        ledger_path,
        (json.dumps(ledger, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        ),
    )
    return ledger_path, month, party, reservation


def _finalize_reservation(
    ledger_path: Path,
    month: str,
    party: str,
    reservation: dict[str, float | int],
) -> None:
    """Finalize the conservative reservation; the estimate is the charged amount."""
    ledger = _read_ledger(ledger_path)
    if ledger.get(month, {}).get(party) != reservation:
        raise ImageError("image spend reservation changed unexpectedly")


def _write_records(path: Path, records: list[dict[str, object]]) -> None:
    # Serialize through the shared IR first so its schema remains the source of field values.
    figures = figures_from_json(json.dumps(records, ensure_ascii=False))
    canonical = cast(list[dict[str, object]], json.loads(figures_to_json(figures)))
    for output, record in zip(canonical, records, strict=True):
        if "model" in record:
            output["model"] = record["model"]
    _atomic_write(
        path,
        (
            json.dumps(canonical, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
    )


def _float_setting(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ImageInputError(f"{name} must be a positive number") from error
    if value <= 0 or not math.isfinite(value):
        raise ImageInputError(f"{name} must be a positive number")
    return value


def _cap_setting() -> float:
    try:
        value = float(os.environ.get("PROPOSAL_IMAGE_MONTHLY_CAP_USD", "10"))
    except ValueError as error:
        raise ImageInputError(
            "PROPOSAL_IMAGE_MONTHLY_CAP_USD must be non-negative"
        ) from error
    if value < 0 or not math.isfinite(value):
        raise ImageInputError("PROPOSAL_IMAGE_MONTHLY_CAP_USD must be non-negative")
    return value


def generate_images(
    slug: str,
    *,
    transport: ImageTransport | None = None,
) -> ImageResult:
    """Generate or restore all current-version figure slots, preserving each success."""
    store = VersionStore.from_environment()
    head = store.head(slug)
    if head is None:
        raise ImageInputError("proposal has no current version")
    version_path = store.resolve_slug_dir(slug) / "versions" / head
    figures_path = version_path / "figures.json"
    records, figures = _load_figures(figures_path)
    images_dir = version_path / "images"
    _private_directory(images_dir)
    state_root = Path(
        os.environ.get("PROPOSAL_STATE_ROOT", "~/.hermes/proposal")
    ).expanduser()
    _private_directory(state_root)
    model = os.environ.get("PROPOSAL_IMAGE_MODEL", DEFAULT_MODEL).strip()
    size = os.environ.get("PROPOSAL_IMAGE_SIZE", DEFAULT_SIZE).strip()
    if not model or not size:
        raise ImageInputError("image model and size must be non-empty")
    params = {"size": size}
    timeout = _float_setting("PROPOSAL_IMAGE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
    cap = _cap_setting()
    active_transport = transport or _selected_transport()
    allow_test_stub = active_transport is _fake_transport

    plans = tuple(
        _Plan(index, figure, _final_prompt(figure.prompt))
        for index, figure in enumerate(figures)
    )
    # Guard the complete batch before any transport call, including fake transports.
    for plan in plans:
        assert_route_allowed(
            plan.prompt,
            "image-api",
            source_keys=plan.figure.source_claim_ids,
        )

    images: list[tuple[Path, str]] = []
    missing: list[MissingImage] = []
    seen_sha256: dict[str, str] = {}
    for plan in plans:
        target = images_dir / f"{plan.figure.figure_id}.png"
        cache = _cache_path(state_root, model, params, plan.prompt)
        try:
            target_sha = _valid_png_sha(target)
            recorded_sha = plan.figure.png_sha256
            if target_sha is not None and recorded_sha and target_sha == recorded_sha:
                png_sha = _assert_artifact_quality(
                    target.read_bytes(),
                    plan.figure.figure_id,
                    seen_sha256,
                    allow_test_stub=allow_test_stub,
                )
            else:
                ledger_path, month, party, reservation = _reserve_budget(
                    state_root, cap
                )
                png = active_transport(plan.prompt, model, dict(params), timeout)
                png_sha = _assert_artifact_quality(
                    png,
                    plan.figure.figure_id,
                    seen_sha256,
                    allow_test_stub=allow_test_stub,
                )
                _finalize_reservation(ledger_path, month, party, reservation)
                _atomic_write(cache, png)
                _atomic_write(target, png)
            records[plan.index]["prompt"] = plan.prompt
            records[plan.index]["model"] = model
            records[plan.index]["png_sha256"] = png_sha
            _write_records(figures_path, records)
            images.append((target, png_sha))
        except (ImageBudgetBlocked, ImageArtifactError):
            raise
        except (ImageError, OSError) as error:
            missing.append(MissingImage(plan.figure.figure_id, str(error)))
    return ImageResult(tuple(images), tuple(missing))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proposal-images")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    transport: ImageTransport | None = None,
) -> int:
    args = _parser().parse_args(argv)
    try:
        result = generate_images(args.slug, transport=transport)
    except ImageBudgetBlocked as error:
        print(f"IMAGE-BUDGET-BLOCK: {error}", file=sys.stderr)
        return IMAGE_BUDGET_BLOCK_EXIT
    except RouteRefused as error:
        print(f"IMAGE-ROUTE-REFUSED: {error}", file=sys.stderr)
        return 4
    except (ImageError, VersionError, OSError) as error:
        print(f"IMAGE-INPUT-ERROR: {error}", file=sys.stderr)
        return 2
    payload = {
        "images": [{"path": str(path), "sha256": sha} for path, sha in result.images],
        "missing": [item.figure_id for item in result.missing],
    }
    if args.json:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        for image in payload["images"]:
            assert isinstance(image, dict)
            print(f"IMAGE path={image['path']} sha256={image['sha256']}")
        if result.missing:
            print(
                "IMAGE-MISSING: " + ", ".join(item.figure_id for item in result.missing)
            )
    return IMAGE_PARTIAL_EXIT if result.missing else 0


if __name__ == "__main__":
    raise SystemExit(main())

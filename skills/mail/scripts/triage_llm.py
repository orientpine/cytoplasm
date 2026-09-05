"""LLM routing for mail triage (W4-2, constraint 6) — one Codex OAuth tier.

Routing contract (2026-09-04 provider migration):
- EVERY call — classification, digest summary, Korean reply draft, sensitive or
  not — runs on the shared Codex OAuth client (``automation.codex_llm``,
  provider ``openai-codex``).
- the sensitivity gate is unchanged and still runs first. Its routing guard
  survives as an identity check: gate-hit text only leaves after the shared
  client is confirmed to be the approved Codex OAuth tier
  (``PatentRoutingError`` otherwise). There is no longer a GLM tier to keep it
  away from, so the guard now protects against the tier being repointed.
- there is NO second tier and no fallback. When Codex OAuth cannot answer
  (missing credentials, quota, transport) the call raises
  ``LlmUnavailableError`` and the caller fails closed — never a downgrade.

Every call appends one masked line to the routing log (provider/model/purpose/
opaque uid) — the auditable call-count surface for QA.

Test hooks (never set in production units; read by the shared client):
  AUTOPHAGY_HERMES_BIN   overrides the hermes binary.
  AUTOPHAGY_CODEX_MODEL  overrides the model.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import triage_core

CODEX_PROVIDER = "openai-codex"  # the only approved tier
CALL_TIMEOUT_S = 600.0  # unchanged per-call budget of the mail pipeline
REPO_ROOT_ENV = "AUTOPHAGY_REPO_ROOT"
MOUNTED_REPO_ROOT = Path("/srv/autophagy-agent-current")


class PatentRoutingError(RuntimeError):
    """Raised when triage text is about to reach a non-approved model tier."""


class LlmCallError(RuntimeError):
    """Raised when an LLM call fails at the transport level."""


class LlmUnavailableError(LlmCallError):
    """Raised when the Codex OAuth tier itself is unavailable, not this request.

    Missing OAuth credentials, quota exhaustion and transport failures mean every
    following call fails the same way. Before the migration this type selected the
    non-GLM degrade path; now it is the fail-closed signal — the caller refuses the
    run instead of routing the prompt anywhere else.
    """


def _repo_root() -> Path:
    """Locate the repository without importing it (skills stay import-light)."""
    override = os.environ.get(REPO_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    for parent in Path(__file__).resolve().parents:
        if (parent / "automation" / "skill_mount.py").is_file():
            return parent
    return MOUNTED_REPO_ROOT


def _codex_module() -> ModuleType:
    """Import the shared Codex client lazily; an ImportError REFUSES the call.

    Fail closed exactly like the approval_lifecycle rule in ``skills/AGENTS.md``:
    a skill that cannot reach the governed client does not improvise its own
    provider call.
    """
    root = str(_repo_root())
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        import automation.codex_llm as codex_llm
    except ImportError:
        raise LlmUnavailableError(
            "automation.codex_llm 임포트 실패 — 승인된 Codex OAuth 경로 없음 (호출 거부)"
        ) from None
    return codex_llm


def _approved_codex(sensitive: bool) -> ModuleType:
    """Return the shared client only if it is bound to the approved tier.

    Constraint 6's guard, carried through the migration: gate-hit text may only
    ever leave for the approved tier. With a single tier left the check is on the
    tier's identity instead of a GLM/non-GLM split — a client repointed at any
    other provider refuses the prompt before it is built into a request.
    """
    codex = _codex_module()
    if codex.PROVIDER != CODEX_PROVIDER:
        subject = "민감도 게이트 적중 메일" if sensitive else "메일"
        raise PatentRoutingError(
            f"{subject} 프롬프트가 승인되지 않은 티어({codex.PROVIDER})로 향함 — 호출 거부"
        )
    return codex


def codex_model() -> str:
    """The model the shared client will use for this call (single source: the client)."""
    codex = _codex_module()
    return os.environ.get(codex.MODEL_ENV, "").strip() or codex.DEFAULT_MODEL


def _log_path() -> Path:
    return Path(
        os.environ.get("TRIAGE_LLM_LOG", "~/.hermes/mail-triage/logs/llm-calls.jsonl")
    ).expanduser()


def _append_record(record: dict) -> None:
    """Append one masked JSON line to the routing log (0600, flock-serialized)."""
    path = _log_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(line + "\n")
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    path.chmod(0o600)


def _log_call(*, model: str, purpose: str, uid_opaque: str, sensitive: bool) -> None:
    """Append one masked routing line recording the codex route of this call.

    The line keeps its shape (provider/model/purpose/opaque uid/sensitive) so the
    QA call-count surface still parses. ``fallback_from`` is gone with the tier it
    described — a degraded call can no longer exist.
    """
    _append_record(
        {
            "model": model,
            "provider": CODEX_PROVIDER,
            "purpose": purpose,
            "sensitive": sensitive,
            "timestamp": triage_core.utc_now(),
            "uid": uid_opaque,
        }
    )


def log_failure(*, purpose: str, uid_opaque: str, sensitive: bool, error: BaseException) -> None:
    """Record one masked ``<purpose>_failed`` line next to the successful calls.

    The digest runs under a no-agent cron that drops stderr, so this line is the
    only forensic trace of a per-mail LLM failure. Only the exception class and a
    redacted, clipped message are kept — never subject, sender, body or addresses.
    """
    _append_record(
        {
            "error": f"{type(error).__name__}: {triage_core.redact(str(error))[:160]}",
            "purpose": f"{purpose}_failed",
            "sensitive": sensitive,
            "timestamp": triage_core.utc_now(),
            "uid": uid_opaque,
        }
    )


def call_codex(prompt: str, *, sensitive: bool = False, timeout: float = CALL_TIMEOUT_S) -> str:
    """One completion on the approved Codex OAuth tier — no retry, no fallback.

    The shared client owns the argv (``--ignore-user-config`` is load bearing:
    without it hermes reads the user config and may switch to a configured
    fallback provider on auth/quota/transport errors), the child environment and
    the success rule (rc 0 AND non-empty stdout).
    """
    codex = _approved_codex(sensitive)
    try:
        client = codex.CodexClient.from_environment(timeout=timeout)
        return client.complete(prompt)
    except codex.CodexUnavailableError as error:
        raise LlmUnavailableError(_failure_text(error)) from None
    except codex.CodexError as error:
        raise LlmCallError(_failure_text(error)) from None


def _failure_text(error: BaseException) -> str:
    return f"codex 호출 실패: {triage_core.redact(str(error))[:200]}"


def classify(
    *, subject: str, sender: str, body: str, sensitive: bool, uid_opaque: str, prompt_path: Path,
) -> tuple[triage_core.Classification, str]:
    """Step-2 classification on the approved Codex OAuth tier.

    The step-① sensitivity verdict no longer selects a tier (there is one), but it
    still travels with the call: it arms the routing guard and stays in the masked
    audit line.
    """
    prompt = triage_core.build_prompt(
        triage_core.load_prompt_template(prompt_path),
        subject=subject, sender=sender, body=body,
    )
    raw = call_codex(prompt, sensitive=sensitive)
    _log_call(model=codex_model(), purpose="classify",
              uid_opaque=uid_opaque, sensitive=sensitive)
    return triage_core.parse_classification(raw), CODEX_PROVIDER


def draft_reply(
    *, subject: str, sender: str, body: str, sensitive: bool, uid_opaque: str,
    prompt_path: Path, instruction: str = "", evidence: str = "",
) -> tuple[str, str, str]:
    """Step-3 Korean final-text reply — the approved Codex OAuth tier, as before."""
    prompt = triage_core.build_prompt(
        triage_core.load_prompt_template(prompt_path),
        subject=subject, sender=sender, body=body, instruction=instruction,
        evidence=evidence,
    )
    raw = call_codex(prompt, sensitive=sensitive)
    _log_call(model=codex_model(), purpose="draft_reply",
              uid_opaque=uid_opaque, sensitive=sensitive)
    llm_subject, reply_body = triage_core.parse_reply(raw)
    return triage_core.reply_subject(llm_subject, subject), reply_body, CODEX_PROVIDER


def summarize(
    *, subject: str, sender: str, body: str, sensitive: bool, uid_opaque: str, prompt_path: Path,
) -> str:
    """One-line Korean digest summary on the approved Codex OAuth tier."""
    prompt = triage_core.build_prompt(
        triage_core.load_prompt_template(prompt_path),
        subject=subject, sender=sender, body=body,
    )
    raw = call_codex(prompt, sensitive=sensitive)
    _log_call(model=codex_model(), purpose="digest_summary",
              uid_opaque=uid_opaque, sensitive=sensitive)
    return triage_core.parse_digest_summary(raw)

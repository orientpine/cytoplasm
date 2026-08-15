"""W4-5 scenario actor: procurement document generation with ZERO external
send (W4-4 — the documents path never mails anything, GO branch or not).

Fully offline against synthetic template fixtures (stdlib hwpx leg so the
bank has no optional-dependency requirement) plus stub Discord/gws transports
for the review-DM size branch. Asserts refusals are fail-closed (binary .hwp,
missing fields) and that the WHOLE path performs 0 mail sends and writes 0
approval records — documents need review, not an external-effect approval.

Emits one flat observation map per scenario case as `OBS-JSON: {...}`.
No network, no production paths, zero real sends.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

GWS_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$(dirname "$0")/gws-calls.log"
case "$*" in
  *"files list"*) printf '{"files":[]}\\n' ;;
  *"files create"*) printf '{"id":"stub-fold"}\\n' ;;
  *"+upload"*) printf '{"id":"stub-drive-1"}\\n' ;;
  *"files get"*) printf '{"webViewLink":"https://drive.google.com/file/d/stub-drive-1/view"}\\n' ;;
esac
"""


def _cli(root: Path, env: dict[str, str], *args: str):
    cli = root / "skills" / "procurement" / "scripts" / "procure_cli.py"
    return subprocess.run(  # noqa: S603
        [sys.executable, str(cli), *args],
        env=env, capture_output=True, text=True, timeout=300, check=False, cwd=env["PROCURE_WORKDIR"],
    )


def _attempts(audit: Path) -> int:
    if not audit.exists():
        return 0
    return audit.read_text(encoding="utf-8").count("GENERATION-ATTEMPT")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    root = Path(parser.parse_args().root).resolve()
    scripts = root / "skills" / "procurement" / "scripts"

    with tempfile.TemporaryDirectory(prefix="w4-procure-bank-") as tmp:
        work = Path(tmp)
        (work / "out").mkdir()
        (work / "stub").mkdir()
        (work / "home").mkdir()
        gws = work / "gws-stub"
        gws.write_text(GWS_STUB, encoding="utf-8")
        gws.chmod(0o755)
        audit = work / "audit.log"
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(work / "home"),
            "PROCURE_SESSION_DIR": str(work / "sess"),
            "PROCURE_AUDIT_LOG": str(audit),
            "PROCURE_WORKDIR": str(work),
        }
        subprocess.run(  # noqa: S603
            [sys.executable, str(scripts / "make_fixtures.py"), str(work / "fx"),
             "--large-hwpx-bytes", str(26 * 1024 * 1024)],
            env=env, capture_output=True, text=True, timeout=300, check=True, cwd=str(work),
        )
        obs: dict[str, dict[str, Any]] = {}

        # --- case 1: preflight + fail-closed refusals + generate/verify --------
        pre = _cli(root, env, "preflight", "fx/용역요청서-샘플.hwpx")
        hwp = _cli(root, env, "preflight", "fx/구양식-샘플.hwp")
        hwp_gen = _cli(root, env, "generate", "--template", "fx/구양식-샘플.hwp",
                       "--fields-json", '{"품목":"x","금액":"1000원","업체":"y"}', "--out", "out/nope.hwpx")
        hwp_attempts = _attempts(audit)
        start = _cli(root, env, "collect-start", "--template", "fx/용역요청서-샘플.hwpx")
        sid_match = re.search(r"COLLECT-STARTED session=([0-9a-f]+)", start.stdout)
        sid = sid_match.group(1) if sid_match else ""
        _cli(root, env, "collect-answer", "--session", sid, "--field", "품목", "--value", "합성 소모품")
        bad_amount = _cli(root, env, "collect-answer", "--session", sid, "--field", "금액", "--value", "많이요")
        refused = _cli(root, env, "generate", "--session", sid, "--out", "out/refused.hwpx")
        _cli(root, env, "collect-answer", "--session", sid, "--field", "금액", "--value", "123,000원")
        complete = _cli(root, env, "collect-answer", "--session", sid, "--field", "업체", "--value", "합성벤더")
        generated = _cli(root, env, "generate", "--session", sid, "--out", "out/draft.hwpx")
        obs["generate_verify"] = {
            "preflight_hwpx_ok": "format=hwpx parser=zip+XML" in pre.stdout,
            "hwp_conversion_request_exit": hwp.returncode,
            "hwp_conversion_request": "CONVERSION-REQUEST" in (hwp.stdout + hwp.stderr),
            "hwp_generate_refused_exit": hwp_gen.returncode,
            "hwp_generation_attempts": hwp_attempts,
            "hwp_output_absent": not (work / "out" / "nope.hwpx").exists(),
            "nonnumeric_amount_exit": bad_amount.returncode,
            "nonnumeric_amount_rejected": "ANSWER-REJECTED" in (bad_amount.stdout + bad_amount.stderr),
            "missing_field_exit": refused.returncode,
            "missing_field_refused": "GENERATION-REFUSED" in (refused.stdout + refused.stderr),
            "refused_output_absent": not (work / "out" / "refused.hwpx").exists(),
            "collection_complete": "COLLECT-COMPLETE" in complete.stdout,
            "hwpx_generated_verified": "VERIFIED parser=zip+XML" in generated.stdout,
            "error": None,
        }

        # --- case 2: review size branches with ZERO external send --------------
        large = _cli(root, env, "generate", "--template", "fx/대형-용역요청서-샘플.hwpx",
                     "--fields-json", '{"품목":"대형 테스트","금액":"999,000원","업체":"합성벤더"}',
                     "--out", "out/large.hwpx")
        review_env = {**env, "PROCURE_DISCORD_STUB": str(work / "stub"), "PROCURE_GWS_BIN": str(gws)}
        small_review = _cli(root, review_env, "review", "--file", "out/draft.hwpx", "--note", "합성")
        large_review = _cli(root, review_env, "review", "--file", "out/large.hwpx", "--note", "합성 대형")
        gws_text = (work / "gws-calls.log").read_text(encoding="utf-8") if (work / "gws-calls.log").exists() else ""
        obs["zero_external_send"] = {
            "large_hwpx_generated": "VERIFIED" in large.stdout,
            "small_review_attach": "mode=attach" in small_review.stdout,
            "large_review_drive_link": "mode=drive-link" in large_review.stdout,
            "drive_upload_via_stub": "drive +upload" in gws_text,
            "drive_folder_created": "files create" in gws_text,
            "drive_upload_parented": "+upload" in gws_text and "--parent" in gws_text,
            "gmail_send_calls": gws_text.count("gmail +send"),
            "mailon_send_calls": gws_text.count("mailon"),
            "approvals_file_created": (work / "approvals.jsonl").exists(),
            "external_effect_records_in_audit": audit.read_text(encoding="utf-8").count("external_effect") if audit.exists() else 0,
            "error": None,
        }

        print("OBS-JSON: " + json.dumps(obs, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

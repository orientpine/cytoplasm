#!/usr/bin/env bash
# W0-7c: validate the institutional-mail go/no-go verdict in configs/mail-mode.default.json.
# PASS (exit 0) iff the file exists, is valid JSON, and:
#   mode       ∈ {no-go, read-go, full-go}
#   decided_at is an ISO-8601 UTC timestamp (...Z)
#   source     is a non-empty string
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_root=$(cd -- "$script_dir/.." && pwd -P)
mode_file=${MAIL_MODE_PATH:-"$repo_root/configs/mail-mode.default.json"}

[[ -f $mode_file ]] || { echo "FAIL: missing $mode_file" >&2; exit 1; }

python3 - "$mode_file" <<'PY'
import datetime as dt
import json
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
except (OSError, json.JSONDecodeError) as error:
    print(f"FAIL: unreadable or invalid JSON: {error}", file=sys.stderr)
    raise SystemExit(1)

errors = []
mode = data.get("mode")
if mode not in ("no-go", "read-go", "full-go"):
    errors.append(f"mode must be one of no-go|read-go|full-go, got {mode!r}")

decided_at = data.get("decided_at")
if not isinstance(decided_at, str) or not decided_at.endswith("Z"):
    errors.append(f"decided_at must be an ISO-8601 UTC string ending in Z, got {decided_at!r}")
else:
    try:
        dt.datetime.fromisoformat(decided_at.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"decided_at is not parseable ISO-8601: {decided_at!r}")

source = data.get("source")
if not isinstance(source, str) or not source:
    errors.append(f"source must be a non-empty string, got {source!r}")

if errors:
    for line in errors:
        print(f"FAIL: {line}", file=sys.stderr)
    raise SystemExit(1)

print(f"PASS mail-mode: mode={mode} decided_at={decided_at} source={source}")
PY

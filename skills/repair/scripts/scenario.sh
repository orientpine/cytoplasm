#!/usr/bin/env bash
set -euo pipefail

work="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$work"

python3 -I - <<'PY'
from pathlib import Path

skill = Path("SKILL.md").read_text(encoding="utf-8")
assert "!repair" in skill
assert "blocked" in skill
assert "SHA-256" in skill
PY

printf 'SCENARIO-PASS\n'

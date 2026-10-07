#!/usr/bin/env bash
# Build a versioned, checkout-external runtime release of the vendored mailon
# package and atomically activate it. Runs LOCALLY on the target node as the
# `agent` account (credential-free: no secrets are read or copied here).
#
# Why a separate runtime dir (Oracle S3): mailon/config.py fixes
#   PROJECT_ROOT = Path(__file__).resolve().parent.parent
# and WRITES PROJECT_ROOT/{data,logs} + reads PROJECT_ROOT/.env at runtime.
# The vendored source must stay byte-identical (no edits), so we materialise a
# runtime release OUTSIDE the git checkout and let PROJECT_ROOT resolve there.
# data/ logs/ .venv are symlinked to stable shared state so redeploys/rollbacks
# keep the SQLite DB and the built venv. This never dirties the checkout.
#
# Layout (RUNTIME_ROOT = ~/.hermes/mailon-runtime):
#   RUNTIME_ROOT/
#   ├── current -> releases/<src_digest>-<req_digest>
#   ├── releases/<src_digest>-<req_digest>/{mailon/, data->../../state/data,
#   │       logs->../../state/logs, .venv->../../venvs/<py>-<reqhash>, runtime-manifest.json}
#   ├── state/{data/, logs/}
#   └── venvs/<py>-<reqhash>/
#
# Releases are immutable and never deleted: a stored draft's argv can point at an
# older release's venv python. A release's identity is the (src_digest, req_digest)
# pair recorded in its runtime-manifest.json, re-verified against its own mailon/ —
# legacy directories named releases/<src_digest> are judged the same way, by manifest.
# Same identity already active -> no change at all. Existing target that fails
# verification -> refuse (rc 1) and leave it in place for a human to inspect.
#
# Usage:
#   mailon_runtime_release.sh <vendor_dir>
# where <vendor_dir> holds mailon/ and requirements.txt (byte-identical to the
# approved skill artifact). Prints the activated release path on success.
set -euo pipefail

fail() { printf 'MAILON-RUNTIME-BLOCK: %s\n' "$1" >&2; exit "${2:-1}"; }

vendor_dir="${1:-}"
[[ -n "$vendor_dir" ]] || fail "usage: mailon_runtime_release.sh <vendor_dir>" 2
vendor_dir="$(cd "$vendor_dir" 2>/dev/null && pwd)" || fail "vendor dir not found: ${1}" 2
[[ -d "$vendor_dir/mailon" ]] || fail "no mailon/ under $vendor_dir" 2
[[ -f "$vendor_dir/requirements.txt" ]] || fail "no requirements.txt under $vendor_dir" 2

runtime_root="${MAILON_RUNTIME_ROOT:-$HOME/.hermes/mailon-runtime}"
python_bin="${MAILON_RUNTIME_PYTHON:-python3}"
"$python_bin" --version >/dev/null 2>&1 || fail "python not runnable: $python_bin" 6

# --- content digests (provenance + venv reuse) -----------------------------
# Source digest: sha256 over sorted mailon/*.py contents. The formula lives in the
# shared helper because the drift probe recomputes it from the release tree — if the
# producer and the detector each kept a copy, the drift detector could drift. It used
# to be inline here and folded the absolute vendor path into the hash (sha256sum prints
# "<hash>  <path>"), so identical bytes unpacked elsewhere digested differently.
# shellcheck source=/dev/null
. "$(dirname "${BASH_SOURCE[0]}")/mailon_vendor_digest.sh" \
  || fail "digest helper missing beside mailon_runtime_release.sh" 2
src_digest="$(mailon_vendor_digest "$vendor_dir/mailon")" \
  || fail "digest of $vendor_dir/mailon failed" 2
req_digest="$(sha256sum "$vendor_dir/requirements.txt" | cut -c1-16)"
py_tag="$("$python_bin" -c 'import sys;print(f"cp{sys.version_info.major}{sys.version_info.minor}")')"
venv_key="${py_tag}-${req_digest}"
release_name="${src_digest}-${req_digest}"
release_dir="$runtime_root/releases/$release_name"
venv_dir="$runtime_root/venvs/$venv_key"
state_data="$runtime_root/state/data"
state_logs="$runtime_root/state/logs"

# Exactly one "<key>": "<16 hex>" line per digest, as written by the manifest below.
_manifest_field() {
  local found
  found="$(sed -nE "s/^[[:space:]]*\"$2\"[[:space:]]*:[[:space:]]*\"([0-9a-f]{16})\"[[:space:]]*,?[[:space:]]*\$/\\1/p" \
             "$1/runtime-manifest.json" 2>/dev/null)" || return 1
  [[ -n "$found" && "$found" != *$'\n'* ]] || return 1
  printf '%s' "$found"
}
# A release is ours iff its manifest names this identity AND its mailon/ re-digests to it.
_verify_release() {
  [[ -d "$1" && ! -L "$1" ]] || return 1
  [[ "$(_manifest_field "$1" src_digest)" == "$src_digest" ]] || return 1
  [[ "$(_manifest_field "$1" req_digest)" == "$req_digest" ]] || return 1
  [[ "$(mailon_vendor_digest "$1/mailon" 2>/dev/null)" == "$src_digest" ]]
}
_activate() {
  ln -sfn "$1" "$runtime_root/current.tmp.$$"
  mv -T "$runtime_root/current.tmp.$$" "$runtime_root/current"
}

# (1) Same identity already active (legacy name included): change nothing.
if [[ -L "$runtime_root/current" ]]; then
  active="$(readlink -f "$runtime_root/current")" || active=""
  if [[ -n "$active" ]] && _verify_release "$active" && [[ -x "$active/.venv/bin/python" ]]; then
    printf '%s\n' "$active"; exit 0
  fi
fi

# Only this run's own temporaries are ever removed, and only if they still exist.
tmp_venv="" staged=""
trap '[[ -z "$tmp_venv" ]] || rm -rf "$tmp_venv"; [[ -z "$staged" ]] || rm -rf "$staged"' EXIT

umask 077
mkdir -p "$runtime_root/releases" "$runtime_root/venvs" "$state_data/mails" \
         "$state_data/attachments" "$state_logs"

# --- venv (deploy-time build, pinned) --------------------------------------
if [[ ! -x "$venv_dir/bin/python" ]]; then
  tmp_venv="$venv_dir.tmp.$$"
  rm -rf "$tmp_venv"
  # --clear + no system site-packages: a fully isolated venv so `pip check`
  # sees ONLY mailon's deps, not whatever is installed on the build host.
  "$python_bin" -m venv --clear --without-pip "$tmp_venv" 2>/dev/null \
    || "$python_bin" -m venv --clear "$tmp_venv" || fail "venv creation failed" 6
  "$tmp_venv/bin/python" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$tmp_venv/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  # No source fallback for a missing wheel: prefer a hard block over silently
  # compiling an unpinned C-extension (Oracle risk #2).
  "$tmp_venv/bin/python" -m pip install --quiet --require-virtualenv \
      -r "$vendor_dir/requirements.txt" \
    || fail "pip install failed (deps not reproducible for $venv_key)" 6
  # Gate on the deps mailon actually imports (not `pip check`, which would flag
  # unrelated packages if the venv ever inherited host site-packages).
  "$tmp_venv/bin/python" - <<'PY' || fail "dependency import smoke failed" 6
import pyotp, dotenv, bs4, lxml  # noqa: F401
from lxml import etree  # native import must succeed
PY
  mv "$tmp_venv" "$venv_dir"
  tmp_venv=""
fi

# (2) Target already exists: verify and switch, or refuse. Never delete or rewrite it.
if [[ -e "$release_dir" || -L "$release_dir" ]]; then
  _verify_release "$release_dir" \
    || fail "existing release $release_name failed verification; refusing to delete it"
  _activate "$release_dir"
  printf '%s\n' "$release_dir"; exit 0
fi

# (3) New release: materialise staged, verify, then move into place.
staged="$release_dir.staged.$$"
rm -rf "$staged"
mkdir -p "$staged"
cp -a "$vendor_dir/mailon" "$staged/mailon"
# Byte-identical gate: staged mailon must match the vendor artifact exactly.
_hash_tree() { find "$1" -type f -name '*.py' -print0 | sort -z \
                 | xargs -0 sha256sum | sed "s# $2/# #" | sha256sum; }
if [[ "$(_hash_tree "$staged/mailon" "$staged")" \
      != "$(_hash_tree "$vendor_dir/mailon" "$vendor_dir")" ]]; then
  fail "staged mailon hash != vendor (provenance mismatch)"
fi
ln -s "$state_data" "$staged/data"
ln -s "$state_logs" "$staged/logs"
ln -s "$venv_dir" "$staged/.venv"
cat > "$staged/runtime-manifest.json" <<JSON
{
  "src_digest": "$src_digest",
  "req_digest": "$req_digest",
  "venv_key": "$venv_key",
  "python": "$("$venv_dir/bin/python" --version 2>&1)",
  "built_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "vendor_source": "skills/mail/vendor"
}
JSON

_verify_release "$staged" || fail "staged release $release_name failed verification"
# -n: a release that appeared concurrently is never overwritten; staged then survives
# the mv and is reported (and cleaned up by the trap) instead of being nested inside.
mv -T -n "$staged" "$release_dir" 2>/dev/null || true
[[ ! -e "$staged" ]] \
  || fail "could not move the staged build to $release_name (it may already exist); refusing to overwrite it"
staged=""
_activate "$release_dir"

printf '%s\n' "$release_dir"

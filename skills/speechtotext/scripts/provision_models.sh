#!/usr/bin/env bash
# large-v3 모델은 체크아웃이 아닌 노드 런타임 상태에만 남긴다.
set -uo pipefail

readonly MIN_FREE_KIB=$((10 * 1024 * 1024))
readonly WHISPER_HOME="${WHISPER_CPP_DIR:-$HOME/whisper.cpp}"
readonly MODELS_DIR="$WHISPER_HOME/models"
readonly DOWNLOAD_SCRIPT="$MODELS_DIR/download-ggml-model.sh"
readonly QUANTIZE_BIN="$WHISPER_HOME/build/bin/whisper-quantize"
readonly STATE_DIR="${STT_EVAL_ROOT:-$HOME/.hermes/stt-eval}"
readonly STATE_FILE="$STATE_DIR/models.json"
readonly COMMAND_TIMEOUT_SECS="${MODEL_PROVISION_TIMEOUT_SECS:-1800}"
readonly F16_PATH="$MODELS_DIR/ggml-large-v3.bin"
readonly Q8_PATH="$MODELS_DIR/ggml-large-v3-q8_0.bin"

block() {
    printf 'MODEL-PROVISION-BLOCK %s\n' "$1" >&2
    exit 1
}

skip_q8() {
    printf 'MODEL-PROVISION-SKIP model=large-v3-q8_0 reason=%s\n' "$1" >&2
}

require_timeout() {
    case "$COMMAND_TIMEOUT_SECS" in
        ''|*[!0-9]*) block 'timeout' ;;
    esac
    if ((10#$COMMAND_TIMEOUT_SECS == 0)); then
        block 'timeout'
    fi
}

check_disk() {
    local available_kib
    available_kib="$(df -Pk "$MODELS_DIR" 2>/dev/null | awk 'NR == 2 { print $4 }')"
    case "$available_kib" in
        ''|*[!0-9]*) block 'disk' ;;
    esac
    if ((10#$available_kib < MIN_FREE_KIB)); then
        block 'disk'
    fi
}

download_model() {
    local model="$1"
    local target="$2"
    if [[ -s "$target" ]]; then
        printf 'MODEL-PROVISION-EXISTS model=%s\n' "$model"
        return 0
    fi
    if ! (cd "$MODELS_DIR" && timeout "$COMMAND_TIMEOUT_SECS" bash "$DOWNLOAD_SCRIPT" "$model"); then
        printf 'MODEL-PROVISION-FAIL model=%s reason=download\n' "$model" >&2
        return 1
    fi
    if [[ ! -s "$target" ]]; then
        printf 'MODEL-PROVISION-FAIL model=%s reason=download-output-missing\n' "$model" >&2
        return 1
    fi
    printf 'MODEL-PROVISION-READY model=%s\n' "$model"
}

write_model_state() {
    python3 - "$STATE_FILE" "$F16_PATH" "$Q8_PATH" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

state = Path(sys.argv[1])
try:
    prior = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}
    models = prior.get("models", {})
    if not isinstance(models, dict):
        raise ValueError("models object")
except (OSError, ValueError, json.JSONDecodeError) as failure:
    print(f"MODEL-PROVISION-BLOCK state reason={type(failure).__name__}", file=sys.stderr)
    raise SystemExit(1)

for raw_path, label in zip(sys.argv[2:], ("large-v3-f16", "large-v3-q8_0"), strict=True):
    path = Path(raw_path)
    if not path.is_file() or path.stat().st_size == 0:
        continue
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    models[label] = {"file": path.name, "sha256": digest}

state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
temporary = state.with_name(f".{state.name}.tmp")
temporary.write_text(json.dumps({"models": models}, sort_keys=True) + "\n", encoding="utf-8")
temporary.chmod(0o600)
os.replace(temporary, state)
PY
}

require_timeout
check_disk
[[ -f "$DOWNLOAD_SCRIPT" ]] || block 'download-script'

if ! download_model 'large-v3' "$F16_PATH"; then
    exit 1
fi

if [[ ! -s "$Q8_PATH" ]]; then
    if ! download_model 'large-v3-q8_0' "$Q8_PATH"; then
        rm -f "$Q8_PATH"
        if [[ ! -x "$QUANTIZE_BIN" ]]; then
            skip_q8 'whisper-quantize-missing'
        elif ! timeout "$COMMAND_TIMEOUT_SECS" "$QUANTIZE_BIN" "$F16_PATH" "$Q8_PATH" q8_0; then
            rm -f "$Q8_PATH"
            skip_q8 'whisper-quantize-failed'
        elif [[ ! -s "$Q8_PATH" ]]; then
            skip_q8 'whisper-quantize-output-missing'
        else
            printf 'MODEL-PROVISION-READY model=large-v3-q8_0 source=quantize\n'
        fi
    fi
else
    printf 'MODEL-PROVISION-EXISTS model=large-v3-q8_0\n'
fi

if ! write_model_state; then
    exit 1
fi
printf 'MODEL-PROVISION-READY model=large-v3-f16\n'

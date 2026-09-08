#!/usr/bin/env bash
# 비밀 파일은 읽지 않는다. 호출자가 HF_TOKEN 과 체크아웃 밖 venv 경로를 준다.
set +x
set -euo pipefail
if [[ -z "${HF_TOKEN:-}" || -z "${HF_TOKEN//[[:space:]]/}" ]]; then
    printf '%s\n' STT-ENGINES-NO-TOKEN >&2
    exit 4
fi
if [[ $# -gt 1 || -z "${1:-${STT_ENGINES_VENV:-}}" ]]; then
    printf '%s\n' 'STT-ENGINES-VENV-REQUIRED' >&2
    exit 2
fi
project="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export STT_ENGINES_CHECKOUT_ROOT="$(cd -- "$project/../.." && pwd -P)"
export UV_PROJECT_ENVIRONMENT="${1:-$STT_ENGINES_VENV}"
export HF_HOME="${HF_HOME:-$HOME/.cache/stt-engines/huggingface}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$HOME/.cache/uv}"
export PYTHONDONTWRITEBYTECODE=1
export STT_ENGINES_CPU_MAX_MS="${STT_ENGINES_CPU_MAX_MS:-600000}"
# 심볼릭 링크를 해석한 뒤에도 런타임 상태는 어떤 체크아웃 안에도 두지 않는다.
python3 - <<'PY'
import os
from pathlib import Path
root = Path(os.environ['STT_ENGINES_CHECKOUT_ROOT']).resolve()
for key in ('UV_PROJECT_ENVIRONMENT', 'HF_HOME', 'UV_CACHE_DIR'):
    path = Path(os.environ[key])
    if not path.is_absolute():
        raise SystemExit('STT-ENGINES-PATH-MUST-BE-ABSOLUTE')
    path = path.resolve()
    if path == root or path.is_relative_to(root) or any((p / '.git').exists() for p in path.parents):
        raise SystemExit('STT-ENGINES-CHECKOUT-STATE-REFUSED')
PY
if ! command -v uv >/dev/null 2>&1; then
    export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null
# 다운로드·의존성 설치도 무한 대기·자동 재시도 없이 끝낸다.
export UV_HTTP_TIMEOUT=30 UV_HTTP_RETRIES=0
if [[ ! -x "$UV_PROJECT_ENVIRONMENT/bin/python" ]]; then
    timeout --kill-after=10s 600s uv venv --python 3.12 "$UV_PROJECT_ENVIRONMENT"
fi
timeout --kill-after=10s 3600s uv sync --project "$project" --frozen --no-dev --no-editable
"$UV_PROJECT_ENVIRONMENT/bin/python" - <<'PY'
import torch
if not torch.cuda.is_available():
    print('STT-ENGINES-CPU-ONLY')
PY
"$UV_PROJECT_ENVIRONMENT/bin/stt-engines" prepare --engine diarize
"$UV_PROJECT_ENVIRONMENT/bin/stt-engines" prepare --engine align

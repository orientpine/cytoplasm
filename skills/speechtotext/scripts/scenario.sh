#!/usr/bin/env bash
# speechtotext 샌드박스 시나리오 — 완전 오프라인·결정적·실자격증명 없음.
#
# 계약(deploy-skill.sh stage 1): `env -i HOME=<tmp> PATH=/usr/bin:/bin` 아래서 돌고,
# 성공하면 SCENARIO-PASS 를 출력하며 어떤 실패에도 non-zero 로 끝난다. peer 리뷰어는
# HOME/PATH/AUTOPHAGY_DEMO_SECRET 만 준 채 이것을 다시 돌리므로, 여기서는 repo 트리도
# 네트워크도 실제 자격증명도 건드릴 수 없다.
set -euo pipefail

fail() { printf 'SCENARIO-FAIL %s\n' "$1" >&2; exit 1; }

secret="${AUTOPHAGY_DEMO_SECRET:-}"
[ -n "$secret" ] || fail "AUTOPHAGY_DEMO_SECRET is not set"
case "$secret" in
  DUMMY-*) ;;
  *) fail "secret lacks the DUMMY- prefix (a real secret must never reach the sandbox)" ;;
esac

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cli="$skill_dir/scripts/speechtotext_cli.py"
audio="$skill_dir/fixtures/sample-meeting.wav"
work="$(mktemp -d)"
trap 'cd / && rm -rf "$work"' EXIT
export PYTHONPYCACHEPREFIX="$work/pycache"
cd "$work"

# 전사 엔드포인트는 닫힌 포트를 가리키고 자격증명은 비어 있다 — 정말로 밖으로 나가려는
# 시도는 조용히 성공하는 대신 시끄럽게 실패한다.
export SPEECHTOTEXT_TRANSCRIPT_DIR="$work/transcripts"
export SPEECHTOTEXT_BACKEND=api
export SPEECHTOTEXT_BASE_URL="http://127.0.0.1:1/v1"
export OPENAI_API_KEY=""

cat > "$work/fake_meeting.py" <<'PY'
"""meeting CLI 대역: 넘겨받은 argv 만 기록하고 아무것도 실행하지 않는다."""
import json, os, sys
with open(os.environ["FAKE_MEETING_RECORD"], "w", encoding="utf-8") as handle:
    json.dump({"argv": sys.argv[1:]}, handle)
sys.exit(int(os.environ.get("FAKE_MEETING_EXIT", "0")))
PY
export SPEECHTOTEXT_MEETING_CLI="$work/fake_meeting.py"
export FAKE_MEETING_RECORD="$work/meeting-call.json"
printf '%s' '오늘 킥오프 회의를 시작합니다. 예산 초안은 다음 주까지 공유하기로 했습니다.' > "$work/spoken.txt"
: > "$work/blank.txt"

echo "[1] compile"
python3 -m py_compile "$skill_dir"/scripts/*.py

echo "[2] 녹취 텍스트 → 전사본(.md) 생성 + meeting 체인에 전달"
python3 "$cli" ingest --file "$audio" --label '킥오프' --recorded "$work/spoken.txt" \
  > "$work/summary.json" || fail "ingest exited nonzero"
python3 - "$work" <<'PY'
import json, pathlib, sys
work = pathlib.Path(sys.argv[1])
summary = json.loads((work / "summary.json").read_text(encoding="utf-8"))
transcript = pathlib.Path(summary["transcript_path"])
assert transcript.is_file(), "transcript was not written"
assert summary["meeting_exit"] == 0, summary
body = transcript.read_text(encoding="utf-8")
assert "킥오프" in body and "예산 초안" in body, body[:200]
call = json.loads((work / "meeting-call.json").read_text(encoding="utf-8"))["argv"]
assert call[0] == "ingest", call
assert call[call.index("--file") + 1] == str(transcript), call
PY

echo "[3] 미지원 입력 → exit 5, meeting 미호출"
rm -f "$work/meeting-call.json"
printf 'not audio' > "$work/notes.txt"
rc=0; python3 "$cli" ingest --file "$work/notes.txt" --label x > /dev/null || rc=$?
[ "$rc" -eq 5 ] || fail "unsupported input rc=$rc"
[ ! -f "$work/meeting-call.json" ] || fail "a refused input still reached the meeting chain"

echo "[4] 빈 전사 결과 → exit 5, 내용을 추측하지 않음"
rc=0; python3 "$cli" ingest --file "$audio" --label x --recorded "$work/blank.txt" > /dev/null || rc=$?
[ "$rc" -eq 5 ] || fail "blank transcription rc=$rc"

echo "[5] 명시 local 백엔드 + 도구 부재 → exit 4, 네트워크 폴백 없음"
rc=0
SPEECHTOTEXT_BACKEND=local SPEECHTOTEXT_WHISPER_BIN="$work/absent-whisper" \
  python3 "$cli" ingest --file "$audio" --label '비공개' > /dev/null || rc=$?
[ "$rc" -eq 4 ] || fail "explicit local backend rc=$rc"
[ ! -f "$work/meeting-call.json" ] || fail "a refused backend still reached the meeting chain"

# 스텁으로 만든 로컬 도구 — 모델을 샌드박스에 싣지 않고도 "완결성 판정이 회의록 생성 여부를
# 결정한다"는 계약을 그대로 증명한다.
cat > "$work/ffmpeg" <<'SH'
#!/bin/sh
for a in "$@"; do last="$a"; done
printf 'RIFF' > "$last"
SH
cat > "$work/ffprobe" <<'SH'
#!/bin/sh
printf '{"format":{"duration":"7200.0"}}'
SH
chmod +x "$work/ffmpeg" "$work/ffprobe"
printf 'ggml' > "$work/model.bin"
make_whisper() {
  cat > "$work/whisper-cli" <<SH
#!/bin/sh
of=""
while [ \$# -gt 0 ]; do case "\$1" in -of) of="\$2"; shift 2;; *) shift;; esac; done
printf '%s' '{"transcription":[{"offsets":{"from":0,"to":$1},"text":" 회의를 시작합니다."}]}' > "\$of.json"
SH
  chmod +x "$work/whisper-cli"
}
export SPEECHTOTEXT_WHISPER_MODEL="$work/model.bin"
export SPEECHTOTEXT_FFMPEG_BIN="$work/ffmpeg"
export SPEECHTOTEXT_FFPROBE_BIN="$work/ffprobe"

echo "[6] 2시간 녹음인데 전사가 12분에서 멈춤 → exit 8, 회의록 미생성"
make_whisper 720000
rc=0
SPEECHTOTEXT_BACKEND=local SPEECHTOTEXT_WHISPER_BIN="$work/whisper-cli" \
  python3 "$cli" ingest --file "$audio" --label '장시간' > /dev/null || rc=$?
[ "$rc" -eq 8 ] || fail "truncated long recording rc=$rc"
[ ! -f "$work/meeting-call.json" ] || fail "a truncated transcript still became minutes"

echo "[7] 같은 녹음이 끝까지 덮였을 때 → exit 0 + complete 판정"
make_whisper 7195000
SPEECHTOTEXT_BACKEND=local SPEECHTOTEXT_WHISPER_BIN="$work/whisper-cli" \
  python3 "$cli" ingest --file "$audio" --label '장시간' > "$work/long.json" \
  || fail "complete long recording exited nonzero"
python3 - "$work" <<'PY'
import json, pathlib, sys
summary = json.loads((pathlib.Path(sys.argv[1]) / "long.json").read_text(encoding="utf-8"))
assert summary["coverage"]["complete"] is True, summary
assert summary["model"].startswith("local:"), summary
PY

echo "[8] 감시 폴더 미설정 → 워처는 아무것도 하지 않는다"
python3 - "$skill_dir" <<'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/scripts")
import stt_drive
try:
    stt_drive.folder_parts({})
except stt_drive.DriveScanRefused as refusal:
    sys.exit(0 if refusal.exit_code == 4 else 1)
sys.exit(1)
PY

printf 'SCENARIO-PASS\n'

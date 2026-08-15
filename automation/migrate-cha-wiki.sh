#!/usr/bin/env bash
# automation/migrate-cha-wiki.sh — OPTIONAL copy-migration of the legacy
# cha_wiki into the W2-2 personal wiki (~agent/wiki/imported-cha-wiki/).
#
# ** cha 본인이 직접 실행하는 스크립트다. 에이전트가 자율 실행하지 않는다. **
# 원본(W0-2 보존 대상)은 절대 수정/삭제하지 않는다 (read-only tar copy).
#
# Sources (W0-2 preserve targets):
#   primary node: <operator-home>/cha_wiki
#   RAG node:     <service-root>/cha_wiki
#       tar -xzf /tmp/cha_wiki-node2.tgz -C /tmp && migrate-cha-wiki.sh --source-dir /tmp/cha_wiki
#
# Usage (primary node에서, configured agent 계정으로 sudo 가능한 계정으로):
#   migrate-cha-wiki.sh                 # DRY-RUN: 무엇이 복사될지 출력만
#   migrate-cha-wiki.sh --execute       # 실제 복사 수행
#   migrate-cha-wiki.sh --source-dir <dir> [--execute] [--force]
#
# 동작: 원본 전체를 ~agent/wiki/imported-cha-wiki/ 로 복사(agent 소유,
# dir 700 / file 600). frontmatter가 없는 .md에는 W2-2 스키마 frontmatter
# (title=파일명, tags=[imported], created/updated=mtime, links=[])를 앞에 붙인다.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
eval "$(python3 "$REPO_ROOT/automation/node_config_sh.py" --print-env)"
SRC="/home/$NODE_OPERATOR_ACCOUNT/cha_wiki"
EXECUTE=0 FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-dir) SRC="$2"; shift 2 ;;
    --execute) EXECUTE=1; shift ;;
    --force) FORCE=1; shift ;;
    *) echo "unknown flag: $1" >&2; exit 4 ;;
  esac
done

[[ -d "$SRC" ]] || { echo "source dir not found/readable: $SRC" >&2; exit 4; }
DEST_REL="wiki/imported-cha-wiki"

echo "== migrate-cha-wiki (copy only; source untouched) =="
echo "source : $SRC"
echo "target : ~agent/$DEST_REL"
file_count="$(find "$SRC" -type f | wc -l)"
md_count="$(find "$SRC" -type f -name '*.md' | wc -l)"
echo "files  : $file_count total, $md_count markdown"

if [[ "$EXECUTE" != 1 ]]; then
  echo "-- DRY-RUN (첫 40개 파일 미리보기; 실제 복사는 --execute) --"
  find "$SRC" -type f | head -40
  exit 0
fi

if sudo -n -u agent -H bash -c "test -e \"\$HOME/$DEST_REL\"" && [[ "$FORCE" != 1 ]]; then
  echo "target ~agent/$DEST_REL already exists — use --force to overwrite" >&2
  exit 4
fi

tar -C "$SRC" -cf - . | sudo -n -u agent -H bash -c "
  set -euo pipefail
  umask 077
  rm -rf \"\$HOME/$DEST_REL\"
  mkdir -p \"\$HOME/$DEST_REL\"
  tar -xf - -C \"\$HOME/$DEST_REL\"
  chmod 700 \"\$HOME/wiki\"
  find \"\$HOME/$DEST_REL\" -type d -exec chmod 700 {} +
  find \"\$HOME/$DEST_REL\" -type f -exec chmod 600 {} +
"

# frontmatter 정규화: 스키마 frontmatter가 없는 .md에만 생성해 prepend.
sudo -n -u agent -H python3 - "$DEST_REL" <<'PY'
import re, sys
from datetime import datetime, timezone
from pathlib import Path

dest = Path.home() / sys.argv[1]
skill = Path.home() / ".hermes/skills/wiki/scripts"
sys.path.insert(0, str(skill))
try:
    import wiki_store  # mounted W2-2 skill: authoritative schema
except ImportError:
    print("wiki skill not mounted; skipping frontmatter normalization", file=sys.stderr)
    raise SystemExit(0)

normalized = skipped = 0
for path in sorted(dest.rglob("*.md")):
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        wiki_store.parse_note(text)
        skipped += 1
        continue
    except wiki_store.SchemaError:
        pass
    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    title = path.stem.strip() or "imported-note"
    meta = {"title": title, "tags": ["imported"], "created": stamp, "updated": stamp, "links": []}
    body = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S)  # strip alien frontmatter
    path.write_text(wiki_store.compose_note(meta, body), encoding="utf-8")
    path.chmod(0o600)
    normalized += 1
print(f"normalized={normalized} already-valid={skipped}")
PY

echo "MIGRATION-DONE copied into ~agent/$DEST_REL (source untouched)"

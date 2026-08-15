#!/usr/bin/env bash
# hello-autophagy demo skill: deterministic, zero-side-effect greeting.
set -euo pipefail

printf 'HELLO-AUTOPHAGY greeting account=%s host=%s: 자가포식 연구실 안녕!\n' \
  "$(whoami)" "$(hostname -s)"

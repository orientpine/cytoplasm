#!/usr/bin/env bash
set -euo pipefail

case "${E2E_TEST_MODE:-0}" in
  1|true|TRUE|yes|YES|on|ON)
    printf '%s\n' 'Refusing production Hermes gateway in E2E_TEST_MODE' >&2
    exit 78
    ;;
esac

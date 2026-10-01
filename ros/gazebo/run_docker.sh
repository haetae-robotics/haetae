#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/../.." && pwd)
if [[ $# -gt 0 ]]; then export HAETAE_DEMO_OUT="$1"; fi
exec "$repo/haetae-demo" start

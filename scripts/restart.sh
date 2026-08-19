#!/usr/bin/env bash
# 重启 weave-mem。
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash "$DIR/scripts/stop.sh"
bash "$DIR/scripts/start.sh"

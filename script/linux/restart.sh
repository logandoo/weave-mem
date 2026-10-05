#!/usr/bin/env bash
# 重启 weave-mem。
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
bash "$DIR/script/linux/stop.sh"
bash "$DIR/script/linux/start.sh"

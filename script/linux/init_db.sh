#!/usr/bin/env bash
# 初始化 weave-mem 数据库（幂等，可重复执行）。
# config 驱动方言：[database] type = "postgres"（默认，PG+pgvector 完整模式）
#                | "sqlite"（降级模式，免 PG——服务首次启动自动建库建表）
# PG 模式：1. 幂等创建 weave_mem 数据库 2. 幂等启用 vector 扩展
#          3. 有 venv 时经 init_db 预建表（无 venv 跳过，服务首次启动自动建）
#
# 用法：bash script/linux/init_db.sh
# PG 参数可用环境变量覆盖：PGUSER / PGHOST / PGPORT / PGPASSWORD
# 前置：本机已安装 PostgreSQL 14+ 与 pgvector 扩展包
#      （分平台安装方式见 README "安装并启用 pgvector" 一节：macOS brew / Ubuntu 源码编译 / Windows 官方 build）
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FAMILY_DIR="$(dirname "$DIR")"

# config 驱动方言（家族 wave4.1 模式）：type=sqlite 时免 PG。
# 探测用 venv python（3.11+ 有 tomllib；系统 python3 可能 3.9 无 tomllib）。
DB_TYPE="postgres"
for _py in "$DIR/.venv/bin/python" python3; do
  if command -v "$_py" >/dev/null 2>&1 || [ -x "$_py" ]; then
    _t="$("$_py" -c "
import tomllib
cfg = tomllib.load(open('$DIR/backend/config.toml', 'rb'))
print(cfg.get('database', {}).get('type', 'postgres'))
" 2>/dev/null)"
    if [ -n "$_t" ]; then DB_TYPE="$_t"; break; fi
  fi
done
if [ "$DB_TYPE" = "sqlite" ]; then
  echo "[init_db] SQLite 模式：无需 PostgreSQL（服务首次启动自动建库建表）"
  exit 0
fi
if [ "$DB_TYPE" != "postgres" ]; then
  echo "[init_db] 警告：无法读取 config.toml 的 database.type，按 postgres 处理"
fi

PGUSER="${PGUSER:-postgres}"
PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-5432}"
export PGPASSWORD="${PGPASSWORD:-}"

if ! command -v psql >/dev/null 2>&1; then
  echo "未找到 psql。weave-mem 依赖 PostgreSQL 14+ + pgvector，请先安装：macOS: brew install postgresql pgvector；" >&2
  echo "Ubuntu: sudo apt install postgresql （pgvector 官方源无稳定包，需源码编译，见 README 分平台指引）。" >&2
  exit 1
fi

if [[ -n "${PYTHON:-}" ]]; then
  VENV_PYTHON="$PYTHON"
else
  VENV_PYTHON=""
  for _v in "$DIR/.venv" "$FAMILY_DIR/.venv"; do
    if [[ -x "$_v/bin/python" ]]; then VENV_PYTHON="$_v/bin/python"; break; fi
    if [[ -f "$_v/Scripts/python.exe" ]]; then VENV_PYTHON="$_v/Scripts/python.exe"; break; fi
  done
fi

echo "weave-mem 数据库类型: postgres（pgvector）"
if psql -U "$PGUSER" -h "$PGHOST" -p "$PGPORT" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='weave_mem'" | grep -q 1; then
  echo "  PG 数据库 weave_mem 已存在，跳过创建"
else
  createdb -U "$PGUSER" -h "$PGHOST" -p "$PGPORT" weave_mem
  echo "  PG 数据库 weave_mem 创建完成"
fi

if psql -U "$PGUSER" -h "$PGHOST" -p "$PGPORT" -d weave_mem \
    -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null 2>&1; then
  echo "  pgvector 扩展已启用"
else
  echo "  pgvector 启用失败。请确认已安装 pgvector 扩展包（macOS: brew install pgvector；Ubuntu: 源码编译，见 README）" >&2
  exit 1
fi

if [[ -z "$VENV_PYTHON" ]]; then
  echo "  未找到 Python venv，跳过预建表（服务首次启动时自动创建；可先运行 script/linux/install_venv.sh）"
else
  if (cd "$DIR/backend" && "$VENV_PYTHON" -c \
      "import asyncio; from app.db.database import init_db; asyncio.run(init_db())"); then
    echo "  表结构已预建（init_db，含 pgvector 探测与迁移）"
  else
    echo "  init_db 预建失败，请检查日志" >&2
    exit 1
  fi
fi
echo "weave-mem 数据库初始化完成"

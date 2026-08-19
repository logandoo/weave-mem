# weave-mem

> [English](README.md) · 中文版

**记忆系统后端服务**：概念记忆、情节记忆、梦境记忆、双通道召回与后台调度器。

## 项目特点

- **概念记忆（semantic）**：概念 CRUD、遗忘（forget，软删除）、重要性/权重/可信度字段，
  同名 upsert 自动合并
- **双通道召回**：pgvector 余弦向量召回 + PostgreSQL ILIKE 文本召回 + BM25 混合检索，自动选择通道
- **情节记忆（episode）与梦境记忆（dream）**：服务层巩固/梦境整合闭环（consolidation/dreaming）；
  HTTP 侧暴露梦境列表与概念接口，情节记忆由内部调度器消费
- **深度记忆栈**：21 个 `memory_*` 服务——提取、巩固、梦境、召回、聚类、画像、潜意识、澄清、
  多模态、成本治理、调度器等
- **pgvector 自适应**：启动探测扩展可用性，缺失时自动降级文本召回，服务不崩
- **GDPR 全量擦除**：一键清空用户全部记忆（含文件层）
- **可选 embedding provider**：OpenAI 兼容接口自动向量化；未配置时 `embedding` 为 NULL 走文本召回
- **后台调度器**：记忆巩固/梦境等周期性任务内嵌服务进程
- **MCP Server**：主服务 `/mcp`（streamable-http）+ stdio 入口（`python -m app.mcp`），
  16 个工具全部为 HTTP API 薄转发
- **SQLite 降级模式**：`[database] type = "sqlite"` 零外部依赖运行（BM25/文本召回，无向量）

## 目录结构

```
weave-mem/
├── backend/
│   ├── app/
│   │   ├── main.py         # FastAPI 入口：init_db + pgvector 探测 + 调度器
│   │   ├── api/            # auth（注册/登录/登出/me/PAT）+ memory（16+ 端点）
│   │   ├── core/           # config.py 配置加载（config.toml + config_model.toml 合并）
│   │   ├── db/             # database.py 模型（17 表）+ migrations（HNSW 等）
│   │   ├── schemas/        # pydantic 模型
│   │   ├── services/       # 记忆栈：提取/巩固/梦境/召回/调度器（21 个 memory_* 服务）
│   │   ├── tools/          # memory 路径 shim（文件层记忆目录解析）
│   │   ├── mcp/            # MCP stdio 入口（python -m app.mcp）
│   │   └── mcp_server.py   # MCP server（HTTP 薄转发）
│   ├── config.toml         # infra + [memory] 全量配置（148 键）
│   └── requirements.txt
├── scripts/
│   ├── install_venv.sh     # 本项目 .venv + 依赖（幂等）
│   ├── init_db.sh          # createdb + pgvector + 预建表（幂等）
│   ├── start.sh
│   ├── stop.sh
│   ├── restart.sh
│   └── export_openapi.sh   # 固化 OpenAPI 规范到 docs/openapi.json
├── docs/
│   └── openapi.json        # 固化 OpenAPI 规范（30+ 端点）
└── tests/                  # 9 个验收套件（见"测试"）
```

## 部署前提

| 依赖 | 版本要求 | 说明 |
|---|---|---|
| 操作系统 | macOS / Ubuntu 22.04+ / Windows（WSL2 推荐，原生需 Git Bash） | 本服务无 UI，纯后端 |
| Python | 3.11+（建议 3.13） | `scripts/install_venv.sh` 自动探测 |
| PostgreSQL | 14+ | 完整模式（默认，需 pgvector 扩展） |
| pgvector | 0.5+ | 分平台安装见"安装并启用 pgvector" |
| SQLite（aiosqlite） | - | 降级模式，零外部依赖：`[database] type = "sqlite"` |
| embedding provider | 可选（OpenAI 兼容） | 未配置时召回走文本通道，ingest 返回 503 |

三个脚本自包含且幂等，标准部署只需三步：

```bash
bash scripts/install_venv.sh   # 幂等：.venv + 依赖
bash scripts/init_db.sh        # 幂等：createdb weave_mem + pgvector + 表
bash scripts/start.sh          # 启动（自动探测 .venv）
curl http://127.0.0.1:8202/healthz
```

## 部署步骤

### 1. 安装并启用 pgvector

**macOS（Homebrew，最简单）**

```bash
brew install postgresql@17 pgvector
brew services start postgresql@17          # 若 PG 未运行
export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"
createdb weave_mem
psql -d weave_mem -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

**Ubuntu / Debian** — 官方源通常无 pgvector 包，需源码编译（约 1 分钟）：

```bash
sudo apt update && sudo apt install -y postgresql postgresql-server-dev-all build-essential git
sudo systemctl start postgresql && sudo systemctl enable postgresql
git clone --branch v0.8.0 https://github.com/pgvector/pgvector /tmp/pgvector
cd /tmp/pgvector && make && sudo make install   # 通过 pg_config 自动匹配 PG 版本
# 可选：加 PGDG 官方源后 `sudo apt install postgresql-XX-pgvector`，免编译
```

> Ubuntu 默认本地 TCP 为 scram 认证：脚本经 `127.0.0.1` 连接前需
> `sudo -u postgres psql -c "ALTER USER postgres PASSWORD '<强密码>'"`，
> 执行脚本时 `export PGPASSWORD='<强密码>'`（init_db.sh 透传），
> **并同步写入 `backend/config.toml` 的 `[database] password`**
> （服务运行只读 config，不走 PGPASSWORD 环境变量）。

**Windows** — 推荐 WSL2（发行版内走上面 Ubuntu 流程）。
原生 Windows：从 postgresql.org 下载官方安装器，再从 GitHub Releases 下载 pgvector
Windows 预编译包，将 `.dll` 放入 PG 安装目录 `bin\`，`.control`/`vector--*.sql` 放入 `share\extension\`。

### 2. 虚拟环境 + 依赖

```bash
bash scripts/install_venv.sh
```

等价手工步骤：

```bash
python3.11 -m venv .venv     # 或 python3.13
./.venv/bin/pip install -r backend/requirements.txt
```

### 3. 修改配置（backend/config.toml）

```toml
[server]
host = "127.0.0.1"
port = 8202

[security]
jwt_secret_key = "改成足够长的随机字符串"

[database]
host = "127.0.0.1"
port = 5432
username = "postgres"
password = ""    # scram 认证环境（Ubuntu 默认）必填：与 PG 密码一致
name = "weave_mem"

[memory]
enabled = true
embedding_dim = 1024     # 需与 pgvector 列维度一致

# 可选：OpenAI 兼容 embedding 服务；不配置时召回走文本匹配
# embedding_api_base = "https://api.openai.com/v1"
# embedding_api_key = "sk-..."
# embedding_model = "text-embedding-3-small"
```

支持的环境变量：

| 环境变量 | 说明 |
|---|---|
| `PYTHON` | 指定启动解释器（start.sh 优先使用） |
| `HOST` / `PORT` | 覆盖监听地址/端口（start.sh） |
| `LOG_FILE` / `PID_FILE` | 日志/PID 文件路径（start.sh） |
| `JWT_SECRET_KEY` | JWT 密钥（config.toml 未配置时生效） |
| `CONFIG_MODEL_PATH` | config_model.toml 路径（默认取 config.toml 同目录） |
| `AGENT_MEMORY_DIR` | 文件层记忆目录（默认 backend/agent_memories；缺失自动降级） |
| `PGPASSWORD` | 仅供 scripts/init_db.sh 建库时使用（服务运行不读） |

> 模型相关配置（`[api]`、`[defaults]`、`[providers]`、`[memory]` 等）可拆分到
> `config.toml` 同目录的 `config_model.toml`（或 `CONFIG_MODEL_PATH` 指定），
> 以"整段覆盖"方式合并进主配置；文件不存在时 `config.toml` 保持权威。

### 4. 初始化 + 启动 + 验证

```bash
bash scripts/init_db.sh       # createdb weave_mem（幂等）+ pgvector + 预建表
bash scripts/start.sh         # 启动（日志 weave-mem.log，PID weave-mem.pid）
curl http://127.0.0.1:8202/healthz
# 期望：{"status":"ok","service":"weave-mem","database":"ok","pgvector":true}
```

启动时自动创建默认测试账号 `test / 123456`。

```bash
TOKEN=$(curl -sS -X POST http://127.0.0.1:8202/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"test","password":"123456"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl http://127.0.0.1:8202/api/memory/status -H "Authorization: Bearer $TOKEN"
```

### 5. 停止

```bash
bash scripts/stop.sh          # 按 PID 文件安全停止（不会误杀其他进程）
```

## SQLite 降级模式（零外部依赖）

`[database] type = "sqlite"` 时无需 PostgreSQL/pgvector：

```toml
[database]
type = "sqlite"
path = "weave_mem.db"        # 相对 backend/ 解析
```

| 能力 | SQLite 模式 | PG 模式 |
|---|---|---|
| 概念/情节/梦境/澄清/GDPR/迁移 | ✓ 全功能 | ✓ 全功能 |
| 召回 | 文本/BM25 通道（`mode:"text"`） | 向量+BM25+ILIKE 混合 |
| 潜意识摄入（ingest） | 需 embedding provider（无则 503） | 同左 |
| 潜意识自动提炼/向量去重/梦境向量 | 跳过（无向量） | ✓ |
| 部署 | 零外部依赖（aiosqlite） | PG+pgvector |

`scripts/init_db.sh` 自动识别 type（SQLite 免 PG）；切换回 PG 只需改 type 后重跑 init_db.sh。
健康检查在 SQLite 下返回 `"pgvector": false`（预期降级标记）。

## MCP Server（HTTP API 薄转发）

挂载于主服务 `/mcp`（streamable-http），另提供 stdio 入口 `python -m app.mcp`
（宿主进程，供 Claude Desktop 等配置）。**每个工具都是当前 HTTP API 的薄转发**
（httpx 调 `[mcp].base_url`，默认 http://127.0.0.1:8202），鉴权/校验/序列化复用 HTTP 端点。

```toml
[mcp]
enabled = true
base_url = "http://127.0.0.1:8202"
# 认证二选一：token（PAT，推荐）或 username/password（自动登录）
token = ""
username = "test"
password = "123456"
timeout = 60.0        # 工具调用 httpx 超时（秒）
```

16 个工具：memory_status / concept_list / concept_get / concept_create /
concept_delete / concept_forget / recall / ingest / episodes_list /
dreams_list / clarifications_list / clarification_process /
clarification_apply / clarification_revert / cost_governance_status / gdpr_erase。

接入示例（Claude Code）：

```json
{ "mcpServers": { "weave-mem": { "command": "/path/to/weave-mem/.venv/bin/python",
  "args": ["-m", "app.mcp"], "cwd": "/path/to/weave-mem/backend" } } }
```

## 个人访问令牌（PAT）

长期有效的 Bearer 令牌（`wm_` 前缀，库中仅存 sha256 哈希，可随时撤销）：

```bash
TOKEN=$(curl -sS -X POST http://127.0.0.1:8202/api/auth/tokens \
  -H "Authorization: Bearer $JWT" -H 'Content-Type: application/json' -d '{"name":"ci"}')
# 响应中的 token 明文仅此一次可见
curl -sS http://127.0.0.1:8202/api/memory/status -H "Authorization: Bearer $PAT"
curl -sS -X DELETE http://127.0.0.1:8202/api/auth/tokens/<id> -H "Authorization: Bearer $JWT"  # 撤销
```

端点：`POST /api/auth/tokens`（创建）、`GET /api/auth/tokens`（列表）、`DELETE /api/auth/tokens/{id}`（撤销）。

## OpenAPI 规范

- 运行时：`http://127.0.0.1:8202/docs`（Swagger UI）/ `/openapi.json`
- 固化版本：`bash scripts/export_openapi.sh` → `docs/openapi.json`（30+ 端点全量）

## 端到端工作流验收

```bash
# 1. 写入概念（未配置 embedding provider 时为文本召回模式）
curl -sS -X POST http://127.0.0.1:8202/api/memory/concepts \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"canonical_name":"工作流验证","description_short":"由部署验收写入","importance":0.9}'

# 2. 文本召回（未配置 provider 时返回 "mode":"text"）
curl -sS -X POST http://127.0.0.1:8202/api/memory/recall \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"工作流"}'

# 3. 潜意识摄入（需配置 embedding provider；否则 503）
curl -sS -X POST http://127.0.0.1:8202/api/memory/ingest \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"content":"用户偏好深色主题","unit_kind":"message"}'

# 4. 澄清处理（需配置 LLM；否则 detected=true 但 clarification=null 降级）
curl -sS -X POST http://127.0.0.1:8202/api/memory/clarifications/process \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"user_message":"其实不是这样，正确的是深色主题"}'

# 5. 遗忘 + GDPR 全量擦除
curl -sS -X POST http://127.0.0.1:8202/api/memory/concepts/<概念ID>/forget -H "Authorization: Bearer $TOKEN"
curl -sS -X DELETE http://127.0.0.1:8202/api/memory/all -H "Authorization: Bearer $TOKEN"
```

## 核心 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/login` | 登录 |
| POST | `/api/auth/logout` | 登出 |
| GET | `/api/auth/me` | 当前用户 |
| POST | `/api/auth/tokens` | 创建 PAT（明文仅返回一次） |
| GET | `/api/auth/tokens` | PAT 列表 |
| DELETE | `/api/auth/tokens/{id}` | 撤销 PAT |
| GET | `/api/memory/status` | pgvector/维度/数量统计 |
| GET | `/api/memory/concepts` | 概念列表（支持 `q`、`limit`） |
| POST | `/api/memory/concepts` | 新建/更新概念（同名 upsert） |
| GET | `/api/memory/concepts/{id}` | 概念详情 |
| DELETE | `/api/memory/concepts/{id}` | 删除概念 |
| POST | `/api/memory/concepts/{id}/forget` | 遗忘概念（soft delete） |
| POST | `/api/memory/recall` | 召回（自动选择向量或文本通道；`?include_meta=true` 返回 memory_ids/top_gate_score） |
| GET | `/api/memory/episodes` | 情节记忆列表 |
| POST | `/api/memory/ingest` | 潜意识摄入（需 embedding provider；未配置 503，故障 502） |
| GET | `/api/memory/dreams` | 梦境记忆列表 |
| GET | `/api/memory/clarifications` | 澄清问题列表 |
| POST | `/api/memory/clarifications/process` | 澄清处理（信号词 → LLM 判定 → 高置信度自动应用） |
| POST | `/api/memory/clarifications/{id}/apply` | 手动应用 pending 澄清 |
| POST | `/api/memory/clarifications/{id}/revert` | 回退已应用澄清 |
| GET | `/api/admin/users` | 用户列表（管理员） |
| PUT | `/api/admin/users/{user_id}/role` | 角色变更 user/admin（管理员） |
| POST | `/api/admin/reload-config` | 配置热重载（管理员，SIGHUP 同语义） |
| PUT | `/api/memory/{user_id}/cost_governance/reset` | 重置成本治理（管理员） |
| GET | `/api/memory/cost_governance/status` | 成本治理状态 |
| DELETE | `/api/memory/all` | GDPR 全量擦除 |
| POST | `/api/admin/memory/migration/run` | 迁移（管理员） |
| POST | `/api/admin/memory/migration/rollback` | 回滚迁移（管理员） |
| GET | `/api/admin/memory/migration/status` | 迁移状态（管理员） |
| GET | `/healthz` | 健康检查（含 pgvector 探测） |

概念写入示例：

```bash
curl -sS -X POST http://127.0.0.1:8202/api/memory/concepts \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "canonical_name": "weave-family",
    "description_short": "三个独立服务的家族",
    "description_full": "weave-note / weave-mem / weave-talk",
    "memory_type": "project",
    "importance": 0.9,
    "embedding": null
  }'
```

未传 `embedding` 且配置了 `embedding_api_base` 时，服务会调用
`POST {embedding_api_base}/embeddings` 自动向量化；未配置时
`embedding` 为 NULL，recall 使用文本匹配。

## 测试

9 个验收套件（服务需先启动）：

```bash
for t in test_api test_recall test_ingest_clarify test_clarify_apply test_blindspot \
         test_full_chain test_pat test_mcp test_sqlite_mode; do
  ./.venv/bin/python tests/$t.py > tests/$t.log 2>&1 && echo "$t PASS" || echo "$t FAIL"
done
```

| 套件 | 覆盖 |
|---|---|
| test_api.py | 22 项：注册/登录/概念 CRUD/梦境/澄清列表回退/成本治理/GDPR/迁移/admin 鉴权/healthz |
| test_recall.py | 13 项：8 概念写入 → BM25 文本召回命中 + 服务层检索可运行 |
| test_ingest_clarify.py | 10 项：ingest 401/422/503/502 + clarifications/process 全分支 |
| test_clarify_apply.py | 6 项：mock LLM 的 auto_apply 行为链（落库 + 概念更新 + 审计键） |
| test_blindspot.py | 12 项：概念详情/episodes/recall-meta/admin users+role/clarify apply/reload-config |
| test_full_chain.py | 13 项：整条记忆链（写入→详情→召回→摄入→自动提炼→召回命中） |
| test_pat.py | 8 项：个人访问令牌（创建/列表/认证/撤销/哈希存储） |
| test_mcp.py | 10 项：MCP server（HTTP 薄转发 + 进程内验证） |
| test_sqlite_mode.py | 13 项：SQLite 降级模式（healthz/概念链/立即召回/ingest 503/admin/GDPR） |

## 常见问题

### /healthz 返回 `"pgvector": false`

```bash
psql -U postgres -h 127.0.0.1 -d weave_mem -c 'CREATE EXTENSION IF NOT EXISTS vector;'
bash scripts/stop.sh; bash scripts/start.sh
```

### 已创建表后修改 embedding_dim

`embedding_dim` 改变后需要重建 `memory_concepts.embedding` 列：

```sql
ALTER TABLE memory_concepts DROP COLUMN embedding;
ALTER TABLE memory_concepts ADD COLUMN embedding vector(新维度);
```

或者直接删除重建数据库。

### 如何创建 admin 用户（迁移/用户管理端点需要）

```bash
# 先注册/登录普通用户，然后 psql 提升（首次提升只能 psql，之后可走 API）：
psql -U postgres -h 127.0.0.1 -d weave_mem -c "UPDATE users SET role='admin' WHERE username='你的用户名';"
# 之后用 admin 账号调 GET /api/admin/users + PUT /api/admin/users/{id}/role 管理其他用户
```

### 为什么 clarifications/process 返回 `detected=true` 但 `clarification=null`

LLM 判定调用失败时降级（服务日志出现 `Clarification LLM call failed`）：
未配置 [llm] 域或 LLM 不可达。配置 LLM 后即恢复真实判定与自动应用。

### 外部 embedding provider 不可用

服务不会崩溃：写入时 embedding 留空，recall 自动返回
`"mode": "text"`；provider 恢复后重新 upsert 概念即可补齐向量。

## License

MIT

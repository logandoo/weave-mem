# weave-mem

> English · [中文版](README.zh.md)

A **memory system backend service**: semantic concepts, episodic memories, dream memories, dual-channel recall, and a background scheduler.

## Highlights

- **Semantic concepts**: CRUD, forget (soft delete), importance/weight/confidence fields, same-name upsert merging
- **Dual-channel recall**: pgvector cosine vector recall + PostgreSQL ILIKE text recall + BM25 hybrid retrieval, with automatic channel selection
- **Adoption feedback loop**: an answer citing injected memory names writes back `+0.02` weight and relation edges (HTTP `POST /api/memory/adoption`, per-user hourly budget, fail-open)
- **Recall ledger**: per-turn metadata-only log (`GET /api/memory/recall_log`) with composite-keyset pagination, sampling and retention cleanup
- **Episodic & dream memory**: consolidation + dreaming closed loop in the service layer; the HTTP layer exposes dream/concept endpoints while episodes are consumed by the internal scheduler
- **Deep memory stack**: 23 `memory_*` services — extraction, consolidation, dreaming, retrieval, clustering, profiling, subconscious, clarification, multimodal, cost governance, scheduler, etc.
- **pgvector adaptive**: startup probe; when the extension is missing the system degrades to text recall — the service never crashes
- **GDPR full erase**: one-click wipe of all user memory (DB + file layer)
- **Optional embedding provider**: OpenAI-compatible API auto-vectorizes; unconfigured → `embedding` stays NULL and recall falls back to text
- **Background scheduler**: consolidation/dreaming periodic jobs embedded in the service process
- **MCP server**: streamable-http mount at `/mcp` plus a stdio entry (`python -m app.mcp`), 16 tools all thin-forwarding the HTTP API
- **SQLite fallback mode**: `[database] type = "sqlite"` runs with zero external dependencies (BM25/text recall, no vectors)

## Directory Structure

```
weave-mem/
├── backend/
│   ├── app/
│   │   ├── main.py         # FastAPI entry: init_db + pgvector probe + scheduler
│   │   ├── api/            # auth (7 routes) + memory/admin (19 routes)
│   │   ├── core/           # config.py loading (config.toml; optional config_model.toml override)
│   │   ├── db/             # database.py models (19 tables) + migrations (HNSW etc.)
│   │   ├── schemas/        # pydantic models
│   │   ├── services/       # memory stack: extraction/consolidation/dreaming/retrieval/adoption/ledger (23 memory_* services)
│   │   ├── tools/          # memory path shim (file-layer memory dir resolution)
│   │   ├── mcp/            # stdio entry for MCP (`python -m app.mcp`)
│   │   └── mcp_server.py   # MCP server (HTTP thin forwarder)
│   ├── config.toml         # infra + [memory] full config (179 keys)
│   └── requirements.txt
├── script/linux/           # all entry points: start / stop / restart / project_build / install_venv / init_db / export_openapi
├── docs/
│   └── openapi.json        # frozen OpenAPI spec (32 paths)
├── memory/                 # project memory (MEMORY.md index + topic notes)
└── tests/                  # 13 acceptance suites (see "Testing")
```

## Prerequisites

| Dependency | Version | Notes |
|---|---|---|
| OS | - | macOS / Ubuntu 22.04+ / Windows (WSL2 recommended; Git Bash on native Windows) |
| Python | 3.11+ (3.13 recommended) | auto-detected by `script/linux/install_venv.sh` |
| PostgreSQL | 14+ | pgvector extension required (full mode, default) |
| pgvector | 0.5+ | per-platform install: see "Installing and enabling pgvector" below |
| SQLite (aiosqlite) | - | fallback mode, zero external deps: `[database] type = "sqlite"` |
| embedding provider | optional | OpenAI-compatible; unconfigured → recall uses the text channel, ingest returns 503 |

The three scripts are self-contained and idempotent — the standard deployment is three steps:

```bash
bash script/linux/install_venv.sh   # idempotent: .venv + dependencies
bash script/linux/init_db.sh        # idempotent: createdb weave_mem + pgvector + tables
bash script/linux/start.sh          # start (auto-detect .venv)
curl http://127.0.0.1:8202/healthz
```

## Deployment Steps

### 1. Installing and enabling pgvector

**macOS (Homebrew, easiest)**

```bash
brew install postgresql@17 pgvector
brew services start postgresql@17          # if PG is not running
export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"
createdb weave_mem
psql -d weave_mem -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

**Ubuntu / Debian** — the official repos usually have no pgvector package; compile from source (~1 min):

```bash
sudo apt update && sudo apt install -y postgresql postgresql-server-dev-all build-essential git
sudo systemctl start postgresql && sudo systemctl enable postgresql
git clone --branch v0.8.0 https://github.com/pgvector/pgvector /tmp/pgvector
cd /tmp/pgvector && make && sudo make install   # matches installed PG version via pg_config
# Alternative: add the PGDG apt source and `sudo apt install postgresql-XX-pgvector` to skip compiling
```

> Ubuntu's default local TCP auth is scram: before the scripts connect via `127.0.0.1`,
> run `sudo -u postgres psql -c "ALTER USER postgres PASSWORD '<strong password>'"`,
> then `export PGPASSWORD='<strong password>'` when running scripts (init_db.sh passes it through),
> **and write the same password into `backend/config.toml` `[database] password`**
> (the service does not read PGPASSWORD — it only reads config).

**Windows** — WSL2 recommended (follow the Ubuntu steps inside the distro).
Native Windows: install PostgreSQL from the official installer, then download the pgvector
Windows prebuilt from GitHub Releases, placing the `.dll` into the PG install dir `bin\`
and the `.control`/`vector--*.sql` files into `share\extension\`.

### 2. Virtual Environment

```bash
bash script/linux/install_venv.sh
```

Equivalent manual steps:

```bash
python3.11 -m venv .venv     # or python3.13
./.venv/bin/pip install -r backend/requirements.txt
./.venv/bin/pip install -e ./client   # weave-mem-client SDK（MCP 依赖，必装）
```

### 3. Configuration

Edit `backend/config.toml`:

```toml
[server]
host = "127.0.0.1"
port = 8202

[security]
jwt_secret_key = "change-me-to-a-long-random-string"

[database]
host = "127.0.0.1"
port = 5432
username = "postgres"
password = ""    # required in scram-auth environments (Ubuntu default): same password as PG
name = "weave_mem"

[memory]
enabled = true
embedding_dim = 1024     # must match the pgvector column dimension

# Optional: OpenAI-compatible embedding service; empty → recall uses text matching
# embedding_api_base = "https://api.openai.com/v1"
# embedding_api_key = "sk-..."
# embedding_model = "text-embedding-3-small"
#
# Retrieval gates added by the 2026-10-05 sync (all in [memory.retrieval], default off
# unless noted): strategy_route_enabled=true · concept_link_expansion_enabled=true ·
# consistency_enabled=true · expansion_rho_enabled / agpr_enabled /
# deterministic_edges_enabled / edge_read_whitelist_enabled / assembly_mmr_enabled /
# assembly_grouping_enabled / contradicts_read_downgrade_enabled /
# adaptive_cardinality_enabled / text_cross_turn_dedup_enabled /
# merge_fast_path_enabled / merge_mst_enabled / injection_usage_instruction_enabled /
# listwise_verifier_enabled (all false) · injection_monopoly_share=0.7 ·
# stage0_hard_ceiling_ms=0. Ledger keys in [memory]: recall_log_enabled=true,
# recall_log_retention_days=30, recall_log_max_per_user=20000, recall_log_sample_rate=1.0
```

Supported environment variables:

| Variable | Description |
|---|---|
| `PYTHON` | interpreter override (`start.sh` uses it first) |
| `HOST` / `PORT` | listen address/port override (`start.sh`) |
| `LOG_FILE` / `PID_FILE` | log/PID file paths (`start.sh`) |
| `JWT_SECRET_KEY` | JWT secret (used when not set in config.toml) |
| `CONFIG_MODEL_PATH` | config_model.toml path (defaults to config.toml's directory) |
| `AGENT_MEMORY_DIR` | file-layer memory dir (default backend/agent_memories; auto-degrades if missing) |
| `PGPASSWORD` | only used by `script/linux/init_db.sh` when creating the database (the service itself does not read it) |

> Model-related settings (`[api]`, `[defaults]`, `[providers]`, `[memory]`, ...) may be split into
> a separate `config_model.toml` next to `config.toml` (or `CONFIG_MODEL_PATH`); it is merged
> **over** the main file at section granularity. When absent, `config.toml` remains authoritative.

### 4. Initialize, Start, Verify

```bash
bash script/linux/init_db.sh       # createdb weave_mem (idempotent) + enable pgvector + prebuild tables
bash script/linux/start.sh         # start (log weave-mem.log, PID weave-mem.pid)
curl http://127.0.0.1:8202/healthz
# expect: {"status":"ok","service":"weave-mem","database":"ok","pgvector":true}
```

A default test account `test / 123456` is auto-created on startup.

```bash
TOKEN=$(curl -sS -X POST http://127.0.0.1:8202/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"test","password":"123456"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl http://127.0.0.1:8202/api/memory/status -H "Authorization: Bearer $TOKEN"
```

### 5. Stop

```bash
bash script/linux/stop.sh          # safe stop via PID file (won't kill unrelated processes)
```

## SQLite Fallback Mode (zero external dependencies)

With `[database] type = "sqlite"` no PostgreSQL/pgvector is needed:

```toml
[database]
type = "sqlite"
path = "weave_mem.db"        # resolved relative to backend/
```

| Capability | SQLite mode | PG mode |
|---|---|---|
| Concepts / episodes / dreams / clarifications / GDPR / migrations | ✓ full | ✓ full |
| Recall | BM25/text channel (`"mode":"text"`) | vector + BM25 + ILIKE hybrid |
| Subconscious ingest | needs embedding provider (503 otherwise) | same |
| Auto-extraction / vector dedup / dream vectors | skipped (no vectors) | ✓ |
| Deployment | zero external deps (aiosqlite) | PG + pgvector |

`script/linux/init_db.sh` auto-detects the type (SQLite skips PG). Switching back to PG is just a
`type` change plus re-running init_db.sh. `/healthz` returns `"pgvector": false` under SQLite
(expected degradation marker).

## MCP Server (HTTP API thin forwarder)

Mounted at `/mcp` (streamable-http) on the main service, plus a stdio entry
`python -m app.mcp` for Claude Desktop and similar hosts. **Each tool is a thin forwarder of
the current HTTP API** (httpx to `[mcp].base_url`, default http://127.0.0.1:8202); auth,
validation, and serialization are all reused from the HTTP endpoints.

```toml
[mcp]
enabled = true
base_url = "http://127.0.0.1:8202"
# auth: either token (PAT, recommended) or username/password (auto-login)
token = ""
username = "test"
password = "123456"
timeout = 60.0        # per-tool-call httpx timeout (seconds)
```

16 tools: memory_status / concept_list / concept_get / concept_create /
concept_delete / concept_forget / recall / ingest / episodes_list /
dreams_list / clarifications_list / clarification_process /
clarification_apply / clarification_revert / cost_governance_status / gdpr_erase.

Claude Code example:

```json
{ "mcpServers": { "weave-mem": { "command": "/path/to/weave-mem/.venv/bin/python",
  "args": ["-m", "app.mcp"], "cwd": "/path/to/weave-mem/backend" } } }
```

## Personal Access Tokens (PAT)

Long-lived bearer tokens (`wm_` prefix; only the sha256 hash is stored; revocable at any time):

```bash
TOKEN=$(curl -sS -X POST http://127.0.0.1:8202/api/auth/tokens \
  -H "Authorization: Bearer $JWT" -H 'Content-Type: application/json' -d '{"name":"ci"}')
# the plaintext token in the response is shown only once
curl -sS http://127.0.0.1:8202/api/memory/status -H "Authorization: Bearer $PAT"
curl -sS -X DELETE http://127.0.0.1:8202/api/auth/tokens/<id> -H "Authorization: Bearer $JWT"  # revoke
```

Endpoints: `POST /api/auth/tokens` (create), `GET /api/auth/tokens` (list), `DELETE /api/auth/tokens/{id}` (revoke).

## OpenAPI Spec

- At runtime: `http://127.0.0.1:8202/docs` (Swagger UI) / `/openapi.json`
- Frozen: `bash script/linux/export_openapi.sh` → `docs/openapi.json` (30+ endpoints)

## End-to-end Walkthrough

```bash
# 1. write a concept (text-recall mode when no embedding provider is configured)
curl -sS -X POST http://127.0.0.1:8202/api/memory/concepts \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"canonical_name":"workflow-check","description_short":"written by deploy acceptance","importance":0.9}'

# 2. text recall (returns "mode":"text" when no provider is configured)
curl -sS -X POST http://127.0.0.1:8202/api/memory/recall \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"workflow"}'

# 3. subconscious ingest (needs embedding provider; otherwise 503)
curl -sS -X POST http://127.0.0.1:8202/api/memory/ingest \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"content":"user prefers dark theme","unit_kind":"message"}'

# 4. clarification processing (needs LLM; otherwise detected=true but clarification=null degrade)
curl -sS -X POST http://127.0.0.1:8202/api/memory/clarifications/process \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"user_message":"Actually not that — it is the dark theme"}'

# 5. forget + GDPR full erase
curl -sS -X POST http://127.0.0.1:8202/api/memory/concepts/<concept-id>/forget -H "Authorization: Bearer $TOKEN"
curl -sS -X DELETE http://127.0.0.1:8202/api/memory/all -H "Authorization: Bearer $TOKEN"
```

## Core API

| Method | Path | Description |
|---|---|---|
| POST | `/api/auth/register` | register |
| POST | `/api/auth/login` | login |
| POST | `/api/auth/logout` | logout |
| GET | `/api/auth/me` | current user |
| POST | `/api/auth/tokens` | create PAT (plaintext shown once) |
| GET | `/api/auth/tokens` | list PATs |
| DELETE | `/api/auth/tokens/{id}` | revoke PAT |
| GET | `/api/memory/status` | pgvector/dimension/count stats |
| GET | `/api/memory/concepts` | concept list (supports `limit`; returns `{concepts, count}`) |
| POST | `/api/memory/concepts` | create/update concept (same-name upsert) |
| GET | `/api/memory/concepts/{id}` | concept detail |
| DELETE | `/api/memory/concepts/{id}` | delete concept |
| POST | `/api/memory/concepts/{id}/forget` | forget concept (soft delete) |
| POST | `/api/memory/recall` | recall (auto-picks vector or text channel; `?include_meta=true` returns memory_ids/top_gate_score) |
| GET | `/api/memory/episodes` | episodic memories list |
| POST | `/api/memory/ingest` | subconscious ingest (needs embedding provider; 503/502 degrade) |
| POST | `/api/memory/adoption` | adoption feedback loop (answer cites injected concept names → weight/edge +0.02; per-user budget, fail-open) |
| GET | `/api/memory/recall_log` | recall ledger (metadata only; `before_id` keyset pagination, `limit`≤200) |
| GET | `/api/memory/dreams` | dream memory list |
| GET | `/api/memory/clarifications` | clarification list |
| POST | `/api/memory/clarifications/process` | clarification processing (signal words → LLM → auto-apply) |
| POST | `/api/memory/clarifications/{id}/apply` | manually apply a pending clarification |
| POST | `/api/memory/clarifications/{id}/revert` | revert an applied clarification |
| GET | `/api/admin/users` | list users (admin) |
| PUT | `/api/admin/users/{user_id}/role` | change role user/admin (admin) |
| POST | `/api/admin/reload-config` | hot-reload config (admin; same semantics as SIGHUP) |
| PUT | `/api/memory/{user_id}/cost_governance/reset` | reset cost governance (admin) |
| GET | `/api/memory/cost_governance/status` | cost governance status |
| DELETE | `/api/memory/all` | GDPR full erase |
| POST | `/api/admin/memory/migration/run` | run migration (admin) |
| POST | `/api/admin/memory/migration/rollback` | rollback migration (admin) |
| GET | `/api/admin/memory/migration/status` | migration status (admin) |
| GET | `/healthz` | health check (incl. pgvector probe) |

Concept write example:

```bash
curl -sS -X POST http://127.0.0.1:8202/api/memory/concepts \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "canonical_name": "weave-family",
    "description_short": "a family of three standalone services",
    "description_full": "weave-note / weave-mem / weave-talk",
    "memory_type": "project",
    "importance": 0.9,
    "embedding": null
  }'
```

When `embedding` is omitted and `embedding_api_base` is configured, the service auto-vectorizes
via `POST {embedding_api_base}/embeddings`; when unconfigured, `embedding` stays NULL and recall
uses text matching.

## Client SDK (`weave-mem-client`)

The `client/` directory ships a typed async SDK covering all 32 API paths — the same
`MemoryClient` the MCP layer uses (single source). Install and call:

```bash
pip install -e ./client        # from the repo root; or pip install <repo>/client
```

```python
from weave_mem_client import MemoryClient

async with MemoryClient("http://127.0.0.1:8202", username="test", password="123456") as c:
    concept = await c.create_concept(canonical_name="weave-family", description_short="a family of services")
    rec = await c.recall(query="weave-family", include_meta=True)   # rec["meta"]["memory_ids"]
    ledger = await c.recall_log(limit=10)                            # metadata-only
```

Auth: pass a PAT as `token=` (recommended), or `username`/`password` (auto-login, cached),
or call `request(method, path)` directly. Errors raise `RuntimeError("HTTP <code>: …")`;
an unauthenticated call surfaces the server's 401. Outbound provider calls from the service
itself use the official `openai` SDK (LLM + embeddings); the TEI `/rerank` call stays raw
HTTP because no standard SDK exists for that contract.

## Testing

14 acceptance suites (the service must be running first; `test_sqlite_mode` requires
`[database] type="sqlite"` and is run separately):

```bash
for t in test_api test_recall test_ingest_clarify test_clarify_apply test_blindspot \
         test_full_chain test_pat test_mcp test_sync_p0 test_adoption \
         test_p2_gates test_recall_log test_sdk test_outbound_sdk; do
  ./.venv/bin/python tests/$t.py > tests/$t.log 2>&1 && echo "$t PASS" || echo "$t FAIL"
done
```

| Suite | Coverage |
|---|---|
| test_api.py | 22 items: register/login/concept CRUD/dreams/clarification fallback/cost governance/GDPR/migrations/admin auth/healthz |
| test_recall.py | 13 items: 8 concepts → BM25 text recall hits + service-layer retrieval runs |
| test_ingest_clarify.py | 10 items: ingest 401/422/503/502 + clarifications/process all branches |
| test_clarify_apply.py | 6 items: mock-LLM auto_apply behavior chain (DB write + concept update + audit key) |
| test_blindspot.py | 12 items: concept detail/episodes/recall-meta/admin users+role/clarify apply/reload-config |
| test_full_chain.py | 13 items: full memory chain (write → detail → recall → ingest → auto-extract → recall hit) |
| test_pat.py | 8 items: PAT (create/list/auth/revoke/hash storage) |
| test_mcp.py | 10 items: MCP server (HTTP thin forwarding + in-process verification) |
| test_sqlite_mode.py | 13 items: SQLite fallback mode (healthz/concept chain/immediate recall/ingest 503/admin/GDPR) |
| test_sync_p0.py | 19 items: no-key guards ×3 sites / resurrect decay-anchor / atomic weight + answer_cited / billing_class split / cluster embedding write path + backfill dry-run |
| test_adoption.py | 17 items: POST /api/memory/adoption contract + write-back chain + edge cap + cross-modal consistency pure-fn 8 states |
| test_p2_gates.py | 33 items: strategy routing / D1 edges+whitelist+P/L / D2 MMR+contradicts / D3 gray-zone+MST / E1+A4c / W8 link expansion |
| test_sdk.py | 20 items: weave-mem-client SDK end-to-end (32 paths incl. root) + auth/headers semantics |
| test_outbound_sdk.py | 11 items: outbound embedding via official SDK (stub server) incl. no-key sentinel + C1 empty-base fail-closed |
| test_recall_log.py | 16 items: ledger metadata-only + keyset pagination + dual-dialect cleanup + truncation note |

## FAQ

### `/healthz` returns `"pgvector": false`

```bash
psql -U postgres -h 127.0.0.1 -d weave_mem -c 'CREATE EXTENSION IF NOT EXISTS vector;'
bash script/linux/stop.sh; bash script/linux/start.sh
```

### Changing `embedding_dim` after tables exist

Rebuild the `memory_concepts.embedding` column:

```sql
ALTER TABLE memory_concepts DROP COLUMN embedding;
ALTER TABLE memory_concepts ADD COLUMN embedding vector(new-dimension);
```

Or drop and recreate the database.

### How to create an admin user (required for migration/user-management endpoints)

```bash
# register/login a normal user first, then promote via psql (first promotion must be psql):
psql -U postgres -h 127.0.0.1 -d weave_mem -c "UPDATE users SET role='admin' WHERE username='<your username>';"
# afterwards use the admin account to call GET /api/admin/users + PUT /api/admin/users/{id}/role
```

### `clarifications/process` returns `detected=true` but `clarification=null`

The LLM call failed and degraded (service log shows `Clarification LLM call failed`):
the `[llm]` domain is not configured or the LLM is unreachable. Configure the LLM to restore
real detection and auto-apply.

### External embedding provider unavailable

The service does not crash: `embedding` stays empty on write, recall returns
`"mode": "text"`; re-upsert concepts after the provider recovers to backfill vectors.

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License

MIT

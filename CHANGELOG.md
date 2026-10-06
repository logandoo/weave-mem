# Changelog — weave-mem

No version numbers during v0.x; entries are date-based. Format loosely follows Keep a Changelog.

## 2026-10-06 — agent integrations (Claude Code / Codex / OpenCode / dsh)

### Added
- `integrations/shared/` — zero-dependency Node core: config resolution (`WEAVE_MEM_*` env > `~/.weave-mem/config.json` > defaults), HTTP client (recall/ingest/health, `X-Agent-Id` header), recall-block build/strip, transcript turn extraction (Claude Code + Codex JSONL shapes), capture cursor state (atomic writes), debug log. `node integrations/shared/selftest.mjs` → 27 checks.
- `integrations/claude-code/` — plugin: `UserPromptSubmit` recall injection, incremental capture on `Stop`/`PreCompact`/`SessionEnd`/`SubagentStop` (subagent transcripts under `cc-<session>:<agent>`), `.mcp.json`, README.
- `integrations/codex/` — same hook core with Codex's output contract (no `decision:approve`; `Stop` no-op `{}`), README with hook-trust and path notes.
- `integrations/opencode/weave-mem-memory.js` — V1 plugin (`chat.message` synthetic recall part; `session.idle` message pull + capture) and V2 `setup()` beta; README.
- `integrations/dsh/` — Cordis plugin written against the real dsh 0.1.5-rc API, verified end-to-end against a live headless profile: `agent/session-start` stages the recall block and `agent/pre-step` merges it with the step query into one durable plugin message (the late `agent.inject()` spurious-turn race was found at runtime); `session/event` captures user/assistant text (plugin-source and reasoning parts filtered; assistant nested at `data.message`); a durable pending queue (`~/.weave-mem/state/dsh-pending.jsonl`) survives process-exit truncation and replays at the next session start. Real constructor resolution via `createRequire(process.argv[1])` (bare import fails for absolute-path plugin loading).
- `tests/test_integrations.py` — 29 checks: shared selftest, CC/Codex hook contract against a recording sink (payload/header/scope assertions), fail-open verification, live-server recall (text channel + PAT + agent scope), OpenCode/dsh contract harnesses. CI pg-full includes it.

### Design notes
- Capture is a single `POST /api/memory/ingest` per turn — recurrence gating means no session/commit/token-threshold machinery (the structural difference from session-commit memory systems).
- Injected `<weave-mem-context>` blocks are stripped before capture (no self-referential pollution); duplicate capture is prevented by per-session turn cursors (Claude Code/Codex/OpenCode) and a content-hash id set (dsh). The server has no source_ids idempotency, so retries after an aborted response are at-least-once (documented, see tests/decisions.md D-16).
- All hooks fail open: server unreachable → empty output, exit 0, host never blocked.

## 2026-10-06 — agent scope wave (multi-agent memory namespaces)

### Added
- **Agent identity namespace**: `agent_id` on memory tables (`memory_concepts`/`memory_episodes`/`subconscious_log`/`memory_clusters`/`concept_relations`/`memory_clarifications`/`memory_recall_log`/`memory_llm_calls`), `personal_access_tokens.agent_id`, `subconscious_log.conversation_id`; new `agents` registry table. `NULL` = user-level shared memory; non-NULL = agent-private. Reads resolve `shared + own`; writes are stamped with the caller.
- **Identity plumbing**: `X-Agent-Id` request header (validated `[A-Za-z0-9._-]{1,64}`, 422 otherwise; wins over token binding) · `POST /api/auth/tokens {"agent_id": …}` binds a PAT · `POST /api/auth/login {"agent_id": …}` mints a scoped JWT.
- **Per-(user, agent) state**: `user_agent_states` keyed by expression unique index `(user_id, COALESCE(agent_id,''))`; scheduler iterates `(user, agent)` scopes, per-scope advisory locks, per-scope consolidation/scan; consolidation merges only within one scope (`IS NOT DISTINCT FROM` on ANN prefilter).
- **Attribution**: `agent_id` in concepts/episodes/dreams/clarifications list responses, recall-log rows, and `POST /api/memory/ingest` responses.
- **SDK/MCP**: `MemoryClient(..., agent_id="…")` sends the scope header; MCP reads `WEAVE_MEM_AGENT_ID` env (or `[mcp] agent_id`).
- `tests/test_agent_scope.py` (39 checks; PG) + CI pg-full includes it.

### Changed
- Retrieval: BM25 indexes and session cache are keyed per `(user, agent scope)`; dense search, graph expansion, UAS summary/dream/profile reads are scope-filtered.
- Legacy per-user services (message/note watermark scan, migration, cost governance, profile sync) explicitly pin the shared (`agent_id IS NULL`) state row.

### Notes
- Existing rows keep `agent_id = NULL` → full backward compatibility (legacy clients read/write shared scope).
- SQLite: `DROP CONSTRAINT` migrations are skipped (SQLite lacks the syntax); stale SQLite DBs keep their old `unique(user_id)` and degrade to missing per-agent state rows (scoping is unaffected); fresh DBs are correct.

## 2026-10-05 — client SDK + outbound provider normalization

### Added
- `weave-mem-client` publishable typed async SDK (`client/`, pip-installable): all 32 API paths 1:1 (incl. root), PAT/login/own-headers auth, uniform `RuntimeError("HTTP …")` errors (transport errors propagate as httpx exceptions); the MCP `MemoryClient` now single-sources this package. Note: `logout()` clears the locally held token (a constructor PAT remains valid server-side).

### Changed
- Outbound embedding calls (runtime `_do_embed`, startup probe `_probe_main_provider`, `provider_router.embedding_available`) moved from hand-rolled httpx to the official `openai` SDK (no-key sentinel / timeout / circuit-breaker preserved; empty-base fail-closed — never egresses to api.openai.com); TEI `/rerank` stays raw HTTP (no standard SDK for that contract).
- README: `GET /api/memory/concepts` documented shape fixed (`{concepts, count}`, `limit` only).

## 2026-10-05 — leftover cleanup wave

### Added
- GitHub Actions CI (`.github/workflows/ci.yml`): scoped lint hard gate, sqlite smoke suites, pgvector full acceptance suites.
- AGPR two-hop propagation gate (`agpr_enabled`, default off).
- `[related memories]` grouping wrapper (`assembly_grouping_enabled`, default off) with the Proteus per-section monopoly clamp (`injection_monopoly_share=0.7`).
- E3 listwise verifier module (default off, fail-open; parser unit-tested).
- Explicit config keys with real consumers: `min_today_calls` / `recovery_ratio` (cost-governance escalation floor + recovery condition), `dream_concept_window_days` (dream lists windowed into recent-high / recent-low / fading groups).

### Fixed
- Three weight read-modify-write paths made atomic/optimistic (bulk boost, cross-boost, decay writeback with stale-read guard); the heat/status legs now yield to concurrent writers.
- SQLite dual-dialect date handling: `run_weight_decay` row dates coerced (`_as_dt`), `GET /api/memory/recall_log` tolerant of string timestamps (raw-SQL date columns come back as str on SQLite).
- sqlite acceptance fixture resolves its DB path from config and fails fast when the server is not in sqlite mode (admin-elevation cases restored; suite now 13/13).
- Recovery condition `today < avg×0.5` → `today ≤ avg×recovery_ratio` (stuck-forever degrade bug class).

### Changed
- `strategy_route_enabled` / `concept_link_expansion_enabled` flipped on to match upstream state.
- `migration_llm_timeout_seconds` 60 → 120; four consumer-less config keys removed.

## 2026-10-05 — memory-sync wave P0-P3 (+F-4a)

### Added
- HTTP adoption endpoint (`POST /api/memory/adoption`, budgeted, fail-open), graph-density consistency weighting, strategy routing and deterministic edges, adaptive + MMR selection, contradicts handling, fast-merge guards + MST assembly, recall ledger with composite keyset pagination and dual-dialect cleanup, cluster-embedding write path with backfill script.
- New services: `memory_adoption_service`, `memory_recall_log_service`; retrieval instrumentation with truncation notes.

### Fixed
- No-key guards across three call sites (explicit endpoint + empty key never falls back to the global LLM key), decay-anchor resurrection refresh, billing-class read/write split.

### Changed
- Single script entry point: `script/linux/` (the former `scripts/` directory is gone).

## 2026-08-20

### Added
- Memos-inspired wave: OpenAPI spec (`docs/openapi.json`), personal access tokens (sha256 hashed, dual-channel auth), MCP server (16 tools over HTTP + stdio), SQLite fallback mode (dialect-aware).

## 2026-08-19

### Added
- Subconscious ingest endpoint, clarification processing endpoint, and six blind-spot endpoints (concept detail, episodes, recall-meta, admin users/role, clarify apply, reload-config).

### Removed
- Dead-code cleanup: 1 module, 16 functions, 1 class, 20 config keys (user-authorized break of the chatbot diff=0 constraint).

## 2026-08-17

### Added
- First version: split from chatbot; full memory stack (21 services: extraction / consolidation / dreaming / recall / decay / cost governance) on FastAPI + PostgreSQL + pgvector.

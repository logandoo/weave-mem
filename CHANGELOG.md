# Changelog — weave-mem

No version numbers during v0.x; entries are date-based. Format loosely follows Keep a Changelog.

## 2026-10-05 — client SDK + outbound provider normalization

### Added
- `weave-mem-client` publishable typed async SDK (`client/`, pip-installable): 32 API paths 1:1, PAT/login auth, uniform `RuntimeError("HTTP …")` errors; the MCP `MemoryClient` now single-sources this package.

### Changed
- Outbound embedding calls (`memory_embedding_service` + `provider_router.embedding_available`) moved from hand-rolled httpx to the official `openai` SDK (no-key sentinel / timeout / circuit-breaker semantics preserved); TEI `/rerank` stays raw HTTP (no standard SDK for that contract).
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

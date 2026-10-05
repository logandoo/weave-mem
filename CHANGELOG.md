# Changelog — weave-mem

No version numbers during v0.x; entries are date-based. Format loosely follows Keep a Changelog.

## 2026-10-05

### Added
- Memory-sync wave P0-P3 (+F-4a): HTTP adoption endpoint (`POST /api/memory/adoption`, budgeted, fail-open), graph-density consistency weighting, strategy routing and deterministic edges, adaptive + MMR selection, contradicts handling, fast-merge guards + MST assembly, listwise verifier module (default off), recall ledger with composite keyset pagination and dual-dialect cleanup, cluster-embedding write path with backfill script.
### Fixed
- Atomic read-modify-write paths (weights / heat / state), AGPR two-hop propagation gate, `[related memories]` grouping wrapper, no-key guards across three call sites, decay-anchor resurrection, billing-class read/write split.
- Dead-code cleanup: 1 module, 16 functions, 1 class, 20 config keys removed.

### Changed
- Single script entry point: `script/linux/` (the former `scripts/` directory is gone).

## 2026-08-20

### Added
- Memos-inspired wave: OpenAPI spec (`docs/openapi.json`), personal access tokens (sha256 hashed, dual-channel auth), MCP server (16 tools over HTTP + stdio), SQLite fallback mode (dialect-aware).

## 2026-08-19

### Added
- Subconscious ingest endpoint, clarification processing endpoint, and six blind-spot endpoints (concept detail, episodes, recall-meta, admin users/role, clarify apply, reload-config).

## 2026-08-17

### Added
- First version: split from chatbot; full memory stack (21 services: extraction / consolidation / dreaming / recall / decay / cost governance) on FastAPI + PostgreSQL + pgvector,.

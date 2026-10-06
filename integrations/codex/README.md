# weave-mem for Codex

Cross-session long-term memory for Codex: automatic recall on every prompt and
incremental capture after every turn (agent scope: `codex`). Shares the hook
implementations with the Claude Code plugin — no `decision:"approve"` (Codex
only accepts `block`), no-op output is `{}`.

## Install

Two supported routes; **route A (user-level hooks) is the most reliable**
across Codex builds, because plugin-bundled hook discovery and plugin-root
environment variables vary by version.

### Route A — user-level hooks (recommended)

```bash
mkdir -p ~/.weave-mem/plugins
ln -s /path/to/weave-mem ~/.weave-mem/plugins/weave-mem   # or: git clone <repo> there
mkdir -p ~/.codex && cat >> ~/.codex/hooks.json <<'JSON'
{ "hooks": { "UserPromptSubmit": [ { "hooks": [ { "type": "command",
  "command": "node \"$HOME/.weave-mem/plugins/weave-mem/integrations/shared/hooks/auto-recall.mjs\" --harness=codex" } ] } ],
  "Stop": [ { "hooks": [ { "type": "command",
  "command": "node \"$HOME/.weave-mem/plugins/weave-mem/integrations/shared/hooks/auto-capture.mjs\" --harness=codex" } ] } ] } }
JSON
```

(Add SessionStart/PreCompact/SessionEnd blocks from `hooks/hooks.json` the
same way if you want them.) Launch Codex, open `/hooks`, review and trust the
commands.

### Route B — bundled plugin

Copy/symlink this directory into your Codex plugins location and enable it
(`codex plugin add …` for marketplace installs, or a manual symlink where your
build discovers plugins). The bundled `hooks/hooks.json` uses
`${CODEX_PLUGIN_ROOT:-$HOME/.weave-mem/plugins/weave-mem/integrations}` so it
works whether or not the build expands a plugin-root variable; if neither
applies, hardcode the absolute path.

### Credentials

```bash
export WEAVE_MEM_URL=http://127.0.0.1:8202   # or ~/.weave-mem/config.json
export WEAVE_MEM_TOKEN=wm_...
```

Notes:
- `SessionEnd` has a 1s default / 3s max budget in Codex; the plugin sets
  `timeout: 3` and saves capture progress per turn, so a partial last flush is
  harmless (the next hook resumes from the cursor).
- Hook output contract: `UserPromptSubmit` → `hookSpecificOutput.additionalContext`;
  `Stop`/`PreCompact`/`SessionEnd` → `{}` (capture happens regardless).

## Configuration

Identical to the Claude Code plugin (same `integrations/shared` core):
`WEAVE_MEM_URL` / `WEAVE_MEM_TOKEN` / `WEAVE_MEM_AGENT_ID` (default `codex`) /
`WEAVE_MEM_AUTO_RECALL` / `WEAVE_MEM_AUTO_CAPTURE` / `WEAVE_MEM_RECALL_MAX_CHARS` /
`WEAVE_MEM_CAPTURE_MAX_CHARS` / `WEAVE_MEM_MIN_QUERY_LENGTH` /
`WEAVE_MEM_BYPASS_SESSION_PATTERNS` / `WEAVE_MEM_DEBUG`.

Captured turns carry `conversation_id: cx-<session_id>` and
`X-Agent-Id: codex`; the MCP tools (if configured) use the same scope when you
set `WEAVE_MEM_AGENT_ID=codex` on the MCP server process.

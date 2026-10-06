# weave-mem for Claude Code

Cross-session long-term memory for Claude Code: recall is injected before every
prompt, conversation turns are captured after every response — no MCP tool
calls required from the model. The model still gets the weave-mem MCP tools
(`search`/`read`/`write`/…) via `.mcp.json`.

Agent scope: every request carries `X-Agent-Id: claude-code`, so this plugin's
memory is isolated from Codex/OpenCode/dsh scopes (shared, NULL-scope memory
is visible to all).

## Install

1. Start weave-mem (`bash script/linux/start.sh`) and create a PAT for the
   agent (recommended):

   ```bash
   curl -sS -X POST http://127.0.0.1:8202/api/auth/tokens \
     -H "Authorization: Bearer $JWT" -H 'Content-Type: application/json' \
     -d '{"name":"claude-code","agent_id":"claude-code"}'
   ```

2. Point the plugin at the server — either env vars (exported in your shell
   profile) or `~/.weave-mem/config.json`:

   ```bash
   export WEAVE_MEM_URL=http://127.0.0.1:8202
   export WEAVE_MEM_TOKEN=wm_...
   ```

   `~/.weave-mem/config.json`: `{"baseUrl":"http://127.0.0.1:8202","token":"wm_..."}`

3. Load the plugin (from a clone of this repo):

   ```bash
   claude --plugin-dir /path/to/weave-mem/integrations/claude-code
   ```

   The hooks live in `integrations/shared/hooks/` (shared with the Codex
   plugin), so keep the repository tree together.

## Hooks

| Event | Behavior |
|---|---|
| `SessionStart` | lightweight recall using the project directory name |
| `UserPromptSubmit` | recall relevant memory, inject into `additionalContext` |
| `Stop` / `PreCompact` / `SessionEnd` | incremental transcript capture → `POST /api/memory/ingest` |
| `SubagentStop` | subagent transcript captured under `cc-<session>:<agent_id>` |

All hooks fail open: server unreachable → no output, exit 0, the session is
never blocked. Captured turns have the injected `<weave-mem-context>` block
stripped (no self-referential pollution). Duplicate capture is prevented by a
per-session cursor (`~/.weave-mem/state/`).

## Configuration (env > `~/.weave-mem/config.json` > defaults)

| Env | Default | Meaning |
|---|---|---|
| `WEAVE_MEM_URL` | `http://127.0.0.1:8202` | server base URL |
| `WEAVE_MEM_TOKEN` | (empty) | PAT for auth |
| `WEAVE_MEM_AGENT_ID` | `claude-code` | scope override |
| `WEAVE_MEM_AUTO_RECALL` / `WEAVE_MEM_AUTO_CAPTURE` | `true` | enable/disable |
| `WEAVE_MEM_RECALL_MAX_CHARS` | `1600` | injected block char cap |
| `WEAVE_MEM_CAPTURE_MAX_CHARS` | `4000` | per-turn capture cap |
| `WEAVE_MEM_MIN_QUERY_LENGTH` | `3` | skip recall for short prompts |
| `WEAVE_MEM_BYPASS_SESSION_PATTERNS` | (empty) | CSV globs matched against session id / cwd |
| `WEAVE_MEM_DEBUG` | `false` | log to `~/.weave-mem/logs/claude-code.log` |

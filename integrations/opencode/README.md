# weave-mem for OpenCode

Automatic recall (`chat.message`) and session capture (`session.idle`) for
OpenCode, backed by weave-mem. Agent scope: `opencode`.

## Install

```bash
# 1. credentials (env or ~/.weave-mem/config.json)
export WEAVE_MEM_URL=http://127.0.0.1:8202
export WEAVE_MEM_TOKEN=wm_...

# 2. install the plugin (keep the repository tree: the plugin imports ../../shared)
ln -s /path/to/weave-mem/integrations/opencode/weave-mem-memory.js \
      ~/.config/opencode/plugins/weave-mem-memory.js
```

OpenCode auto-discovers first-level `*.js` files under
`~/.config/opencode/plugins/`. The plugin reaches the shared core through the
symlink; if you copy the file instead, also copy `integrations/shared/` next to
it and adjust the relative import.

## How it works

- **Recall** — the V1 `chat.message` hook appends a synthetic text part
  containing the `<weave-mem-context>` block (recall for the current message).
- **Capture** — on `session.idle` the plugin pulls the session's messages via
  the OpenCode client and ingests new user/assistant text, tracked by a
  per-session positional cursor (append-ordered list; a truncated/rewritten
  history conservatively rescans) as `conversation_id: oc-<session_id>`,
  `X-Agent-Id: opencode`.
- **V2 (beta)** — `setup(ctx)` registers the documented `ctx.session.hook("prompt", …)`
  hook. This path is contract-tested with a mock context only; verify against
  your OpenCode v2 build before relying on it.
- Everything fails open: server errors never affect the session.

## Configuration

Same shared core as the other integrations: `WEAVE_MEM_URL` /
`WEAVE_MEM_TOKEN` / `WEAVE_MEM_AGENT_ID` (default `opencode`) /
`WEAVE_MEM_AUTO_RECALL` / `WEAVE_MEM_AUTO_CAPTURE` /
`WEAVE_MEM_RECALL_MAX_CHARS` / `WEAVE_MEM_CAPTURE_MAX_CHARS` /
`WEAVE_MEM_MIN_QUERY_LENGTH` / `WEAVE_MEM_BYPASS_SESSION_PATTERNS` /
`WEAVE_MEM_DEBUG` (`~/.weave-mem/logs/opencode.log`).

# weave-mem for DeepSeek Harness (dsh)

Cordis plugin adding weave-mem recall/capture to `dsh` sessions. Agent scope:
`dsh`.

- `agent/session-start` — recalls a short profile block and **stages** it; the
  block is merged into the first real `agent/pre-step` message. (Calling
  `agent.inject()` here was measured at runtime to create a spurious extra
  turn: the async recall makes the injected message arrive after the task step
  has already started.)
- `agent/pre-step` — waterfall: appends ONE durable plugin message
  (`source: {kind:"plugin"}`, form `snapshot`) combining the staged
  session-start block with the recall for the current step input; steps whose
  input is plugin-only (e.g. dsh runtime-context snapshots) are skipped to
  avoid self-referential recall. Never touches the system prompt (dsh presets
  with `complete: true` restore their persona and would discard other prompt
  sections silently).
- `session/event` — captures `user/message` / `assistant/message` from the
  event stream as `conversation_id: dsh-<session_id>`, `X-Agent-Id: dsh`;
  plugin-source messages (weave-mem blocks, dsh runtime-context snapshots) and
  `reasoning` parts are skipped; assistant messages nest at
  `event.data.message`; deduplicated by message id.
- **Durable pending queue** — capture is fire-and-forget, so a process that
  exits mid-request (headless one-shot) would lose the last message. Each
  record is written to `~/.weave-mem/state/dsh-pending.jsonl` before the
  request and removed after success; leftovers replay at the next session
  start (at-least-once).
- `turn/end` — lifecycle placeholder (no commit machinery: capture is a single
  immediate `POST /api/memory/ingest`, promoted later by recurrence gating).

## Install

```bash
# keep the repo tree (this plugin imports ../../shared)
mkdir -p ~/.weave-mem/plugins/weave-mem
ln -s /path/to/weave-mem ~/.weave-mem/plugins/weave-mem
# then, in your dsh profile:
dsh plugin --profile <name> add ~/.weave-mem/plugins/weave-mem/integrations/dsh
```

Or add to the profile's Cordis patch:

```yaml
- insert:
    - id: weave-mem-memory
      name: '@deepseek-ai/cordis-plugin-group'
      group: true
      config:
        - id: weave-mem-memory-runtime
          name: '/abs/path/to/weave-mem/integrations/dsh/index.mjs'
          config:
            baseUrl: http://127.0.0.1:8202
            token: wm_...
```

Credentials resolve env (`WEAVE_MEM_*`) → `~/.weave-mem/config.json` →
plugin config. Everything fails open.

## Status

Verified end-to-end against a live dsh 0.1.5-rc.3 headless profile: single
step, session-start + pre-step recalls, user and assistant ingest, no spurious
turns, pending queue empty — see `tests/dsh_runtime_evidence.txt`. Contract
tests cover the real API shapes (`tests/integrations/dsh_harness.mjs`; the
integration suite now runs 29 checks).

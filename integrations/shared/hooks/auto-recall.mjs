#!/usr/bin/env node
// shared/hooks/auto-recall.mjs — UserPromptSubmit 钩子（Claude Code / Codex 共用）
// 输出 {hookSpecificOutput:{hookEventName, additionalContext}}；无结果/失败 → {}；永远 exit 0。
import { loadConfig, isBypassed } from "../config.mjs";
import { resolveAgentId } from "../agent-id.mjs";
import { recall } from "../client.mjs";
import { buildRecallBlock } from "../recall-block.mjs";
import { readStdin, parseHookInput, writeJson, argValue } from "../io.mjs";
import { debugLog } from "../debug-log.mjs";

const harness = argValue("--harness") || process.env.WEAVE_MEM_HARNESS || "claude-code";

try {
  const input = parseHookInput(await readStdin());
  const cfg = loadConfig();
  cfg._agentId = resolveAgentId(cfg, harness);
  const prompt = String(input.prompt || input.user_prompt || "");
  debugLog(harness, `auto-recall start len=${prompt.length}`, { enabled: cfg.debug });

  if (!cfg.autoRecall
      || isBypassed(cfg, { sessionId: input.session_id || "", cwd: input.cwd || "" })
      || prompt.trim().length < cfg.minQueryLength) {
    writeJson({});
    process.exit(0);
  }

  const result = await recall(cfg, prompt.slice(0, 2000));
  const block = result ? buildRecallBlock(result.context, { maxChars: cfg.recallMaxChars }) : null;
  debugLog(harness, `auto-recall done hit=${!!block}`, { enabled: cfg.debug });
  if (!block) {
    writeJson({});
    process.exit(0);
  }
  writeJson({
    hookSpecificOutput: {
      hookEventName: input.hook_event_name || "UserPromptSubmit",
      additionalContext: block,
    },
  });
} catch (err) {
  debugLog(harness, `auto-recall error ${String(err)}`, { enabled: true });
  writeJson({});
}
process.exit(0);

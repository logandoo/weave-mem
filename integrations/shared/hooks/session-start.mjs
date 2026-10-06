#!/usr/bin/env node
// shared/hooks/session-start.mjs — SessionStart 钩子：以 cwd 项目名做轻量召回注入。
import { loadConfig, isBypassed } from "../config.mjs";
import { resolveAgentId } from "../agent-id.mjs";
import { recall } from "../client.mjs";
import { buildRecallBlock } from "../recall-block.mjs";
import { readStdin, parseHookInput, writeJson, argValue } from "../io.mjs";

const harness = argValue("--harness") || process.env.WEAVE_MEM_HARNESS || "claude-code";

try {
  const input = parseHookInput(await readStdin());
  const cfg = loadConfig();
  cfg._agentId = resolveAgentId(cfg, harness);
  if (!cfg.autoRecall
      || isBypassed(cfg, { sessionId: input.session_id || "", cwd: input.cwd || "" })) {
    writeJson({});
    process.exit(0);
  }
  const cwd = String(input.cwd || "");
  const project = cwd.split("/").filter(Boolean).pop() || "";
  if (project.length < cfg.minQueryLength) {
    writeJson({});
    process.exit(0);
  }
  const result = await recall(cfg, `项目 ${project} 的上下文与偏好`, { timeoutMs: cfg.requestTimeoutMs });
  const block = result ? buildRecallBlock(result.context, { maxChars: Math.floor(cfg.recallMaxChars / 2) }) : null;
  if (!block) {
    writeJson({});
    process.exit(0);
  }
  writeJson({
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: block,
    },
  });
} catch {
  writeJson({});
}
process.exit(0);

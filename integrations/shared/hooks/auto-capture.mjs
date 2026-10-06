#!/usr/bin/env node
// shared/hooks/auto-capture.mjs — Stop/PreCompact/SessionEnd/SubagentStop 钩子（CC/Codex 共用）
// transcript JSONL → 游标增量（全量解析，批次上限保护宿主超时）→ sanitize → 逐条 ingest。
// ingest 失败停在原地下次重试；逐条推进游标（crash-safe）；永远 exit 0；输出 {}。
import { readFileSync } from "node:fs";
import { loadConfig, isBypassed } from "../config.mjs";
import { resolveAgentId, conversationId } from "../agent-id.mjs";
import { ingest } from "../client.mjs";
import { sanitizeText, extractTurnsFromJsonl } from "../capture.mjs";
import { loadState, saveState, stateKey } from "../state.mjs";
import { readStdin, parseHookInput, writeJson, argValue } from "../io.mjs";
import { debugLog } from "../debug-log.mjs";

const harness = argValue("--harness") || process.env.WEAVE_MEM_HARNESS || "claude-code";

try {
  const input = parseHookInput(await readStdin());
  const cfg = loadConfig();
  cfg._agentId = resolveAgentId(cfg, harness);

  const sessionId = String(input.session_id || "default");
  const sub = String(input.agent_id || input.agent_type || "");
  const isSub = Boolean(sub);
  // SubagentStop：优先子代理专属 transcript（CC/Codex 同时提供主 transcript）
  const transcriptPath = String((isSub && input.agent_transcript_path) || input.transcript_path || "");

  if (!cfg.autoCapture
      || !transcriptPath
      || isBypassed(cfg, { sessionId, cwd: input.cwd || "" })) {
    writeJson({});
    process.exit(0);
  }

  const raw = readFileSync(transcriptPath, "utf8");
  const turns = extractTurnsFromJsonl(raw);
  const key = stateKey(harness, isSub ? `${sessionId}:${sub}` : sessionId);
  const state = loadState(key);
  const start = Math.max(0, Number(state.capturedTurnCount || 0));
  debugLog(harness, `auto-capture turns=${turns.length} from=${start}`, { enabled: cfg.debug });

  let scanned = start;
  let ingested = 0;
  for (let i = start; i < turns.length; i++) {
    if (ingested >= cfg.captureBatchSize) break; // 批次上限：剩余轮次由下次钩子继续
    const clean = sanitizeText(turns[i].text, { maxChars: cfg.captureMaxChars });
    if (clean.length < 5) {
      scanned = i + 1;
      saveState(key, { capturedTurnCount: scanned, updatedAt: Date.now() });
      continue;
    }
    const res = await ingest(cfg, {
      content: clean,
      unitKind: "message",
      sourceIds: [`${harness}:${sessionId}${isSub ? ":" + sub : ""}:${i}`],
      conversationId: conversationId(harness, sessionId, sub),
    });
    if (!res.ok) {
      debugLog(harness, `auto-capture ingest failed status=${res.status} (retry next run)`, { enabled: true });
      break;
    }
    scanned = i + 1;
    ingested++;
    saveState(key, { capturedTurnCount: scanned, updatedAt: Date.now() });
  }
  debugLog(harness, `auto-capture done scanned=${scanned} ingested=${ingested}`, { enabled: cfg.debug });
} catch (err) {
  debugLog(harness, `auto-capture error ${String(err)}`, { enabled: true });
}
writeJson({});
process.exit(0);

// weave-mem-memory.js — OpenCode plugin（纯 ESM，零运行时依赖）
// V1: 导出函数返回 hooks（chat.message 注入召回 / event 订阅 session.idle 捕获）。
// V2: setup(ctx) 按文档 API 注册 prompt 钩子（beta，运行时未验）。
// 安装：复制本文件到 ~/.config/opencode/plugins/（opencode 自动发现一级 *.js/*.ts）。
import { loadConfig, isBypassed } from "../shared/config.mjs";
import { resolveAgentId, conversationId } from "../shared/agent-id.mjs";
import { recall, ingest } from "../shared/client.mjs";
import { buildRecallBlock } from "../shared/recall-block.mjs";
import { sanitizeText } from "../shared/capture.mjs";
import { loadState, saveState, stateKey } from "../shared/state.mjs";

function configFor() {
  const cfg = loadConfig();
  cfg._agentId = resolveAgentId(cfg, "opencode");
  return cfg;
}

function partsText(parts) {
  if (!Array.isArray(parts)) return "";
  return parts.filter((p) => p?.type === "text" && typeof p.text === "string").map((p) => p.text).join("\n");
}

async function flushSession(client, cfg, sessionID) {
  if (!cfg.autoCapture || !sessionID || typeof client?.session?.messages !== "function") return;
  if (isBypassed(cfg, { sessionId: sessionID })) return;
  let data;
  try {
    const res = await client.session.messages({ path: { id: sessionID } });
    data = res?.data ?? res;
  } catch {
    return;
  }
  if (!Array.isArray(data)) return;
  const turns = [];
  for (const entry of data) {
    const info = entry?.info || {};
    const role = info.role || entry?.role;
    if (role !== "user" && role !== "assistant") continue;
    turns.push({ text: sanitizeText(partsText(entry?.parts), { maxChars: cfg.captureMaxChars }) });
  }
  const key = stateKey("opencode", sessionID);
  const st = loadState(key);
  // 位置游标（列表按时间序）：有界 id 集会因驱逐重发旧消息（A4.9 C2），改为纯位置推进。
  let idx = Math.max(0, Number(st.cursor || 0));
  if (idx > turns.length) idx = 0; // 列表被宿主重建/截断 → 保守重扫
  let ingested = 0;
  for (let i = idx; i < turns.length; i++) {
    if (ingested >= cfg.captureBatchSize) break;
    if (turns[i].text.length < 5) {
      idx = i + 1;
      continue;
    }
    const res = await ingest(cfg, {
      content: turns[i].text,
      sourceIds: [`opencode:${sessionID}:${i}`],
      conversationId: conversationId("opencode", sessionID),
    });
    if (!res.ok) break;
    idx = i + 1;
    ingested++;
    saveState(key, { cursor: idx, updatedAt: Date.now() });
  }
  saveState(key, { cursor: idx, updatedAt: Date.now() });
}

export const WeaveMemPlugin = async (ctx = {}) => {
  const cfg = configFor();
  const client = ctx.client;
  return {
    "chat.message": async (input, output) => {
      try {
        if (!Array.isArray(output?.parts)) return;
        // 幂等守卫：钩子重入时不得重复追加（合成块会自引用进下次查询）
        if (output.parts.some((p) => p?.synthetic && String(p.text || "").includes("<weave-mem-context>"))) return;
        const text = partsText(output.parts.filter((p) => !p?.synthetic));
        if (!cfg.autoRecall
            || isBypassed(cfg, { sessionId: input?.sessionID || "", cwd: input?.cwd || "" })
            || text.trim().length < cfg.minQueryLength) return;
        const r = await recall(cfg, text.slice(0, 2000));
        const block = r ? buildRecallBlock(r.context, { maxChars: cfg.recallMaxChars }) : null;
        if (block) {
          output.parts.push({ type: "text", text: block, synthetic: true });
        }
      } catch { /* fail-open */ }
    },
    event: async ({ event } = {}) => {
      try {
        if (event?.type !== "session.idle") return;
        const sid = String(
          event?.properties?.sessionID
          || event?.properties?.info?.id
          || event?.properties?.id
          || "",
        );
        await flushSession(client, cfg, sid);
      } catch { /* fail-open */ }
    },
  };
};

export const server = WeaveMemPlugin;
export default WeaveMemPlugin;

// ---- V2 (beta) ----
function extractV2Text(event) {
  if (typeof event?.text === "string") return event.text;
  if (Array.isArray(event?.parts)) return partsText(event.parts);
  const m = event?.message;
  if (typeof m?.content === "string") return m.content;
  if (Array.isArray(m?.parts)) return partsText(m.parts);
  return "";
}

export async function setup(ctx = {}) {
  const cfg = configFor();
  const promptHandler = async (event) => {
    try {
      if (Array.isArray(event?.parts) && event.parts.some((p) => p?.synthetic && String(p.text || "").includes("<weave-mem-context>"))) return;
      const sid = String(event?.sessionID || event?.sessionId || "");
      if (!cfg.autoRecall || isBypassed(cfg, { sessionId: sid, cwd: event?.cwd || "" })) return;
      const text = extractV2Text(event);
      if (text.trim().length < cfg.minQueryLength) return;
      const r = await recall(cfg, text.slice(0, 2000));
      const block = r ? buildRecallBlock(r.context, { maxChars: cfg.recallMaxChars }) : null;
      if (!block) return;
      if (Array.isArray(event?.parts)) event.parts.push({ type: "text", text: block, synthetic: true });
      else if (Array.isArray(event?.messages)) event.messages.push({ role: "user", content: block });
      else if (Array.isArray(event?.message?.parts)) event.message.parts.push({ type: "text", text: block });
    } catch { /* fail-open */ }
  };
  try {
    if (typeof ctx?.session?.hook === "function") await ctx.session.hook("prompt", promptHandler);
  } catch { /* fail-open */ }
  return () => {};
}

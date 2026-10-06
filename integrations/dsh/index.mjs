// index.mjs — weave-mem Cordis plugin for DeepSeek Harness (dsh)
// 真 API（dsh 0.1.5-rc 实测）：
//   agent/session-start  emit  ({ agent, source })                          → 召回块暂存
//   agent/pre-step       waterfall ({ agent, messages, turn, step, signal }, next)
//                        → {...decision, messages:[...decision.messages, 合并块]}
//   session/event        emit  (session, event)                             → user/assistant 捕获
//   turn/end             emit  （占位；单条即时 ingest，无 commit）
// createUserMessage：优先真实 peer（@deepseek-ai/dsh-llm，可经 dsh 安装解析）；
// 不可达时用字面量兜底（形态等价：role/content/source，运行时可接受）。
import { loadConfig, isBypassed } from "../shared/config.mjs";
import { resolveAgentId, conversationId } from "../shared/agent-id.mjs";
import { recall, ingest } from "../shared/client.mjs";
import { buildRecallBlock } from "../shared/recall-block.mjs";
import { sanitizeText } from "../shared/capture.mjs";
import { loadState, saveState, stateKey } from "../shared/state.mjs";
import { debugLog } from "../shared/debug-log.mjs";
import { createHash, randomUUID } from "node:crypto";
import { appendFileSync, existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

export const name = "weave-mem-memory";

// ---- createUserMessage：真实 peer 解析（plugin 经绝对路径加载时裸 import 会失败，
// 用 createRequire 锚定 dsh 入口再解析）→ 失败回退等价字面量。 ----
let createUserMessage = (input) => ({
  id: randomUUID(),
  role: "user",
  ...input,
});
try {
  ({ createUserMessage } = await import("@deepseek-ai/dsh-llm"));
} catch {
  try {
    const { createRequire } = await import("node:module");
    const anchor = process.argv[1] || import.meta.url;
    const resolved = createRequire(anchor).resolve("@deepseek-ai/dsh-llm");
    ({ createUserMessage } = await import(pathToFileURL(resolved).href));
  } catch {
    /* 独立/测试环境：保留字面量兜底 */
  }
}

const PLUGIN_SOURCE = { kind: "plugin", plugin: name, form: "snapshot" };

function pluginMessage(text) {
  return createUserMessage({
    content: [{ type: "text", text }],
    source: { ...PLUGIN_SOURCE, sections: [{ name, text }] },
  });
}

// ---- durable pending 队列（append-only ops + ack fold；跨进程单行 append 原子） ----
// 行格式：{"op":"add","record":{...}} | {"op":"ack","ids":[...]}
function pendingPath(home = homedir()) {
  return join(home, ".weave-mem", "state", "dsh-pending.jsonl");
}

function _readOps() {
  try {
    const p = pendingPath();
    if (!existsSync(p)) return [];
    return readFileSync(p, "utf8").split("\n").filter(Boolean).map((l) => {
      try {
        return JSON.parse(l);
      } catch {
        return null;
      }
    }).filter(Boolean);
  } catch {
    return [];
  }
}

function readPending() {
  const map = new Map();
  for (const op of _readOps()) {
    if (op.op === "add" && op.record?.sourceIds?.[0]) map.set(op.record.sourceIds[0], op.record);
    else if (op.op === "ack" && Array.isArray(op.ids)) for (const id of op.ids) map.delete(id);
  }
  return [...map.values()];
}

function addPending(record) {
  try {
    const dir = join(homedir(), ".weave-mem", "state");
    mkdirSync(dir, { recursive: true });
    const pending = readPending();
    if (pending.length >= 1000) {
      // 有界：极端长时断网时丢最旧（留痕），避免无界增长
      const drop = pending.slice(0, pending.length - 999).map((r) => r.sourceIds?.[0]).filter(Boolean);
      appendFileSync(pendingPath(), JSON.stringify({ op: "ack", ids: drop }) + "\n");
      debugLog("dsh", `pending queue over cap: dropped ${drop.length} oldest`, { enabled: true });
    }
    appendFileSync(pendingPath(), JSON.stringify({ op: "add", record }) + "\n");
  } catch {
    /* fail-open */
  }
}

function ackPending(ids) {
  try {
    appendFileSync(pendingPath(), JSON.stringify({ op: "ack", ids: [...ids] }) + "\n");
    // 压实：ops 行过多时重写为未确认的 add 行（best-effort；跨进程极端竞态下可能丢一条，at-least-once 边界已记录 D-16/D-17）
    const ops = _readOps();
    if (ops.length > 1000) {
      const keep = readPending();
      writeFileSync(pendingPath(), keep.map((r) => JSON.stringify({ op: "add", record: r })).join("\n") + (keep.length ? "\n" : ""));
    }
  } catch {
    /* fail-open */
  }
}

async function flushPending(cfg) {
  try {
    for (const rec of readPending().slice(0, 200)) {
      const res = await ingest(cfg, {
        content: rec.content,
        sourceIds: rec.sourceIds || [],
        conversationId: rec.conversationId || "",
      });
      if (!res.ok) break; // 服务仍不可达：保留队列下次再试
      ackPending([rec.sourceIds?.[0]]);
    }
  } catch {
    /* fail-open */
  }
}

function partsText(parts) {
  if (typeof parts === "string") return parts;
  if (!Array.isArray(parts)) return "";
  return parts
    .filter((p) => p && typeof p === "object" && p.type === "text" && typeof p.text === "string")
    .map((p) => p.text)
    .join("\n");
}

function isPluginSource(msg) {
  return msg?.source?.kind === "plugin";
}

function latestUserText(messages) {
  if (!Array.isArray(messages)) return "";
  // 排除 plugin 注入消息（含 weave-mem 自身与 dsh 运行时快照）——否则会以自身输出为查询递归注入
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m?.role === "user" && !isPluginSource(m)) {
      const text = partsText(m.content);
      if (text.trim()) return text;
    }
  }
  return "";
}

function sessionIdOf(session) {
  return String(session?.id ?? session?.sessionId ?? "default");
}

export function apply(ctx, config = {}) {
  // 优先级：env > ~/.weave-mem/config.json > 插件内联 config > defaults
  const cfg = loadConfig(process.env, undefined, config || {});
  cfg._agentId = resolveAgentId(cfg, "dsh");
  const on = typeof ctx?.on === "function" ? ctx.on.bind(ctx) : () => {};
  // session-start 召回块暂存：由首个真实 pre-step 合并注入。
  // 不用 agent.inject()——异步 recall 会让注入迟到成"下一步"的输入，产生虚假回合（真运行时实测）。
  const pendingSessionBlocks = new Map(); // sid -> block
  const sessionInflight = new Map(); // sid -> Promise<void>（首个 pre-step 等待在途召回，防 one-shot 竞态丢失）

  // 会话开始：emit。召回仅暂存（不投递）；flush 先于/bypass 后执行。
  on("agent/session-start", async ({ agent, source } = {}) => {
    const sid = sessionIdOf(agent?.session);
    if (isBypassed(cfg, { sessionId: sid })) return;
    const task = (async () => {
      try {
        if (cfg.autoCapture) await flushPending(cfg);
        if (!cfg.autoRecall) return;
        const r = await recall(cfg, "项目上下文");
        const block = r ? buildRecallBlock(r.context, { maxChars: Math.floor(cfg.recallMaxChars / 2) }) : null;
        if (!block) return;
        if (pendingSessionBlocks.size >= 512) {
          pendingSessionBlocks.delete(pendingSessionBlocks.keys().next().value);
        }
        pendingSessionBlocks.set(sid, block);
      } catch (err) {
        debugLog("dsh", `session-start error ${String(err)}`, { enabled: cfg.debug });
      }
    })();
    sessionInflight.set(sid, task);
    task.finally(() => {
      if (sessionInflight.get(sid) === task) sessionInflight.delete(sid);
    });
    await task;
  });

  // 每步之前：waterfall。批准后把召回（session-start 暂存 + 本步查询）合并为一条 durable plugin 消息。
  on("agent/pre-step", async ({ agent, messages = [], turn, step, signal } = {}, next) => {
    let decision;
    try {
      decision = typeof next === "function" ? await next() : { kind: "enter", messages };
      if (!decision || decision.kind === "reject" || signal?.aborted) return decision;
      if (!cfg.autoRecall) return decision;
      const sid = sessionIdOf(agent?.session);
      if (isBypassed(cfg, { sessionId: sid })) return decision;
      const text = latestUserText(messages);
      if (text.trim().length < cfg.minQueryLength) return decision; // plugin-only 步骤：暂存保留给真实步骤
      // 首个真实步骤若 session-start 召回仍在途：等待其完成（有界 1.5s），防 one-shot 竞态
      if (!pendingSessionBlocks.has(sid) && sessionInflight.has(sid)) {
        await Promise.race([
          sessionInflight.get(sid),
          new Promise((resolve) => setTimeout(resolve, 1500)),
        ]);
      }
      const parts = [];
      const pending = pendingSessionBlocks.get(sid);
      if (pending) {
        pendingSessionBlocks.delete(sid);
        parts.push(pending);
      }
      const r = await recall(cfg, text.slice(0, 2000));
      const block = r ? buildRecallBlock(r.context, { maxChars: cfg.recallMaxChars }) : null;
      if (block) parts.push(block);
      if (!parts.length) return decision;
      return { ...decision, messages: [...decision.messages, pluginMessage(parts.join("\n\n"))] };
    } catch (err) {
      debugLog("dsh", `pre-step error ${String(err)}`, { enabled: cfg.debug });
      return decision ?? { kind: "enter", messages };
    }
  });

  // 会话事件流：捕获 user/assistant 提交消息（消息带稳定 id → 幂等去重）。
  on("session/event", async (session, event) => {
    try {
      if (!cfg.autoCapture || !event) return;
      const role = event.type === "user/message" ? "user"
        : event.type === "assistant/message" ? "assistant" : null;
      if (!role) return;
      // assistant 事件把消息嵌在 data.message；user 事件直接在 data
      const data = (role === "assistant" ? (event.data?.message || event.data) : event.data) || {};
      // 跳过 plugin 注入（weave-mem 自身块 / dsh 运行时快照）——只捕获真实对话
      if (isPluginSource(data)) return;
      const sid = sessionIdOf(session);
      if (isBypassed(cfg, { sessionId: sid })) return;
      const clean = sanitizeText(partsText(data.content), { maxChars: cfg.captureMaxChars });
      if (clean.length < 5) return;
      const key = stateKey("dsh", sid);
      const st = loadState(key);
      const done = new Set(Array.isArray(st.ingestedIds) ? st.ingestedIds : []);
      const id = String(data.id
        || createHash("sha1").update(`${role}:${clean}`).digest("hex").slice(0, 16));
      if (done.has(id)) return;
      const sourceId = `dsh:${sid}:${id}`;
      const record = {
        content: clean,
        sourceIds: [sourceId],
        conversationId: conversationId("dsh", sid),
      };
      addPending(record); // 先落盘：进程随时退出也不丢
      const res = await ingest(cfg, record);
      if (!res.ok) return; // 失败留在队列，下次 session-start 补发
      ackPending([sourceId]);
      done.add(id);
      saveState(key, { ingestedIds: [...done].slice(-2000), updatedAt: Date.now() });
    } catch (err) {
      debugLog("dsh", `session/event error ${String(err)}`, { enabled: cfg.debug });
    }
  });

  // 无 commit 机制：捕获为单条即时 ingest；turn/end 仅作生命周期占位。
  on("turn/end", async () => {});

  return () => {};
}

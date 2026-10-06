// tests/integrations/dsh_harness.mjs — dsh Cordis 插件契约测试（真实运行时 API 形态 + mock ctx + sink 服务）
// env: WEAVE_MEM_URL / WEAVE_MEM_TOKEN / HARNESS_SESSION
import { apply, name } from "../../integrations/dsh/index.mjs";

let failed = 0;
const check = (n, c, d = "") => {
  if (c) console.log(`PASS  ${n} ${d}`);
  else { failed++; console.log(`FAIL  ${n} ${d}`); }
};

const SID = process.env.HARNESS_SESSION || "dsh-test";
const handlers = {};
const ctx = { on: (event, fn) => { handlers[event] = fn; } };
const dispose = apply(ctx, {});
check("插件名", name === "weave-mem-memory");
check("注册四个生命周期", ["agent/session-start", "agent/pre-step", "session/event", "turn/end"].every((k) => typeof handlers[k] === "function"),
  `keys=${Object.keys(handlers).join(",")}`);

// agent/session-start（emit）：不投递（避免虚假回合），召回块暂存给首个真实 pre-step
const injected = [];
await handlers["agent/session-start"]({
  agent: { session: { id: SID }, inject: (m) => injected.push(m) },
  source: "startup",
});
check("session-start 不调用 agent.inject（防虚假回合）", injected.length === 0, `n=${injected.length}`);

// agent/pre-step（waterfall）：next() 放行 → session-start 暂存块与本步查询块合并为一条 durable 消息
const downstreamMsg = { role: "user", source: { kind: "user" }, content: [{ type: "text", text: "真实下游消息" }] };
const nextEnter = async () => ({ kind: "enter", messages: [downstreamMsg] });
const stepPayload = {
  agent: { session: { id: SID } },
  messages: [{ role: "user", source: { kind: "user" }, content: [{ type: "text", text: "dsh 当前步骤输入内容" }] }],
  turn: 1, step: 1, signal: { aborted: false },
};
const decision = await handlers["agent/pre-step"](stepPayload, nextEnter);
const merged = decision?.messages?.[1];
check("pre-step 保留下游消息并追加合并注入块",
  decision?.kind === "enter" && decision.messages.length === 2
  && decision.messages[0] === downstreamMsg
  && merged?.source?.kind === "plugin" && merged?.source?.plugin === "weave-mem-memory"
  && String(merged.content?.[0]?.text || "").includes("项目上下文")
  && String(merged.content?.[0]?.text || "").includes("dsh 当前步骤输入内容"),
  `n=${decision?.messages?.length}`);

// 暂存只消费一次：第二次 pre-step 只剩查询块（下游消息保留）
const decision2 = await handlers["agent/pre-step"](stepPayload, nextEnter);
const text2 = String(decision2?.messages?.[1]?.content?.[0]?.text || "");
check("暂存块仅消费一次", decision2.messages.length === 2 && !text2.includes("项目上下文") && text2.includes("dsh 当前步骤输入内容"));

// reject 短路：不得注入、不得吞掉 decision
const rejected = await handlers["agent/pre-step"](stepPayload, async () => ({ kind: "reject" }));
check("pre-step reject 透传", rejected?.kind === "reject");

// 自引用防递归：步骤输入仅 plugin 注入消息（含 dsh 运行时快照）→ 不注入
const pluginOnly = await handlers["agent/pre-step"]({
  agent: { session: { id: SID } },
  messages: [{ role: "user", source: { kind: "plugin", plugin: "@deepseek-ai/dsh-system-prompt" }, content: [{ type: "text", text: "Current runtime context snapshot" }] }],
  turn: 2, step: 1, signal: { aborted: false },
}, async () => ({ kind: "enter", messages: [] }));
check("仅 plugin 输入不递归注入",
  pluginOnly?.kind === "enter" && pluginOnly.messages.length === 0);

// session/event（emit，(session, event) 双参，真实事件形态）：捕获 + 去重 + plugin/工具过滤
await handlers["session/event"]({ id: SID }, { type: "user/message", time: 1, data: { id: "d1", role: "user", source: { kind: "user" }, content: [{ type: "text", text: "dsh 捕获内容一" }] } });
await handlers["session/event"]({ id: SID }, { type: "assistant/message", time: 2, data: { turn: 1, step: 1, message: { id: "d2", role: "assistant", source: { kind: "model" }, content: [{ type: "reasoning", text: "推理不入库" }, { type: "text", text: "dsh 捕获内容二" }] } } });
await handlers["session/event"]({ id: SID }, { type: "user/message", time: 3, data: { id: "d1", role: "user", source: { kind: "user" }, content: [{ type: "text", text: "dsh 捕获内容一" }] } }); // 去重
await handlers["session/event"]({ id: SID }, { type: "user/message", time: 4, data: { id: "d4", role: "user", source: { kind: "plugin", plugin: "@deepseek-ai/dsh-system-prompt" }, content: [{ type: "text", text: "运行时快照不应入库" }] } }); // plugin 源跳过
await handlers["session/event"]({ id: SID }, { type: "tool/call", time: 5, data: { id: "d5", name: "shell" } }); // 非消息忽略

await handlers["turn/end"]({ id: SID }, { type: "turn/end", time: 5 });

check("dispose 可调用", typeof dispose === "function");
console.log(`RESULT: ${failed ? "FAILED" : "ALL PASS"} (${failed} failed)`);
process.exit(failed ? 1 : 0);

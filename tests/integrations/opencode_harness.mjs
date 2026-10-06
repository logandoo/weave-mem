// tests/integrations/opencode_harness.mjs — OpenCode 插件契约测试（mock client/ctx + sink 服务）
// env: WEAVE_MEM_URL / WEAVE_MEM_TOKEN / HARNESS_SESSION
import { WeaveMemPlugin, setup } from "../../integrations/opencode/weave-mem-memory.js";

let failed = 0;
const check = (name, cond, detail = "") => {
  if (cond) console.log(`PASS  ${name} ${detail}`);
  else { failed++; console.log(`FAIL  ${name} ${detail}`); }
};

const SID = process.env.HARNESS_SESSION || "oc-test";

let messagesCalls = 0;
const client = {
  session: {
    messages: async () => {
      messagesCalls++;
      return {
        data: [
          { info: { id: "m-user-1", role: "user" }, parts: [{ type: "text", text: "opencode 用户问题内容" }] },
          { info: { id: "m-asst-1", role: "assistant" }, parts: [{ type: "text", text: "opencode 助手回答内容" }] },
          { info: { id: "m-tool-1", role: "tool" }, parts: [{ type: "text", text: "工具输出忽略" }] },
        ],
      };
    },
  },
};

const hooks = await WeaveMemPlugin({ client });
check("V1 hooks 形状", typeof hooks["chat.message"] === "function" && typeof hooks.event === "function");

// chat.message → synthetic 召回 part
const output = { parts: [{ type: "text", text: "请召回相关上下文" }], message: { id: "x" } };
await hooks["chat.message"]({ sessionID: SID }, output);
const injected = output.parts.find((p) => p.synthetic && String(p.text || "").includes("<weave-mem-context>"));
check("chat.message 注入 synthetic 块", !!injected, `parts=${output.parts.length}`);

// 短消息不召回
const output2 = { parts: [{ type: "text", text: "x" }] };
await hooks["chat.message"]({ sessionID: SID }, output2);
check("短消息跳过召回", output2.parts.length === 1);

// session.idle → 捕获（user+assistant，tool 忽略）
await hooks.event({ event: { type: "session.idle", properties: { sessionID: SID } } });
// 二次 idle：位置游标 → 不再 ingest（Python 侧断 sink 计数 + 本行断调用发生）
await hooks.event({ event: { type: "session.idle", properties: { sessionID: SID } } });
check("session.idle 二次执行", messagesCalls === 2, `messagesCalls=${messagesCalls}`);
console.log(`MESSAGES_CALLS=${messagesCalls}`);

// V2 setup（beta）：注册 prompt 钩子 + 注入
let v2Handler = null;
const registered = [];
const ctx = { session: { hook: async (kind, fn) => { registered.push(kind); v2Handler = fn; } } };
const dispose = await setup(ctx);
check("V2 setup 注册 prompt 钩子", registered.includes("prompt") && typeof v2Handler === "function");
const v2Event = { parts: [{ type: "text", text: "v2 召回查询内容" }] };
await v2Handler(v2Event);
check("V2 prompt 钩子注入", v2Event.parts.some((p) => p.synthetic && String(p.text || "").includes("<weave-mem-context>")));
check("V2 setup 返回 dispose", typeof dispose === "function");

console.log(`RESULT: ${failed ? "FAILED" : "ALL PASS"} (${failed} failed)`);
process.exit(failed ? 1 : 0);

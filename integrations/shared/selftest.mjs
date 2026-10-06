// shared/selftest.mjs — integrations/shared 纯函数自测（零依赖，node 直跑）
// 运行：node integrations/shared/selftest.mjs
import { mkdtempSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

let passed = 0, failed = 0;
const check = (name, cond, detail = "") => {
  if (cond) { passed++; console.log(`PASS  ${name} ${detail}`); }
  else { failed++; console.log(`FAIL  ${name} ${detail}`); }
};

const { loadConfig, isBypassed, globMatch } = await import("./config.mjs");
const { buildRecallBlock, stripRecallBlocks, RECALL_START, RECALL_END } = await import("./recall-block.mjs");
const { sanitizeText, extractTurnsFromJsonl } = await import("./capture.mjs");
const { loadState, saveState, stateKey } = await import("./state.mjs");
const { resolveAgentId } = await import("./agent-id.mjs");

// ---- config 优先级 env > file > defaults ----
{
  const home = mkdtempSync(join(tmpdir(), "wm-cfg-"));
  const dir = join(home, ".weave-mem");
  const fs = await import("node:fs");
  fs.mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, "config.json"), JSON.stringify({ baseUrl: "http://file:1", agentId: "file-agent", recallMaxChars: 999 }));
  const cfg = loadConfig({ WEAVE_MEM_URL: "http://env:2/" }, home);
  check("config: env 覆盖 file", cfg.baseUrl === "http://env:2", `got=${cfg.baseUrl}`);
  check("config: file 覆盖默认", cfg.agentId === "file-agent" && cfg.recallMaxChars === 999);
  const d = loadConfig({}, home);
  check("config: 默认值", d.autoRecall === true && d.autoCapture === true && d.minQueryLength === 3 && d.captureMaxChars === 4000);
  check("config: bool 解析", loadConfig({ WEAVE_MEM_AUTO_RECALL: "false" }, home).autoRecall === false);
  check("config: overrides 仅补空缺", (() => {
    const a = loadConfig({ WEAVE_MEM_URL: "http://env:3" }, home, { baseUrl: "http://ov:1", token: "ovtok" });
    return a.baseUrl === "http://env:3" && a.token === "ovtok"; // env 胜；env 未设的 token 用 overrides
  })());
  check("config: 退化值钳制", (() => {
    const c = loadConfig({ WEAVE_MEM_RECALL_MAX_CHARS: "0", WEAVE_MEM_CAPTURE_MAX_CHARS: "-5", WEAVE_MEM_REQUEST_TIMEOUT_MS: "0" }, home);
    return c.recallMaxChars >= 200 && c.captureMaxChars >= 100 && c.requestTimeoutMs >= 500;
  })());
  check("config: captureBatchSize 默认/钳制", (() => {
    const d = loadConfig({}, home);
    const c = loadConfig({ WEAVE_MEM_CAPTURE_BATCH_SIZE: "0" }, home);
    return d.captureBatchSize === 50 && c.captureBatchSize >= 1;
  })());
  check("resolveAgentId: env>harness", resolveAgentId(loadConfig({}, home), "claude-code") === "file-agent"
    && resolveAgentId(loadConfig({ WEAVE_MEM_AGENT_ID: "" }, tmpdir()), "codex") === "codex");
  rmSync(home, { recursive: true, force: true });
}
check("globMatch: 通配", globMatch("secret-*", "secret-x") && !globMatch("secret-*", "other"));
check("isBypassed: 模式命中", (() => {
  const cfg = { bypassPatterns: "secret-*,*/private/*" };
  return isBypassed(cfg, { sessionId: "secret-1" }) && isBypassed(cfg, { cwd: "/tmp/private/x" }) && !isBypassed(cfg, { sessionId: "normal" });
})());

// ---- recall block ----
{
  const b = buildRecallBlock("记忆A\n记忆B");
  check("block: 包裹标记", b.startsWith(RECALL_START) && b.endsWith(RECALL_END) && b.includes("记忆A"));
  check("block: 空上下文→null", buildRecallBlock("") === null && buildRecallBlock(null) === null);
  check("block: 超长截断并带省略", buildRecallBlock("x".repeat(100), { maxChars: 10 }).includes("…"));
  const polluted = `用户说：请查看。\n${b}\n后续内容`;
  const clean = stripRecallBlocks(polluted);
  check("strip: 剥离注入块", !clean.includes(RECALL_START) && !clean.includes("记忆A") && clean.includes("用户说") && clean.includes("后续内容"));
  check("strip: 无标记原样", stripRecallBlocks("普通文本") === "普通文本");
  check("strip: 未闭合标记截断", stripRecallBlocks(`前${RECALL_START}污染`) === "前");
}

// ---- capture ----
{
  check("sanitize: 剥离块+trim", sanitizeText(`x ${RECALL_START}污染${RECALL_END} y`) === "x  y");
  check("sanitize: 截断上限", sanitizeText("y".repeat(100), { maxChars: 10 }).length === 10);
  // CC 形态：{type, message:{role, content(string|array)}}
  const cc = [
    JSON.stringify({ type: "user", message: { role: "user", content: "第一条 用户消息" } }),
    JSON.stringify({ type: "assistant", message: { role: "assistant", content: [{ type: "text", text: "A 回复一" }, { type: "tool_use", name: "Bash" }] } }),
    JSON.stringify({ type: "summary", summary: "meta 行忽略" }),
    "not-json",
    JSON.stringify({ type: "user", message: { role: "user", content: [{ type: "text", text: "第二条 用户消息" }] } }),
  ].join("\n");
  const t1 = extractTurnsFromJsonl(cc);
  check("extract: CC 形态 3 轮", t1.length === 3 && t1[0].role === "user" && t1[1].text === "A 回复一" && t1[2].text.includes("第二条"),
    `n=${t1.length} ${JSON.stringify(t1.map(t => t.role))}`);
  // Codex/容忍形态：{role, content} / {type:"message", role, content}
  const cx = [
    JSON.stringify({ role: "user", content: "codex 用户" }),
    JSON.stringify({ type: "message", role: "assistant", content: "codex 助手" }),
    JSON.stringify({ type: "function_call", name: "shell" }),
  ].join("\n");
  const t2 = extractTurnsFromJsonl(cx);
  check("extract: Codex 形态 2 轮", t2.length === 2 && t2[0].role === "user" && t2[1].text === "codex 助手", `n=${t2.length}`);
  // Codex rollout 真实形态：{type:"response_item", payload:{type:"message", role, content:[{type:"input_text"|"output_text"}]}}
  const rollout = [
    JSON.stringify({ timestamp: "2026-10-06T00:00:00Z", type: "response_item",
      payload: { type: "message", role: "user", content: [{ type: "input_text", text: "真实 rollout 用户" }] } }),
    JSON.stringify({ timestamp: "2026-10-06T00:00:01Z", type: "response_item",
      payload: { type: "message", role: "assistant", content: [{ type: "output_text", text: "真实 rollout 助手" }] } }),
    JSON.stringify({ type: "session_meta", payload: { id: "x" } }),
  ].join("\n");
  const t3 = extractTurnsFromJsonl(rollout);
  check("extract: Codex rollout 真实形态 2 轮", t3.length === 2 && t3[0].text === "真实 rollout 用户" && t3[1].text === "真实 rollout 助手",
    `n=${t3.length}`);
  // 长会话：无 200 上限（游标停滞回归，A4.9 C1）
  const many = [];
  for (let i = 0; i < 250; i++) many.push(JSON.stringify({ role: "user", content: `第${i}轮消息内容` }));
  const t4 = extractTurnsFromJsonl(many.join("\n"));
  check("extract: 250 轮全量返回", t4.length === 250 && t4[249].text === "第249轮消息内容", `n=${t4.length}`);
}

// ---- state ----
{
  const home = mkdtempSync(join(tmpdir(), "wm-st-"));
  const key = stateKey("claude-code", "abc/def:123");
  check("state: key 净化", /^claude-code-[A-Za-z0-9._-]+$/.test(key) && !key.includes("/"));
  check("state: 净化碰撞抗性", stateKey("cc", "a/b") !== stateKey("cc", "a_b") && stateKey("cc", "a/b") === stateKey("cc", "a/b"));
  check("state: 缺省空对象", Object.keys(loadState(key, home)).length === 0);
  saveState(key, { capturedTurnCount: 3 }, home);
  check("state: 往返", loadState(key, home).capturedTurnCount === 3);
  check("state: 文件落盘", existsSync(join(home, ".weave-mem", "state", key + ".json")));
  rmSync(home, { recursive: true, force: true });
}

console.log(`\nRESULT: ${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);

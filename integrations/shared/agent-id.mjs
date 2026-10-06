// shared/agent-id.mjs
export const HARNESSES = ["claude-code", "codex", "opencode", "dsh"];

export function resolveAgentId(cfg, harness) {
  const configured = String(cfg?.agentId || "").trim();
  return configured || harness;
}

export function conversationId(harness, sessionId, sub = "") {
  const prefix = { "claude-code": "cc", codex: "cx", opencode: "oc", dsh: "dsh" }[harness] || harness;
  const base = `${prefix}-${sessionId || "default"}`;
  return sub ? `${base}:${sub}` : base;
}

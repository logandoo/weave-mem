// shared/capture.mjs — transcript 轮次抽取与净化
// 支持形态：Claude Code {type, message:{role, content}} · Codex rollout
// {type:"response_item", payload:{type:"message", role, content}} · 容忍 {role, content}
import { stripRecallBlocks } from "./recall-block.mjs";

export function sanitizeText(text, { maxChars = 4000 } = {}) {
  let s = stripRecallBlocks(text).replace(/\u0000/g, "");
  if (s.length > maxChars) s = s.slice(0, maxChars);
  return s.trim();
}

function textFromContent(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((p) => p && typeof p === "object"
        && (p.type === "text" || p.type === "input_text" || p.type === "output_text")
        && typeof p.text === "string")
      .map((p) => p.text)
      .join("\n");
  }
  return "";
}

function roleText(role, content) {
  if (role !== "user" && role !== "assistant") return null;
  const text = textFromContent(content);
  return text ? { role, text } : null;
}

function turnFromObject(obj) {
  if (!obj || typeof obj !== "object") return null;
  // Claude Code: {type:"user"|"assistant", message:{role, content}}
  if (obj.message && typeof obj.message === "object" && typeof obj.message.role === "string") {
    return roleText(obj.message.role, obj.message.content);
  }
  // Codex rollout: {type:"response_item", payload:{type:"message", role, content}}
  if (obj.payload && typeof obj.payload === "object" && obj.payload.type === "message") {
    return roleText(obj.payload.role, obj.payload.content);
  }
  // 容忍形态: {role, content} / {type:"message", role, content}
  if ((obj.role === "user" || obj.role === "assistant") && obj.content !== undefined) {
    return roleText(obj.role, obj.content);
  }
  return null;
}

export function extractTurnsFromJsonl(jsonlText) {
  const turns = [];
  for (const line of String(jsonlText ?? "").split(/\r?\n/)) {
    const t = line.trim();
    if (!t) continue;
    let obj;
    try {
      obj = JSON.parse(t);
    } catch {
      continue;
    }
    const turn = turnFromObject(obj);
    if (turn) turns.push(turn);
  }
  return turns;
}

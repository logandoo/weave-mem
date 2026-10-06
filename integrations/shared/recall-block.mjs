// shared/recall-block.mjs — 注入块构造与剥离（捕获时必须剥离，防自引用污染）
export const RECALL_START = "<weave-mem-context>";
export const RECALL_END = "</weave-mem-context>";

const USAGE = "Relevant memories from weave-mem (may be stale or not applicable to the current task; judge against the current conversation).";

export function buildRecallBlock(context, { maxChars = 1600 } = {}) {
  const body = String(context ?? "").trim();
  if (!body) return null;
  const clipped = body.length > maxChars ? body.slice(0, maxChars) + "\n…" : body;
  return `${RECALL_START}\n${USAGE}\n${clipped}\n${RECALL_END}`;
}

export function stripRecallBlocks(text) {
  let s = String(text ?? "");
  for (;;) {
    const a = s.indexOf(RECALL_START);
    if (a === -1) break;
    const b = s.indexOf(RECALL_END, a);
    s = b === -1 ? s.slice(0, a) : s.slice(0, a) + s.slice(b + RECALL_END.length);
  }
  return s.replace(/\n{3,}/g, "\n\n").trim();
}

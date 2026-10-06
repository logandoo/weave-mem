// shared/config.mjs — 配置解析（env > ~/.weave-mem/config.json > overrides > defaults）
// overrides 用于插件内联配置（dsh Cordis config 等）：仅当 env 与文件都未设置时生效。
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export const DEFAULTS = {
  baseUrl: "http://127.0.0.1:8202",
  token: "",
  agentId: "",
  autoRecall: true,
  autoCapture: true,
  recallMaxChars: 1600,
  captureMaxChars: 4000,
  captureBatchSize: 50,
  minQueryLength: 3,
  bypassPatterns: "",
  requestTimeoutMs: 8000,
  debug: false,
};

function asBool(v, d) {
  if (v === undefined || v === null || v === "") return d;
  return !["0", "false", "no", "off"].includes(String(v).toLowerCase());
}

function asInt(v, d, min) {
  const n = parseInt(String(v), 10);
  if (!Number.isFinite(n)) return d;
  return min !== undefined ? Math.max(min, n) : n;
}

export function loadConfig(env = process.env, home = homedir(), overrides = {}) {
  let file = {};
  try {
    const p = join(home, ".weave-mem", "config.json");
    if (existsSync(p)) file = JSON.parse(readFileSync(p, "utf8")) || {};
  } catch {
    file = {};
  }
  const pick = (envKey, fileKey) => {
    const ev = env[envKey];
    if (ev !== undefined && ev !== "") return ev;
    const fv = file[fileKey];
    if (fv !== undefined && fv !== null && fv !== "") return fv;
    const ov = overrides[fileKey];
    if (ov !== undefined && ov !== null && ov !== "") return ov;
    return undefined;
  };
  return {
    baseUrl: String(pick("WEAVE_MEM_URL", "baseUrl") ?? DEFAULTS.baseUrl).replace(/\/+$/, ""),
    token: String(pick("WEAVE_MEM_TOKEN", "token") ?? DEFAULTS.token),
    agentId: String(pick("WEAVE_MEM_AGENT_ID", "agentId") ?? DEFAULTS.agentId).trim(),
    autoRecall: asBool(pick("WEAVE_MEM_AUTO_RECALL", "autoRecall"), DEFAULTS.autoRecall),
    autoCapture: asBool(pick("WEAVE_MEM_AUTO_CAPTURE", "autoCapture"), DEFAULTS.autoCapture),
    recallMaxChars: asInt(pick("WEAVE_MEM_RECALL_MAX_CHARS", "recallMaxChars"), DEFAULTS.recallMaxChars, 200),
    captureMaxChars: asInt(pick("WEAVE_MEM_CAPTURE_MAX_CHARS", "captureMaxChars"), DEFAULTS.captureMaxChars, 100),
    captureBatchSize: asInt(pick("WEAVE_MEM_CAPTURE_BATCH_SIZE", "captureBatchSize"), DEFAULTS.captureBatchSize, 1),
    minQueryLength: asInt(pick("WEAVE_MEM_MIN_QUERY_LENGTH", "minQueryLength"), DEFAULTS.minQueryLength, 1),
    bypassPatterns: String(pick("WEAVE_MEM_BYPASS_SESSION_PATTERNS", "bypassPatterns") ?? DEFAULTS.bypassPatterns),
    requestTimeoutMs: asInt(pick("WEAVE_MEM_REQUEST_TIMEOUT_MS", "requestTimeoutMs"), DEFAULTS.requestTimeoutMs, 500),
    debug: asBool(pick("WEAVE_MEM_DEBUG", "debug"), DEFAULTS.debug),
  };
}

export function globMatch(pattern, value) {
  if (!pattern) return false;
  const re = new RegExp("^" + String(pattern).replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".") + "$");
  return re.test(String(value ?? ""));
}

export function isBypassed(cfg, { sessionId = "", cwd = "" } = {}) {
  const pats = String(cfg.bypassPatterns || "").split(",").map((s) => s.trim()).filter(Boolean);
  return pats.some((p) => globMatch(p, sessionId) || globMatch(p, cwd));
}

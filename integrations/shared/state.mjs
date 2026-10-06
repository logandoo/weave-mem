// shared/state.mjs — 捕获游标（原子写 + 冲突抗性键 + 有界清理）
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export function stateDir(home = homedir()) {
  return join(home, ".weave-mem", "state");
}

export function stateKey(harness, sessionId) {
  const raw = String(sessionId || "default");
  const safe = raw.replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 80);
  // 净化/截断会碰撞（a/b vs a_b、超长 id）→ 附原始串短哈希消歧
  const h = createHash("sha1").update(raw).digest("hex").slice(0, 8);
  return `${harness}-${safe}-${h}`;
}

const _MAX_STATE_FILES = 512;

function pruneStateDir(dir) {
  try {
    const files = readdirSync(dir).filter((f) => f.endsWith(".json"));
    if (files.length <= _MAX_STATE_FILES) return;
    const entries = files
      .map((f) => {
        try {
          return { f, m: statSync(join(dir, f)).mtimeMs };
        } catch {
          return { f, m: 0 };
        }
      })
      .sort((a, b) => a.m - b.m);
    for (const { f } of entries.slice(0, entries.length - _MAX_STATE_FILES + 128)) {
      try {
        rmSync(join(dir, f), { force: true });
      } catch {
        /* best-effort */
      }
    }
  } catch {
    /* best-effort */
  }
}

export function loadState(key, home = homedir()) {
  try {
    const p = join(stateDir(home), key + ".json");
    if (!existsSync(p)) return {};
    return JSON.parse(readFileSync(p, "utf8")) || {};
  } catch {
    return {};
  }
}

export function saveState(key, value, home = homedir()) {
  try {
    const dir = stateDir(home);
    mkdirSync(dir, { recursive: true });
    const p = join(dir, key + ".json");
    const tmp = `${p}.${process.pid}.tmp`;
    writeFileSync(tmp, JSON.stringify(value));
    renameSync(tmp, p);
    pruneStateDir(dir);
  } catch {
    /* fail-open */
  }
}

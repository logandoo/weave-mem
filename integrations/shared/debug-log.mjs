// shared/debug-log.mjs
import { appendFileSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export function debugLog(harness, message, { enabled = false, home = homedir() } = {}) {
  if (!enabled) return;
  try {
    const dir = join(home, ".weave-mem", "logs");
    mkdirSync(dir, { recursive: true });
    appendFileSync(join(dir, `${harness}.log`), `${new Date().toISOString()} ${message}\n`);
  } catch {
    /* fail-open */
  }
}

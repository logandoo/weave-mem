// shared/io.mjs — hook stdin/stdout 约定（永远 exit 0 由调用方保证）
export async function readStdin() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString("utf8");
}

export function parseHookInput(raw) {
  try {
    return JSON.parse(String(raw || "").trim() || "{}") || {};
  } catch {
    return {};
  }
}

export function writeJson(obj) {
  try {
    process.stdout.write(JSON.stringify(obj ?? {}));
  } catch {
    /* fail-open */
  }
}

export function argValue(name, argv = process.argv) {
  const hit = argv.find((a) => a.startsWith(`${name}=`));
  return hit ? hit.slice(name.length + 1) : "";
}

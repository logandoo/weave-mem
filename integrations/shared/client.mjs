// shared/client.mjs — weave-mem HTTP 薄客户端（global fetch；fail-open 返回 {ok:false}）
export async function api(cfg, method, path, body, { timeoutMs } = {}) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs ?? cfg.requestTimeoutMs);
  try {
    const headers = { "Content-Type": "application/json" };
    if (cfg.token) headers.Authorization = `Bearer ${cfg.token}`;
    if (cfg._agentId) headers["X-Agent-Id"] = cfg._agentId;
    const res = await fetch(cfg.baseUrl + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctl.signal,
    });
    const text = await res.text();
    if (!res.ok) return { ok: false, status: res.status, error: text.slice(0, 300) };
    let data = {};
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = { raw: text };
      }
    }
    return { ok: true, status: res.status, data };
  } catch (err) {
    return { ok: false, status: 0, error: String(err) };
  } finally {
    clearTimeout(timer);
  }
}

export async function recall(cfg, query, { timeoutMs } = {}) {
  const r = await api(cfg, "POST", "/api/memory/recall?include_meta=true", { query }, { timeoutMs });
  if (!r.ok) return null;
  return {
    context: String(r.data.context ?? ""),
    ids: Array.isArray(r.data?.meta?.memory_ids) ? r.data.meta.memory_ids : [],
    mode: r.data.mode,
  };
}

export async function ingest(cfg, { content, unitKind = "message", sourceIds = [], conversationId = "" }, { timeoutMs } = {}) {
  return api(
    cfg,
    "POST",
    "/api/memory/ingest",
    {
      content,
      unit_kind: unitKind,
      source_ids: sourceIds,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    },
    { timeoutMs },
  );
}

export async function health(cfg) {
  return api(cfg, "GET", "/healthz", undefined, { timeoutMs: 3000 });
}

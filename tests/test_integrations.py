"""weave-mem 验收：integrations 四端插件（Waves 2-4）。

结构：
  A. shared selftest（node，纯函数）
  B. Claude Code hooks（sink 录制断言 + 真服务 recall + fail-open）
  C. Codex hooks（输出形状差异 + sink 断言）
  D. OpenCode 插件契约 harness（node，mock client）
  E. dsh 插件契约 harness（node，mock ctx）

运行：./.venv/bin/python tests/test_integrations.py
日志：tests/test_integrations.log（bash 重定向）
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8202"
NODE = shutil.which("node")
SHARED = ROOT / "integrations" / "shared"
HOOKS = SHARED / "hooks"

passed = 0
failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name} {detail}")
    else:
        failed += 1
        print(f"FAIL  {name} {detail}")


# ---------------- Sink（录制 weave-mem 请求） ----------------
class _SinkState:
    requests: list = []  # {"path","headers","body"}


SINK = _SinkState()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: N802
        pass

    def _json(self, obj, code=200):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            self._json({"status": "ok"})
        else:
            self._json({}, 404)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode("utf-8") if n else ""
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {"raw": raw}
        SINK.requests.append({
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": data,
        })
        if self.path.startswith("/api/memory/recall"):
            q = str(data.get("query") or "")
            self._json({"mode": "text", "query": q,
                        "context": f"SINK召回标记ABC\n- 关于“{q[:40]}”的共享记忆",
                        "meta": {"memory_ids": ["sink-id-1"], "top_gate_score": 0.5}})
        elif self.path.startswith("/api/memory/ingest"):
            if not data.get("content"):
                self._json({"detail": "content required"}, 422)
            else:
                self._json({"unit_id": "sink-unit", "ingested": True})
        else:
            self._json({}, 404)


def sink_requests(kind: str) -> list:
    return [r for r in SINK.requests if r["path"].startswith(f"/api/memory/{kind}")]


def reset_sink() -> None:
    SINK.requests.clear()


# ---------------- helpers ----------------
def run_node(script: Path, args=None, stdin_obj=None, env_extra=None, timeout=60):
    env = dict(os.environ)
    env.update(env_extra or {})
    p = subprocess.run(
        [NODE, str(script), *(args or [])],
        input=json.dumps(stdin_obj) if stdin_obj is not None else "",
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    return p


def hook(name: str, harness: str, stdin_obj: dict, env_extra: dict):
    return run_node(HOOKS / name, [f"--harness={harness}"], stdin_obj, env_extra)


def write_transcript(path: Path, turns: list) -> None:
    path.write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in turns), encoding="utf-8")


def main() -> None:
    global passed, failed
    if not NODE:
        print("SKIP: node not found")
        sys.exit(0)

    tmp_home = Path(tempfile.mkdtemp(prefix="wm-it-home-"))
    work = Path(tempfile.mkdtemp(prefix="wm-it-work-"))
    sink = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    sink_port = sink.server_address[1]
    threading.Thread(target=sink.serve_forever, daemon=True).start()
    sink_url = f"http://127.0.0.1:{sink_port}"

    base_env = {
        "HOME": str(tmp_home),
        "WEAVE_MEM_URL": sink_url,
        "WEAVE_MEM_TOKEN": "wm_sink_token",
        "WEAVE_MEM_REQUEST_TIMEOUT_MS": "4000",
    }
    suffix = uuid.uuid4().hex[:8]

    try:
        # ---------- A. shared selftest ----------
        p = subprocess.run([NODE, str(SHARED / "selftest.mjs")],
                           capture_output=True, text=True, timeout=60,
                           env={**os.environ, "HOME": str(tmp_home)})
        check("A shared selftest 27/27", p.returncode == 0 and "27 passed, 0 failed" in p.stdout,
              f"exit={p.returncode}")

        # ---------- B. Claude Code ----------
        reset_sink()
        prompt = f"CC召回查询问题{suffix}"
        p = hook("auto-recall.mjs", "claude-code",
                 {"prompt": prompt, "session_id": f"ccr-{suffix}", "hook_event_name": "UserPromptSubmit"},
                 base_env)
        try:
            out = json.loads(p.stdout or "{}")
        except ValueError:
            out = {}
        block = (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""
        check("B1 CC recall 注入块", p.returncode == 0 and "SINK召回标记ABC" in block, f"exit={p.returncode}")
        rec_reqs = [r for r in sink_requests("recall") if r["body"].get("query") == prompt]
        check("B2 CC recall 请求形状",
              len(rec_reqs) == 1
              and rec_reqs[0]["headers"].get("x-agent-id") == "claude-code"
              and rec_reqs[0]["headers"].get("authorization") == "Bearer wm_sink_token",
              f"n={len(rec_reqs)}")

        # 短 prompt 跳过
        reset_sink()
        p = hook("auto-recall.mjs", "claude-code", {"prompt": "x", "session_id": f"ccr-{suffix}"}, base_env)
        check("B3 CC 短消息跳过", (p.stdout or "").strip() == "{}" and len(sink_requests("recall")) == 0)

        # fail-open：server 不可达
        dead_env = {**base_env, "WEAVE_MEM_URL": "http://127.0.0.1:9"}
        p = hook("auto-recall.mjs", "claude-code", {"prompt": f"failopen{suffix}问题"}, dead_env)
        check("B4 CC fail-open exit0 空输出", p.returncode == 0 and (p.stdout or "").strip() == "{}",
              f"exit={p.returncode} stdout={p.stdout[:40]!r}")

        # capture：增量 + 剥离 + 去重 + bypass
        reset_sink()
        sid = f"ccc-{suffix}"
        tfile = work / f"{sid}.jsonl"
        write_transcript(tfile, [
            {"type": "user", "message": {"role": "user",
             "content": f"CC用户消息一{suffix} <weave-mem-context>污染内容</weave-mem-context> 尾部"}},
            {"type": "assistant", "message": {"role": "assistant",
             "content": [{"type": "text", "text": f"CC助手回复一{suffix}"}]}},
        ])
        stdin = {"session_id": sid, "transcript_path": str(tfile), "hook_event_name": "Stop"}
        p1 = hook("auto-capture.mjs", "claude-code", stdin, base_env)
        caps = [r for r in sink_requests("ingest") if r["body"].get("conversation_id") == f"cc-{sid}"]
        check("B5 CC capture 落两条", p1.returncode == 0 and (p1.stdout or "").strip() == "{}" and len(caps) == 2,
              f"n={len(caps)}")
        check("B6 CC capture 剥离注入块",
              len(caps) == 2 and "污染内容" not in caps[0]["body"]["content"] and f"CC用户消息一{suffix}" in caps[0]["body"]["content"])
        check("B7 CC capture 归因头与 source_ids",
              len(caps) == 2
              and caps[0]["headers"].get("x-agent-id") == "claude-code"
              and caps[0]["body"]["source_ids"] == [f"claude-code:{sid}:0"]
              and caps[0]["body"]["unit_kind"] == "message")

        n_before = len(sink_requests("ingest"))
        hook("auto-capture.mjs", "claude-code", stdin, base_env)
        check("B8 CC capture 幂等（游标去重）", len(sink_requests("ingest")) == n_before)

        with tfile.open("a", encoding="utf-8") as f:
            f.write("\n" + json.dumps({"type": "user", "message": {"role": "user", "content": f"CC用户消息三{suffix}"}}, ensure_ascii=False))
        hook("auto-capture.mjs", "claude-code", stdin, base_env)
        caps3 = [r for r in sink_requests("ingest") if r["body"].get("conversation_id") == f"cc-{sid}"]
        check("B9 CC capture 增量第三条", len(caps3) == 3 and caps3[2]["body"]["source_ids"] == [f"claude-code:{sid}:2"],
              f"n={len(caps3)}")

        sid_bypass = f"ccb-{suffix}"
        bfile = work / f"{sid_bypass}.jsonl"
        write_transcript(bfile, [{"type": "user", "message": {"role": "user", "content": f"bypass 内容{suffix}"}}])
        n_before = len(sink_requests("ingest"))
        hook("auto-capture.mjs", "claude-code",
             {"session_id": sid_bypass, "transcript_path": str(bfile)},
             {**base_env, "WEAVE_MEM_BYPASS_SESSION_PATTERNS": sid_bypass})
        check("B10 CC bypass 生效", len(sink_requests("ingest")) == n_before)

        # B11 真服务 recall（文本通道 + 作用域头 + PAT 认证）
        uname = f"itg_{suffix}"
        with httpx.Client(base_url=BASE, timeout=30) as c:
            c.post("/api/auth/register", json={"username": uname, "password": "test123"})
            tok = c.post("/api/auth/login", json={"username": uname, "password": "test123"}).json()["access_token"]
            pat = c.post("/api/auth/tokens", headers={"Authorization": f"Bearer {tok}"},
                         json={"name": "itg", "agent_id": "claude-code"}).json()["token"]
            ah = {"Authorization": f"Bearer {pat}", "X-Agent-Id": "claude-code"}
            # 预热 6 条越过冷启动回退阈值（recallable > 5 → 正常管线）
            for i in range(6):
                c.post("/api/memory/concepts", headers=ah,
                       json={"canonical_name": f"ITG预热{suffix}_{i}", "description_short": f"背景{i}"})
            marker = f"ITG标记{suffix}"
            c.post("/api/memory/concepts", headers=ah,
                   json={"canonical_name": marker, "description_short": f"集成测试标记{suffix}"})
        p = hook("auto-recall.mjs", "claude-code", {"prompt": marker, "session_id": f"ccreal-{suffix}"},
                 {**base_env, "WEAVE_MEM_URL": BASE, "WEAVE_MEM_TOKEN": pat})
        out = json.loads(p.stdout or "{}")
        block = (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""
        check("B11 CC 真服务 recall 命中", marker in block, f"exit={p.returncode}")

        # B12 SessionStart（sink）：项目名召回 + bypass
        reset_sink()
        p = hook("session-start.mjs", "claude-code",
                 {"session_id": f"ccs-{suffix}", "cwd": f"/tmp/proj-{suffix}", "hook_event_name": "SessionStart"},
                 base_env)
        out = json.loads(p.stdout or "{}")
        block = (out.get("hookSpecificOutput") or {}).get("additionalContext") or ""
        sr = sink_requests("recall")
        check("B12 CC session-start 注入", p.returncode == 0 and "SINK召回标记ABC" in block
              and len(sr) == 1 and f"proj-{suffix}" in str(sr[0]["body"].get("query")),
              f"n={len(sr)}")
        reset_sink()
        hook("session-start.mjs", "claude-code",
             {"session_id": f"ccs-{suffix}", "cwd": "/tmp/x"},
             {**base_env, "WEAVE_MEM_BYPASS_SESSION_PATTERNS": f"ccs-{suffix}"})
        check("B12b session-start bypass", len(sink_requests("recall")) == 0)

        # B13 长会话（>200 轮）不因上限停滞：批次上限显式放大
        reset_sink()
        sid_long = f"ccl-{suffix}"
        tfile_long = work / f"{sid_long}.jsonl"
        turns_long = [{"type": "user", "message": {"role": "user", "content": f"长会话第{i}轮内容{suffix}"}} for i in range(250)]
        write_transcript(tfile_long, turns_long)
        long_env = {**base_env, "WEAVE_MEM_CAPTURE_BATCH_SIZE": "500"}
        stdin_long = {"session_id": sid_long, "transcript_path": str(tfile_long), "hook_event_name": "Stop"}
        hook("auto-capture.mjs", "claude-code", stdin_long, long_env)
        caps_long = [r for r in sink_requests("ingest") if r["body"].get("conversation_id") == f"cc-{sid_long}"]
        hook("auto-capture.mjs", "claude-code", stdin_long, long_env)
        caps_long2 = [r for r in sink_requests("ingest") if r["body"].get("conversation_id") == f"cc-{sid_long}"]
        with tfile_long.open("a", encoding="utf-8") as f:
            for i in (250, 251):
                f.write("\n" + json.dumps({"type": "user", "message": {"role": "user", "content": f"长会话第{i}轮内容{suffix}"}}, ensure_ascii=False))
        hook("auto-capture.mjs", "claude-code", stdin_long, long_env)
        caps_long3 = [r for r in sink_requests("ingest") if r["body"].get("conversation_id") == f"cc-{sid_long}"]
        check("B13 长会话 250 轮全量捕获且续传", len(caps_long) == 250 and len(caps_long2) == 250 and len(caps_long3) == 252,
              f"n1={len(caps_long)} n2={len(caps_long2)} n3={len(caps_long3)}")

        # ---------- C. Codex ----------
        reset_sink()
        prompt_cx = f"CX召回查询问题{suffix}"
        p = hook("auto-recall.mjs", "codex",
                 {"prompt": prompt_cx, "session_id": f"cxr-{suffix}", "hook_event_name": "UserPromptSubmit"},
                 base_env)
        out = json.loads(p.stdout or "{}")
        check("C1 Codex recall 输出形状", p.returncode == 0
              and "decision" not in out
              and "SINK召回标记ABC" in ((out.get("hookSpecificOutput") or {}).get("additionalContext") or ""),
              f"keys={sorted(out.keys())}")
        rec_reqs = [r for r in sink_requests("recall") if r["body"].get("query") == prompt_cx]
        check("C2 Codex 作用域头", len(rec_reqs) == 1 and rec_reqs[0]["headers"].get("x-agent-id") == "codex")

        sid_cx = f"cxc-{suffix}"
        tfile_cx = work / f"{sid_cx}.jsonl"
        write_transcript(tfile_cx, [
            {"role": "user", "content": f"Codex用户消息{suffix}"},
            {"type": "message", "role": "assistant", "content": f"Codex助手回复{suffix}"},
        ])
        p = hook("auto-capture.mjs", "codex",
                 {"session_id": sid_cx, "transcript_path": str(tfile_cx), "hook_event_name": "Stop"}, base_env)
        caps_cx = [r for r in sink_requests("ingest") if r["body"].get("conversation_id") == f"cx-{sid_cx}"]
        check("C3 Codex capture 输出 {} 且落库", (p.stdout or "").strip() == "{}" and len(caps_cx) == 2,
              f"stdout={(p.stdout or '')[:10]!r} n={len(caps_cx)}")
        check("C4 Codex 捕获头/会话", len(caps_cx) == 2
              and caps_cx[0]["headers"].get("x-agent-id") == "codex"
              and caps_cx[1]["body"]["source_ids"] == [f"codex:{sid_cx}:1"])

        # C5 Codex rollout 真实 transcript 形态（response_item/payload/input_text|output_text）
        reset_sink()
        sid_ro = f"cxr2-{suffix}"
        tfile_ro = work / f"{sid_ro}.jsonl"
        write_transcript(tfile_ro, [
            {"timestamp": "2026-10-06T00:00:00Z", "type": "response_item",
             "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"Codex真实形态用户{suffix}"}]}},
            {"timestamp": "2026-10-06T00:00:01Z", "type": "response_item",
             "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": f"Codex真实形态助手{suffix}"}]}},
            {"type": "session_meta", "payload": {"id": sid_ro}},
        ])
        hook("auto-capture.mjs", "codex", {"session_id": sid_ro, "transcript_path": str(tfile_ro)}, base_env)
        caps_ro = [r for r in sink_requests("ingest") if r["body"].get("conversation_id") == f"cx-{sid_ro}"]
        check("C5 Codex rollout 形态捕获", len(caps_ro) == 2
              and f"Codex真实形态用户{suffix}" in caps_ro[0]["body"]["content"], f"n={len(caps_ro)}")

        # ---------- D. OpenCode harness ----------
        reset_sink()
        oc_sid = f"oc-{suffix}"
        p = run_node(ROOT / "tests" / "integrations" / "opencode_harness.mjs",
                     env_extra={**base_env, "HARNESS_SESSION": oc_sid})
        check("D1 OpenCode harness 全绿", p.returncode == 0 and "ALL PASS" in p.stdout
              and "MESSAGES_CALLS=2" in p.stdout,
              f"exit={p.returncode} {(p.stdout or '')[-120:]}")
        oc_caps = [r for r in sink_requests("ingest") if r["body"].get("conversation_id") == f"oc-{oc_sid}"]
        check("D2 OpenCode 捕获 2 条（tool 忽略 + 去重）", len(oc_caps) == 2
              and all(r["headers"].get("x-agent-id") == "opencode" for r in oc_caps), f"n={len(oc_caps)}")
        oc_queries = sorted(r["body"].get("query") for r in sink_requests("recall"))
        check("D3 OpenCode V1+V2 recall 两查询", f"请召回相关上下文" in oc_queries and "v2 召回查询内容" in oc_queries,
              f"queries={oc_queries}")

        # ---------- E. dsh harness ----------
        reset_sink()
        dsh_sid = f"dsh-{suffix}"
        p = run_node(ROOT / "tests" / "integrations" / "dsh_harness.mjs",
                     env_extra={**base_env, "HARNESS_SESSION": dsh_sid})
        check("E1 dsh harness 全绿", p.returncode == 0 and "ALL PASS" in p.stdout,
              f"exit={p.returncode} {(p.stdout or '')[-120:]}")
        dsh_caps = [r for r in sink_requests("ingest") if r["body"].get("conversation_id") == f"dsh-{dsh_sid}"]
        check("E2 dsh 捕获 2 条（同 id 去重 + tool 忽略）", len(dsh_caps) == 2
              and all(r["headers"].get("x-agent-id") == "dsh" for r in dsh_caps), f"n={len(dsh_caps)}")
        dsh_recalls = sink_requests("recall")
        check("E3 dsh session-start 暂存 + 两次真实 pre-step 召回", len(dsh_recalls) == 3
              and all(r["headers"].get("x-agent-id") == "dsh" for r in dsh_recalls), f"n={len(dsh_recalls)}")

        # E4 durable 队列：sink 可达时成功即 ack → 逻辑无未确认记录（ops fold）
        def pending_unacked(path: Path) -> list:
            if not path.exists():
                return []
            adds, acks = {}, set()
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    op = json.loads(line)
                except ValueError:
                    continue
                if op.get("op") == "add" and op.get("record", {}).get("sourceIds"):
                    adds[op["record"]["sourceIds"][0]] = op["record"]
                elif op.get("op") == "ack":
                    acks.update(op.get("ids") or [])
            return [k for k in adds if k not in acks]

        pend = tmp_home / ".weave-mem" / "state" / "dsh-pending.jsonl"
        check("E4 dsh 队列无未确认记录", pending_unacked(pend) == [], f"unacked={pending_unacked(pend)[:2]}")

        # E5 进程退出恢复：预置未确认 add → 下次 session-start flush 补发并 ack
        rec = {"content": f"退出恢复补发内容{suffix}",
               "sourceIds": [f"dsh:dead-session:{suffix}"],
               "conversationId": f"dsh-dead-{suffix}"}
        pend.parent.mkdir(parents=True, exist_ok=True)
        with pend.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"op": "add", "record": rec}, ensure_ascii=False) + "\n")
        reset_sink()
        p = run_node(ROOT / "tests" / "integrations" / "dsh_harness.mjs",
                     env_extra={**base_env, "HARNESS_SESSION": f"dsh2-{suffix}"})
        got = [r for r in sink_requests("ingest") if r["body"].get("content") == rec["content"]]
        check("E5 退出恢复补发", p.returncode == 0 and len(got) == 1
              and got[0]["body"].get("conversation_id") == rec["conversationId"], f"n={len(got)}")
        check("E5b 补发后队列清空", pending_unacked(pend) == [])
    finally:
        sink.shutdown()
        shutil.rmtree(tmp_home, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)

    print(f"\nRESULT: {passed} passed, {failed} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

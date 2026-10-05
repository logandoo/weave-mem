"""weave-mem 验收测试 9：SQLite 降级模式（config type=sqlite）。

前置：服务以 SQLite 模式运行（config.toml [database] type="sqlite"）。
用例（SQLite 语义断言——PG 假设的套件在此不适用）：
1. /healthz ok + pgvector=false（降级标记）
2. 注册/登录 + 概念 CRUD 全链
3. 召回：写入 8 概念 → 立即召回命中（文本通道 mode=text，SQLite 无向量）
4. ingest 503（无 embedding provider——SQLite 下同样降级）
5. clarifications/process 结构（detected 键）
6. episodes/dreams 列表结构
7. GDPR 擦除（独立用户）
8. admin role：sqlite3 直改（无 psql）
"""
import asyncio
import json
import sqlite3
import subprocess
import sys
import uuid
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8202"


def _resolve_db_path() -> Path:
    """夹具 DB 路径跟随 config（[database].path 解析，与 database_url 同逻辑）——
    旧常量写死 weave_mem_sqlite_test.db 与服务默认 weave_mem.db 不一致，
    admin sqlite3 提升打错库 → 403×2（ADR D-4 预存失败根因）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    from app.core.config import get_config
    cfg = get_config()
    if cfg.database_type != "sqlite":
        raise SystemExit("前置不满足：config.toml [database] type 必须为 sqlite"
                         "（否则 admin 提升会打错库——ADR D-4 复现）")
    raw = str((cfg._config.get("database") or {}).get("path", "weave_mem.db"))
    pp = Path(raw)
    if not pp.is_absolute():
        pp = Path(cfg.config_path).resolve().parent / raw
    return pp.resolve()


DB_PATH = _resolve_db_path()
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


async def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    uname = f"sqlite_{suffix}"
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.get("/healthz")
        body = r.json()
        check("healthz ok + pgvector=false", r.status_code == 200 and body.get("pgvector") is False,
              f"status={r.status_code} pgvector={body.get('pgvector')}")

        r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
        check("register", r.status_code == 201, f"status={r.status_code}")
        r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
        token = r.json()["access_token"]
        uid = r.json()["user"]["id"]
        h = {"Authorization": f"Bearer {token}"}

        # 概念写入 ×8 → 立即召回命中（SQLite 无向量，文本通道）
        statuses = []
        for i in range(8):
            r = await c.post("/api/memory/concepts", headers=h, json={
                "canonical_name": f"sqlit{ suffix }主题{i}号", "description_short": "SQLite 验证", "importance": 0.9})
            statuses.append(r.status_code)
        check("概念写入 ×8", all(s == 201 for s in statuses), f"statuses={statuses}")

        r = await c.get("/api/memory/concepts", headers=h)
        check("概念列表 8", r.json().get("count") == 8, f"count={r.json().get('count')}")

        r = await c.post("/api/memory/recall", headers=h, json={"query": f"sqlit{ suffix } 主题"})
        body = r.json()
        ctx = body.get("context") or ""
        check("立即召回命中（文本通道）", body.get("mode") == "text" and f"sqlit{ suffix }主题0号" in ctx,
              f"mode={body.get('mode')} hit={f'sqlit{ suffix }主题0号' in ctx} ctx_len={len(ctx)}")

        r = await c.post("/api/memory/recall?include_meta=true", headers=h, json={"query": f"sqlit{ suffix } 主题"})
        check("recall meta 结构", "meta" in r.json() and set(("memory_ids", "top_gate_score")) <= set(r.json()["meta"].keys()),
              f"keys={list(r.json().keys())}")

        # ingest 503（无 provider——SQLite 与 PG 同降级）
        r = await c.post("/api/memory/ingest", headers=h, json={"content": "SQLite 摄入验证"})
        check("ingest 503", r.status_code == 503, f"status={r.status_code}")

        # clarifications 结构
        r = await c.post("/api/memory/clarifications/process", headers=h, json={"user_message": "今天天气不错"})
        check("clarify 无信号词", r.json().get("detected") is False, f"body={r.json()}")

        # episodes/dreams 列表
        r = await c.get("/api/memory/episodes", headers=h)
        check("episodes 列表", r.status_code == 200 and "episodes" in r.json(), f"status={r.status_code}")
        r = await c.get("/api/memory/dreams", headers=h)
        check("dreams 列表", r.status_code == 200 and "dreams" in r.json(), f"status={r.status_code}")

        # admin role（sqlite3 直改——SQLite 无 psql）
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("UPDATE users SET role='admin' WHERE id=?", (uid,))
        conn.commit()
        conn.close()
        r = await c.get("/api/admin/users", headers=h)
        check("admin users（sqlite3 提升）", r.status_code == 200, f"status={r.status_code}")
        r = await c.post("/api/admin/reload-config", headers=h)
        check("reload-config admin", r.status_code == 200 and r.json().get("ok") is True,
              f"status={r.status_code}")

        # GDPR 擦除（独立用户）
        gname = f"sqlg_{suffix}"
        await c.post("/api/auth/register", json={"username": gname, "password": "test123"})
        r = await c.post("/api/auth/login", json={"username": gname, "password": "test123"})
        gh = {"Authorization": f"Bearer {r.json()['access_token']}"}
        r = await c.delete("/api/memory/all", headers=gh)
        check("GDPR 擦除", r.status_code == 200, f"status={r.status_code}")

    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())

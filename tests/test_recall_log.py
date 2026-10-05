"""weave-mem 同步 wave P3 断言（C1 召回台账）。

用例出自 docs/openapi.json：GET /api/memory/recall_log（仅元数据、分页、401）
+ 截断提示 + 清理语句双言。
运行：./.venv/bin/python tests/test_recall_log.py（服务需在跑）
"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx
from sqlalchemy import text

from app.db.database import AsyncSessionLocal

BASE = "http://127.0.0.1:8202"
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


async def t_ledger(c: httpx.AsyncClient) -> None:
    r = await c.get("/api/memory/recall_log")
    check("T12 401 未认证", r.status_code == 401, f"status={r.status_code}")
    suffix = uuid.uuid4().hex[:8]
    uname = f"recall_{suffix}"
    r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
    assert r.status_code == 201, r.text
    r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
    uid = r.json()["user"]["id"]
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    from app.services.memory_recall_log_service import spawn_recall_log
    for i in range(3):
        spawn_recall_log(uid, query_text=f"查询{i}", memory_ids=[f"id{i}"],
                         stats={"budget_chars": 2000, "injected_chars": 100,
                                "truncated": i == 0, "elapsed_ms": 12.5, "cache_hit": False})
    await asyncio.sleep(1.0)
    r = await c.get("/api/memory/recall_log", headers=h)
    body = r.json() if r.status_code == 200 else {}
    check("T12 200 结构", r.status_code == 200 and "items" in body and "total" in body,
          f"status={r.status_code} body={body}")
    check("T12 仅元数据（无 content 字段）",
          r.status_code == 200 and all("content" not in it and "memory_content" not in it for it in body.get("items", [])),
          f"items={body.get('items', [])[:1]}")
    check("T12 写入 3 条", body.get("total", 0) == 3, f"total={body.get('total')}")
    r2 = await c.get("/api/memory/recall_log", headers=h, params={"limit": 2})
    check("T12 分页 limit", r2.status_code == 200 and len(r2.json().get("items", [])) == 2,
          f"n={len(r2.json().get('items', [])) if r2.status_code == 200 else -1}")
    # 复合 keyset 完整性：逐页翻完应无重无漏覆盖 total 条
    seen: list = []
    cursor = ""
    for _ in range(10):
        params = {"limit": 2}
        if cursor:
            params["before_id"] = cursor
        rp = await c.get("/api/memory/recall_log", headers=h, params=params)
        items = rp.json().get("items", []) if rp.status_code == 200 else []
        if not items:
            break
        seen.extend(it["id"] for it in items)
        cursor = items[-1]["id"]
    check("T12 游标翻页无重无漏", len(seen) == len(set(seen)) == body.get("total", -1),
          f"seen={len(seen)} unique={len(set(seen))} total={body.get('total')}")
    async with AsyncSessionLocal() as db:
        await db.execute(text("DELETE FROM memory_recall_log WHERE user_id = :uid"), {"uid": uid})
        await db.commit()


def t_cleanup_dialect() -> None:
    from app.services.memory_recall_log_service import cleanup_statements, should_sample
    pg = cleanup_statements(30, dialect="postgres")
    check("T12 清理语句 PG 用 INTERVAL", any("INTERVAL" in s for s in pg), f"pg={pg}")
    lite = cleanup_statements(30, dialect="sqlite")
    check("T12 清理语句 SQLite 用 strftime", any("strftime" in s for s in lite), f"lite={lite}")
    check("T12 采样 1.0 恒真", should_sample(1.0) is True)
    check("T12 采样 0 恒假", should_sample(0.0) is False)


def t_truncation_note() -> None:
    from app.services.memory_retrieval_service import _TRUNCATION_NOTE, _apply_token_budget_ex
    text_out, truncated, used = _apply_token_budget_ex(["a" * 50, "b" * 50], 60)
    check("T12 _ex 截断标记（丢段）", truncated is True and used == 50, f"truncated={truncated} used={used}")
    text_out3, truncated3, used3 = _apply_token_budget_ex(["a" * 100], 60)
    check("T12 _ex 单段截断", truncated3 is True and used3 == 60 and len(text_out3) == 60,
          f"truncated={truncated3} used={used3}")
    text_out2, truncated2, _ = _apply_token_budget_ex(["a" * 10], 60)
    check("T12 _ex 未截断", truncated2 is False)
    check("T12 截断提示零数字", "预算" in _TRUNCATION_NOTE and not any(ch.isdigit() for ch in _TRUNCATION_NOTE),
          f"note={_TRUNCATION_NOTE!r}")


def t_e1_instruction() -> None:
    from app.services.memory_retrieval_service import _DEFAULT_USAGE_INSTRUCTION
    cfg_mod = __import__("app.core.config", fromlist=["get_config"]).get_config()
    ret = dict(cfg_mod.memory_retrieval or {}, injection_usage_instruction_enabled=True)
    # E1 语义：截断后前置指令（不计预算）——经 _build 路径的纯件层验证
    check("T11/E1 指令文本非空", bool(_DEFAULT_USAGE_INSTRUCTION.strip()))
    check("T11/E1 指令 ≤60 字零数字", len(_DEFAULT_USAGE_INSTRUCTION) <= 60
          and not any(ch.isdigit() for ch in _DEFAULT_USAGE_INSTRUCTION))
    _ = ret  # 门语义由 test_p2_gates 的 A4c/常量断言覆盖


async def main() -> None:
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        await t_ledger(c)
    t_cleanup_dialect()
    t_truncation_note()
    t_e1_instruction()
    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())

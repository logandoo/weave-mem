"""weave-mem 验收测试 4：clarification auto_apply 行为链（mock LLM，服务层）。

覆盖 A4.9 Important-3：process_clarification 的 confidence>=0.8 自动应用分支
（HTTP 测试无法覆盖——无 LLM provider 时降级为 null）：
1. mock _memory_llm 返回 refine JSON（confidence 0.9）
2. 调服务层 process_clarification
3. DB 断言：memory_clarifications 落库 applied=TRUE + 概念 description_short 更新 + 审计键写入

运行：./.venv/bin/python tests/test_clarify_apply.py
"""
import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx

from app.db.database import AsyncSessionLocal
from app.services.memory_clarification_service import process_clarification
from app.services import memory_llm_factory as mlf

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


class FakeLLM:
    def __init__(self, payload: dict):
        self._payload = payload

    async def complete_chat(self, messages, **kwargs):
        return json.dumps(self._payload, ensure_ascii=False)


async def main() -> None:
    suffix = uuid.uuid4().hex[:8]
    uname = f"clarapply_{suffix}"
    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as c:
        r = await c.post("/api/auth/register", json={"username": uname, "password": "test123"})
        check("register", r.status_code == 201, f"status={r.status_code}")
        r = await c.post("/api/auth/login", json={"username": uname, "password": "test123"})
        token = r.json()["access_token"]
        uid = r.json()["user"]["id"]
        h = {"Authorization": f"Bearer {token}"}

        r = await c.post("/api/memory/concepts", headers=h, json={
            "canonical_name": f"偏好主题{suffix}",
            "description_short": "旧描述",
            "importance": 0.8,
        })
        check("创建概念", r.status_code == 201, f"status={r.status_code}")

        # mock LLM：refine + confidence 0.9（> 0.8 阈值 → auto_apply）
        orig = mlf._memory_llm
        mlf._memory_llm = lambda kind: FakeLLM({
            "is_correction": True,
            "correction_type": "refine",
            "affected_concept_ids": [r.json()["id"]],
            "new_description": "修正后的新描述",
            "confidence": 0.9,
        })
        try:
            async with AsyncSessionLocal() as db:
                result = await process_clarification(
                    db, uid, "其实不是那样，正确的是新的描述", None, None,
                )
                check("process_clarification 返回 parsed", result is not None
                      and result.get("correction_type") == "refine", f"result={result}")

                from sqlalchemy import text
                row = (await db.execute(text(
                    "SELECT correction_type, applied FROM memory_clarifications WHERE user_id = :uid ORDER BY created_at DESC LIMIT 1"
                ), {"uid": uid})).fetchone()
                check("memory_clarifications 落库 applied=TRUE",
                      row is not None and row[1] is True, f"row={row}")

                concept = (await db.execute(text(
                    "SELECT description_short, metadata_json FROM memory_concepts WHERE user_id = :uid ORDER BY created_at DESC LIMIT 1"
                ), {"uid": uid})).fetchone()
                check("概念 description_short 已更新",
                      concept is not None and concept[0] == "修正后的新描述", f"desc={concept[0] if concept else None}")
                meta_ok = concept is not None and concept[1] and "audit_old_description" in concept[1]
                check("审计键 audit_old_description 写入", meta_ok, f"meta={concept[1] if concept else None}")
        finally:
            mlf._memory_llm = orig

    print(f"\n==== 结果: {passed} passed, {failed} failed ====")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())

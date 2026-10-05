"""C1 召回台账（上游 beda68eb4 移植，2026-10-05 同步 wave）。

只存**元数据**（query_hash/候选 id/档位分/gate 分/预算/截断/耗时/cache_hit），
绝不存记忆内容（帕累托 D6 隐私约束）。写入 fire-and-forget 独立会话静默失败；
保留期 30 天 + 每用户条数上限；清理 SQL 双言（PG INTERVAL / SQLite strftime）。

weave-mem 适配：无 chat 域——conversation_id 恒 NULL（列保留兼容上游形状）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_config

logger = logging.getLogger(__name__)

_WRITE_TASKS: set = set()


def query_hash_of(query_text: str) -> str:
    """查询指纹（不可逆；台账不存原文）。"""
    return hashlib.sha256((query_text or "").encode("utf-8")).hexdigest()[:32]


def should_sample(rate: float) -> bool:
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return True
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    return random.random() < rate


def cleanup_statements(days: int, dialect: str = "postgres") -> list[str]:
    """保留期清理 SQL（双言）。返回语句列表（参数内联受控整数）。"""
    days = max(1, int(days))
    if dialect == "sqlite":
        cutoff = f"strftime('%s','now') - {days * 86400}"
        return [
            f"DELETE FROM memory_recall_log WHERE CAST(strftime('%s', created_at) AS INTEGER) < {cutoff}",
        ]
    return [
        f"DELETE FROM memory_recall_log WHERE created_at < NOW() - ({days} * INTERVAL '1 day')",
    ]


def _row_payload(user_id: str, query_text: str, memory_ids, stats: dict) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "query_hash": query_hash_of(query_text),
        "candidate_ids": json.dumps([str(i) for i in (memory_ids or [])][:200], ensure_ascii=False),
        "tier_scores": json.dumps(stats.get("tier_scores") or {}, ensure_ascii=False),
        "gate_score": float(stats.get("gate_score") or 0.0),
        "budget_chars": int(stats.get("budget_chars") or 0),
        "injected_chars": int(stats.get("injected_chars") or 0),
        "truncated": bool(stats.get("truncated")),
        "elapsed_ms": float(stats.get("elapsed_ms") or 0.0),
        "cache_hit": bool(stats.get("cache_hit")),
    }


async def record_recall_log(
    db: AsyncSession, user_id: str, query_text: str,
    memory_ids: list[str] | None = None, stats: dict | None = None,
) -> None:
    cfg = get_config().memory or {}
    if not cfg.get("recall_log_enabled", True):
        return
    if not should_sample(float(cfg.get("recall_log_sample_rate", 1.0))):
        return
    payload = _row_payload(user_id, query_text, memory_ids, stats or {})
    async with db.begin_nested():
        await db.execute(
            text("INSERT INTO memory_recall_log (id, user_id, query_hash, candidate_ids, tier_scores, "
                 "gate_score, budget_chars, injected_chars, truncated, elapsed_ms, cache_hit, created_at) "
                 "VALUES (:id, :user_id, :query_hash, :candidate_ids, :tier_scores, :gate_score, "
                 ":budget_chars, :injected_chars, :truncated, :elapsed_ms, :cache_hit, CURRENT_TIMESTAMP)"),
            payload,
        )


async def record_recall_log_bg(
    user_id: str, query_text: str,
    memory_ids: list[str] | None = None, stats: dict | None = None,
) -> None:
    """fire-and-forget：独立会话，失败静默（绝不拖慢召回主链路）。"""
    try:
        from app.db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            await record_recall_log(db, user_id, query_text, memory_ids=memory_ids, stats=stats)
            await db.commit()
    except Exception:
        logger.debug("record_recall_log_bg silent-fail user=%s", user_id)


def spawn_recall_log(
    user_id: str, query_text: str,
    memory_ids: list[str] | None = None, stats: dict | None = None,
):
    task = asyncio.create_task(record_recall_log_bg(user_id, query_text, memory_ids, stats))
    _WRITE_TASKS.add(task)
    task.add_done_callback(_WRITE_TASKS.discard)
    return task


async def cleanup_recall_log(db: AsyncSession, user_id: str | None = None) -> dict:
    """保留期 + 每用户条数上限清理（scheduler 环调用）。"""
    from app.db.database import IS_SQLITE
    cfg = get_config().memory or {}
    days = int(cfg.get("recall_log_retention_days", 30))
    max_rows = int(cfg.get("recall_log_max_per_user", 20000))
    deleted = 0
    # 0/负值 = 禁用该项清理（上游语义）——绝不把"不限"误执行成"清空"
    if days > 0:
        for stmt in cleanup_statements(days, dialect="sqlite" if IS_SQLITE else "postgres"):
            r = await db.execute(text(stmt))
            deleted += r.rowcount or 0
    if max_rows > 0:
        # 每用户条数上限：只留最近 max_rows 条（窗口函数，PG/SQLite≥3.25 通用）
        r = await db.execute(text(
            "DELETE FROM memory_recall_log WHERE id NOT IN ("
            "  SELECT id FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY user_id "
            "  ORDER BY created_at DESC) AS rn FROM memory_recall_log) t WHERE rn <= :lim)"),
            {"lim": max_rows},
        )
        deleted += r.rowcount or 0
    return {"deleted": deleted}

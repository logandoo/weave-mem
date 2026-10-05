import json
import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_config
from app.db.database import MemoryLLMCall

config = get_config()
logger = logging.getLogger(__name__)

def _IS_SQLITE_CM() -> bool:
    try:
        from app.db.database import IS_SQLITE
        return IS_SQLITE
    except Exception:
        return False


# 进程内缓存（DB 为权威；缓存仅作 is_step_enabled 无 db 场景兜底）
_user_degrade_state: dict[str, int] = {}


async def record_llm_call(
    db: AsyncSession, user_id: str, kind: str, model: str = "",
    prompt_tokens: int = 0, completion_tokens: int = 0,
    billing_class: str = "write",
) -> None:
    if not config.memory.get("cost_governance_enabled", True):
        return
    call = MemoryLLMCall(
        id=str(uuid.uuid4()),
        user_id=user_id,
        kind=kind,
        model=model or "",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        billing_class=billing_class or "write",
    )
    # F2（上游 3378f9907）：savepoint 隔离——计费 INSERT 失败不毒化宿主事务
    async with db.begin_nested():
        db.add(call)
        await db.flush()


async def record_llm_call_bg(
    user_id: str, kind: str, model: str = "",
    prompt_tokens: int = 0, completion_tokens: int = 0,
    billing_class: str = "read",
) -> None:
    """DC1（上游 3378f9907）：读路径遥测独立会话、静默失败——绝不拖慢/拖垮主链路。"""
    try:
        from app.db.database import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            await record_llm_call(
                session, user_id, kind, model=model,
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                billing_class=billing_class)
            await session.commit()
    except Exception:
        logger.debug("record_llm_call_bg silent-fail user=%s kind=%s", user_id, kind)


async def _load_level(db: AsyncSession, user_id: str) -> int:
    result = await db.execute(
        text("SELECT metadata_json FROM user_agent_states WHERE user_id = :uid"),
        {"uid": user_id},
    )
    raw = result.scalar()
    if not raw:
        return 0
    try:
        meta = json.loads(raw)
        return int(meta.get("cost_governance", {}).get("level", 0))
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return 0


async def _save_level(db: AsyncSession, user_id: str, level: int, reason: str = "") -> None:
    result = await db.execute(
        text("SELECT metadata_json FROM user_agent_states WHERE user_id = :uid"
         + (" FOR UPDATE" if not _IS_SQLITE_CM() else "")),
        {"uid": user_id},
    )
    raw = result.scalar()
    try:
        meta = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError):
        meta = {}
    meta["cost_governance"] = {
        "level": level,
        "reason": reason,
        "updated_at": datetime.utcnow().isoformat(),
    }
    await db.execute(
        text("UPDATE user_agent_states SET metadata_json = :meta WHERE user_id = :uid"),
        {"meta": json.dumps(meta, ensure_ascii=False), "uid": user_id},
    )
    _user_degrade_state[user_id] = level


async def check_user_threshold_and_degrade(db: AsyncSession, user_id: str) -> int:
    cg = config.memory_cost_governance
    if not config.memory.get("cost_governance_enabled", True):
        return 0
    if not cg.get("per_user_independent", True):
        return 0

    rolling_days = max(int(cg.get("rolling_window_days", 7)), 1)
    warn_mult = float(cg.get("warn_multiplier", 1.5))
    degrade_steps = cg.get("degrade_steps", [])

    result = await db.execute(
        text("SELECT COUNT(*) FROM memory_llm_calls WHERE user_id = :uid AND created_at >= :since AND COALESCE(billing_class, 'write') = 'write'"),
        {"uid": user_id, "since": datetime.utcnow() - timedelta(days=rolling_days)},
    )
    total_calls = result.scalar() or 0
    daily_avg = total_calls / rolling_days

    result = await db.execute(
        text("SELECT COUNT(*) FROM memory_llm_calls WHERE user_id = :uid AND created_at >= :since AND COALESCE(billing_class, 'write') = 'write'"),
        {"uid": user_id, "since": datetime.utcnow() - timedelta(days=1)},
    )
    today_calls = result.scalar() or 0

    current_level = await _load_level(db, user_id)
    _user_degrade_state[user_id] = current_level
    max_level = max(len(degrade_steps), 1)

    # 2026-08-25 上游修复（A4.9 I1）：绝对下限——产品正常节奏每天 1-4 次记忆 LLM
    # 调用，纯相对阈值 avg×warn 使普通天也触发升级且恢复条件永不满足 → 长期卡级
    min_today_calls = float(cg.get("min_today_calls", 8))
    recovery_ratio = float(cg.get("recovery_ratio", 1.0))

    escalate_floor = max(daily_avg * warn_mult, min_today_calls)
    if today_calls > escalate_floor and current_level < max_level:
        new_level = current_level + 1
        reason = (f"today {today_calls} > 7d avg {daily_avg:.1f} x {warn_mult} "
                  f"(floor {min_today_calls:.0f})")
        await _save_level(db, user_id, new_level, reason)
        logger.warning("Cost governance: user %s degraded to level %d (%s)",
                       user_id, new_level, reason)
        return new_level

    # 2026-08-25 上游修复（A4.9 I1 终版）：恢复条件 = today ≤ avg × recovery_ratio
    # （旧 today < avg×0.5 在稳态非零使用日不可达 → burst 后永久卡死）。
    # 恢复时保留原 reason（解释"为何降级"），不覆写为 usage recovered。
    if current_level > 0 and today_calls <= daily_avg * recovery_ratio:
        new_level = current_level - 1
        old_reason = ""
        try:
            raw = (await db.execute(
                text("SELECT metadata_json FROM user_agent_states WHERE user_id = :uid"),
                {"uid": user_id})).scalar()
            meta = json.loads(raw) if raw else {}
            old_reason = (meta.get("cost_governance") or {}).get("reason") or ""
        except Exception:
            old_reason = ""
        await _save_level(db, user_id, new_level, old_reason or "历史降级（无触发记录）")
        logger.info("Cost governance: user %s restored to level %d (usage recovered)", user_id, new_level)
        return new_level

    return current_level


async def is_step_enabled(user_id: str, step_name: str, db: AsyncSession | None = None) -> bool:
    if not config.memory.get("cost_governance_enabled", True):
        return True
    cg = config.memory_cost_governance
    degrade_steps = cg.get("degrade_steps", [])
    if db is not None:
        level = await _load_level(db, user_id)
        _user_degrade_state[user_id] = level
    else:
        level = _user_degrade_state.get(user_id, 0)
    disabled = set(degrade_steps[:level])
    return step_name not in disabled


async def reset_user_degrade(user_id: str, db: AsyncSession | None = None) -> None:
    if db is not None:
        await _save_level(db, user_id, 0, "manual reset")
    _user_degrade_state[user_id] = 0
    logger.info("Cost governance: user %s reset to level 0", user_id)


async def get_user_degrade_status(db: AsyncSession, user_id: str) -> dict:
    cg = config.memory_cost_governance
    rolling_days = max(int(cg.get("rolling_window_days", 7)), 1)
    degrade_steps = cg.get("degrade_steps", [])
    level = await _load_level(db, user_id)
    _user_degrade_state[user_id] = level

    result = await db.execute(
        text("SELECT metadata_json FROM user_agent_states WHERE user_id = :uid"),
        {"uid": user_id},
    )
    raw = result.scalar()
    reason = ""
    try:
        meta = json.loads(raw) if raw else {}
        reason = meta.get("cost_governance", {}).get("reason", "")
    except (json.JSONDecodeError, TypeError):
        pass

    result = await db.execute(
        text("SELECT COUNT(*) FROM memory_llm_calls WHERE user_id = :uid AND created_at >= :since AND COALESCE(billing_class, 'write') = 'write'"),
        {"uid": user_id, "since": datetime.utcnow() - timedelta(days=rolling_days)},
    )
    total_calls = result.scalar() or 0
    result = await db.execute(
        text("SELECT COUNT(*) FROM memory_llm_calls WHERE user_id = :uid AND created_at >= :since AND COALESCE(billing_class, 'write') = 'write'"),
        {"uid": user_id, "since": datetime.utcnow() - timedelta(days=1)},
    )
    today_calls = result.scalar() or 0

    return {
        "level": level,
        "disabled_steps": list(degrade_steps[:level]),
        "reason": reason,
        "today_calls": today_calls,
        "daily_avg_7d": round(total_calls / rolling_days, 2),
        "warn_multiplier": float(cg.get("warn_multiplier", 1.5)),
    }

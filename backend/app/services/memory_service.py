import logging
from datetime import date, datetime
from typing import List

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_config
from app.db.database import AgentDream, AgentMemory, Conversation, Message, Note, Notebook, UserAgentState
from app.services.memory_llm_factory import _memory_llm

config = get_config()
logger = logging.getLogger(__name__)

_memory_gen_in_flight: set[str] = set()




def _trim_text(value: str, limit: int = 1200) -> str:
    normalized = (value or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _format_notes_payload(notes: List[Note]) -> str:
    chunks = []
    for note in notes:
        chunks.append(
            f"标题: {note.title or '无标题'}\n"
            f"内容: {_trim_text(note.content or '', 300)}"
        )
    return "\n\n".join(chunks)


def _format_messages_payload(messages: List[Message]) -> str:
    return "\n".join(f"- {_trim_text(message.content or '', 220)}" for message in messages)


def _build_fallback_memory_summary(notes: List[Note], messages: List[Message]) -> str:
    note_titles = [note.title or "无标题笔记" for note in notes[:5]]
    question_samples = [message.content.strip() for message in messages[:5] if message.content.strip()]

    sections = ["用户近期共享记忆："]
    if note_titles:
        sections.append("- 最近笔记主题：" + "、".join(note_titles))
    if question_samples:
        sections.append("- 最近高频问题：" + "；".join(_trim_text(sample, 40) for sample in question_samples))
    if len(sections) == 1:
        sections.append("- 暂无足够数据，先按当前会话上下文协作。")
    return "\n".join(sections)


def _build_fallback_dream_summary(notes: List[Note], messages: List[Message]) -> str:
    note_count = len(notes)
    message_count = len(messages)
    return (
        "近期 dream 摘要："
        f"用户最近整理了 {note_count} 条笔记线索，并提出了 {message_count} 个问题片段。"
        "优先延续这些主题的上下文。"
    )


async def ensure_user_agent_state(db: AsyncSession, user_id: str, agent_id: str | None = None) -> UserAgentState:
    """get-or-create per (user, agent) 状态行（Wave 1；agent_id=None 为共享行）。

    唯一性由迁移表达式索引 ON (user_id, COALESCE(agent_id,'')) 保证；并发插入
    用 SAVEPOINT + IntegrityError 重试（跨 PG/SQLite，且兼容老 SQLite 库残留的
    unique(user_id) 约束——那种库上多 agent 行会降级为无状态行，但作用域不受影响）。
    """
    from sqlalchemy.exc import IntegrityError
    import uuid as _uuid

    async def _select():
        result = await db.execute(select(UserAgentState).where(
            UserAgentState.user_id == user_id,
            UserAgentState.agent_id == agent_id,
        ))
        return result.scalar_one_or_none()

    state = await _select()
    if state is not None:
        return state

    agent_name = agent_id[:120] if agent_id else config.agent_name
    try:
        async with db.begin_nested():
            db.add(UserAgentState(id=str(_uuid.uuid4()), user_id=user_id, agent_id=agent_id,
                                  agent_name=agent_name))
            await db.flush()
    except IntegrityError:
        pass

    state = await _select()
    if state is None:
        raise RuntimeError(f"Failed to create UserAgentState for user {user_id} agent {agent_id}")
    await db.commit()
    return state


async def _load_note_context(db: AsyncSession, user_id: str, limit: int) -> List[Note]:
    result = await db.execute(
        select(Note)
        .join(Notebook, Note.notebook_id == Notebook.id)
        .where(Notebook.user_id == user_id)
        .order_by(desc(Note.updated_at))
        .limit(limit)
    )
    return result.scalars().all()


async def _load_user_message_context(db: AsyncSession, user_id: str, limit: int) -> List[Message]:
    result = await db.execute(
        select(Message)
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == user_id, Message.role == "user")
        .order_by(desc(Message.created_at))
        .limit(limit)
    )
    return result.scalars().all()


async def _generate_summary(prompt_messages: list, fallback: str) -> str:
    llm_service = _memory_llm("concept_extraction")
    mem_cfg = config.agent_memory
    try:
        response = await llm_service.complete_chat(
            prompt_messages,
            temperature=float(mem_cfg.get("summary_temperature", 0.2)),
            max_tokens=int(mem_cfg.get("summary_max_tokens", 800)),
        )
    except Exception:
        logger.exception("Failed to generate agent memory summary via LLM")
        return fallback

    cleaned = (response or "").strip()
    return cleaned or fallback


async def generate_user_agent_memory(db: AsyncSession, user_id: str, *, force: bool = False) -> UserAgentState:
    state = await ensure_user_agent_state(db, user_id)
    today = date.today()
    already_generated_today = (
        state.last_memory_generated_at is not None
        and state.last_dream_generated_at is not None
        and state.last_memory_generated_at.date() >= today
        and state.last_dream_generated_at.date() >= today
        and state.memory_summary
        and state.dream_summary
    )
    if already_generated_today and not force:
        return state

    notes = await _load_note_context(db, user_id, config.agent_memory_refresh_note_limit)
    user_messages = await _load_user_message_context(db, user_id, config.agent_memory_refresh_message_limit)

    notes_payload = _format_notes_payload(notes)
    messages_payload = _format_messages_payload(user_messages)
    fallback_memory = _build_fallback_memory_summary(notes, user_messages)
    fallback_dream = _build_fallback_dream_summary(notes, user_messages)

    memory_summary = await _generate_summary(
        [
            {
                "role": "system",
                "content": (
                    "你在整理一个用户级共享 agent 的长期记忆。"
                    "请用简洁中文输出 5-10 条要点，提炼用户长期关注的主题、偏好、正在推进的任务和上下文。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"最近笔记：\n{notes_payload or '无'}\n\n"
                    f"最近问题：\n{messages_payload or '无'}\n\n"
                    "请生成可直接注入系统提示的长期记忆摘要。"
                ),
            },
        ],
        fallback_memory,
    )
    dream_summary = await _generate_summary(
        [
            {
                "role": "system",
                "content": (
                    "你在为共享 agent 生成 nightly dream。"
                    "请输出一段简短抽象总结，说明最近主题之间的关联、下一步可能延续的问题，以及需要持续关注的方向。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"最近笔记：\n{notes_payload or '无'}\n\n"
                    f"最近问题：\n{messages_payload or '无'}\n\n"
                    "请生成一段 4-6 句的中文摘要。"
                ),
            },
        ],
        fallback_dream,
    )

    now = datetime.utcnow()
    today_key = today.isoformat()

    state.memory_summary = _trim_text(memory_summary, int(config.agent_memory.get("summary_storage_limit", 4000)))
    state.dream_summary = _trim_text(dream_summary, int(config.agent_memory.get("dream_storage_limit", 4000)))
    state.last_memory_generated_at = now
    state.last_dream_generated_at = now

    # 2026-08-09 修复 B：v2 运行时跳过 v1 nightly dream 写入 agent_dreams——
    # v2 的 consolidation dream（_generate_dream）每日生成且注入路径
    # （_get_latest_dream）限定 dream_type='consolidation'；v1 nightly 再写
    # 会造成双写混淆（实测 528 行 v1 风格 dream 混入 consolidation 标签、
    # metadata 为空，注入读到旧内容）。memory_summary 列仍保留（双轨兼容）。
    from app.services.memory_runtime_state import memory_runtime_enabled
    if not memory_runtime_enabled(config):
        dream_result = await db.execute(
            select(AgentDream).where(
                AgentDream.agent_state_id == state.id,
                AgentDream.generated_for_date == today_key,
                # A4.9 复审 Minor：只更新 v1 nightly 行——防 runtime 中途
                # active→disabled（SIGHUP 重探测失败）把当日 consolidation 行
                # 改写为 nightly + v1 metadata
                AgentDream.dream_type == "nightly",
            )
        )
        dream = dream_result.scalar_one_or_none()
        if dream is None:
            dream = AgentDream(
                agent_state_id=state.id,
                generated_for_date=today_key,
                summary=state.dream_summary,
                source_note_count=len(notes),
                source_message_count=len(user_messages),
                # 2026-08-09 修复 A：显式标记 nightly 类型 + metadata——
                # ORM 默认 dream_type='consolidation' 使 v1/v2 dream 无法区分
                # （实测 134 行 metadata 为 NULL 的"consolidation"实为 v1 写入）
                dream_type="nightly",
                metadata_json='{"source": "v1_nightly"}',
            )
            db.add(dream)
        else:
            dream.summary = state.dream_summary
            dream.source_note_count = len(notes)
            dream.source_message_count = len(user_messages)
            dream.dream_type = "nightly"
            dream.metadata_json = '{"source": "v1_nightly"}'

    memory_result = await db.execute(
        select(AgentMemory).where(
            AgentMemory.agent_state_id == state.id,
            AgentMemory.source_type == "daily-summary",
            AgentMemory.source_id == today_key,
        )
    )
    memory_entry = memory_result.scalar_one_or_none()
    _default_importance = float(config.agent_memory.get("default_importance", 0.85))
    if memory_entry is None:
        memory_entry = AgentMemory(
            agent_state_id=state.id,
            source_type="daily-summary",
            source_id=today_key,
            title=f"Daily memory {today_key}",
            content=state.memory_summary,
            importance=_default_importance,
        )
        db.add(memory_entry)
    else:
        memory_entry.content = state.memory_summary
        memory_entry.importance = _default_importance

    await db.commit()
    await db.refresh(state)
    return state





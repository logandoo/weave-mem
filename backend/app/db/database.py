# weave-mem 裁剪：从 chatbot app/db/database.py 原样摘取，每行内容与原文一致。
# 保留模型：User/UserSession/Conversation/Message/Note/Notebook（memory_scheduler
# 水位线扫描要读 messages/notes 表，独立库下这些表存在但为空）、UserAgentState、
# AgentMemory、AgentDream、MemoryConcept、MemoryCluster、
# ConceptRelation、MemoryClarification、SubconsciousLog、MemoryEpisode、MemoryLLMCall。
# 删除模型：Assistant/ConversationGroup/ChatSession/UserWorkspace/UserAsrHotword/
# UserSkill/SkillFile/AgentTask/ScheduledTask/ExportTask/WebSearchResult，及
# User/Conversation 对已删模型的 relationship 与 FK 列（assistant_id/group_id）；
# Conversation 的 deathmatch_* 列（agent 域）一并删除。
# init_db 顺序改为先 create_all 后迁移（weave-note 同款裁剪，见函数注释）。

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Column, String, DateTime, Text, ForeignKey, Boolean, Float, Integer, JSON, TypeDecorator
from datetime import datetime
import uuid

from app.core.config import get_config
from app.db import migrations
from app.db.migrations import run_startup_migrations

import logging

logger = logging.getLogger(__name__)

config = get_config()

IS_SQLITE = config.database_type == "sqlite"


class memory_vector(TypeDecorator):
    """方言感知向量列：PG→pgvector.vector(dim)，SQLite→JSON 数组文本。

    SQLite 降级模式下无向量检索（PGVECTOR_AVAILABLE=False），embedding 仅作
    存储；检索自动走文本/BM25 通道（既有 kill-switch 设计）。
    """

    impl = JSON if not IS_SQLITE else Text

    def __init__(self, dim: int = 1536):
        super().__init__()
        self.dim = dim

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            from sqlalchemy import Text as _Text
            return dialect.type_descriptor(_Text())
        from pgvector.sqlalchemy import Vector as _Vector
        return dialect.type_descriptor(_Vector(self.dim))

    def bind_processor(self, dialect):
        import json as _json

        def process(value):
            if value is None:
                return None
            if isinstance(value, (list, tuple)):
                if dialect.name == "sqlite":
                    return _json.dumps(list(value))
                # PG：pgvector 文本格式 [0.1,0.2]（绕过 Vector impl 的处理器链——
                # TypeDecorator 嵌套时 SQLAlchemy 不调用内层 impl 处理器，2026-08-19 排障）
                return "[" + ",".join(str(float(v)) for v in value) + "]"
            return value
        return process

    def result_processor(self, dialect, coltype):
        import json as _json
        import re as _re

        def process(value):
            if value is None:
                return None
            if isinstance(value, str):
                try:
                    return _json.loads(value)
                except Exception:
                    m = _re.match(r"^\[(.*)\]$", value.strip())
                    if m and m.group(1).strip():
                        return [float(v) for v in m.group(1).split(",") if v.strip()]
                    return value
            return value
        return process


Base = declarative_base()

class PersonalAccessToken(Base):
    """个人访问令牌（memos 借鉴）：存 sha256 哈希，明文仅创建时返回一次。"""

    __tablename__ = "personal_access_tokens"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    name = Column(String(100), nullable=False, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), default="user")
    is_active = Column(Boolean, default=True)
    last_login_at = Column(DateTime, nullable=True)
    last_login_ip = Column(String(45), nullable=True)
    agent_permissions = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    conversations = relationship("Conversation", back_populates="user", cascade="all, delete-orphan")

    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")

    notebooks = relationship("Notebook", back_populates="user", cascade="all, delete-orphan")
    agent_state = relationship("UserAgentState", back_populates="user", uselist=False, cascade="all, delete-orphan")

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    title = Column(String(255), default="新对话")
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    user = relationship("User", back_populates="conversations")

class Message(Base):
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"))
    role = Column(String(20))
    content = Column(Text)
    reasoning_content = Column(Text, nullable=True)
    tool_results = Column(Text, nullable=True)
    # PHASE 2B: OpenAI-style tool_calls array (JSON-encoded list of
    # {id, type:"function", function:{name, arguments}}). Persisted so
    # multi-turn conversations replay structured tool history through the
    # LLM context instead of just opaque content text.
    tool_calls = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")



class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_token = Column(String(512), unique=True, index=True, nullable=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    last_active_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="sessions")

class UserAgentState(Base):
    __tablename__ = "user_agent_states"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    agent_name = Column(String(120), nullable=False, default="共享智能体")
    memory_summary = Column(Text, nullable=True)
    dream_summary = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    last_memory_generated_at = Column(DateTime, nullable=True)
    last_dream_generated_at = Column(DateTime, nullable=True)
    last_note_processed_at = Column(DateTime, nullable=True)
    last_message_processed_at = Column(DateTime, nullable=True)
    last_file_memory_processed_at = Column(DateTime, nullable=True)
    last_subconscious_scan_at = Column(DateTime, nullable=True)
    last_consolidation_at = Column(DateTime, nullable=True)
    total_concept_count = Column(Integer, default=0)
    total_episode_count = Column(Integer, default=0)
    latest_dream_id = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="agent_state")
    memories = relationship("AgentMemory", back_populates="agent_state", cascade="all, delete-orphan")
    dreams = relationship("AgentDream", back_populates="agent_state", cascade="all, delete-orphan")

class AgentMemory(Base):
    __tablename__ = "agent_memories"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_state_id = Column(String(36), ForeignKey("user_agent_states.id", ondelete="CASCADE"), nullable=False)
    source_type = Column(String(50), nullable=False, default="daily-summary")
    source_id = Column(String(64), nullable=True)
    title = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)
    importance = Column(Float, nullable=False, default=0.5)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    agent_state = relationship("UserAgentState", back_populates="memories")


class AgentDream(Base):
    __tablename__ = "agent_dreams"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_state_id = Column(String(36), ForeignKey("user_agent_states.id", ondelete="CASCADE"), nullable=False)
    generated_for_date = Column(String(10), nullable=False)
    summary = Column(Text, nullable=False)
    source_note_count = Column(Integer, nullable=False, default=0)
    source_message_count = Column(Integer, nullable=False, default=0)
    source_concept_count = Column(Integer, nullable=False, default=0)
    source_cluster_count = Column(Integer, nullable=False, default=0)
    metadata_json = Column(Text, nullable=True)
    dream_type = Column(String(20), nullable=False, default="consolidation")
    created_at = Column(DateTime, default=datetime.utcnow)

    agent_state = relationship("UserAgentState", back_populates="dreams")

class MemoryConcept(Base):
    __tablename__ = "memory_concepts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    canonical_name = Column(String(500), nullable=False)
    description_short = Column(String(80), nullable=False)
    description_full = Column(Text, nullable=True)
    aliases = Column(Text, nullable=True)
    weight = Column(Float, nullable=False, default=0.5)
    importance = Column(Float, nullable=False, default=0.5)
    importance_evaluated = Column(Boolean, nullable=False, default=False)
    stability = Column(Float, nullable=False, default=14.0)
    source_trust = Column(String(20), nullable=False, default="user_stated")
    memory_type = Column(String(20), nullable=False, default="semantic")
    activation_strength = Column(Float, nullable=False, default=1.0)
    recurrence_count = Column(Integer, nullable=False, default=0)
    last_recurrence_at = Column(DateTime, nullable=True)
    hot_forget_count = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="active")
    source_type = Column(String(50), nullable=False, default="extracted")
    source_raw_ids = Column(Text, nullable=True)
    source_unit_ids = Column(Text, nullable=True)
    needs_review = Column(Boolean, nullable=False, default=False)
    metadata_json = Column(Text, nullable=True)
    last_recalled_at = Column(DateTime, nullable=True)
    valid_from = Column(DateTime, default=datetime.utcnow)
    valid_to = Column(DateTime, nullable=True)
    superseded_by = Column(String(36), nullable=True)
    embedding = Column(memory_vector(), nullable=True)
    embedding_updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MemoryCluster(Base):
    __tablename__ = "memory_clusters"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    summary = Column(Text, nullable=True)
    weight = Column(Float, nullable=False, default=0.5)
    embedding = Column(memory_vector(), nullable=True)
    member_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)




class ConceptClusterMember(Base):
    """集群-概念关联（create_all 建表用；查询走 SQL 文本）。
    死代码清理时曾删除——SQLite 模式无迁移 DDL 兜底，必须由 ORM 建表（2026-08-19 恢复）。
    """

    __tablename__ = "concept_cluster_members"

    concept_id = Column(String(36), ForeignKey("memory_concepts.id", ondelete="CASCADE"), primary_key=True)
    cluster_id = Column(String(36), ForeignKey("memory_clusters.id", ondelete="CASCADE"), primary_key=True)


class ConceptRelation(Base):
    __tablename__ = "concept_relations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    source_id = Column(String(36), ForeignKey("memory_concepts.id", ondelete="CASCADE"), nullable=False)
    target_id = Column(String(36), ForeignKey("memory_concepts.id", ondelete="CASCADE"), nullable=False)
    relation_type = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    weight = Column(Float, nullable=False, default=0.5)
    created_at = Column(DateTime, default=datetime.utcnow)


class MemoryClarification(Base):
    __tablename__ = "memory_clarifications"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(String(36), nullable=True)
    message_id = Column(String(36), nullable=True)
    original_text = Column(Text, nullable=False)
    correction_type = Column(String(30), nullable=False)
    affected_concept_ids = Column(Text, nullable=True)
    new_description = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, default=0.0)
    applied = Column(Boolean, default=False)
    applied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class SubconsciousLog(Base):
    __tablename__ = "subconscious_log"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    unit_kind = Column(String(20), nullable=False, default="message")
    raw_text = Column(Text, nullable=False)
    source_ids = Column(Text, nullable=False)
    embedding = Column(memory_vector(), nullable=True)
    promoted = Column(Boolean, nullable=False, default=False)
    promoted_at = Column(DateTime, nullable=True)
    recurrence_count = Column(Integer, nullable=False, default=0)
    last_recurrence_at = Column(DateTime, nullable=True)
    recurrence_scan_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class MemoryEpisode(Base):
    __tablename__ = "memory_episodes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    narrative = Column(Text, nullable=False)
    source_unit_ids = Column(Text, nullable=False)
    source_concept_ids = Column(Text, nullable=True)
    valid_from = Column(DateTime, default=datetime.utcnow)
    valid_to = Column(DateTime, nullable=True)
    superseded_by = Column(String(36), nullable=True)
    embedding = Column(memory_vector(), nullable=True)
    merged_from = Column(String(36), nullable=True)
    last_recalled_at = Column(DateTime, nullable=True)
    source_type = Column(String(50), default="extracted")  # 'extracted' | 'migration'
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MemoryLLMCall(Base):
    __tablename__ = "memory_llm_calls"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String(50), nullable=False)
    model = Column(String(100), nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)



class Notebook(Base):
    __tablename__ = "notebooks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False, default="新笔记本")
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="notebooks")
    notes = relationship("Note", back_populates="notebook", cascade="all, delete-orphan")


class Note(Base):
    __tablename__ = "notes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    notebook_id = Column(String(36), ForeignKey("notebooks.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=True)
    content = Column(Text, default="")
    raw_transcription = Column(Text, nullable=True)  # Original voice transcription before editing
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    notebook = relationship("Notebook", back_populates="notes")

if IS_SQLITE:
    engine = create_async_engine(
        config.database_url,
        echo=False,
        connect_args={"timeout": 30},
    )
else:
    engine = create_async_engine(
        config.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=config.database_pool_size,
        max_overflow=config.database_max_overflow,
        pool_timeout=config.database_pool_timeout,
        pool_recycle=config.database_pool_recycle,
    )
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def register_advisory_lock_cleanup(target_engine) -> None:
    """连接归还池时释放该会话持有的全部 session 级 advisory 锁。

    背景（2026-08-10 线上事故）：memory scheduler 用 session 级 pg_try_advisory_lock
    做 per-user 互斥；当 per-user 处理异常（超时/事务中止）时 finally 里的 unlock 失败，
    锁随连接回池残留（SQLAlchemy 池 reset 只 rollback 事务、不释放 advisory 锁），
    导致 25/25 用户锁全部挂死在 idle 池连接上，调度器（扫描+consolidation）静默跳过
    所有用户。此监听器在每条连接归还时执行 pg_advisory_unlock_all() 根治泄漏类问题。
    失败必须留痕（降频 warn）：静默吞异常正是本次事故"无任何告警"的原罪。
    """
    from sqlalchemy import event

    _reset_failure_count = 0

    @event.listens_for(target_engine.sync_engine, "reset")
    def _reset_advisory_locks(dbapi_conn, record):
        nonlocal _reset_failure_count
        try:
            dbapi_conn.await_(dbapi_conn._connection.execute("SELECT pg_advisory_unlock_all()"))
        except Exception:
            # 失败路径安全（连接会被池 invalidate 关闭，session 锁随连接消亡自愈），
            # 但必须留痕：连续失败说明清理机制失效，不能回到静默状态。
            _reset_failure_count += 1
            if _reset_failure_count <= 3 or _reset_failure_count % 50 == 0:
                logger.warning(
                    "pg_advisory_unlock_all on pool reset failed (%d times so far) — "
                    "advisory lock cleanup may be broken", _reset_failure_count,
                )


if IS_SQLITE:
    from sqlalchemy import event as _sqlalchemy_event

    @_sqlalchemy_event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()
else:
    register_advisory_lock_cleanup(engine)

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

async def init_db():
    async with engine.begin() as conn:
        # weave-mem 裁剪：先 create_all 再跑迁移——chatbot 的迁移假定表已由
        # 历史 create_all 建好，而 weave_mem 是全新库，迁移里的 uas_*/ad_* 等
        # ALTER 语句需要先有 user_agent_states / agent_dreams 表。
        if migrations.PGVECTOR_AVAILABLE or IS_SQLITE:
            # SQLite 降级模式：memory_vector 列为 Text，全部表可建（PGVECTOR_AVAILABLE
            # 为 False 但表结构无向量依赖——§9.5 排除逻辑仅适用于 PG 无扩展场景）
            await conn.run_sync(Base.metadata.create_all)
        else:
            # §9.5：pgvector 缺失时 memory v2 表（含 vector 列）无法建，排除后照常启动
            memory_tables = {
                "memory_concepts", "memory_clusters", "concept_cluster_members",
                "concept_relations", "memory_clarifications", "subconscious_log",
                "memory_episodes", "memory_llm_calls",
            }
            tables = [t for t in Base.metadata.sorted_tables if t.name not in memory_tables]
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
        await run_startup_migrations(conn)



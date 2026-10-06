# weave-mem 裁剪：从 chatbot app/db/migrations.py 原样摘取，每行内容与原文一致。
# 保留：memory v2 DDL（HNSW 索引 m=16/ef_construction=64、pgvector_extension 起的
# 连续后缀块）、uas_*/ad_* ALTER、users_agent_permissions、create_worker_instances
# （memory_scheduler 领导选举要写 worker_instances 表）。
# 删除：assistant/conversation/deathmatch/messages FTS/agent_tasks/scheduled_tasks/
# shared_kv/web_search_results/user_asr_hotwords/user_skills/skill_files 等 agent 域条目。

import logging
import re


_sqlite_flag_cache: bool | None = None


def _sqlite_flag() -> bool:
    global _sqlite_flag_cache
    if _sqlite_flag_cache is None:
        try:
            from app.db.database import IS_SQLITE as _f
            _sqlite_flag_cache = _f
        except Exception:
            _sqlite_flag_cache = False
    return _sqlite_flag_cache

from sqlalchemy import text

logger = logging.getLogger(__name__)

_EXT_IDENT_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


STARTUP_MIGRATIONS = [
    # Agent permission settings per user
    ("users_agent_permissions", "ALTER TABLE users ADD COLUMN IF NOT EXISTS agent_permissions TEXT"),

    # ---- Wave 1: agent 身份命名空间（非 memory 表；pgvector 缺失时也应执行）----
    ("create_agents", """CREATE TABLE IF NOT EXISTS agents (
        id VARCHAR(36) PRIMARY KEY,
        user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        agent_key VARCHAR(64) NOT NULL,
        display_name VARCHAR(120),
        kind VARCHAR(32) NOT NULL DEFAULT 'manual',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMP
    )"""),
    ("idx_agents_user", "CREATE INDEX IF NOT EXISTS idx_agents_user ON agents(user_id)"),
    ("uq_agents_user_key", "CREATE UNIQUE INDEX IF NOT EXISTS uq_agents_user_key ON agents(user_id, agent_key)"),
    ("pat_agent_id", "ALTER TABLE personal_access_tokens ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64)"),
    ("uas_agent_id", "ALTER TABLE user_agent_states ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64)"),
    # 旧库 user_id 唯一约束必须移除（每 user 多 agent 行）；PG 专用，SQLite 走 skip 规则。
    ("uas_drop_user_unique", "ALTER TABLE user_agent_states DROP CONSTRAINT IF EXISTS user_agent_states_user_id_key"),
    # 表达式唯一索引（PG NULL 不去重，不能直接 (user_id, agent_id) 唯一）
    ("uas_unique_user_agent", "CREATE UNIQUE INDEX IF NOT EXISTS uq_uas_user_agent ON user_agent_states (user_id, COALESCE(agent_id, ''))"),

    # Worker instance registry for cross-worker health checks
    ("create_worker_instances", """CREATE TABLE IF NOT EXISTS worker_instances (
        id TEXT PRIMARY KEY,
        host TEXT,
        port INTEGER,
        pid INTEGER,
        started_at DOUBLE PRECISION NOT NULL,
        last_heartbeat DOUBLE PRECISION NOT NULL,
        status TEXT DEFAULT 'active',
        metadata TEXT
    )"""),

    # ---- Memory & Dreaming v2: 纯列 ALTER（不依赖 pgvector，pgvector 缺失时也应执行）----
    # ALTER user_agent_states: new columns for concept extraction watermarks + management
    ("uas_last_note_processed_at", "ALTER TABLE user_agent_states ADD COLUMN IF NOT EXISTS last_note_processed_at TIMESTAMP"),
    ("uas_last_message_processed_at", "ALTER TABLE user_agent_states ADD COLUMN IF NOT EXISTS last_message_processed_at TIMESTAMP"),
    ("uas_last_file_memory_processed_at", "ALTER TABLE user_agent_states ADD COLUMN IF NOT EXISTS last_file_memory_processed_at TIMESTAMP"),
    ("uas_last_subconscious_scan_at", "ALTER TABLE user_agent_states ADD COLUMN IF NOT EXISTS last_subconscious_scan_at TIMESTAMP"),
    ("uas_last_consolidation_at", "ALTER TABLE user_agent_states ADD COLUMN IF NOT EXISTS last_consolidation_at TIMESTAMP"),
    ("uas_total_concept_count", "ALTER TABLE user_agent_states ADD COLUMN IF NOT EXISTS total_concept_count INTEGER DEFAULT 0"),
    ("uas_total_episode_count", "ALTER TABLE user_agent_states ADD COLUMN IF NOT EXISTS total_episode_count INTEGER DEFAULT 0"),
    ("uas_latest_dream_id", "ALTER TABLE user_agent_states ADD COLUMN IF NOT EXISTS latest_dream_id VARCHAR(36)"),
    ("uas_metadata_json", "ALTER TABLE user_agent_states ADD COLUMN IF NOT EXISTS metadata_json TEXT"),
    # ALTER agent_dreams: new columns
    ("ad_source_concept_count", "ALTER TABLE agent_dreams ADD COLUMN IF NOT EXISTS source_concept_count INTEGER DEFAULT 0"),
    ("ad_source_cluster_count", "ALTER TABLE agent_dreams ADD COLUMN IF NOT EXISTS source_cluster_count INTEGER DEFAULT 0"),
    ("ad_metadata_json", "ALTER TABLE agent_dreams ADD COLUMN IF NOT EXISTS metadata_json TEXT"),
    ("ad_dream_type", "ALTER TABLE agent_dreams ADD COLUMN IF NOT EXISTS dream_type VARCHAR(20) DEFAULT 'consolidation'"),
    # 2026-08-09 数据迁移：历史 v1 nightly dream 误标为 consolidation 且 metadata 为空
    # （v2 consolidation 恒写 metadata，metadata_json IS NULL 是稳健判别式——
    # 用 source_note_count>0 会漏掉零笔记用户的 v1 行）；改为 nightly + provenance，
    # 注入路径（_get_latest_dream 限定 consolidation）不再读到 v1 内容
    ("ad_mislabeled_v1_nightly", """UPDATE agent_dreams SET dream_type = 'nightly', metadata_json = '{"source":"v1_nightly"}' WHERE dream_type = 'consolidation' AND (metadata_json IS NULL OR metadata_json = '')"""),

    # ---- Memory & Dreaming v2: pgvector + schema（以下块依赖 pgvector，缺失时整体跳过）----
    ("pgvector_extension", "CREATE EXTENSION IF NOT EXISTS vector"),
    # memory_concepts
    ("create_memory_concepts", """CREATE TABLE IF NOT EXISTS memory_concepts (
        id VARCHAR(36) PRIMARY KEY,
        user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        canonical_name VARCHAR(500) NOT NULL,
        description_short VARCHAR(80) NOT NULL,
        description_full TEXT,
        aliases TEXT,
        weight FLOAT NOT NULL DEFAULT 0.5,
        stability FLOAT NOT NULL DEFAULT 14.0,
        source_trust VARCHAR(20) NOT NULL DEFAULT 'user_stated',
        memory_type VARCHAR(20) NOT NULL DEFAULT 'semantic',
        activation_strength FLOAT NOT NULL DEFAULT 1.0,
        recurrence_count INTEGER NOT NULL DEFAULT 0,
        last_recurrence_at TIMESTAMP,
        hot_forget_count INTEGER NOT NULL DEFAULT 0,
        status VARCHAR(20) NOT NULL DEFAULT 'active',
        source_type VARCHAR(50) NOT NULL DEFAULT 'extracted',
        source_raw_ids TEXT,
        source_unit_ids TEXT,
        needs_review BOOLEAN NOT NULL DEFAULT FALSE,
        metadata_json TEXT,
        last_recalled_at TIMESTAMP,
        valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        valid_to TIMESTAMP,
        superseded_by VARCHAR(36),
        embedding vector(1536),
        embedding_updated_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""),
    ("idx_concepts_user_status", "CREATE INDEX IF NOT EXISTS idx_concepts_user_status ON memory_concepts(user_id, status)"),
    ("idx_concepts_user_weight", "CREATE INDEX IF NOT EXISTS idx_concepts_user_weight ON memory_concepts(user_id, weight DESC)"),
    ("idx_concepts_user_recalled", "CREATE INDEX IF NOT EXISTS idx_concepts_user_recalled ON memory_concepts(user_id, last_recalled_at DESC)"),
    ("idx_concepts_needs_review", "CREATE INDEX IF NOT EXISTS idx_concepts_needs_review ON memory_concepts(user_id, needs_review) WHERE needs_review = TRUE"),
    ("idx_concepts_embedding", "CREATE INDEX IF NOT EXISTS idx_concepts_embedding ON memory_concepts USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"),
    # memory_clusters
    ("create_memory_clusters", """CREATE TABLE IF NOT EXISTS memory_clusters (
        id VARCHAR(36) PRIMARY KEY,
        user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL,
        summary TEXT,
        weight FLOAT NOT NULL DEFAULT 0.5,
        embedding vector(1536),
        member_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""),
    ("idx_clusters_user", "CREATE INDEX IF NOT EXISTS idx_clusters_user ON memory_clusters(user_id)"),
    # concept_cluster_members
    ("create_concept_cluster_members", """CREATE TABLE IF NOT EXISTS concept_cluster_members (
        concept_id VARCHAR(36) NOT NULL REFERENCES memory_concepts(id) ON DELETE CASCADE,
        cluster_id VARCHAR(36) NOT NULL REFERENCES memory_clusters(id) ON DELETE CASCADE,
        PRIMARY KEY (concept_id, cluster_id)
    )"""),
    ("idx_ccm_cluster", "CREATE INDEX IF NOT EXISTS idx_ccm_cluster ON concept_cluster_members(cluster_id)"),
    # concept_relations
    ("create_concept_relations", """CREATE TABLE IF NOT EXISTS concept_relations (
        id VARCHAR(36) PRIMARY KEY,
        user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        source_id VARCHAR(36) NOT NULL REFERENCES memory_concepts(id) ON DELETE CASCADE,
        target_id VARCHAR(36) NOT NULL REFERENCES memory_concepts(id) ON DELETE CASCADE,
        relation_type VARCHAR(50) NOT NULL,
        description TEXT,
        weight FLOAT NOT NULL DEFAULT 0.5,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CHECK (source_id != target_id)
    )"""),
    ("idx_rel_source", "CREATE INDEX IF NOT EXISTS idx_rel_source ON concept_relations(source_id)"),
    ("idx_rel_target", "CREATE INDEX IF NOT EXISTS idx_rel_target ON concept_relations(target_id)"),
    ("idx_rel_user", "CREATE INDEX IF NOT EXISTS idx_rel_user ON concept_relations(user_id)"),
    # memory_clarifications
    ("create_memory_clarifications", """CREATE TABLE IF NOT EXISTS memory_clarifications (
        id VARCHAR(36) PRIMARY KEY,
        user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        conversation_id VARCHAR(36),
        message_id VARCHAR(36),
        original_text TEXT NOT NULL,
        correction_type VARCHAR(30) NOT NULL,
        affected_concept_ids TEXT,
        new_description TEXT,
        confidence FLOAT NOT NULL DEFAULT 0.0,
        applied BOOLEAN DEFAULT FALSE,
        applied_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""),
    ("idx_clar_user", "CREATE INDEX IF NOT EXISTS idx_clar_user ON memory_clarifications(user_id)"),
    ("idx_clar_applied", "CREATE INDEX IF NOT EXISTS idx_clar_applied ON memory_clarifications(user_id, applied)"),
    # subconscious_log
    ("create_subconscious_log", """CREATE TABLE IF NOT EXISTS subconscious_log (
        id VARCHAR(36) PRIMARY KEY,
        user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        unit_kind VARCHAR(20) NOT NULL DEFAULT 'message',
        raw_text TEXT NOT NULL,
        source_ids TEXT NOT NULL,
        embedding vector(1536),
        promoted BOOLEAN NOT NULL DEFAULT FALSE,
        promoted_at TIMESTAMP,
        recurrence_count INTEGER NOT NULL DEFAULT 0,
        last_recurrence_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""),
    ("idx_sub_user_created", "CREATE INDEX IF NOT EXISTS idx_sub_user_created ON subconscious_log(user_id, created_at DESC)"),
    ("idx_sub_user_unpromoted", "CREATE INDEX IF NOT EXISTS idx_sub_user_unpromoted ON subconscious_log(user_id, promoted) WHERE promoted = FALSE"),
    ("idx_sub_embedding", "CREATE INDEX IF NOT EXISTS idx_sub_embedding ON subconscious_log USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"),
    # 2026-08-10 队头阻塞修复：单元作为批次头的扫描次数预算（防最旧 50 条永不晋升挡住新单元）
    ("add_subconscious_recurrence_scan_count", "ALTER TABLE subconscious_log ADD COLUMN IF NOT EXISTS recurrence_scan_count INTEGER NOT NULL DEFAULT 0"),
    # 2026-08-10 权重语义修正：importance = 持久重要性（与 weight 热度正交），
    # 创建时由提取 LLM 判定/规则兜底；展示与检索排序以 importance 主导
    ("add_concept_importance", "ALTER TABLE memory_concepts ADD COLUMN IF NOT EXISTS importance DOUBLE PRECISION NOT NULL DEFAULT 0.5"),
    # 2026-08-10 F1 修复：独立"已评估"标记（importance=0.5 是合法 verdict，不能当哨兵）
    ("add_concept_importance_evaluated", "ALTER TABLE memory_concepts ADD COLUMN IF NOT EXISTS importance_evaluated BOOLEAN NOT NULL DEFAULT FALSE"),
    # memory_episodes
    ("create_memory_episodes", """CREATE TABLE IF NOT EXISTS memory_episodes (
        id VARCHAR(36) PRIMARY KEY,
        user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        narrative TEXT NOT NULL,
        source_unit_ids TEXT NOT NULL,
        source_concept_ids TEXT,
        valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        valid_to TIMESTAMP,
        superseded_by VARCHAR(36),
        embedding vector(1536),
        merged_from VARCHAR(36),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""),
    ("idx_epi_user_created", "CREATE INDEX IF NOT EXISTS idx_epi_user_created ON memory_episodes(user_id, valid_from DESC)"),
    ("idx_epi_embedding", "CREATE INDEX IF NOT EXISTS idx_epi_embedding ON memory_episodes USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"),
    # memory_concepts: 防御性 ALTER（老库 CREATE 无此列时补齐；新库 CREATE 已含则幂等跳过）
    ("mc_last_recurrence_at", "ALTER TABLE memory_concepts ADD COLUMN IF NOT EXISTS last_recurrence_at TIMESTAMP"),
    # memory_episodes: recall tracking (M&D §5.3.1a-2)
    ("me_last_recalled_at", "ALTER TABLE memory_episodes ADD COLUMN IF NOT EXISTS last_recalled_at TIMESTAMP"),
    # memory_episodes: 来源标记（§8.5.5 回滚按 source_type='migration' 精确删除）
    ("me_source_type", "ALTER TABLE memory_episodes ADD COLUMN IF NOT EXISTS source_type VARCHAR(50) DEFAULT 'extracted'"),
    # memory_concepts: 衰减写回时间戳（2026-08-16）——run_weight_decay 写回
    # weight 时记录 anchor，dream/active-dreaming 的有效权重用"距上次写回/召回"
    # 的残差衰减，避免同一事务内二次全量衰减（A4.9 审查 C1/C2）
    ("mc_weight_decayed_at", "ALTER TABLE memory_concepts ADD COLUMN IF NOT EXISTS weight_decayed_at TIMESTAMP"),
    ("idx_epi_source_type", "CREATE INDEX IF NOT EXISTS idx_epi_source_type ON memory_episodes(user_id, source_type)"),
    # memory_llm_calls
    ("create_memory_llm_calls", """CREATE TABLE IF NOT EXISTS memory_llm_calls (
        id VARCHAR(36) PRIMARY KEY,
        user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind VARCHAR(50) NOT NULL,
        model VARCHAR(100),
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        completion_tokens INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""),
    ("idx_mlc_user_ts", "CREATE INDEX IF NOT EXISTS idx_mlc_user_ts ON memory_llm_calls(user_id, created_at DESC)"),
    # memory_llm_calls: 读写计费分离（上游 3378f9907 DC1，2026-09-14）——
    # 'write'=写路径进降级计数；'read'=读路径遥测仅计费观测
    ("mlc_billing_class", "ALTER TABLE memory_llm_calls ADD COLUMN IF NOT EXISTS billing_class VARCHAR(20) NOT NULL DEFAULT 'write'"),
    # concept_relations: 边来源标记（上游 f5d2f6401 D1）——'llm'/'co_occurs'
    ("cr_edge_source", "ALTER TABLE concept_relations ADD COLUMN IF NOT EXISTS edge_source VARCHAR(20) NOT NULL DEFAULT 'llm'"),
    # memory_episodes: 参与者/地点（上游 f5d2f6401 D1 P/L/T，JSON 数组字符串）
    ("me_participants", "ALTER TABLE memory_episodes ADD COLUMN IF NOT EXISTS participants TEXT"),
    ("me_locations", "ALTER TABLE memory_episodes ADD COLUMN IF NOT EXISTS locations TEXT"),
    # memory_clusters: 向量溯源模型（上游 3378f9907 DC2）
    ("mc_cluster_embedding_model", "ALTER TABLE memory_clusters ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(100)"),
    # memory_recall_log: C1 召回台账（仅元数据，上游 beda68eb4）
    ("mrl_create", """CREATE TABLE IF NOT EXISTS memory_recall_log (
        id VARCHAR(36) PRIMARY KEY,
        user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        conversation_id VARCHAR(36) REFERENCES conversations(id) ON DELETE CASCADE,
        query_hash VARCHAR(64) NOT NULL,
        candidate_ids TEXT,
        tier_scores TEXT,
        gate_score DOUBLE PRECISION NOT NULL DEFAULT 0,
        budget_chars INTEGER NOT NULL DEFAULT 0,
        injected_chars INTEGER NOT NULL DEFAULT 0,
        truncated BOOLEAN NOT NULL DEFAULT FALSE,
        elapsed_ms DOUBLE PRECISION NOT NULL DEFAULT 0,
        cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )"""),
    ("mrl_idx_user_created", "CREATE INDEX IF NOT EXISTS mrl_idx_user_created ON memory_recall_log(user_id, created_at DESC)"),

    # ---- Wave 1: memory 表 agent 作用域列（依赖 memory 表存在，置于 pgvector 块后）----
    ("memory_concepts_agent_id", "ALTER TABLE memory_concepts ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64)"),
    ("idx_concepts_user_agent", "CREATE INDEX IF NOT EXISTS idx_concepts_user_agent ON memory_concepts(user_id, agent_id)"),
    ("memory_episodes_agent_id", "ALTER TABLE memory_episodes ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64)"),
    ("memory_clusters_agent_id", "ALTER TABLE memory_clusters ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64)"),
    ("concept_relations_agent_id", "ALTER TABLE concept_relations ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64)"),
    ("memory_clarifications_agent_id", "ALTER TABLE memory_clarifications ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64)"),
    ("subconscious_log_agent_id", "ALTER TABLE subconscious_log ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64)"),
    ("subconscious_log_conversation_id", "ALTER TABLE subconscious_log ADD COLUMN IF NOT EXISTS conversation_id VARCHAR(64)"),
    ("memory_llm_calls_agent_id", "ALTER TABLE memory_llm_calls ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64)"),
    ("memory_recall_log_agent_id", "ALTER TABLE memory_recall_log ADD COLUMN IF NOT EXISTS agent_id VARCHAR(64)"),
]

# §9.5 pgvector 缺失降级：启动探测结果（run_startup_migrations 期间更新）。
# False 时 memory v2 迁移整体跳过、init_db 的 create_all 排除 memory 表、
# main.py 强制 memory.enabled=false —— 服务继续以旧记忆方案运行，不崩溃。
PGVECTOR_AVAILABLE = True

_MEMORY_MIGRATION_START = "pgvector_extension"

_VECTOR_TABLES = [
    ("subconscious_log", "embedding", "idx_sub_embedding"),
    ("memory_concepts", "embedding", "idx_concepts_embedding"),
    ("memory_episodes", "embedding", "idx_epi_embedding"),
    ("memory_clusters", "embedding", None),
]

async def probe_pgvector(conn, extension: str = "vector") -> bool:
    """§9.5 启动探测：pgvector 扩展是否可用（已安装或可创建）。

    CREATE EXTENSION 失败（无权限/未安装）会被 PG 拒绝并使事务进入 aborted
    状态——savepoint 隔离保证探测失败不毒化调用方事务（init_db 后续
    create_all 依赖同一事务）。
    """
    if not _EXT_IDENT_RE.fullmatch(extension):
        logger.error("probe_pgvector: 非法扩展名标识符 %r", extension)
        return False
    try:
        async with conn.begin_nested():
            await conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {extension}"))
    except Exception:
        return False
    try:
        r = await conn.execute(text("SELECT extversion FROM pg_extension WHERE extname = :ext"),
                               {"ext": extension})
    except Exception:
        # SELECT 失败属异常环境（事务已毒化等），与"扩展缺失"区分开——
        # 不当静默禁用：记录 warning 便于排查当次启动 memory 被禁的原因
        if not _sqlite_flag():
            logger.warning("probe_pgvector: pg_extension 查询失败，按不可用处理", exc_info=True)
        return False
    return r.scalar() is not None

async def _reconcile_vector_dims(conn) -> None:
    """检测 DB 中 vector 列维度是否与配置 embedding_dim 一致，不一致则 ALTER 重建。

    典型场景：旧迁移创建 vector(1536)，用户切换 embedding 模型后配置改为 1024。
    """
    try:
        from app.core.config import get_config
        cfg = get_config()
        expected = int(cfg.memory.get("embedding_dim", 1536))
    except Exception:
        return

    for table, col, idx_name in _VECTOR_TABLES:
        try:
            async with conn.begin_nested():
                r = await conn.execute(text(
                    "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                    "WHERE attrelid = CAST(:tbl AS regclass) AND attname = :col AND NOT attisdropped"
                ), {"tbl": table, "col": col})
                fmt = r.scalar()
                if not fmt:
                    continue
                match = re.search(r"vector\((\d+)\)", fmt)
                if not match:
                    continue
                current = int(match.group(1))
                if current == expected:
                    continue
                logger.warning(
                    "vector dim mismatch: %s.%s is vector(%d), config expects %d — altering",
                    table, col, current, expected,
                )
                if idx_name:
                    await conn.execute(text(f"DROP INDEX IF EXISTS {idx_name}"))
                await conn.execute(text(
                    f"ALTER TABLE {table} ALTER COLUMN {col} TYPE vector({expected})"
                ))
                if idx_name:
                    await conn.execute(text(
                        f"CREATE INDEX {idx_name} ON {table} USING hnsw ({col} vector_cosine_ops) "
                        f"WITH (m = 16, ef_construction = 64)"
                    ))
                logger.info("vector dim reconciled: %s.%s → vector(%d)", table, col, expected)
        except Exception:
            logger.warning("vector dim reconcile failed for %s.%s", table, col, exc_info=True)

async def run_startup_migrations(conn) -> None:
    global PGVECTOR_AVAILABLE
    # §9.5：无条件探测（ cheap + 幂等）——已记录 applied 的旧库亦需覆盖
    # “migration_versions 被恢复进无 pgvector 集群”场景，不能靠 applied 跳过探测
    PGVECTOR_AVAILABLE = await probe_pgvector(conn)

    await conn.execute(text(
        """CREATE TABLE IF NOT EXISTS migration_versions (
            version VARCHAR(255) PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )"""
    ))

    versions = [v for v, _ in STARTUP_MIGRATIONS]
    mem_start = versions.index(_MEMORY_MIGRATION_START) if _MEMORY_MIGRATION_START in versions else len(STARTUP_MIGRATIONS)

    _sqlite = _sqlite_flag()
    for idx, (version, statement) in enumerate(STARTUP_MIGRATIONS):
        result = await conn.execute(
            text("SELECT 1 FROM migration_versions WHERE version = :version"),
            {"version": version},
        )
        if result.scalar_one_or_none():
            continue

        if _sqlite:
            m = re.match(r"ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+)(.*)", statement)
            if m:
                pragma = await conn.execute(text(f"PRAGMA table_info({m.group(1)})"))
                cols = {row[1] for row in pragma.fetchall()}
                if m.group(2) in cols:
                    await conn.execute(
                        text("INSERT INTO migration_versions (version) VALUES (:version)"),
                        {"version": version},
                    )
                    continue
                await conn.execute(text(f"ALTER TABLE {m.group(1)} ADD COLUMN {m.group(2)}{m.group(3)}"))
                await conn.execute(
                    text("INSERT INTO migration_versions (version) VALUES (:version)"),
                    {"version": version},
                )
                continue

        if _sqlite and "DROP CONSTRAINT" in statement:
            # SQLite 无 DROP CONSTRAINT 语法：旧 SQLite 库的 unique(user_id) 无法在线
            # 删除（建库即由 ORM 固化）。老库多 agent 状态行由 ensure_agent_registered
            # 的 savepoint 重试兜底降级（无状态行≠作用域失效）；新库 schema 无此约束。
            await conn.execute(
                text("INSERT INTO migration_versions (version) VALUES (:version)"),
                {"version": version},
            )
            continue

        if idx >= mem_start and not PGVECTOR_AVAILABLE:
            if _sqlite:
                # SQLite 降级：memory v2 块按语句分类执行——表结构由 create_all
                # 建（memory_vector→Text）；跳过 pgvector 专属语法（CREATE
                # EXTENSION / HNSW 索引）；其余幂等语句照常（ADD COLUMN 已由
                # 循环顶部统一 PRAGMA 预检处理，不会到达这里）。
                if "CREATE EXTENSION" in statement or "USING hnsw" in statement:
                    continue
                await conn.execute(text(statement))
                await conn.execute(
                    text("INSERT INTO migration_versions (version) VALUES (:version)"),
                    {"version": version},
                )
                continue
            # PG + pgvector 缺失：整块跳过（现状）——不记录版本号，
            # 安装 pgvector 后重启可补跑
            if idx == mem_start:
                msg = (
                    "pgvector 扩展不可用（CREATE EXTENSION vector 失败或未安装）；"
                    "跳过 memory v2 全部迁移，memory 子系统将被禁用。"
                    "请安装 pgvector（如 brew install pgvector）并授予 CREATE EXTENSION 权限后重启。")
                try:
                    from app.core.config import get_config
                    _mem_requested = bool(get_config().memory.get("enabled")) or \
                        bool(get_config().memory.get("migration_enabled"))
                except Exception:
                    _mem_requested = True  # 配置读不出时按高调处理，不错过告警
                if _mem_requested:
                    logger.error(msg)
                else:
                    # memory 未开启时降级为 info，避免每次启动刷错误日志
                    logger.info(msg)
            continue

        await conn.execute(text(statement))
        await conn.execute(
            text("INSERT INTO migration_versions (version) VALUES (:version)"),
            {"version": version},
        )

    # 静态迁移跑完后，校验 vector 列维度是否与配置一致
    if PGVECTOR_AVAILABLE:
        await _reconcile_vector_dims(conn)


import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.db.database import User

router = APIRouter(prefix="/api/memory", tags=["memory"])
logger = logging.getLogger(__name__)


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """§8.5.1：admin 端点鉴权（fail-closed）。users.role == 'admin' 才放行。"""
    if getattr(current_user, "role", "user") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return current_user


@router.get("/concepts")
async def list_concepts(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        text("""
            SELECT id, canonical_name, description_short, description_full,
                   weight, importance, source_trust, memory_type, activation_strength,
                   status, valid_from, valid_to, created_at
            FROM memory_concepts
            WHERE user_id = :uid
            ORDER BY importance DESC, weight DESC, created_at DESC LIMIT :lim
        """),
        {"uid": current_user.id, "lim": max(1, min(limit, 200))},
    )
    concepts = []
    for row in result.fetchall():
        concepts.append({
            "id": row[0],
            "canonical_name": row[1],
            "description_short": row[2],
            "description_full": row[3],
            "weight": row[4],
            "importance": row[5],
            "source_trust": row[6],
            "memory_type": row[7],
            "activation_strength": row[8],
            "status": row[9],
            "valid_from": str(row[10]) if row[10] else None,
            "valid_to": str(row[11]) if row[11] else None,
            "created_at": str(row[12]) if row[12] else None,
        })
    return {"concepts": concepts, "count": len(concepts)}


@router.get("/concepts/{concept_id}")
async def get_concept_detail(
    concept_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        text("""
            SELECT id, canonical_name, description_short, description_full,
                   weight, importance, source_trust, memory_type, activation_strength,
                   status, valid_from, valid_to, created_at, aliases,
                   stability, last_recalled_at, metadata_json
            FROM memory_concepts
            WHERE id = :id AND user_id = :uid
        """),
        {"id": concept_id, "uid": current_user.id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Concept not found")
    return {
        "id": row[0], "canonical_name": row[1], "description_short": row[2],
        "description_full": row[3], "weight": row[4], "importance": row[5],
        "source_trust": row[6], "memory_type": row[7], "activation_strength": row[8],
        "status": row[9], "valid_from": str(row[10]) if row[10] else None,
        "valid_to": str(row[11]) if row[11] else None, "created_at": str(row[12]) if row[12] else None,
        "aliases": row[13], "stability": row[14],
        "last_recalled_at": str(row[15]) if row[15] else None, "metadata_json": row[16],
    }


@router.get("/episodes")
async def list_episodes(
    limit: int = 10,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        text("""
            SELECT id, narrative, source_unit_ids, source_type,
                   valid_to, superseded_by, created_at
            FROM memory_episodes
            WHERE user_id = :uid
            ORDER BY (created_at IS NULL), created_at DESC LIMIT :lim
        """),
        {"uid": current_user.id, "lim": max(1, min(limit, 50))},
    )
    episodes = []
    for row in result.fetchall():
        episodes.append({
            "id": row[0], "narrative": row[1], "source_unit_ids": row[2],
            "source_type": row[3],
            "valid_to": str(row[4]) if row[4] else None,
            "superseded_by": row[5],
            "created_at": str(row[6]) if row[6] else None,
        })
    return {"episodes": episodes, "count": len(episodes)}


@router.get("/dreams")
async def list_dreams(
    limit: int = 10,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        text("""
            SELECT d.id, d.generated_for_date, d.summary, d.source_concept_count,
                   d.source_cluster_count, d.dream_type, d.created_at
            FROM agent_dreams d
            JOIN user_agent_states s ON d.agent_state_id = s.id
            WHERE s.user_id = :uid
            ORDER BY (d.created_at IS NULL), d.created_at DESC, d.generated_for_date DESC LIMIT :lim
        """),
        {"uid": current_user.id, "lim": max(1, min(limit, 50))},
    )
    dreams = []
    for row in result.fetchall():
        dreams.append({
            "id": row[0],
            "generated_for_date": row[1],
            "summary": row[2],
            "source_concept_count": row[3],
            "source_cluster_count": row[4],
            "dream_type": row[5],
            "created_at": str(row[6]) if row[6] else None,
        })
    return {"dreams": dreams, "count": len(dreams)}


@router.delete("/concepts/{concept_id}")
async def delete_concept(
    concept_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        text("DELETE FROM memory_concepts WHERE id = :id AND user_id = :uid RETURNING id"),
        {"id": concept_id, "uid": current_user.id},
    )
    deleted = result.fetchone()
    if not deleted:
        raise HTTPException(status_code=404, detail="Concept not found")
    await db.execute(
        text("DELETE FROM concept_cluster_members WHERE concept_id = :id"),
        {"id": concept_id},
    )
    await db.execute(
        text("DELETE FROM concept_relations WHERE source_id = :id OR target_id = :id"),
        {"id": concept_id},
    )
    await db.commit()
    return {"deleted": concept_id}


@router.delete("/all")
async def delete_all_memory(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """§10.4 全量擦除（GDPR Art. 17）：新概念层 + 文件层 + 旧 agent_memories；保留 raw 对话与笔记。"""
    uid = current_user.id
    await db.execute(
        text("DELETE FROM concept_relations WHERE user_id = :uid"),
        {"uid": uid},
    )
    await db.execute(
        text("DELETE FROM concept_cluster_members WHERE concept_id IN (SELECT id FROM memory_concepts WHERE user_id = :uid)"),
        {"uid": uid},
    )
    await db.execute(
        text("DELETE FROM memory_concepts WHERE user_id = :uid"),
        {"uid": uid},
    )
    await db.execute(
        text("DELETE FROM memory_clusters WHERE user_id = :uid"),
        {"uid": uid},
    )
    await db.execute(
        text("DELETE FROM subconscious_log WHERE user_id = :uid"),
        {"uid": uid},
    )
    await db.execute(
        text("DELETE FROM memory_episodes WHERE user_id = :uid"),
        {"uid": uid},
    )
    await db.execute(
        text("DELETE FROM memory_clarifications WHERE user_id = :uid"),
        {"uid": uid},
    )
    await db.execute(
        text("DELETE FROM memory_llm_calls WHERE user_id = :uid"),
        {"uid": uid},
    )
    # 旧扁平记忆条目（agent_memories）
    await db.execute(
        text("DELETE FROM agent_memories WHERE agent_state_id IN (SELECT id FROM user_agent_states WHERE user_id = :uid)"),
        {"uid": uid},
    )
    # 共享摘要与 dream（含 legacy）
    await db.execute(
        text("DELETE FROM agent_dreams WHERE agent_state_id IN (SELECT id FROM user_agent_states WHERE user_id = :uid)"),
        {"uid": uid},
    )
    await db.execute(
        text("UPDATE user_agent_states SET memory_summary = NULL, dream_summary = NULL, latest_dream_id = NULL, total_concept_count = 0, total_episode_count = 0 WHERE user_id = :uid"),
        {"uid": uid},
    )
    await db.commit()

    # 文件层记忆（AGENT.md / USER.md）
    import asyncio as _asyncio
    import shutil as _shutil
    from app.tools.memory import _get_memory_dir

    def _remove_file_layer() -> None:
        user_dir = _get_memory_dir() / str(uid)
        if user_dir.exists():
            _shutil.rmtree(user_dir, ignore_errors=True)

    await _asyncio.to_thread(_remove_file_layer)

    # 进程内缓存同步失效（GDPR 擦除彻底性：BM25 文档、会话缓存、复现滑窗）
    try:
        from app.services import memory_bm25 as _bm
        for idx_map in (_bm._name_indexes, _bm._desc_indexes, _bm._epi_indexes, _bm._sub_indexes):
            idx_map.pop(str(uid), None)
    except Exception:
        pass
    try:
        from app.services import memory_retrieval_service as _rs
        for key in [k for k in _rs._session_cache if k.startswith(f"{uid}:")]:
            _rs._session_cache.pop(key, None)
        for window in (_rs._concept_window, _rs._episodic_window, _rs._subconscious_window):
            window.pop(str(uid), None)
    except Exception:
        pass
    return {"deleted": "all"}


@router.get("/clarifications")
async def list_clarifications(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """§10.4：澄清记录列表（revert 端点的前置——用户需能看到已应用澄清的 ID）。"""
    result = await db.execute(
        text("""
            SELECT id, original_text, correction_type, affected_concept_ids,
                   new_description, confidence, applied, applied_at, created_at
            FROM memory_clarifications
            WHERE user_id = :uid
            ORDER BY created_at DESC LIMIT :lim
        """),
        {"uid": current_user.id, "lim": max(1, min(limit, 200))},
    )
    clarifications = []
    for row in result.fetchall():
        clarifications.append({
            "id": row[0],
            "original_text": row[1],
            "correction_type": row[2],
            "affected_concept_ids": row[3],
            "new_description": row[4],
            "confidence": row[5],
            "applied": bool(row[6]),
            "applied_at": str(row[7]) if row[7] else None,
            "created_at": str(row[8]) if row[8] else None,
        })
    return {"clarifications": clarifications, "count": len(clarifications)}


@router.post("/clarifications/{clarification_id}/revert")
async def revert_clarification_endpoint(
    clarification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """§9.6/§10.4：撤销已应用的澄清（negate 恢复有效；refine/add_constraint 回滚旧版本）。"""
    from app.services.memory_clarification_service import revert_clarification
    ok = await revert_clarification(db, current_user.id, clarification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Clarification not found, not applied, or irreversible (forget)")
    await db.commit()
    return {"reverted": clarification_id}


@router.post("/clarifications/{clarification_id}/apply")
async def apply_clarification_endpoint(
    clarification_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """手动应用 pending 澄清（B-5：confidence < 0.8 落库后的人工确认途径）。"""
    from app.services.memory_clarification_service import apply_clarification
    ok = await apply_clarification(db, current_user.id, clarification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Clarification not found or already applied")
    await db.commit()
    return {"applied": clarification_id}


@router.put("/{user_id}/cost_governance/reset")
async def reset_cost_governance(
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """§9.10：admin 手动 reset 用户降级状态（本人可 reset 自己）。"""
    if current_user.id != user_id and getattr(current_user, "role", "user") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    from app.services.memory_cost_governance_service import reset_user_degrade
    await reset_user_degrade(user_id, db)
    await db.commit()
    return {"reset": user_id}


@router.get("/cost_governance/status")
async def get_cost_governance_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """§9.10：用户在设置页查看自己的降级状态与触发原因。"""
    from app.services.memory_cost_governance_service import get_user_degrade_status
    return await get_user_degrade_status(db, current_user.id)


# ---------- B-4 用户角色管理（admin 段，独立前缀） ----------

admin_users_router = APIRouter(prefix="/api/admin", tags=["admin-users"])


# ---------- §8.5 迁移管理端点（admin 段） ----------

admin_router = APIRouter(prefix="/api/admin/memory", tags=["memory-admin"])


@admin_users_router.get("/users")
async def admin_list_users(
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """admin 用户列表（B-4：角色管理前置；初始 admin 经 psql 提升，见 README FAQ）。"""
    result = await db.execute(text("SELECT id, username, role, is_active, created_at FROM users ORDER BY created_at ASC"))
    return [
        {"id": r[0], "username": r[1], "role": r[2], "is_active": r[3],
         "created_at": str(r[4]) if r[4] else None}
        for r in result.fetchall()
    ]


@admin_users_router.put("/users/{user_id}/role")
async def admin_set_user_role(
    user_id: str,
    body: dict,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """admin 角色变更（B-4）：role ∈ {user, admin}。"""
    role = str(body.get("role") or "").strip()
    if role not in ("user", "admin"):
        raise HTTPException(status_code=422, detail="role must be user|admin")
    if user_id == current_user.id:
        raise HTTPException(status_code=403, detail="cannot change your own role")
    result = await db.execute(
        text("UPDATE users SET role = :r WHERE id = :id RETURNING id, username, role"),
        {"r": role, "id": user_id},
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    await db.commit()
    return {"id": row[0], "username": row[1], "role": row[2]}


@admin_router.post("/migration/run")
async def admin_migration_run(
    body: dict | None = None,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """§8.5.1：手动触发迁移。user_id 缺省全量排队；dry_run=true 只读统计。"""
    from app.services import memory_migration_service as mig
    body = body or {}
    user_id = body.get("user_id")
    dry_run = bool(body.get("dry_run"))

    if dry_run:
        if user_id:
            return {"dry_run": [await mig.migrate_user_dry_run(db, user_id)]}
        result = await db.execute(text("SELECT user_id FROM user_agent_states"))
        stats = []
        for (uid,) in result.fetchall():
            stats.append(await mig.migrate_user_dry_run(db, uid))
        return {"dry_run": stats}

    if user_id:
        progress = (body.get("reset_attempts") and True) or False
        if progress:
            meta, prog = await mig._load_progress(db, user_id)
            prog["attempts"] = 0
            prog["status"] = "pending"
            prog["next_retry_at"] = None
            await mig._save_progress(db, user_id, meta, prog)
            await db.commit()
        status = await mig.migrate_user(user_id)
        return {"user_id": user_id, "status": status}

    result = await mig.enqueue_pending_migrations()
    return result


@admin_router.post("/migration/rollback")
async def admin_migration_rollback(
    body: dict,
    current_user: User = Depends(require_admin),
):
    """§8.5.5：单用户回滚。"""
    user_id = (body or {}).get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")
    from app.services import memory_migration_service as mig
    return await mig.rollback_user(user_id)


@admin_router.get("/migration/status")
async def admin_migration_status(
    user_id: str | None = None,
    current_user: User = Depends(require_admin),
):
    """§8.5.1：各用户迁移进度（读取 metadata_json.migration）。"""
    from app.services import memory_migration_service as mig
    return {"users": await mig.get_migration_status(user_id)}


@router.post("/concepts/{concept_id}/forget")
async def forget_concept(
    concept_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        text("UPDATE memory_concepts SET valid_to = CURRENT_TIMESTAMP, weight = 0, status = 'forgotten', updated_at = CURRENT_TIMESTAMP WHERE id = :id AND user_id = :uid RETURNING id"),
        {"id": concept_id, "uid": current_user.id},
    )
    updated = result.fetchone()
    if not updated:
        raise HTTPException(status_code=404, detail="Concept not found")
    await db.commit()
    return {"forgotten": concept_id}


# =====================================================================
# ---- weave-mem 解耦新增端点（兼容家族 script/linux/smoke_test.sh）----
# chatbot 的 memory.py 无 /status、POST /concepts、POST /recall 三个端点
# （chatbot 走内部服务调用与 chat 域注入）。weave-mem 作为独立记忆服务
# 对外暴露这些入口，全部调用服务层函数（memory_concept_service /
# memory_retrieval_service），不裸 ORM 写库。
# =====================================================================


@router.get("/status")
async def memory_status(current_user: User = Depends(get_current_user)):
    """家族兼容端点：pgvector 状态探测（保留原 healthz 语义）。"""
    from app.db import migrations as _m
    payload = {
        "status": "ok",
        "service": "weave-mem",
        "pgvector": bool(_m.PGVECTOR_AVAILABLE),
        "embedding_provider_configured": bool(
            (config_memory_base := get_config_memory_base()) != ""
        ),
    }
    del config_memory_base
    return payload


def get_config_memory_base() -> str:
    from app.core.config import get_config
    cfg = get_config()
    return (cfg.memory.get("embedding_api_base") or "").strip() or cfg.api_base_url


@router.post("/concepts", status_code=201)
async def create_concept_endpoint(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """家族兼容端点：经 memory_concept_service.create_concept 写入服务层。

    与 chatbot 内部写入路径一致：embedding 生成、BM25 索引更新、
    集群归属全部由服务层完成；embedding provider 未配置时概念照常落库
    （无向量列值），召回走 BM25/文本路径。
    """
    from app.services.memory_concept_service import create_concept

    canonical_name = str(body.get("canonical_name") or "").strip()
    if not canonical_name:
        raise HTTPException(status_code=422, detail="canonical_name required")
    concept_id = await create_concept(
        db,
        current_user.id,
        canonical_name,
        description_short=str(body.get("description_short") or "")[:80],
        description_full=str(body.get("description_full") or ""),
        source_trust=str(body.get("source_trust") or "user_stated"),
        memory_type=str(body.get("memory_type") or "semantic"),
        source_type="manual",
    )
    await db.commit()
    if not concept_id:
        raise HTTPException(status_code=500, detail="concept creation failed")
    return {"id": concept_id, "canonical_name": canonical_name}


@router.post("/recall")
async def recall_endpoint(
    body: dict,
    include_meta: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """家族兼容端点：调用 memory_retrieval_service.retrieve_and_build_context。

    无 embedding provider 时管线自动走 BM25/文本路径（Stage 1 jieba 词法
    检索 + 摘要兜底），响应 mode 标注实际路径。
    """
    from app.services.memory_retrieval_service import retrieve_and_build_context

    query = str(body.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=422, detail="query required")
    messages = [{"role": "user", "content": query}]
    mode = "embedding" if get_config_memory_base() else "text"
    if include_meta:
        from app.services.memory_retrieval_service import retrieve_with_meta
        context, memory_ids, top_gate_score = await retrieve_with_meta(db, current_user.id, messages)
        return {"mode": mode, "query": query, "context": context,
                "meta": {"memory_ids": memory_ids, "top_gate_score": top_gate_score}}
    context = await retrieve_and_build_context(db, current_user.id, messages)
    return {"mode": mode, "query": query, "context": context}


# =====================================================================
# ---- 家族接入端点（缺口 A/B：潜意识摄入 + 澄清处理）----
# ingest：chatbot 中由 agent 工具（tools/memory.py ingest_raw_unit）执行；
# weave-mem 无 agent 域，改为 HTTP 暴露——写入 SubconsciousLog 后由
# memory_scheduler.scan_recurrence 自动提炼概念，闭环与 chatbot 一致。
# clarifications/process：chatbot 中由 chat 流 detect_signal→process_clarification
# 自动触发；weave-mem 无 chat 域，改为显式调用端点。
# =====================================================================


@router.post("/ingest")
async def ingest_endpoint(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """潜意识摄入：文本写入 SubconsciousLog（PII scrub + embedding）。

    与 chatbot agent 工具同款语义；embedding provider 未配置时 503 显式降级
    （scan_recurrence 依赖向量邻居，无向量则摄入无意义）。
    """
    content = str(body.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="content required")
    if len(content) < 5:
        raise HTTPException(status_code=422, detail="content too short (min 5 chars)")
    unit_kind = str(body.get("unit_kind") or "message")
    if unit_kind not in ("message", "note", "file_memory"):
        raise HTTPException(status_code=422, detail="unit_kind must be message|note|file_memory")
    source_ids = body.get("source_ids") or []
    if not isinstance(source_ids, list):
        raise HTTPException(status_code=422, detail="source_ids must be a list")
    source_ids = [str(s) for s in source_ids if str(s).strip()][:50]
    if not get_config_memory_base():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="embedding provider not configured; subconscious ingest requires vector embedding",
        )

    from app.services.memory_subconscious_service import ingest_raw_unit
    unit_id = await ingest_raw_unit(db, current_user.id, unit_kind, content[:1000], source_ids)
    await db.commit()
    if not unit_id:
        # provider 已配置但请求失败（网络/密钥/熔断）→ 502 上游依赖故障，可重试
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="embedding provider request failed; ingestion not persisted",
        )
    return {"unit_id": unit_id, "ingested": True}


@router.post("/adoption")
async def record_answer_adoption_endpoint(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """采纳反馈闭环（P1-①）：回答文本引用本轮注入概念名/别名 → 权重/边权 +0.02。

    weave-mem 无 chat 流，由接入方在回答定稿时显式调用（ingest/clarify 同款
    模式）；入参 injected_ids 取 recall?include_meta=true 的 meta.memory_ids。
    写回 fail-open：返回摘要，绝不 500 拖垮调用方主流程。
    """
    answer_text = str(body.get("answer_text") or "").strip()
    if not answer_text:
        raise HTTPException(status_code=422, detail="answer_text required")
    injected_ids = body.get("injected_ids") or []
    if not isinstance(injected_ids, list):
        raise HTTPException(status_code=422, detail="injected_ids must be a list")
    injected_ids = [str(s) for s in injected_ids if str(s).strip()][:50]

    from app.services.memory_adoption_service import record_answer_adoption
    summary = await record_answer_adoption(db, current_user.id, injected_ids, answer_text)
    try:
        await db.commit()
    except Exception:
        # fail-open 契约：写回失败绝不 500（回滚后按 skipped 报告）
        await db.rollback()
        summary = {"adopted": [], "skipped": 1}
    matched = list(summary.get("adopted") or [])
    return {"matched": matched, "adopted": len(matched), "relation_bumped": len(matched)}


@router.get("/recall_log")
async def list_recall_log(
    before_id: str | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """C1 召回台账读出：仅元数据（不含记忆内容），created_at 倒序游标分页。"""
    from sqlalchemy import text as _text
    limit = max(1, min(int(limit or 50), 200))
    where = "user_id = :uid"
    params: dict = {"uid": current_user.id, "lim": limit}
    if before_id:
        # 复合 keyset（created_at,id）——单 id 游标在 UUID 主键上会漏行/重行
        where += (" AND (created_at, id) < (SELECT created_at, id FROM memory_recall_log "
                  "WHERE id = :before AND user_id = :uid)")
        params["before"] = str(before_id)
    rows = (await db.execute(_text(
        f"SELECT id, query_hash, candidate_ids, tier_scores, gate_score, budget_chars, "
        f"injected_chars, truncated, elapsed_ms, cache_hit, created_at "
        f"FROM memory_recall_log WHERE {where} ORDER BY created_at DESC, id DESC LIMIT :lim"),
        params,
    )).fetchall()
    total = (await db.execute(_text(
        "SELECT COUNT(*) FROM memory_recall_log WHERE user_id = :uid"),
        {"uid": current_user.id},
    )).scalar() or 0
    items = [{
        "id": r[0], "query_hash": r[1], "candidate_ids": r[2], "tier_scores": r[3],
        "gate_score": r[4], "budget_chars": r[5], "injected_chars": r[6],
        "truncated": bool(r[7]), "elapsed_ms": r[8], "cache_hit": bool(r[9]),
        "created_at": r[10].isoformat() if r[10] else None,
    } for r in rows]
    return {"items": items, "total": total}


@router.post("/clarifications/process")
async def process_clarification_endpoint(
    body: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """澄清处理：detect_signal 规则预筛 → process_clarification（LLM 判定）。

    confidence >= clarification_auto_apply_threshold（默认 0.8）时自动应用
    修正并写入 memory_clarifications 审计（chatbot chat 流同款逻辑）。
    """
    user_message = str(body.get("user_message") or "").strip()
    if not user_message:
        raise HTTPException(status_code=422, detail="user_message required")

    from app.services.memory_clarification_service import detect_signal, process_clarification
    if not detect_signal(user_message):
        return {"detected": False, "clarification": None}

    conversation_id = str(body.get("conversation_id") or "")[:36] or None
    message_id = str(body.get("message_id") or "")[:36] or None
    result = await process_clarification(
        db, current_user.id, user_message,
        conversation_id=conversation_id,
        message_id=message_id,
    )
    return {"detected": True, "clarification": result}

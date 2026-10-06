import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_config
from app.db.database import (
    MemoryConcept,
    MemoryEpisode,
)
from app.services.memory_embedding_service import embed_text
from app.services.memory_security import scrub_pii, scan_injection

config = get_config()
logger = logging.getLogger(__name__)

TRUST_CAPS = {
    "user_stated": 1.0,
    "user_authored": 1.0,
    "agent_inferred": 0.7,
    "external": 0.5,
}


def _get_trust_cap(source_trust: str) -> float:
    return TRUST_CAPS.get(source_trust, 1.0)


# 2026-08-10 权重语义修正：importance = 持久重要性（用户身份/偏好/关系/长期事实高，
# 一次性任务/临时话题低），与 weight（被召回的热度）正交。LLM 提取未提供时按来源兜底。
# 注意：兜底值刻意低于衰减豁免阈值 0.8（memory_weight_service），避免 LLM 未输出
# importance 时所有 user_stated 概念自动永久免衰减（A4.9 审查 I1）。
_IMPORTANCE_FALLBACK = {
    "user_stated": 0.75,
    "user_authored": 0.75,
    "agent_inferred": 0.5,
    "external": 0.35,
}


def _sanitize_importance(cdata: dict) -> float:
    """读取 LLM 输出的 importance（0-1），非法/越界回退来源规则值。"""
    raw = cdata.get("importance")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            val = float(raw)
            if 0.0 <= val <= 1.0:
                return val
        except (TypeError, ValueError):
            pass
    trust = cdata.get("source_trust", "agent_inferred")
    return float(_IMPORTANCE_FALLBACK.get(trust, 0.5))




async def get_concepts_for_extraction(db: AsyncSession, user_id: str, limit: int = 200,
                                      agent_id: str | None = None) -> list[dict]:
    from app.services.memory_scope import agent_scope_sql, agent_scope_params
    result = await db.execute(
        text(f"""
            SELECT id, canonical_name, aliases, description_short, source_trust,
                   memory_type, activation_strength, status
            FROM memory_concepts
            WHERE user_id = :uid AND status IN ('active','silent') AND valid_to IS NULL {agent_scope_sql(agent_id)}
            ORDER BY weight DESC LIMIT :lim
        """),
        {"uid": user_id, "lim": limit, **agent_scope_params(agent_id)},
    )
    return [
        {"id": r[0], "canonical_name": r[1], "aliases": r[2], "description_short": r[3],
         "source_trust": r[4], "memory_type": r[5], "activation_strength": r[6], "status": r[7]}
        for r in result.fetchall()
    ]


async def get_clusters_for_extraction(db: AsyncSession, user_id: str, limit: int = 50,
                                      agent_id: str | None = None) -> list[dict]:
    from app.services.memory_scope import agent_scope_sql, agent_scope_params
    result = await db.execute(
        text(f"SELECT id, name, summary FROM memory_clusters WHERE user_id = :uid {agent_scope_sql(agent_id)} ORDER BY weight DESC LIMIT :lim"),
        {"uid": user_id, "lim": limit, **agent_scope_params(agent_id)},
    )
    return [{"id": r[0], "name": r[1], "summary": r[2]} for r in result.fetchall()]


async def extract_concepts_from_recurrence(
    db: AsyncSession, user_id: str, llm_output: dict, source_unit_ids: list[str],
    raw_texts: list[str], agent_id: str | None = None,
) -> tuple[list[str], Optional[str]]:
    from app.services.memory_scope import agent_scope_sql, agent_scope_params
    concept_ids: list[str] = []
    episode_id: Optional[str] = None

    concepts_data = llm_output.get("concepts", [])
    if not isinstance(concepts_data, list):
        concepts_data = []

    for cdata in concepts_data:
        cid = await _process_single_concept(db, user_id, cdata, source_unit_ids, raw_texts,
                                            agent_id=agent_id)
        if cid:
            concept_ids.append(cid)

    epic_data = llm_output.get("episodic", {})
    if epic_data and epic_data.get("narrative"):
        episode_id = await _handle_episode_from_extraction(db, user_id, epic_data, source_unit_ids, concept_ids,
                                                           agent_id=agent_id)

    # D1（门 deterministic_edges_enabled，默认关）：提炼后建确定性共现边（fail-open）
    if concept_ids and config.memory_retrieval.get("deterministic_edges_enabled", False):
        try:
            from app.services.memory_cluster_service import build_deterministic_edges
            await build_deterministic_edges(db, user_id, concept_ids, agent_id=agent_id)
        except Exception:
            logger.debug("deterministic edges hook failed (fail-open)", exc_info=True)

    # §5.1.d step 4：同步 total_concept_count / total_episode_count（精确写本作用域 UAS 行；
    # 读用 scope 片段会同时命中共享行——写定位必须精确，D-9 修复轮 A4.9 I2）
    _exact_w = "AND agent_id = :agent_scope" if agent_id else "AND agent_id IS NULL"
    await db.execute(
        text(f"""UPDATE user_agent_states SET
            total_concept_count = (SELECT COUNT(*) FROM memory_concepts WHERE user_id = :uid AND valid_to IS NULL {agent_scope_sql(agent_id)}),
            total_episode_count = (SELECT COUNT(*) FROM memory_episodes WHERE user_id = :uid AND valid_to IS NULL {agent_scope_sql(agent_id)})
            WHERE user_id = :uid {_exact_w}"""),
        {"uid": user_id, **agent_scope_params(agent_id)},
    )

    await db.commit()
    return concept_ids, episode_id


async def _handle_episode_from_extraction(
    db: AsyncSession, user_id: str, epic_data: dict,
    source_unit_ids: list[str], concept_ids: list[str],
    agent_id: str | None = None,
) -> Optional[str]:
    from app.services.memory_episode_service import create_episode, merge_episode, merge_first

    merge_with = epic_data.get("merge_with_episode_id")
    narrative = epic_data.get("narrative", "")
    valid_from_str = epic_data.get("valid_from")
    # D1 P/L（上游 f5d2f6401）：提取 JSON 的 participants/locations 贯穿到 episode 写路径
    participants = epic_data.get("participants")
    locations = epic_data.get("locations")
    if isinstance(participants, list):
        participants = json.dumps(participants, ensure_ascii=False)
    if isinstance(locations, list):
        locations = json.dumps(locations, ensure_ascii=False)

    valid_from = datetime.utcnow()
    if valid_from_str:
        try:
            valid_from = datetime.fromisoformat(valid_from_str.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            pass

    if merge_with and isinstance(merge_with, str):
        target = await db.get(MemoryEpisode, merge_with)
        if target is not None and target.user_id == user_id and (target.agent_id or None) == (agent_id or None):
            await merge_episode(db, merge_with, narrative, source_unit_ids,
                                participants=participants, locations=locations)
            return merge_with
        logger.warning("merge_with_episode_id rejected (missing or cross-user): %s", merge_with)

    # §4.9 merge-first：LLM 未给 merge id 时，按 sim≥0.85 最近邻 in-place 合并
    try:
        merged_id = await merge_first(db, user_id, narrative, source_unit_ids,
                                      participants=participants, locations=locations,
                                      agent_id=agent_id)
        if merged_id:
            return merged_id
    except Exception:
        logger.debug("merge_first failed, fallback to create", exc_info=True)

    eid = await create_episode(db, user_id, narrative, valid_from, source_unit_ids, concept_ids,
                               participants=participants, locations=locations, agent_id=agent_id)
    return eid


async def _process_single_concept(
    db: AsyncSession, user_id: str, cdata: dict,
    source_unit_ids: list[str], raw_texts: list[str],
    agent_id: str | None = None,
) -> Optional[str]:
    canonical_name = (cdata.get("canonical_name") or "").strip()
    if not canonical_name:
        return None

    description_short = (cdata.get("description_short") or "")[:80]
    description_full = cdata.get("description_full") or ""
    aliases = cdata.get("aliases", [])
    source_trust = cdata.get("source_trust", "agent_inferred")
    memory_type = cdata.get("memory_type", "semantic")
    cluster_suggestion = cdata.get("cluster_suggestion", "")
    match_existing_id = cdata.get("match_existing_id")

    desc_safe, _ = scrub_pii(description_short)
    desc_full_safe, _ = scrub_pii(description_full)
    desc_full_safe = desc_full_safe[:5000]

    scan = scan_injection(canonical_name + desc_safe + desc_full_safe)
    if scan:
        logger.warning("Concept creation blocked for user %s: %s", user_id, scan)
        return None

    weight_init = float(config.memory_concept.get("weight_init", 0.5))
    weight_evidence = float(config.memory_concept.get("weight_evidence_boost", 0.03))
    trust_cap = _get_trust_cap(source_trust)
    maturation_window = int(config.memory_concept.get("maturation_window_days", 21))

    aliases_json = json.dumps(aliases, ensure_ascii=False) if aliases else "[]"
    source_unit_json = json.dumps(source_unit_ids, ensure_ascii=False)
    now = datetime.utcnow()
    status = "active" if source_trust in ("user_stated", "user_authored") else "silent"
    activation_strength = 1.0 if source_trust in ("user_stated", "user_authored") else 0.0

    if match_existing_id and isinstance(match_existing_id, str) and match_existing_id.strip():
        existing = await db.get(MemoryConcept, match_existing_id)
        if existing is not None and (existing.user_id != user_id
                                     or (existing.agent_id or None) != (agent_id or None)):
            logger.warning("match_existing_id rejected (cross-user or cross-agent): %s", match_existing_id)
            existing = None
        if existing:
            existing.description_full = _merge_description_full(existing.description_full or "", desc_full_safe)
            existing.description_short = desc_safe or existing.description_short
            existing.aliases = _merge_aliases(existing.aliases, aliases_json)
            existing.weight = min(existing.weight + weight_evidence, trust_cap)
            # importance 取 max：合并提升不下降（一次性话题证据不得稀释用户偏好）
            new_importance = _sanitize_importance(cdata)
            existing_imp = existing.importance
            if existing_imp is None:
                existing_imp = 0.5
            existing.importance = max(existing_imp, new_importance)
            if "importance" in cdata:
                existing.importance_evaluated = True
            existing.recurrence_count = (existing.recurrence_count or 0) + 1
            existing.last_recurrence_at = now
            if source_trust in ("user_stated", "user_authored") and existing.source_trust == "agent_inferred":
                existing.source_trust = source_trust
                existing.activation_strength = 1.0
                existing.status = "active"
            elif (existing.status == "silent" and (existing.recurrence_count or 0) >= 2
                  and existing.created_at and (now - existing.created_at).days <= maturation_window):
                # §5.3.5 Silent Maturation 路径 #1：窗口内二次 recurrence → 升 active
                existing.activation_strength = 1.0
                existing.status = "active"
            existing.embedding = await _generate_embedding(canonical_name, aliases, desc_safe)
            existing.embedding_updated_at = now
            existing.updated_at = now

            existing_source_ids = _parse_json_array(existing.source_unit_ids)
            existing_source_ids.extend(source_unit_ids)
            existing.source_unit_ids = json.dumps(existing_source_ids, ensure_ascii=False)

            if cluster_suggestion:
                await _ensure_cluster_membership(db, user_id, existing.id, cluster_suggestion, agent_id=agent_id)

            await db.flush()
            _update_bm25_on_concept_change(existing.id, user_id, canonical_name, aliases_json, desc_full_safe, agent_id=agent_id)
            return existing.id

    concept_id = str(uuid.uuid4())
    attr = config.memory_fatigue
    if memory_type == "semantic":
        stability = float(attr.get("semantic_stability_init_days", 14))
    elif memory_type == "episodic":
        stability = float(attr.get("episodic_stability_init_days", 7))
    else:
        stability = float(attr.get("semantic_stability_init_days", 14))

    emb = await _generate_embedding(canonical_name, aliases, desc_safe)

    concept = MemoryConcept(
        id=concept_id,
        user_id=user_id,
        agent_id=agent_id,
        canonical_name=canonical_name,
        description_short=desc_safe,
        description_full=desc_full_safe,
        aliases=aliases_json,
        weight=weight_init,
        importance=_sanitize_importance(cdata),
        importance_evaluated=("importance" in cdata),
        stability=stability,
        source_trust=source_trust,
        memory_type=memory_type,
        activation_strength=activation_strength,
        status=status,
        source_type="extracted",
        source_unit_ids=source_unit_json,
        recurrence_count=1,
        last_recurrence_at=now,
        embedding=emb,
        embedding_updated_at=now,
    )
    db.add(concept)
    await db.flush()

    if cluster_suggestion:
        await _ensure_cluster_membership(db, user_id, concept_id, cluster_suggestion, agent_id=agent_id)

    _update_bm25_on_concept_change(concept_id, user_id, canonical_name, aliases_json, desc_full_safe, agent_id=agent_id)
    return concept_id


def _merge_description_full(old: str, new: str) -> str:
    if not old:
        return new
    if not new:
        return old
    return (old + "\n\n---\n\n" + new)[:5000]


def _merge_aliases(existing_raw: Optional[str], new_json: str) -> str:
    existing = _parse_json_array(existing_raw)
    new = _parse_json_array(new_json)
    merged = list(dict.fromkeys(existing + new))
    return json.dumps(merged, ensure_ascii=False)


def _parse_json_array(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return val
        return []
    except (json.JSONDecodeError, TypeError):
        return []


async def _generate_embedding(name: str, aliases: list, short_desc: str) -> Optional[list]:
    aliases_str = " ".join(aliases) if aliases else ""
    text = f"{name} {aliases_str} {short_desc}".strip()
    return await embed_text(text)


async def _ensure_cluster_membership(
    db: AsyncSession, user_id: str, concept_id: str, cluster_name: str,
    agent_id: str | None = None,
) -> None:
    from app.services.memory_scope import agent_scope_params
    if not cluster_name.strip():
        return
    _exact_w = "AND agent_id = :agent_scope" if agent_id else "AND agent_id IS NULL"
    result = await db.execute(
        text(f"SELECT id, embedding, member_count FROM memory_clusters WHERE user_id = :uid AND name = :nm {_exact_w}"),
        {"uid": user_id, "nm": cluster_name, **agent_scope_params(agent_id)},
    )
    row = result.fetchone()
    if row:
        cluster_id = row[0]
        await db.execute(
            text("INSERT INTO concept_cluster_members (concept_id, cluster_id) VALUES (:cid, :clid) ON CONFLICT DO NOTHING"),
            {"cid": concept_id, "clid": cluster_id},
        )
        new_count = (row[2] or 0) + 1
        await db.execute(
            text("UPDATE memory_clusters SET member_count = :mc, updated_at = CURRENT_TIMESTAMP WHERE id = :clid"),
            {"mc": new_count, "clid": cluster_id},
        )
    else:
        cid = str(uuid.uuid4())
        await db.execute(
            text("INSERT INTO memory_clusters (id, user_id, agent_id, name, member_count) VALUES (:id, :uid, :aid, :nm, 1)"),
            {"id": cid, "uid": user_id, "aid": agent_id, "nm": cluster_name},
        )
        await db.execute(
            text("INSERT INTO concept_cluster_members (concept_id, cluster_id) VALUES (:cid, :clid) ON CONFLICT DO NOTHING"),
            {"cid": concept_id, "clid": cid},
        )
        cluster_id = cid
    # F-4a（上游 A1/DC2）：成员变更后刷新簇 embedding（成员向量均值 + 溯源模型）
    try:
        from app.services.memory_cluster_service import _update_cluster_embedding
        await _update_cluster_embedding(db, cluster_id)
    except Exception:
        logger.debug("cluster embedding refresh failed (fail-open)", exc_info=True)


def _update_bm25_on_concept_change(concept_id: str, user_id: str, name: str, aliases_json: str, desc_full: str,
                                   agent_id: str | None = None) -> None:
    try:
        from app.services.memory_bm25 import update_concept_in_indexes
        aliases = _parse_json_array(aliases_json)
        name_text = " ".join([name] + [str(a) for a in aliases])
        update_concept_in_indexes(concept_id, user_id, agent_id, name_text, desc_full)
    except Exception:
        pass




async def create_concept(
    db: AsyncSession, user_id: str, canonical_name: str, description_short: str = "",
    description_full: str = "", source_trust: str = "user_stated",
    memory_type: str = "semantic", source_type: str = "manual",
    agent_id: str | None = None,
) -> Optional[str]:
    canonical_name = canonical_name.strip()
    if not canonical_name:
        return None
    desc_short = (description_short or "")[:80]
    desc_full = (description_full or "")[:5000]
    aliases_json = "[]"
    activation = 1.0 if source_trust in ("user_stated", "user_authored") else 0.0
    status = "active" if source_trust in ("user_stated", "user_authored") else "silent"
    attr = config.memory_fatigue
    stability = float(attr.get("semantic_stability_init_days", 14))
    emb = await _generate_embedding(canonical_name, [], desc_short)

    cid = str(uuid.uuid4())
    concept = MemoryConcept(
        id=cid,
        user_id=user_id,
        agent_id=agent_id,
        canonical_name=canonical_name,
        description_short=desc_short,
        description_full=desc_full,
        aliases=aliases_json,
        weight=float(config.memory_concept.get("weight_init", 0.5)),
        stability=stability,
        source_trust=source_trust,
        memory_type=memory_type,
        activation_strength=activation,
        status=status,
        source_type=source_type,
        # 精选路径（profile/dreaming）：按来源分派——用户画像事实 0.9；推断类 0.6
        importance=(0.9 if source_trust in ("user_stated", "user_authored") else 0.6),
        importance_evaluated=True,
        embedding=emb,
        embedding_updated_at=datetime.utcnow() if emb else None,
    )
    db.add(concept)
    await db.flush()
    _update_bm25_on_concept_change(cid, user_id, canonical_name, aliases_json, desc_full, agent_id=agent_id)
    return cid


async def update_concept_description(
    db: AsyncSession, concept_id: str, description_short: str, description_full: str,
) -> bool:
    concept = await db.get(MemoryConcept, concept_id)
    if not concept:
        return False
    concept.description_short = description_short[:80]
    concept.description_full = description_full[:5000]
    emb = await _generate_embedding(concept.canonical_name, _parse_json_array(concept.aliases), concept.description_short)
    if emb:
        concept.embedding = emb
        concept.embedding_updated_at = datetime.utcnow()
        _update_bm25_on_concept_change(concept_id, concept.user_id, concept.canonical_name, concept.aliases or "[]", concept.description_full or "", agent_id=concept.agent_id)
    concept.updated_at = datetime.utcnow()
    await db.flush()
    return True


async def merge_concepts(db: AsyncSession, kept_id: str, merged_id: str) -> bool:
    kept = await db.get(MemoryConcept, kept_id)
    merged = await db.get(MemoryConcept, merged_id)
    if not kept or not merged:
        return False

    kept_aliases = _parse_json_array(kept.aliases)
    merged_aliases = _parse_json_array(merged.aliases)
    merged_aliases.append(merged.canonical_name)
    all_aliases = list(dict.fromkeys(kept_aliases + merged_aliases))
    kept.aliases = json.dumps(all_aliases, ensure_ascii=False)

    kept.description_full = _merge_description_full(kept.description_full or "", merged.description_full or "")
    kept.weight = max(kept.weight, merged.weight)
    kept.source_trust = kept.source_trust if kept.source_trust in ("user_stated", "user_authored") else merged.source_trust
    kept.stability = max(kept.stability, merged.stability)
    emb = await _generate_embedding(kept.canonical_name, all_aliases, kept.description_short or "")
    if emb:
        kept.embedding = emb
        kept.embedding_updated_at = datetime.utcnow()

    merged.valid_to = datetime.utcnow()
    merged.superseded_by = kept_id
    merged.updated_at = datetime.utcnow()

    kept.updated_at = datetime.utcnow()
    await db.flush()

    # 成员关系迁移：先删冲突再迁移（UPDATE 不支持 ON CONFLICT）
    await db.execute(
        text("DELETE FROM concept_cluster_members WHERE concept_id = :m AND cluster_id IN (SELECT cluster_id FROM concept_cluster_members WHERE concept_id = :k)"),
        {"m": merged_id, "k": kept_id},
    )
    await db.execute(
        text("UPDATE concept_cluster_members SET concept_id = :k WHERE concept_id = :m"),
        {"k": kept_id, "m": merged_id},
    )

    _update_bm25_on_concept_change(kept_id, kept.user_id, kept.canonical_name, kept.aliases or "[]", kept.description_full or "", agent_id=kept.agent_id)
    return True


async def reconcile_concept_sources(db: AsyncSession, user_id: str, agent_id: str | None = None) -> int:
    """§5.1.d step 3 / §3.3 对账 pass：source_raw_ids 与 source_unit_ids 全部失效
    （raw 条目已删除/不存在）的概念标记 needs_review=TRUE，由夜间 consolidation 复核。"""
    from app.services.memory_scope import agent_scope_sql, agent_scope_params
    result = await db.execute(
        text(f"""SELECT id, source_raw_ids, source_unit_ids FROM memory_concepts
                WHERE user_id = :uid AND valid_to IS NULL AND needs_review = FALSE
                  AND (source_raw_ids IS NOT NULL OR source_unit_ids IS NOT NULL) {agent_scope_sql(agent_id)}"""),
        {"uid": user_id, **agent_scope_params(agent_id)},
    )
    flagged = 0
    for row in result.fetchall():
        cid = row[0]
        raw_ids = _parse_json_array(row[1])
        unit_ids = _parse_json_array(row[2])
        if not raw_ids and not unit_ids:
            continue

        unit_alive = 0
        if unit_ids:
            from app.services.memory_subconscious_service import _ids_in_sql
            _sql, _params = _ids_in_sql("id", unit_ids)
            r = await db.execute(
                text(f"SELECT COUNT(*) FROM subconscious_log WHERE {_sql}"),
                _params,
            )
            unit_alive = r.scalar() or 0

        raw_alive = 0
        for rid in raw_ids:
            if not isinstance(rid, str):
                continue
            if rid.startswith("legacy:"):
                raw_alive += 1  # 迁移键永久有效
                continue
            r = await db.execute(
                text("""SELECT (
                        (SELECT COUNT(*) FROM messages WHERE id = :rid) +
                        (SELECT COUNT(*) FROM notes WHERE id = :rid))"""),
                {"rid": rid},
            )
            if (r.scalar() or 0) > 0:
                raw_alive += 1

        if unit_alive == 0 and raw_alive == 0:
            await db.execute(
                text("UPDATE memory_concepts SET needs_review = TRUE, updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
                {"id": cid},
            )
            flagged += 1
    return flagged

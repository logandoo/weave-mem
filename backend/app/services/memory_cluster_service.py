import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_config
from app.db.database import ConceptRelation

config = get_config()
logger = logging.getLogger(__name__)










async def get_clusters_for_concepts(db: AsyncSession, concept_ids: list[str]) -> list[dict]:
    from app.db.database import IS_SQLITE
    if IS_SQLITE:
        # SQLite 降级：无 ANY(:ids) 数组语法，集群注入跳过
        return []
    if not concept_ids:
        return []
    result = await db.execute(
        text("""
            SELECT DISTINCT mc.id, mc.name, mc.summary, mc.weight
            FROM memory_clusters mc
            JOIN concept_cluster_members ccm ON mc.id = ccm.cluster_id
            WHERE ccm.concept_id = ANY(:ids)
        """),
        {"ids": concept_ids},
    )
    return [
        {"id": r[0], "name": r[1], "summary": r[2], "weight": r[3]}
        for r in result.fetchall()
    ]


async def create_relation(
    db: AsyncSession, user_id: str, source_id: str, target_id: str,
    relation_type: str, description: str = "", weight: float = 0.5,
    edge_source: str = "llm", agent_id: str | None = None,
) -> str:
    rid = str(uuid.uuid4())
    relation = ConceptRelation(
        id=rid, user_id=user_id, agent_id=agent_id, source_id=source_id, target_id=target_id,
        relation_type=relation_type, description=description, weight=weight,
        edge_source=edge_source or "llm",
    )
    db.add(relation)
    await db.flush()
    return rid


async def get_neighbors(
    db: AsyncSession, concept_id: str, min_weight: float = 0.3,
    allowed_types: list[str] | None = None, agent_id: str | None = None,
) -> list[dict]:
    from app.services.memory_scope import agent_scope_sql, agent_scope_params
    result = await db.execute(
        text(f"SELECT target_id, relation_type, weight FROM concept_relations WHERE source_id = :id AND weight >= :mw {agent_scope_sql(agent_id)} UNION ALL SELECT source_id, relation_type, weight FROM concept_relations WHERE target_id = :id AND weight >= :mw {agent_scope_sql(agent_id)}"),
        {"id": concept_id, "mw": min_weight, **agent_scope_params(agent_id)},
    )
    rows = [{"id": r[0], "relation_type": r[1], "weight": r[2]} for r in result.fetchall()]
    # D1（门 edge_read_whitelist_enabled）：读侧边类型白名单过滤
    if allowed_types is not None:
        rows = [r for r in rows if r["relation_type"] in allowed_types]
    return rows


# ---- D1 确定性边（上游 f5d2f6401 移植；门 deterministic_edges_enabled 默认关）----

DETERMINISTIC_EDGE_TYPE = "co_occurs"
# 语义 relation_type 轴（上游 f5d2f6401）——不是 edge_source 轴
BASE_EDGE_TYPES = ["causal", "temporal", "contradicts", "supports", "part_of"]


def edge_read_whitelist(cfg: dict) -> list[str] | None:
    """D1：读侧边类型白名单（None=legacy 全类型；门关恒 None）。

    co_occurs 仅在 deterministic_edges_enabled 开启时并入（确定性边存在才可读）。"""
    if not (cfg or {}).get("edge_read_whitelist_enabled", False):
        return None
    wl = list(BASE_EDGE_TYPES)
    if (cfg or {}).get("deterministic_edges_enabled", False):
        wl.append(DETERMINISTIC_EDGE_TYPE)
    return wl


async def build_deterministic_edges(
    db: AsyncSession, user_id: str, concept_ids: list[str], max_edges: int = 20,
    agent_id: str | None = None,
) -> int:
    """D1：LLM-free 共现边（同 source_unit_ids 窗口内概念两两建边）。

    幂等双向（已有任一方向即跳过）；每轮有界 max_edges；返回新建边数。
    """
    if not concept_ids or len(concept_ids) < 2:
        return 0
    created = 0
    try:
        from app.services.memory_subconscious_service import _ids_in_sql
        ids = [str(c) for c in concept_ids][: max_edges * 2]
        id_sql, id_params = _ids_in_sql("id", ids)
        rows = (await db.execute(
            text(f"SELECT id, source_unit_ids FROM memory_concepts "
                 f"WHERE user_id = :u AND {id_sql} AND valid_to IS NULL"),
            {"u": user_id, **id_params},
        )).fetchall()
        import json as _json
        unit_map: dict[str, set] = {}
        for cid, raw in rows:
            try:
                units = _json.loads(raw) if raw else []
            except (ValueError, TypeError):
                units = []
            if isinstance(units, list):
                unit_map[cid] = {str(u) for u in units}
        cids = [r[0] for r in rows]
        for i in range(len(cids)):
            for j in range(i + 1, len(cids)):
                if created >= max_edges:
                    return created
                a, b = cids[i], cids[j]
                if not (unit_map.get(a) & unit_map.get(b)):
                    continue
                exists = (await db.execute(
                    text("SELECT COUNT(*) FROM concept_relations WHERE "
                         "(source_id = :a AND target_id = :b) OR (source_id = :b AND target_id = :a)"),
                    {"a": a, "b": b},
                )).scalar() or 0
                if exists:
                    continue
                await create_relation(
                    db, user_id, a, b, DETERMINISTIC_EDGE_TYPE,
                    description="co-occurrence", weight=0.5, edge_source=DETERMINISTIC_EDGE_TYPE,
                    agent_id=agent_id)
                created += 1
    except Exception:
        logger.debug("build_deterministic_edges failed (fail-open)", exc_info=True)
    return created


# ---- T13/F-4a：簇 embedding 写路径（上游 _update_cluster_embedding + add/remove 重移植）----

async def _update_cluster_embedding(db: AsyncSession, cluster_id: str) -> int:
    """簇 embedding = 成员概念向量均值（成员变更后刷新）。

    A1（上游 3378f9907）：维度取 _get_embedding_dim()（端点 extra.dim 优先），
    旧版取不存在的 config.memory["embedding_dim"]（默认 1536 与 1024 维端点
    不符 → 成员向量聚合恒空）；DC2：embedding_model 溯源落库。
    """
    from app.db.database import IS_SQLITE
    if IS_SQLITE:
        return 0
    from app.services.memory_embedding_service import (
        _get_embedding_dim, _get_embedding_model)
    rows = (await db.execute(
        text("SELECT c.embedding FROM memory_concepts c "
             "JOIN concept_cluster_members m ON m.concept_id = c.id "
             "WHERE m.cluster_id = :clid AND c.embedding IS NOT NULL"),
        {"clid": cluster_id},
    )).fetchall()
    if not rows:
        return 0
    dim = _get_embedding_dim()
    vecs = []
    for r in rows:
        try:
            v = [float(x) for x in str(r[0]).strip("[]").split(",")]
            if len(v) == dim:
                vecs.append(v)
        except (TypeError, ValueError):
            continue
    if not vecs:
        return 0
    mean = [sum(col) / len(vecs) for col in zip(*vecs)]
    lit = "[" + ",".join(f"{x:.6f}" for x in mean) + "]"
    await db.execute(
        text("UPDATE memory_clusters SET embedding = CAST(:v AS vector), "
             "embedding_model = :m, updated_at = CURRENT_TIMESTAMP WHERE id = :clid"),
        {"v": lit, "m": _get_embedding_model(), "clid": cluster_id},
    )
    return len(vecs)


async def add_concept_to_cluster(
    db: AsyncSession, user_id: str, cluster_id: str, concept_id: str,
) -> None:
    """入簇 + member_count + 簇 embedding 刷新（08-19 死代码清理删除后按 F-4a 回归）。"""
    await db.execute(
        text("INSERT INTO concept_cluster_members (concept_id, cluster_id) VALUES (:cid, :clid) ON CONFLICT DO NOTHING"),
        {"cid": concept_id, "clid": cluster_id},
    )
    await db.execute(
        text("UPDATE memory_clusters SET member_count = "
             "(SELECT COUNT(*) FROM concept_cluster_members WHERE cluster_id = :clid), "
             "updated_at = CURRENT_TIMESTAMP WHERE id = :clid"),
        {"clid": cluster_id},
    )
    await _update_cluster_embedding(db, cluster_id)


async def remove_concept_from_cluster(
    db: AsyncSession, user_id: str, cluster_id: str, concept_id: str,
) -> None:
    """出簇 + member_count + 簇 embedding 刷新。"""
    await db.execute(
        text("DELETE FROM concept_cluster_members WHERE concept_id = :cid AND cluster_id = :clid"),
        {"cid": concept_id, "clid": cluster_id},
    )
    await db.execute(
        text("UPDATE memory_clusters SET member_count = "
             "(SELECT COUNT(*) FROM concept_cluster_members WHERE cluster_id = :clid), "
             "updated_at = CURRENT_TIMESTAMP WHERE id = :clid"),
        {"clid": cluster_id},
    )
    await _update_cluster_embedding(db, cluster_id)

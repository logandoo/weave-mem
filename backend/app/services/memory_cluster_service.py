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
) -> str:
    rid = str(uuid.uuid4())
    relation = ConceptRelation(
        id=rid, user_id=user_id, source_id=source_id, target_id=target_id,
        relation_type=relation_type, description=description, weight=weight,
    )
    db.add(relation)
    await db.flush()
    return rid


async def get_neighbors(db: AsyncSession, concept_id: str, min_weight: float = 0.3) -> list[dict]:
    result = await db.execute(
        text("SELECT target_id, relation_type, weight FROM concept_relations WHERE source_id = :id AND weight >= :mw UNION ALL SELECT source_id, relation_type, weight FROM concept_relations WHERE target_id = :id AND weight >= :mw"),
        {"id": concept_id, "mw": min_weight},
    )
    return [{"id": r[0], "relation_type": r[1], "weight": r[2]} for r in result.fetchall()]

"""知识图谱查询集 — M4 Agent 使用的 Neo4j Cypher 查询。

提供：
  - 技能协同搜索
  - 天赋-技能路径发现
  - 装备-技能词缀匹配
  - BD 相似度搜索
"""

from __future__ import annotations

import logging

from app.knowledge_graph.connection import neo4j_manager

logger = logging.getLogger(__name__)


class KnowledgeQueries:
    """封装 M4 Agent 常用的知识图谱查询。"""

    def __init__(self):
        self._neo4j = neo4j_manager

    # ── 技能协同 ─────────────────────────────────────────

    async def find_synergistic_skills(
        self, skill_name: str, limit: int = 10
    ) -> list[dict]:
        """查找与指定技能共现的协同技能。

        MATCH (s1:Skill {name: $name})-[r:PAIRED_WITH]-(s2:Skill)
        RETURN s2.name, r.co_occurrence_count, r.build_count
        ORDER BY r.co_occurrence_count DESC
        """
        cypher = """
        MATCH (s1:Skill)-[r:PAIRED_WITH]-(s2:Skill)
        WHERE toLower(s1.name) CONTAINS toLower($name)
        RETURN s2.name AS skill_name,
               s2.skill_id AS skill_id,
               r.co_occurrence AS co_occurrence
        ORDER BY r.co_occurrence DESC
        LIMIT $limit
        """
        try:
            return await self._neo4j.execute_read(cypher, name=skill_name, limit=limit)
        except Exception:
            logger.exception("find_synergistic_skills failed")
            return []

    # ── 天赋推荐 ─────────────────────────────────────────

    async def find_keystones_for_skill(self, skill_name: str) -> list[dict]:
        """查找与指定技能搭配的基石天赋。

        MATCH (s:Skill)-[:BENEFITS_FROM]->(k:Keystone)
        WHERE toLower(s.name) CONTAINS toLower($name)
        RETURN k.name, k.effect
        """
        cypher = """
        MATCH (s:Skill)-[r:BENEFITS_FROM]->(k:Keystone)
        WHERE toLower(s.name) CONTAINS toLower($name)
        RETURN k.name AS keystone_name,
               k.effect AS effect,
               r.synergy_strength AS strength
        ORDER BY r.synergy_strength DESC
        """
        try:
            return await self._neo4j.execute_read(cypher, name=skill_name)
        except Exception:
            logger.exception("find_keystones_for_skill failed")
            return []

    async def find_notables_for_skill(self, skill_name: str, limit: int = 15) -> list[dict]:
        """查找技能的相关天赋群。"""
        cypher = """
        MATCH (s:Skill)-[:RELATED_TO]->(n:Notable)
        WHERE toLower(s.name) CONTAINS toLower($name)
        RETURN n.name AS notable_name,
               n.cluster AS cluster,
               n.effect AS effect
        LIMIT $limit
        """
        try:
            return await self._neo4j.execute_read(cypher, name=skill_name, limit=limit)
        except Exception:
            return []

    # ── 升华推荐 ─────────────────────────────────────────

    async def find_ascendancy_for_skill(self, skill_name: str) -> list[dict]:
        """查找哪些升华职业适合该技能。"""
        cypher = """
        MATCH (a:Ascendancy)-[r:BOOSTS]->(s:Skill)
        WHERE toLower(s.name) CONTAINS toLower($name)
        RETURN a.name AS ascendancy,
               a.class AS base_class,
               r.boost_description AS description,
               r.boost_power AS power
        ORDER BY r.boost_power DESC
        """
        try:
            return await self._neo4j.execute_read(cypher, name=skill_name)
        except Exception:
            return []

    # ── 装备词缀推荐 ─────────────────────────────────────

    async def find_affixes_for_skill(self, skill_name: str, slot: str | None = None) -> list[dict]:
        """查找技能对应的关键装备词缀。"""
        cypher = """
        MATCH (s:Skill)-[r:SCALES_WITH]->(m:Modifier)
        WHERE toLower(s.name) CONTAINS toLower($name)
        """
        if slot:
            cypher += " AND m.slot = $slot"
        cypher += """
        RETURN m.name AS modifier,
               m.slot AS slot,
               m.mod_type AS type,
               r.priority AS priority
        ORDER BY r.priority DESC
        """
        try:
            params = {"name": skill_name}
            if slot:
                params["slot"] = slot
            return await self._neo4j.execute_read(cypher, **params)
        except Exception:
            return []

    # ── 伤害转化链 ───────────────────────────────────────

    async def find_conversion_chain(self, damage_type: str) -> list[dict]:
        """查找伤害类型转化链。"""
        cypher = """
        MATCH path = (d1:DamageType)-[r:CONVERTS_TO*1..4]->(d2:DamageType)
        WHERE toLower(d1.name) = toLower($dtype)
        RETURN [node in nodes(path) | node.name] AS chain,
               length(path) AS steps
        ORDER BY steps
        LIMIT 5
        """
        try:
            return await self._neo4j.execute_read(cypher, dtype=damage_type)
        except Exception:
            return []

    # ── BD 相似度搜索 ────────────────────────────────────

    async def find_similar_builds(self, build_name: str, limit: int = 5) -> list[dict]:
        """基于知识图谱寻找相似 BD。"""
        cypher = """
        MATCH (b1:Build {name: $name})-[r:USES]->(s:Skill)
        MATCH (b2:Build)-[r2:USES]->(s)
        WHERE b1 <> b2
        WITH b2, count(s) AS shared_skills
        RETURN b2.name AS build_name,
               b2.class AS char_class,
               shared_skills
        ORDER BY shared_skills DESC
        LIMIT $limit
        """
        try:
            return await self._neo4j.execute_read(cypher, name=build_name, limit=limit)
        except Exception:
            return []

    # ── 机制冲突检测 ─────────────────────────────────────

    async def detect_conflicts(self, mechanics: list[str]) -> list[dict]:
        """检测机制列表中的已知冲突。"""
        cypher = """
        UNWIND $mechanics AS mech
        MATCH (m1:Mechanic {name: mech})-[r:CONFLICTS_WITH]->(m2:Mechanic)
        WHERE m2.name IN $mechanics
        RETURN m1.name AS mechanic_a,
               m2.name AS mechanic_b,
               r.reason AS conflict_reason
        """
        try:
            return await self._neo4j.execute_read(cypher, mechanics=mechanics)
        except Exception:
            return []


knowledge_queries = KnowledgeQueries()

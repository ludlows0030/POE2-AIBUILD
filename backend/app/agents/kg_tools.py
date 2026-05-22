"""M4 Agent 知识图谱工具 — 基于 Neo4j 的高级查询。

扩展 M4 推理能力（需求文档 §4.1）：
  7. query_skill_synergies      — 技能协同搜索（Neo4j PAIRED_WITH）
  8. query_keystone_for_skill   — 基石天赋推荐（Neo4j BENEFITS_FROM）
  9. query_ascendancy_for_skill — 升华职业推荐（Neo4j BOOSTS）
  10. query_affixes_for_skill   — 装备词缀推荐（Neo4j SCALES_WITH）
  11. detect_mechanic_conflicts — 机制冲突检测（Neo4j CONFLICTS_WITH）
  12. query_conversion_chain    — 伤害转化链查询（Neo4j CONVERTS_TO）
"""

from __future__ import annotations

import logging
from typing import Any

from app.knowledge_graph.queries import knowledge_queries

logger = logging.getLogger(__name__)


# ── Tool 7: query_skill_synergies ────────────────────────


async def query_skill_synergies(
    skill_name: str, limit: int = 10
) -> list[dict[str, Any]]:
    """查询与指定技能有共现关系的协同技能（基于 Neo4j PAIRED_WITH 关系）。

    返回按共现频次排序的技能列表，每个条目包含技能名、类型、标签。
    """
    try:
        return await knowledge_queries.find_synergistic_skills(skill_name, limit)
    except Exception:
        logger.exception("query_skill_synergies failed")
        return []


# ── Tool 8: query_keystone_for_skill ─────────────────────


async def query_keystone_for_skill(skill_name: str) -> list[dict[str, Any]]:
    """查询与指定技能搭配的基石天赋推荐（基于 Neo4j BENEFITS_FROM 关系）。

    返回基石名称、效果说明、协同强度（strong/medium/weak）。
    """
    try:
        return await knowledge_queries.find_keystones_for_skill(skill_name)
    except Exception:
        logger.exception("query_keystone_for_skill failed")
        return []


# ── Tool 9: query_ascendancy_for_skill ───────────────────


async def query_ascendancy_for_skill(skill_name: str) -> list[dict[str, Any]]:
    """查询哪些升华职业适合该技能（基于 Neo4j BOOSTS 关系）。

    返回升华名、基础职业、加成描述、加成强度。
    """
    try:
        return await knowledge_queries.find_ascendancy_for_skill(skill_name)
    except Exception:
        logger.exception("query_ascendancy_for_skill failed")
        return []


# ── Tool 10: query_affixes_for_skill ─────────────────────


async def query_affixes_for_skill(
    skill_name: str, slot: str | None = None
) -> list[dict[str, Any]]:
    """查询技能的关键装备词缀推荐（基于 Neo4j SCALES_WITH 关系）。

    可按装备槽位过滤。返回词缀名、槽位、类型、优先级。
    """
    try:
        return await knowledge_queries.find_affixes_for_skill(skill_name, slot)
    except Exception:
        logger.exception("query_affixes_for_skill failed")
        return []


# ── Tool 11: detect_mechanic_conflicts ───────────────────


async def detect_mechanic_conflicts(
    mechanics: list[str],
) -> list[dict[str, Any]]:
    """检测机制列表中的已知冲突（基于 Neo4j CONFLICTS_WITH 关系）。

    返回冲突对及原因说明。
    """
    try:
        return await knowledge_queries.detect_conflicts(mechanics)
    except Exception:
        logger.exception("detect_mechanic_conflicts failed")
        return []


# ── Tool 12: query_conversion_chain ──────────────────────


async def query_conversion_chain(damage_type: str) -> list[dict[str, Any]]:
    """查询伤害类型转化链（基于 Neo4j CONVERTS_TO 关系）。

    返回多条可能的转化路径，每条路径是伤害类型名称的列表。
    """
    try:
        return await knowledge_queries.find_conversion_chain(damage_type)
    except Exception:
        logger.exception("query_conversion_chain failed")
        return []

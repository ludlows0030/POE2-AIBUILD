"""M3 知识图谱 — Neo4j 技能/词缀/天赋/机制关系图。

用途：
  - 为 M4 Agent 提供协同效应查询
  - 发现隐含的技能-天赋-装备三角关系
  - 支持路径搜索：给定技能 → 找到最优天赋/装备路线
"""

from app.knowledge_graph.connection import Neo4jManager, neo4j_manager
from app.knowledge_graph.queries import KnowledgeQueries
from app.knowledge_graph.sync_service import KnowledgeGraphSync

__all__ = [
    "Neo4jManager",
    "neo4j_manager",
    "KnowledgeQueries",
    "KnowledgeGraphSync",
]

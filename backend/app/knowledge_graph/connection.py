"""Neo4j 连接管理器。

提供异步驱动管理、会话创建、健康检查。
支持 Neo4j 5 Community Edition。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

from neo4j import AsyncGraphDatabase, AsyncManagedTransaction
from neo4j.exceptions import ServiceUnavailable

from app.config import settings

logger = logging.getLogger(__name__)


class Neo4jManager:
    """Neo4j 异步驱动管理器。"""

    def __init__(self) -> None:
        self._driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
            max_connection_lifetime=3600,
            max_connection_pool_size=20,
        )

    @property
    def driver(self):
        return self._driver

    async def close(self) -> None:
        await self._driver.close()

    async def health_check(self) -> bool:
        """检查 Neo4j 连接状态。"""
        try:
            async with self._driver.session(database=settings.NEO4J_DATABASE) as session:
                result = await session.run("RETURN 1 AS ok")
                record = await result.single()
                return record is not None and record["ok"] == 1
        except ServiceUnavailable:
            return False
        except Exception:
            logger.exception("Neo4j health check failed")
            return False

    async def session(self):
        """获取异步会话（用作上下文管理器）。"""
        return self._driver.session(database=settings.NEO4J_DATABASE)

    async def execute_write(self, cypher: str, **params) -> list:
        """执行写事务。"""
        async with self._driver.session(database=settings.NEO4J_DATABASE) as session:
            result = await session.execute_write(
                self._run_query, cypher, params
            )
            return await result.data() if result else []

    async def execute_read(self, cypher: str, **params) -> list:
        """执行读事务。"""
        async with self._driver.session(database=settings.NEO4J_DATABASE) as session:
            result = await session.execute_read(
                self._run_query, cypher, params
            )
            return await result.data() if result else []

    @staticmethod
    async def _run_query(tx: AsyncManagedTransaction, cypher: str, params: dict) -> list:
        result = await tx.run(cypher, **params)
        return await result.data()

    async def initialize_schema(self) -> None:
        """创建索引和约束（首次启动时调用）。"""
        constraints = [
            "CREATE CONSTRAINT skill_id IF NOT EXISTS FOR (s:Skill) REQUIRE s.skill_id IS UNIQUE",
            "CREATE CONSTRAINT keystone_name IF NOT EXISTS FOR (k:Keystone) REQUIRE k.name IS UNIQUE",
            "CREATE CONSTRAINT ascendancy_name IF NOT EXISTS FOR (a:Ascendancy) REQUIRE a.name IS UNIQUE",
            "CREATE CONSTRAINT mechanic_name IF NOT EXISTS FOR (m:Mechanic) REQUIRE m.name IS UNIQUE",
            "CREATE CONSTRAINT damagetype_name IF NOT EXISTS FOR (d:DamageType) REQUIRE d.name IS UNIQUE",
            "CREATE CONSTRAINT playstyle_name IF NOT EXISTS FOR (p:Playstyle) REQUIRE p.name IS UNIQUE",
            "CREATE CONSTRAINT item_class IF NOT EXISTS FOR (c:CharClass) REQUIRE c.name IS UNIQUE",
        ]
        indexes = [
            "CREATE INDEX skill_name_idx IF NOT EXISTS FOR (s:Skill) ON (s.name)",
            "CREATE INDEX skill_tag_idx IF NOT EXISTS FOR (s:Skill) ON (s.tags)",
        ]

        async with self._driver.session(database=settings.NEO4J_DATABASE) as session:
            for cypher in constraints + indexes:
                try:
                    await session.run(cypher)
                except Exception:
                    logger.debug(f"Schema statement skipped (may already exist): {cypher[:60]}...")


neo4j_manager = Neo4jManager()

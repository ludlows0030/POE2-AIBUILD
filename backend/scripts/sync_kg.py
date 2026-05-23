"""Neo4j 知识图谱同步 — CLI 入口。

用法:
    cd backend && python scripts/sync_kg.py
    cd backend && python scripts/sync_kg.py --reset  # 清空重建
"""

import asyncio
import logging
import sys

sys.path.insert(0, ".")

from app.database import async_session_factory
from app.knowledge_graph.connection import neo4j_manager
from app.knowledge_graph.sync_service import KnowledgeGraphSync

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sync PostgreSQL data to Neo4j")
    parser.add_argument("--reset", action="store_true", help="Drop all nodes before sync")
    args = parser.parse_args()

    ok = await neo4j_manager.health_check()
    if not ok:
        logger.error("Neo4j is not reachable")
        return
    logger.info("Neo4j health check passed")

    if args.reset:
        logger.info("Dropping all nodes...")
        await neo4j_manager.execute_write("MATCH (n) DETACH DELETE n")
        logger.info("All nodes dropped")

    sync = KnowledgeGraphSync()
    async with async_session_factory() as db:
        counts = await sync.full_sync(db)

    logger.info(f"Sync complete: {counts}")
    await neo4j_manager.close()


if __name__ == "__main__":
    asyncio.run(main())

"""知识图谱同步与维护 Celery 任务。"""

from __future__ import annotations

import asyncio
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.sync_tasks.sync_knowledge_graph",
    bind=True,
    max_retries=1,
    default_retry_delay=3600,
)
def sync_knowledge_graph(self) -> dict:
    """执行 PostgreSQL → Neo4j 知识图谱全量同步。"""
    return _run_async(_do_sync_kg())


@celery_app.task(
    name="app.tasks.sync_tasks.sync_single_character",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def sync_single_character(self, character_id: str) -> dict:
    """同步单个角色到知识图谱（增量更新）。"""
    return _run_async(_do_sync_character(character_id))


@celery_app.task(
    name="app.tasks.sync_tasks.cleanup_stale_data",
    bind=True,
    max_retries=0,
)
def cleanup_stale_data(self) -> dict:
    """清理超过 90 天未更新的过期 BD 数据。"""
    return _run_async(_do_cleanup())


# ── 异步实现 ─────────────────────────────────────────


async def _do_sync_kg() -> dict:
    from app.database import async_session_factory
    from app.knowledge_graph.sync_service import knowledge_graph_sync

    async with async_session_factory() as db:
        counts = await knowledge_graph_sync.full_sync(db)
    return counts


async def _do_sync_character(character_id: str) -> dict:
    from app.database import async_session_factory
    from app.knowledge_graph.sync_service import knowledge_graph_sync

    async with async_session_factory() as db:
        await knowledge_graph_sync.sync_character(db, character_id)
    return {"synced": character_id}


async def _do_cleanup() -> dict:
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import delete
    from app.database import async_session_factory
    from app.models.base import Character

    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    async with async_session_factory() as db:
        result = await db.execute(
            delete(Character).where(Character.last_updated < cutoff)
        )
        await db.commit()
        deleted = result.rowcount
    return {"deleted_builds": deleted}


def _run_async(coro) -> dict:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(coro)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)

"""数据采集 Celery 任务。

每个任务独立运行，带错误隔离 — 单个数据源失败不影响其他采集。
"""

from __future__ import annotations

import asyncio
import logging

from app.celery_app import celery_app
from app.config import settings

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.collect_tasks.collect_poe_ninja",
    bind=True,
    max_retries=2,
    default_retry_delay=600,  # 10 分钟
)
def collect_poe_ninja(self) -> dict:
    """从 poe.ninja 采集 POE2 经济/BD 数据。"""
    return _run_async(_do_collect_poe_ninja())


@celery_app.task(
    name="app.tasks.collect_tasks.collect_poedb_skills",
    bind=True,
    max_retries=2,
    default_retry_delay=600,
)
def collect_poedb_skills(self) -> dict:
    """从 poedb.tw 采集 POE2 技能机制数据。"""
    return _run_async(_do_collect_poedb())


@celery_app.task(
    name="app.tasks.collect_tasks.collect_pobb_trending",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def collect_pobb_trending(self) -> dict:
    """从 pobb.in 采集热门 POE2 BD。"""
    return _run_async(_do_collect_pobb())


# ── 异步实现 ─────────────────────────────────────────


async def _do_collect_poe_ninja() -> dict:
    """异步采集 poe.ninja 经济数据。"""
    from app.collectors.poe_ninja import PoeNinjaClient

    client = PoeNinjaClient()
    try:
        # 采集通货和技能宝石经济数据
        currency = await client.fetch_currency_overview("Necropolis")  # 临时联赛名
        gems = await client.fetch_skill_gems("Necropolis")
        return {
            "source": "poe.ninja",
            "currency_entries": len(currency.get("lines", [])),
            "gem_entries": len(gems.get("lines", [])),
        }
    except Exception:
        logger.exception("poe.ninja collection failed")
        raise
    finally:
        await client.close()


async def _do_collect_poedb() -> dict:
    """异步采集 poedb.tw 技能数据。"""
    from app.collectors.poedb import poedb_client

    skills = poedb_client.poe2_skill_list()
    collected = 0
    failed = 0

    for skill in skills:
        try:
            html = await poedb_client.fetch_skill_page(skill["name"])
            if html:
                collected += 1
        except Exception:
            failed += 1
            logger.warning(f"Failed to fetch poedb data for {skill['name']}")

    return {"source": "poedb.tw", "collected": collected, "failed": failed}


async def _do_collect_pobb() -> dict:
    """异步采集 pobb.in BD。"""
    from app.services.pob_import_service import pob_import_service
    from app.database import async_session_factory

    # pobb.in 没有公开的 "trending" API，此处从种子 BD 列表尝试导入
    known_builds = [
        "01JFHGZ2RQT8PEDFBZ8JDPPKW1",  # 示例 pobb ID，实际使用时替换
    ]
    imported = 0
    failed = 0

    async with async_session_factory() as db:
        for bid in known_builds:
            try:
                result = await pob_import_service.import_from_pobb_id(db, bid)
                if result:
                    imported += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

    return {"source": "pobb.in", "imported": imported, "failed": failed}


def _run_async(coro) -> dict:
    """在同步 Celery task 中运行异步协程。"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(coro)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)

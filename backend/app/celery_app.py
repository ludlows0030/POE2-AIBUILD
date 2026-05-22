"""Celery 任务队列配置。

用于定时执行：
  - 数据采集（poe.ninja, pobb.in, poedb）
  - 知识图谱同步（PostgreSQL → Neo4j）
  - 过期数据清理

启动 worker:  celery -A app.celery_app worker --loglevel=info
启动 beat:    celery -A app.celery_app beat --loglevel=info
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "poe2bd",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
    include=[
        "app.tasks.collect_tasks",
        "app.tasks.sync_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)

# ── 定时任务调度 ────────────────────────────────────────

celery_app.conf.beat_schedule = {
    # 数据采集 — 每周
    "collect-poe-ninja-weekly": {
        "task": "app.tasks.collect_tasks.collect_poe_ninja",
        "schedule": crontab(hour=3, minute=17, day_of_week=1),  # 周一 3:17 AM
    },
    "collect-poedb-weekly": {
        "task": "app.tasks.collect_tasks.collect_poedb_skills",
        "schedule": crontab(hour=4, minute=23, day_of_week=1),  # 周一 4:23 AM
    },
    "collect-pobb-weekly": {
        "task": "app.tasks.collect_tasks.collect_pobb_trending",
        "schedule": crontab(hour=5, minute=37, day_of_week=1),  # 周一 5:37 AM
    },
    # 知识图谱同步 — 每天
    "sync-kg-daily": {
        "task": "app.tasks.sync_tasks.sync_knowledge_graph",
        "schedule": crontab(hour=6, minute=7),  # 每天 6:07 AM
    },
    # 过期数据清理 — 每月
    "cleanup-monthly": {
        "task": "app.tasks.sync_tasks.cleanup_stale_data",
        "schedule": crontab(hour=2, minute=0, day_of_month=1),  # 每月 1 号 2:00 AM
    },
}

#!/usr/bin/env python3
"""POE2 BD Agent — 系统一键初始化脚本。

使用方式:
    python scripts/bootstrap.py              # 完整初始化
    python scripts/bootstrap.py --check-only # 仅环境检查
    python scripts/bootstrap.py --skip-kg    # 跳过知识图谱同步

检查项:
  1. Docker 服务状态 (PG16 + Neo4j + Redis + Qdrant)
  2. 数据库迁移
  3. 种子数据注入
  4. 知识图谱同步
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCKER_DIR = PROJECT_ROOT.parent / "docker"


def header(msg: str) -> None:
    print(f"\n{'='*50}")
    print(f"  {msg}")
    print(f"{'='*50}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


# ── Step 1: Docker 服务检查 ────────────────────────────


def check_docker() -> bool:
    header("Step 1/4: Docker 服务检查")
    try:
        r = subprocess.run(
            ["docker", "compose", "-f", str(DOCKER_DIR / "docker-compose.yml"), "ps", "--format", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            fail("docker compose 不可用，请确保 Docker Desktop 已启动")
            return False

        services_found = set()
        for line in r.stdout.strip().split("\n"):
            if line:
                import json
                s = json.loads(line)
                name = s.get("Service", s.get("service", ""))
                status = s.get("State", s.get("state", ""))
                services_found.add(name)
                if "running" in status.lower() or "healthy" in status.lower():
                    ok(f"{name}: {status}")
                else:
                    warn(f"{name}: {status}")

        expected = {"postgres", "neo4j", "redis", "qdrant"}
        missing = expected - services_found
        if missing:
            warn(f"缺少服务: {missing}，尝试启动...")
            r2 = subprocess.run(
                ["docker", "compose", "-f", str(DOCKER_DIR / "docker-compose.yml"), "up", "-d"],
                capture_output=True, text=True, timeout=120,
            )
            if r2.returncode != 0:
                fail(f"启动失败: {r2.stderr[:200]}")
                return False
            ok("服务已启动，等待健康检查...")
            time.sleep(10)

        return True
    except FileNotFoundError:
        fail("Docker 未安装或不在 PATH 中")
        return False
    except Exception as e:
        fail(f"检查失败: {e}")
        return False


# ── Step 2: 数据库迁移 ─────────────────────────────────


async def run_migrations() -> bool:
    header("Step 2/4: 数据库迁移")
    from alembic.config import Config
    from alembic import command

    try:
        alembic_ini = PROJECT_ROOT / "alembic.ini"
        if not alembic_ini.exists():
            fail(f"alembic.ini 不存在: {alembic_ini}")
            return False

        cfg = Config(str(alembic_ini))
        command.upgrade(cfg, "head")
        ok("数据库迁移完成")
        return True
    except Exception as e:
        fail(f"迁移失败: {e}")
        return False


# ── Step 3: 种子数据注入 ───────────────────────────────


async def seed_data() -> bool:
    header("Step 3/4: 种子数据注入")
    from app.database import async_session_factory
    from app.services.seed_service import seed_builds

    try:
        async with async_session_factory() as db:
            ids = await seed_builds(db)
        ok(f"种子 BD 注入完成 ({len(ids)} 个)")
        return True
    except Exception as e:
        fail(f"种子数据注入失败: {e}")
        return False


# ── Step 4: 知识图谱同步 ───────────────────────────────


async def sync_knowledge_graph() -> bool:
    header("Step 4/4: 知识图谱同步")
    from app.database import async_session_factory
    from app.knowledge_graph.connection import neo4j_manager
    from app.knowledge_graph.sync_service import knowledge_graph_sync

    try:
        # 检查 Neo4j 连接
        healthy = await neo4j_manager.health_check()
        if not healthy:
            warn("Neo4j 未就绪，跳过知识图谱同步")
            return True  # 不阻塞整体流程

        async with async_session_factory() as db:
            counts = await knowledge_graph_sync.full_sync(db)

        ok("知识图谱同步完成:")
        for key, val in counts.items():
            print(f"      {key}: {val}")
        return True
    except Exception as e:
        warn(f"知识图谱同步失败 (非致命): {e}")
        return True  # KG 失败不阻塞


# ── 主流程 ─────────────────────────────────────────────


async def bootstrap(check_only: bool = False, skip_kg: bool = False) -> bool:
    print("=" * 50)
    print("  POE2 BD Agent — 系统初始化")
    print("=" * 50)

    if check_only:
        check_docker()
        return True

    # Step 1
    if not check_docker():
        print("\n请先启动 Docker Desktop 并运行: cd docker && docker compose up -d")
        return False

    # Step 2
    if not await run_migrations():
        return False

    # Step 3
    if not await seed_data():
        return False

    # Step 4
    if not skip_kg:
        await sync_knowledge_graph()

    # ── Summary ──
    header("初始化完成")
    print("  启动 API 服务器:")
    print("    cd backend && uvicorn app.main:app --reload")
    print()
    print("  使用 CLI 测试 Agent:")
    print("    python scripts/cli.py '我想玩一个电系法师'")
    print()
    print("  或直接调用 API:")
    print("    curl -X POST http://localhost:8000/api/builds/generate \\")
    print('      -H "Content-Type: application/json" \\')
    print('      -d \'{"user_request": "Lightning arrow ranger for mapping"}\'')
    return True


def main():
    parser = argparse.ArgumentParser(description="POE2 BD Agent 系统初始化")
    parser.add_argument("--check-only", action="store_true", help="仅检查环境，不执行初始化")
    parser.add_argument("--skip-kg", action="store_true", help="跳过知识图谱同步")
    args = parser.parse_args()

    success = asyncio.run(bootstrap(check_only=args.check_only, skip_kg=args.skip_kg))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

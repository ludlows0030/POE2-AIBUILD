"""POE2 珠宝导入 — 从 PoB2 导入传奇珠宝、星团珠宝。

数据源:
  - Uniques/jewel.lua — 传奇珠宝（[[ ]] 多行文本格式）
  - ClusterJewels.lua — 星团珠宝（标准 Lua 表格式）

用法:
    cd backend && python scripts/import_jewels.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, ".")

from app.database import async_session_factory
from app.models.base import ClusterJewelBase, UniqueItem
from scripts.import_gems import _parse_lua_table, content_hash

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

UNIQUE_JEWEL_PATH = Path(__file__).parent.parent / "data" / "Uniques" / "jewel.lua"
CLUSTER_JEWEL_PATH = Path(__file__).parent.parent / "data" / "ClusterJewels.lua"

KNOWN_JEWEL_BASES = {
    "Ruby", "Emerald", "Sapphire", "Diamond",
    "Timeless Jewel", "Cobalt Jewel", "Crimson Jewel", "Viridian Jewel",
}


def parse_unique_jewels(filepath: Path) -> list[dict]:
    """解析 Uniques/jewel.lua，返回传奇珠宝条目列表。

    格式: return { [[ ... ]], [[ ... ]], ... }
    每个 [[ ]] 块内是多行文本：
      Line 1: 名字
      Line 2: 底材类型
      后续: Source:, Variant:, Limited to:, Radius:, {variant:N} 词缀
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    jewels: list[dict] = []

    # 匹配所有 [[ ... ]] 多行文本块
    for match in re.finditer(r"\[\[(.*?)\]\]", content, re.DOTALL):
        block = match.group(1).strip()
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            continue

        # 跳过注释行
        lines = [l for l in lines if not l.startswith("--")]

        if len(lines) < 2:
            continue

        name_en = lines[0]
        base_type = lines[1]

        entry: dict = {
            "name_en": name_en,
            "base_item_type": base_type,
            "explicit_mods": [],
            "variants": [],
            "source": None,
            "limited_to": None,
            "radius": None,
            "global_stats": [],
        }

        for line in lines[2:]:
            if line.startswith("Source:"):
                entry["source"] = line[len("Source:"):].strip()
            elif line.startswith("Variant:"):
                entry["variants"].append(line[len("Variant:"):].strip())
            elif line.startswith("Limited to:"):
                entry["limited_to"] = line[len("Limited to:"):].strip()
            elif line.startswith("Radius:"):
                entry["radius"] = line[len("Radius:"):].strip()
            elif line.startswith("{"):
                entry["explicit_mods"].append(line)
            else:
                entry["global_stats"].append(line)

        jewels.append(entry)

    return jewels


def parse_cluster_jewels(filepath: Path) -> list[dict]:
    """解析 ClusterJewels.lua，返回星团珠宝底材条目列表。"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 找到 return table
    table_start = content.index("{")
    top_table, _ = _parse_lua_table(content, table_start)

    jewels_data = top_table.get("jewels", {})
    entries: list[dict] = []

    for jewel_name, jewel_info in jewels_data.items():
        if not isinstance(jewel_info, dict):
            continue

        skills = jewel_info.get("skills", {})
        skill_entries: list[dict] = []
        for skill_id, skill_data in skills.items():
            if isinstance(skill_data, dict):
                skill_entries.append({
                    "skill_id": skill_id,
                    "name": skill_data.get("name"),
                    "tag": skill_data.get("tag"),
                    "stats": skill_data.get("stats", []),
                    "enchant": skill_data.get("enchant", []),
                })

        entries.append({
            "name_en": jewel_name,
            "size": jewel_info.get("size"),
            "size_index": jewel_info.get("sizeIndex"),
            "min_nodes": jewel_info.get("minNodes"),
            "max_nodes": jewel_info.get("maxNodes"),
            "small_indices": jewel_info.get("smallIndicies", []),
            "notable_indices": jewel_info.get("notableIndicies", []),
            "socket_indices": jewel_info.get("socketIndicies", []),
            "total_indices": jewel_info.get("totalIndicies"),
            "skills": skill_entries,
        })

    return entries


async def import_unique_jewels(
    game_version: str = "3.26",
    dry_run: bool = False,
) -> dict:
    """导入传奇珠宝到 unique_item 表。"""
    jewels = parse_unique_jewels(UNIQUE_JEWEL_PATH)
    logger.info(f"Parsed {len(jewels)} unique jewels")

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    async with async_session_factory() as db:
        for entry in jewels:
            try:
                name_en = entry["name_en"]

                # 构建 explicit_mods: 变体词缀 + 全局属性（存储为字符串数组）
                mods: list[str] = []
                for m in entry["explicit_mods"]:
                    mods.append(m)

                if entry["global_stats"]:
                    for gs in entry["global_stats"]:
                        mods.append(gs)

                data = {
                    "name_en": name_en,
                    "name_zh": None,
                    "base_item_type": entry["base_item_type"],
                    "item_class": "Jewel" + (f" ({entry['base_item_type']})" if entry["base_item_type"] else ""),
                    "explicit_mods": mods if mods else None,
                    "flavour_text": entry["source"],
                    "is_boss_drop": entry["source"] is not None and "unique{" in (entry["source"] or ""),
                    "boss_source": entry["source"],
                    "is_active": True,
                }

                hval = content_hash(data)

                existing = await db.scalar(
                    select(UniqueItem).where(
                        UniqueItem.name_en == name_en,
                        UniqueItem.base_item_type == entry["base_item_type"],
                    )
                )

                if existing:
                    if existing.content_hash == hval:
                        stats["skipped"] += 1
                    else:
                        for k, v in data.items():
                            if hasattr(existing, k) and k not in ("id", "content_hash"):
                                setattr(existing, k, v)
                        existing.content_hash = hval
                        existing.game_version = game_version
                        existing.updated_at = datetime.now(timezone.utc)
                        stats["updated"] += 1
                else:
                    if not dry_run:
                        db.add(UniqueItem(
                            **data,
                            content_hash=hval,
                            game_version=game_version,
                        ))
                    stats["inserted"] += 1

            except Exception as e:
                stats["errors"] += 1
                logger.warning(f"Error on {entry.get('name_en', '?')}: {e}")

        if not dry_run:
            await db.commit()

    logger.info(
        f"Unique jewel import complete: ins={stats['inserted']}, "
        f"upd={stats['updated']}, skip={stats['skipped']}, err={stats['errors']}"
    )
    return stats


async def import_cluster_jewels(
    game_version: str = "3.26",
    dry_run: bool = False,
) -> dict:
    """导入星团珠宝底材到 cluster_jewel_base 表。"""
    entries = parse_cluster_jewels(CLUSTER_JEWEL_PATH)
    logger.info(f"Parsed {len(entries)} cluster jewel bases")

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    async with async_session_factory() as db:
        for entry in entries:
            try:
                data = {
                    "name_en": entry["name_en"],
                    "size": entry["size"],
                    "size_index": entry["size_index"],
                    "min_nodes": entry["min_nodes"],
                    "max_nodes": entry["max_nodes"],
                    "small_indices": entry["small_indices"] if entry["small_indices"] else None,
                    "notable_indices": entry["notable_indices"] if entry["notable_indices"] else None,
                    "socket_indices": entry["socket_indices"] if entry["socket_indices"] else None,
                    "total_indices": entry["total_indices"],
                    "skills": entry["skills"] if entry["skills"] else None,
                    "is_active": True,
                }

                hval = content_hash(data)

                existing = await db.scalar(
                    select(ClusterJewelBase).where(ClusterJewelBase.name_en == data["name_en"])
                )

                if existing:
                    if existing.content_hash == hval:
                        stats["skipped"] += 1
                    else:
                        for k, v in data.items():
                            if hasattr(existing, k) and k not in ("id", "content_hash"):
                                setattr(existing, k, v)
                        existing.content_hash = hval
                        existing.game_version = game_version
                        existing.updated_at = datetime.now(timezone.utc)
                        stats["updated"] += 1
                else:
                    if not dry_run:
                        db.add(ClusterJewelBase(
                            **data,
                            content_hash=hval,
                            game_version=game_version,
                        ))
                    stats["inserted"] += 1

            except Exception as e:
                stats["errors"] += 1
                logger.warning(f"Error on {entry.get('name_en', '?')}: {e}")

        if not dry_run:
            await db.commit()

    logger.info(
        f"Cluster jewel import complete: ins={stats['inserted']}, "
        f"upd={stats['updated']}, skip={stats['skipped']}, err={stats['errors']}"
    )
    return stats


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Import POE2 jewel data from PoB2")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--game-version", default="3.26")
    parser.add_argument("--unique-only", action="store_true", help="Only import unique jewels")
    parser.add_argument("--cluster-only", action="store_true", help="Only import cluster jewels")
    args = parser.parse_args()

    if not args.cluster_only:
        result = await import_unique_jewels(
            game_version=args.game_version,
            dry_run=args.dry_run,
        )
        print(f"\nUnique jewels: {result}")

    if not args.unique_only:
        result = await import_cluster_jewels(
            game_version=args.game_version,
            dry_run=args.dry_run,
        )
        print(f"\nCluster jewels: {result}")


if __name__ == "__main__":
    asyncio.run(main())

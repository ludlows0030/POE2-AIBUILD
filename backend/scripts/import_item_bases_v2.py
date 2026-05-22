"""POE2 装备底材导入 — 从 PoB2 Bases/*.lua 导入全部装备底材。

数据源: PoB2 社区仓库 src/Data/Bases/*.lua（29 个槽位文件）
  - 武器类: bow, crossbow, mace, staff, sword, axe, spear, flail, claw, dagger, wand, sceptre, fishing
  - 防具类: body, boots, gloves, helmet, shield, focus, quiver
  - 饰品类: ring, amulet, belt, talisman, jewel
  - 其他: flask, incursionlimb, soulcore, traptool

用法:
    cd backend && python scripts/import_item_bases_v2.py
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

from sqlalchemy import select, text

sys.path.insert(0, ".")

from app.database import async_session_factory
from app.models.base import ItemBase
from scripts.import_gems import _parse_lua_table

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

BASES_DIR = Path(__file__).parent.parent / "data" / "Bases"


def content_hash(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def parse_bases_file(filepath: Path) -> list[dict]:
    """解析单个 Bases/*.lua 文件，返回 {name, entry} 列表。"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    entries: list[dict] = []

    # 找出所有 itemBases["Name"] = { ... } 条目
    pos = 0
    while True:
        match = re.search(r'itemBases\["([^"]+)"\]\s*=\s*\{', content[pos:])
        if not match:
            break

        name = match.group(1)
        table_start = pos + match.end() - 1  # position of the opening '{'
        table, end_pos = _parse_lua_table(content, table_start)

        entry = dict(table)
        entry["_name"] = name
        entry["_source"] = filepath.stem
        entries.append(entry)

        pos = table_start + end_pos - table_start
        if pos >= len(content):
            break

    return entries


def map_base_to_db(entry: dict) -> dict:
    """将 PoB2 base entry 映射到 item_base 表字段。"""
    name_en = entry["_name"]
    item_type = entry.get("type", "")
    sub_type = entry.get("subType", "")

    # item_class: 用 type，合并 subType
    if sub_type:
        item_class = f"{item_type} ({sub_type})"
    else:
        item_class = item_type

    # 属性需求
    req = entry.get("req", {})
    required_level = req.get("level")
    required_str = req.get("str")
    required_dex = req.get("dex")
    required_int = req.get("int")

    # 基底词缀
    implicit = entry.get("implicit")
    implicit_mods = [implicit] if implicit else None

    # 基础属性
    base_stats = {
        "quality": entry.get("quality"),
        "socket_limit": entry.get("socketLimit"),
        "sub_type": sub_type or None,
        "tags": [k for k, v in entry.get("tags", {}).items() if v is True],
    }
    # 清理 None 值
    base_stats = {k: v for k, v in base_stats.items() if v is not None}

    # 武器/防具/药剂属性
    weapon_stats = None
    for key in ("weapon", "armour", "charm", "flask"):
        if key in entry:
            weapon_stats = dict(entry[key])
            weapon_stats["_type"] = key
            break

    data = {
        "name_en": name_en,
        "item_class": item_class,
        "required_level": required_level,
        "required_str": required_str,
        "required_dex": required_dex,
        "required_int": required_int,
        "implicit_mods": implicit_mods,
        "base_stats": base_stats if base_stats else None,
        "weapon_stats": weapon_stats,
        "is_active": True,
    }

    return data


async def import_item_bases(
    game_version: str = "3.26",
    dry_run: bool = False,
) -> dict:
    """导入全部装备底材。

    Returns:
        {inserted, updated, skipped, errors}
    """
    # 收集全部底材
    all_entries: list[dict] = []
    for fp in sorted(BASES_DIR.glob("*.lua")):
        logger.info(f"Parsing {fp.name}...")
        entries = parse_bases_file(fp)
        logger.info(f"  {fp.name}: {len(entries)} items")
        all_entries.extend(entries)

    logger.info(f"Total items parsed: {len(all_entries)}")

    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
    total = len(all_entries)

    async with async_session_factory() as db:
        for i, entry in enumerate(all_entries):
            try:
                data = map_base_to_db(entry)
                hval = content_hash(data)

                existing = await db.scalar(
                    select(ItemBase).where(ItemBase.name_en == data["name_en"])
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
                        existing.is_active = True
                        existing.updated_at = datetime.now(timezone.utc)
                        stats["updated"] += 1
                else:
                    if not dry_run:
                        db.add(ItemBase(
                            **data,
                            content_hash=hval,
                            game_version=game_version,
                        ))
                    stats["inserted"] += 1

            except Exception as e:
                stats["errors"] += 1
                if stats["errors"] <= 5:
                    logger.warning(f"Error on {entry.get('_name', '?')}: {e}")

            if (i + 1) % 200 == 0:
                if not dry_run:
                    await db.commit()
                logger.info(
                    f"Progress: {i + 1}/{total} | "
                    f"ins={stats['inserted']} upd={stats['updated']} "
                    f"skip={stats['skipped']} err={stats['errors']}"
                )

        if not dry_run:
            await db.commit()

    logger.info(
        f"Item base import complete: ins={stats['inserted']}, "
        f"upd={stats['updated']}, skip={stats['skipped']}, err={stats['errors']}"
    )
    return stats


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Import POE2 item bases from PoB2 Bases/*.lua")
    parser.add_argument("--dry-run", action="store_true", help="Parse and map without writing")
    parser.add_argument("--game-version", default="3.26")
    parser.add_argument("--clear-old", action="store_true", help="Delete old POE2DB data before import")
    args = parser.parse_args()

    if args.clear_old:
        async with async_session_factory() as db:
            await db.execute(text("DELETE FROM item_base"))
            await db.commit()
        logger.info("Cleared old item_base data")

    result = await import_item_bases(
        game_version=args.game_version,
        dry_run=args.dry_run,
    )
    print(f"\nFinal: {result}")


if __name__ == "__main__":
    asyncio.run(main())

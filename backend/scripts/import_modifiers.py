"""POE2 装备词缀导入 — 从 PoB2 ModItem.lua + ModItemExclusive.lua 导入全部词缀。

数据源:
  - ModItem.lua: 常规装备词缀（前缀/后缀），含物品类型权重
  - ModItemExclusive.lua: 传奇/专属词缀

用法:
    cd backend && python scripts/import_modifiers.py
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
from app.models.base import Modifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

MOD_ITEM_PATH = Path(__file__).parent.parent / "data" / "ModItem.lua"
MOD_EXCLUSIVE_PATH = Path(__file__).parent.parent / "data" / "ModItemExclusive.lua"
MOD_JEWEL_PATH = Path(__file__).parent.parent / "data" / "ModJewel.lua"

# 从 import_gems 复用 Lua 解析器
from scripts.import_gems import _parse_lua_table, _parse_lua_value


def content_hash(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def parse_mod_file(filepath: Path) -> list[dict]:
    """解析 ModItem/ModItemExclusive.lua，返回词缀条目列表。"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    table_start = content.index("{")
    top_table, _ = _parse_lua_table(content, table_start)

    entries: list[dict] = []
    for key, value in top_table.items():
        if isinstance(value, dict):
            value["_key"] = key
            entries.append(value)
        elif isinstance(value, list):
            # Array result (shouldn't happen at top level)
            pass

    return entries


def extract_stat_descriptions(entry: dict) -> list[str]:
    """从解析后的条目中提取无名字符串（属性描述文本）。"""
    texts = []
    for k, v in entry.items():
        if isinstance(k, int) and isinstance(v, str):
            texts.append((k, v))
    texts.sort(key=lambda x: x[0])
    return [v for _, v in texts]


def map_mod_to_db(entry: dict, source: str) -> dict:
    """将 ModItem/ModItemExclusive 条目映射到 modifier 表字段。"""
    # 词缀类型
    if source in ("ModItem", "ModJewel"):
        mod_type = entry.get("type", "").lower()  # "Prefix" → "prefix"
    elif source == "ModItemExclusive":
        key = entry.get("_key", "")
        if key.startswith("Unique"):
            mod_type = "unique"
        elif "Implicit" in key:
            mod_type = "implicit"
        elif "Enchant" in key:
            mod_type = "enchant"
        else:
            mod_type = "exclusive"
    else:
        mod_type = "unknown"

    # 属性描述文本（位置参数）
    stat_descriptions = extract_stat_descriptions(entry)

    # 物品类型权重
    weight_keys = entry.get("weightKey", [])
    weight_vals = entry.get("weightVal", [])
    if weight_keys and weight_vals and len(weight_keys) == len(weight_vals):
        spawn_weight = {k: v for k, v in zip(weight_keys, weight_vals)}
        item_classes = [k for k, v in zip(weight_keys, weight_vals) if v > 0]
    else:
        spawn_weight = {}
        item_classes = []

    # stat_values: 存储 statOrder + 描述文本 + 标签 + 组
    stat_values = {
        "stat_ids": entry.get("statOrder", []),
        "descriptions": stat_descriptions,
        "group": entry.get("group"),
    }
    tags = entry.get("modTags", [])
    if tags:
        stat_values["tags"] = tags

    data = {
        "stat_id": entry["_key"],
        "name_en": entry.get("affix") or None,
        "mod_type": mod_type,
        "item_class_restrictions": item_classes if item_classes else None,
        "spawn_weight": spawn_weight if spawn_weight else None,
        "stat_values": stat_values,
        "min_ilvl": entry.get("level"),
        "game_version": "3.26",
        "is_active": True,
    }

    return data


async def import_modifiers(
    game_version: str = "3.26",
    dry_run: bool = False,
) -> dict:
    """导入全部装备词缀。

    Returns:
        {inserted, updated, skipped, errors} per source
    """
    all_stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    sources = [
        ("ModItem.lua (常规词缀)", MOD_ITEM_PATH, "ModItem"),
        ("ModItemExclusive.lua (专属/传奇词缀)", MOD_EXCLUSIVE_PATH, "ModItemExclusive"),
        ("ModJewel.lua (珠宝词缀)", MOD_JEWEL_PATH, "ModJewel"),
    ]

    for label, path, source_type in sources:
        logger.info(f"Parsing {label}...")
        entries = parse_mod_file(path)
        logger.info(f"Parsed {len(entries)} entries from {label}")

        stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
        total = len(entries)

        async with async_session_factory() as db:
            for i, entry in enumerate(entries):
                try:
                    data = map_mod_to_db(entry, source_type)
                    # Remove game_version from data to avoid duplicate kwarg
                    data.pop("game_version", None)
                    hval = content_hash(data)

                    existing = await db.scalar(
                        select(Modifier).where(Modifier.stat_id == data["stat_id"])
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
                            db.add(Modifier(
                                **data,
                                content_hash=hval,
                                game_version=game_version,
                            ))
                        stats["inserted"] += 1

                except Exception as e:
                    stats["errors"] += 1
                    if stats["errors"] <= 5:
                        logger.warning(f"Error on {entry.get('_key', '?')}: {e}")

                if (i + 1) % 500 == 0:
                    if not dry_run:
                        await db.commit()
                    logger.info(
                        f"  [{label}] Progress: {i + 1}/{total} | "
                        f"ins={stats['inserted']} upd={stats['updated']} "
                        f"skip={stats['skipped']} err={stats['errors']}"
                    )

            if not dry_run:
                await db.commit()

        logger.info(f"  [{label}] Complete: ins={stats['inserted']}, "
                    f"upd={stats['updated']}, skip={stats['skipped']}, err={stats['errors']}")
        for k in all_stats:
            all_stats[k] += stats[k]

    return all_stats


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Import POE2 equipment modifiers")
    parser.add_argument("--dry-run", action="store_true", help="Parse and map without writing")
    parser.add_argument("--game-version", default="3.26")
    args = parser.parse_args()

    result = await import_modifiers(
        game_version=args.game_version,
        dry_run=args.dry_run,
    )
    print(f"\nFinal: {result}")


if __name__ == "__main__":
    asyncio.run(main())

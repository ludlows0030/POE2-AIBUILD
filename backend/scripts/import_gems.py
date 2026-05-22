"""POE2 宝石导入 — 从 PoB2 Gems.lua 导入全部技能/辅助宝石。

数据源: PoB2 社区仓库 src/Data/Gems.lua（902 条，含完整分类/属性/标签）

用法:
    cd backend && python scripts/import_gems.py
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
from app.models.base import GameMechanic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

GEMS_LUA_PATH = Path(__file__).parent.parent / "data" / "Gems.lua"


def content_hash(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── Lua parser for Gems.lua ──────────────────────────────────────────

def _parse_lua_table(text: str, start: int = 0) -> tuple[dict, int]:
    """Parse a Lua table from text starting at start, returning (dict, end_pos).

    Mixed tables (both named and positional values) are stored together.
    Positional values get auto-incrementing integer keys (0, 1, 2...).
    """
    result: dict = {}
    pos = start
    array_index = 0

    # Skip opening brace
    brace_match = re.match(r'\s*\{', text[pos:])
    if not brace_match:
        return result, pos
    pos += brace_match.end()

    while pos < len(text):
        prev_pos = pos

        # Skip whitespace and commas
        ws = re.match(r'\s*', text[pos:])
        pos += ws.end() if ws else 0

        if pos >= len(text):
            break

        # Check for closing brace
        if text[pos] == '}':
            pos += 1
            # Convert pure-array tables (all keys are ints 0..N-1) to lists
            if result and all(isinstance(k, int) for k in result.keys()):
                int_keys = sorted(k for k in result if isinstance(k, int))
                if int_keys and int_keys == list(range(len(int_keys))):
                    return [result[k] for k in int_keys], pos
            return result, pos

        # Parse key (optional — if absent, this is a positional value)
        key: str | int | None = None
        saved_pos = pos
        if text[pos] == '[':
            # ["string key"]
            bracket_key = re.match(r'\["([^"]*)"\]\s*=\s*', text[pos:])
            if bracket_key:
                key = bracket_key.group(1)
                pos += bracket_key.end()
            # ['string key']
            elif re.match(r"\['([^']*)'\]\s*=\s*", text[pos:]):
                bracket_key2 = re.match(r"\['([^']*)'\]\s*=\s*", text[pos:])
                if bracket_key2:
                    key = bracket_key2.group(1)
                    pos += bracket_key2.end()
            # [number] — numeric key (used in tradeHashes etc.)
            elif re.match(r'\[(\d+)\]\s*=\s*', text[pos:]):
                num_key = re.match(r'\[(\d+)\]\s*=\s*', text[pos:])
                if num_key:
                    key = int(num_key.group(1))
                    pos += num_key.end()

        if key is None:
            ident_key = re.match(r'([a-zA-Z_]\w*)\s*=\s*', text[pos:])
            if ident_key:
                key = ident_key.group(1)
                pos += ident_key.end()

        if key is None:
            # Positional value without key — assign integer index
            key = array_index
            array_index += 1
            pos = saved_pos  # rewind to parse value from original position

        # Parse value
        val, pos = _parse_lua_value(text, pos)
        result[key] = val

        # Skip trailing comma
        ws2 = re.match(r'\s*,?\s*', text[pos:])
        pos += ws2.end() if ws2 else 0

        # Safety: break if position didn't advance (truncated file / parse error)
        if pos <= prev_pos:
            break

    return result, pos


def _parse_lua_value(text: str, pos: int) -> tuple[object, int]:
    """Parse a single Lua value at pos, returning (value, new_pos)."""
    if pos >= len(text):
        return None, pos

    ch = text[pos]

    # Nested table
    if ch == '{':
        return _parse_lua_table(text, pos)

    # String — double quotes
    if ch == '"':
        end = text.index('"', pos + 1)
        return text[pos + 1:end], end + 1

    # String — single quotes
    if ch == "'":
        end = text.index("'", pos + 1)
        return text[pos + 1:end], end + 1

    # Number
    num_match = re.match(r'(-?\d+\.?\d*)', text[pos:])
    if num_match:
        num_str = num_match.group(1)
        pos += len(num_str)
        if '.' in num_str:
            return float(num_str), pos
        return int(num_str), pos

    # Boolean
    if text[pos:pos + 4] == 'true':
        return True, pos + 4
    if text[pos:pos + 5] == 'false':
        return False, pos + 5

    # nil
    if text[pos:pos + 3] == 'nil':
        return None, pos + 3

    logger.warning(f"Unknown value at pos {pos}: {text[pos:pos+40]!r}")
    return None, pos


def parse_gems_lua(filepath: Path) -> list[dict]:
    """Parse Gems.lua and return list of gem entry dicts."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Find the return table
    table_start = content.index("{")
    top_table, _ = _parse_lua_table(content, table_start)

    entries: list[dict] = []
    for key, value in top_table.items():
        if isinstance(value, dict):
            value["_key"] = key
            entries.append(value)

    return entries


# ── Data mapping ─────────────────────────────────────────────────────

def map_gem_to_db(entry: dict) -> dict:
    """Convert a Gems.lua entry to game_mechanic row data."""
    tags = entry.get("tags", {})

    # Determine skill classification
    is_active = tags.get("grants_active_skill", False)
    gem_type = entry.get("gemType", "")
    is_support = gem_type == "Support"

    if is_active and is_support:
        skill_type = "meta"
    elif is_active:
        skill_type = "active"
    elif is_support:
        skill_type = "support"
    else:
        skill_type = "unknown"

    # Extract tag keys where value is true
    tag_keys = sorted([k for k, v in tags.items() if v is True])

    # Weapon requirements
    weapon_req_str = entry.get("weaponRequirements", "")
    weapon_reqs: list[str] = []
    if weapon_req_str:
        weapon_reqs = [w.strip() for w in weapon_req_str.split(",") if w.strip()]

    # Attribute requirements
    attr_reqs = {}
    req_str = entry.get("reqStr", 0)
    req_dex = entry.get("reqDex", 0)
    req_int = entry.get("reqInt", 0)
    if req_str:
        attr_reqs["str"] = req_str
    if req_dex:
        attr_reqs["dex"] = req_dex
    if req_int:
        attr_reqs["int"] = req_int

    data = {
        "skill_id": entry.get("gameId", entry["_key"]),
        "skill_name": entry.get("name", ""),
        "base_type_name": entry.get("baseTypeName"),
        "skill_type": skill_type,
        "gem_type": gem_type,
        "gem_tier": entry.get("Tier"),
        "max_level": entry.get("naturalMaxLevel"),
        "variant_id": entry.get("variantId"),
        "granted_effect_id": entry.get("grantedEffectId"),
        "gem_tags_string": entry.get("tagString"),
        "tags": tag_keys if tag_keys else None,
        "weapon_requirements": weapon_reqs if weapon_reqs else None,
        "attribute_requirements": attr_reqs if attr_reqs else None,
        "is_active": True,
    }

    return data


# ── Main import ──────────────────────────────────────────────────────

async def import_gems(
    game_version: str = "3.26",
    dry_run: bool = False,
) -> dict:
    """Import all gems from Gems.lua into game_mechanic table.

    Returns:
        {inserted, updated, skipped, errors}
    """
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    # Step 1: Parse Gems.lua
    logger.info("Parsing Gems.lua...")
    entries = parse_gems_lua(GEMS_LUA_PATH)
    logger.info(f"Parsed {len(entries)} gem entries")

    # Step 2: Import
    total = len(entries)

    async with async_session_factory() as db:
        for i, entry in enumerate(entries):
            try:
                data = map_gem_to_db(entry)
                hval = content_hash(data)

                existing = await db.scalar(
                    select(GameMechanic).where(GameMechanic.skill_id == data["skill_id"])
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
                        db.add(GameMechanic(
                            **data,
                            content_hash=hval,
                            game_version=game_version,
                        ))
                    stats["inserted"] += 1

            except Exception as e:
                stats["errors"] += 1
                logger.warning(f"Error on entry {entry.get('_key', '?')}: {e}")

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
        f"Gem import complete: ins={stats['inserted']}, "
        f"upd={stats['updated']}, skip={stats['skipped']}, err={stats['errors']}"
    )
    return stats


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Import POE2 skill/support gems from Gems.lua")
    parser.add_argument("--dry-run", action="store_true", help="Parse and map without writing to DB")
    parser.add_argument("--game-version", default="3.26")
    args = parser.parse_args()

    result = await import_gems(
        game_version=args.game_version,
        dry_run=args.dry_run,
    )
    print(f"\nFinal: {result}")


if __name__ == "__main__":
    asyncio.run(main())

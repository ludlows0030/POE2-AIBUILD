"""批量导入传奇物品 — 独立脚本，简单延迟控制，每 10 条自动提交。

用法:
    cd backend && python scripts/import_uniques.py [--limit N] [--delay 4.0]
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from sqlalchemy import select

# 确保 backend 在 Python 路径
sys.path.insert(0, ".")

from app.collectors.poe2db_lookup import fetch_poe2db_page, lookup, cache_clear
from app.database import async_session_factory
from app.models.base import UniqueItem

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── 非物品过滤 ─────────────────────────────────────────────

_SKIP = [
    "Damage", "Attack", "Armour", "Evasion", "Energy_Shield", "Physical",
    "Strength", "Dexterity", "Intelligence", "Player_", "Skill_Gems",
    "Support_Gems", "Spirit_Gems", "Stun", "Melee", "Rarity", "Item_",
    "Unique_item", "PoEDB", "Modifier", "Modifiers", "Keywords",
    "Ascendancy", "Passive", "GemTags", "Lineage", "Desecrated",
    "Historic", "Timeless", "Crafting", "Quest", "Waystones",
    "Shrine", "Strongbox", "Essence", "Act", "Patreon",
]


def parse_listing(html: str, lang: str) -> dict[str, str]:
    """从 Unique_item 页面提取 {slug: name}。"""
    soup = BeautifulSoup(html, "lxml")
    items: dict[str, str] = {}
    tab = soup.find("div", class_="tab-content")
    if not tab:
        return items
    for a in tab.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if f"/{lang}/" not in href or not text or len(text) < 3:
            continue
        slug = href.split(f"/{lang}/")[-1]
        if any(p in slug for p in _SKIP):
            continue
        if "/" in slug or "?" in slug:
            continue
        if slug not in items:
            items[slug] = text
    return items


def content_hash(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def parse_int(val) -> int | None:
    import re
    if val is None:
        return None
    try:
        if isinstance(val, str):
            nums = re.findall(r"\d+", val)
            return int(nums[0]) if nums else None
        return int(val)
    except (ValueError, TypeError):
        return None


async def scrape_detail(slug: str, name_cn: str) -> dict | None:
    """抓取单个传奇物品详情。"""
    r = await lookup(slug, "us", format="json")
    if not r.get("found"):
        return None

    tables = r.get("tables", [])
    data = {
        "name_en": slug.replace("_", " "),
        "name_zh": name_cn or None,
        "base_item_type": None,
        "item_class": None,
        "required_level": None,
        "required_str": None,
        "required_dex": None,
        "required_int": None,
        "explicit_mods": [],
        "flavour_text": r.get("description") or None,
    }

    # Table 0: 元数据
    if tables:
        for row in tables[0]:
            k = row.get("Name", "")
            v = row.get("Show Full Descriptions", "")
            if k == "BaseType":
                data["base_item_type"] = v
            elif k == "Acronym":
                data["item_class"] = v
            elif k == "Icon" and not data.get("base_item_type"):
                parts = v.split("/")
                if len(parts) >= 2:
                    data["base_item_type"] = parts[-2]

    # Tables 2+: 词缀
    for table in tables[2:]:
        headers = list(table[0].keys()) if table else []
        if not headers or "BuffGroupsID" in headers or "IsCharged" in headers:
            continue
        if "Version" in headers:
            continue

        stat_col = [h for h in headers if h != "Family"][0] if len(headers) > 1 else ""
        for row in table:
            family = row.get("Family", "")
            sval = row.get(stat_col, "") if stat_col else ""

            if family == "Req. level":
                lv = parse_int(sval)
                if lv and (data["required_level"] is None or lv > data["required_level"]):
                    data["required_level"] = lv
            elif family in ("Domains", "GenerationType", "Craft Tags"):
                pass
            elif family == "Stats" and sval and sval not in ("1", ""):
                data["explicit_mods"].append(f"{stat_col}: {sval}")

    return data


async def main():
    limit = 0
    delay = 4.0  # 每 4 秒一个请求 = 15/分钟

    # 解析命令行参数
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
        elif arg == "--delay" and i + 1 < len(args):
            delay = float(args[i + 1])

    cache_clear()

    async with async_session_factory() as db:
        # 1. 获取列表
        logger.info("Step 1: fetching listing pages...")
        html_us = await fetch_poe2db_page("Unique_item", "us")
        html_cn = await fetch_poe2db_page("Unique_item", "cn")

        items_us = parse_listing(html_us, "us")
        items_cn = parse_listing(html_cn, "cn") if html_cn else {}
        logger.info(f"US: {len(items_us)} items, CN: {len(items_cn)} items")

        all_slugs = sorted(set(items_us.keys()) | set(items_cn.keys()))
        logger.info(f"Merged: {len(all_slugs)} total unique items")

        if limit > 0:
            all_slugs = all_slugs[:limit]

        # 2. 逐条导入
        inserted = 0
        updated = 0
        skipped = 0
        errors = []

        for i, slug in enumerate(all_slugs):
            name_cn = items_cn.get(slug, "")
            name_us = items_us.get(slug, slug.replace("_", " "))

            try:
                detail = await scrape_detail(slug, name_cn)
                if detail is None:
                    errors.append(f"{slug}: not found")
                    continue

                hval = content_hash(detail)

                existing = await db.scalar(
                    select(UniqueItem).where(UniqueItem.name_en == detail["name_en"])
                )
                if existing:
                    if existing.content_hash == hval:
                        skipped += 1
                    else:
                        for k, v in detail.items():
                            if hasattr(existing, k) and k not in ("id", "content_hash"):
                                setattr(existing, k, v)
                        existing.content_hash = hval
                        existing.game_version = "3.26"
                        existing.is_active = True
                        existing.updated_at = datetime.now(timezone.utc)
                        updated += 1
                else:
                    db.add(UniqueItem(
                        **detail,
                        content_hash=hval,
                        game_version="3.26",
                    ))
                    inserted += 1

            except Exception as e:
                errors.append(f"{slug}: {e}")

            # 每 10 条提交并报告进度
            if (i + 1) % 10 == 0:
                await db.commit()
                logger.info(
                    f"Progress: {i + 1}/{len(all_slugs)} | "
                    f"ins={inserted} upd={updated} skip={skipped} err={len(errors)}"
                )

            # 延迟控制（跳过最后一个）
            if i < len(all_slugs) - 1:
                await asyncio.sleep(delay)

        # 最终提交
        await db.commit()

        logger.info("=" * 60)
        logger.info(f"COMPLETE: {inserted} inserted, {updated} updated, "
                     f"{skipped} skipped, {len(errors)} errors")
        if errors:
            logger.info(f"First 10 errors:")
            for e in errors[:10]:
                logger.info(f"  {e}")


if __name__ == "__main__":
    asyncio.run(main())

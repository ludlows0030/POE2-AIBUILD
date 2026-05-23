"""批量抓取 POE2DB 辅助宝石详情页 — 补全增伤效果数据。

针对 GameMechanic 中所有 support 技能，抓取 POE2DB 详情页获取:
  - description (核心机械效果，如 "Supported Skills deal 30% more Elemental Damage")
  - quality_modifier (品质效果)
  - recommended_supports (推荐搭配)
  - mana_multiplier (魔力倍率)
  - tier / required_level / attribute_requirements

使用方式:
  python scripts/scrape_support_gems.py              # 全部 519 个 support 技能
  python scripts/scrape_support_gems.py --limit 20   # 先测试 20 个
  python scripts/scrape_support_gems.py --dry-run    # 预览不写入
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.collectors.poe2db_skill_scraper import POE2DBSkillScraper
from app.config import settings
from app.models.base import GameMechanic

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(description="Scrape POE2DB for support gem effects")
    parser.add_argument("--limit", type=int, default=0, help="Only process N gems (0=all)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent requests (default: 5)")
    args = parser.parse_args()

    engine = create_async_engine(settings.postgres_url, echo=False)

    async with AsyncSession(engine) as db:
        result = await db.execute(
            select(GameMechanic.skill_name, GameMechanic.description, GameMechanic.tags)
            .where(GameMechanic.is_active == True, GameMechanic.skill_type == "support")
        )
        rows = result.all()
        logger.info(f"Found {len(rows)} support gems in DB")
        missing_desc = sum(1 for r in rows if r[1] is None)
        logger.info(f"  missing description: {missing_desc}/{len(rows)}")

        if args.limit > 0:
            rows = rows[:args.limit]
            logger.info(f"  -> processing first {len(rows)} gems")

        skill_list = [{"name_en": r[0]} for r in rows]

    scraper = POE2DBSkillScraper(concurrency=args.concurrency)
    try:
        logger.info(f"Starting Phase 2 scrape for {len(skill_list)} support gems...")
        detailed = await scraper.scrape_skill_details_batch(skill_list, limit=0)

        valid = [d for d in detailed if not isinstance(d, Exception) and d.get("detail_found")]
        failed = [d for d in detailed if isinstance(d, Exception) or not d.get("detail_found")]
        logger.info(f"Scrape complete: {len(valid)} success, {len(failed)} failed")

        if args.dry_run:
            for i, s in enumerate(valid[:20]):
                print(f"\n--- {s['name_en']} ---")
                print(f"  tier: {s.get('tier')}")
                print(f"  display_tags: {s.get('display_tags')}")
                print(f"  mana_cost_multi: {s.get('mana_cost_max')}")
                desc = s.get('description', '')
                print(f"  description: {desc[:150]}...")
                print(f"  quality: {s.get('quality_modifier', '')[:100]}...")
                rec = s.get('recommended_supports', [])
                print(f"  recommended: {[r['name_en'] for r in rec[:5]]}")
            logger.info(f"DRY RUN: would update {len(valid)} gems, {len(failed)} failures")
            if failed:
                logger.info(f"Failed: {[f.get('name_en', str(f)) for f in failed[:10]]}")
            return

        async with AsyncSession(engine) as db:
            updated = 0
            for skill in valid:
                existing = await db.scalar(
                    select(GameMechanic).where(GameMechanic.skill_name == skill["name_en"])
                )
                if not existing:
                    continue

                # 标签 — POE2DB 页面标签比 Gems.lua 更完整
                display_tags = skill.get("display_tags_en") or skill.get("display_tags") or []
                type_tags = skill.get("type_tags") or []
                combined_tags = list(dict.fromkeys(display_tags + type_tags))
                if combined_tags:
                    combined_tags.append("support")
                    existing.tags = combined_tags

                # 描述 — 核心机械效果
                desc = skill.get("description") or ""
                if skill.get("quality_modifier"):
                    desc = f"{desc} | 品质: {skill['quality_modifier']}" if desc else f"品质: {skill['quality_modifier']}"
                if desc:
                    existing.description = desc

                # 类型（从页面覆盖更准确）
                if skill.get("skill_type"):
                    existing.skill_type = skill["skill_type"]

                # 推荐辅助宝石
                rec = skill.get("recommended_supports", [])
                if rec:
                    existing.synergies = [s["name_en"] for s in rec]

                # 更精确的属性需求
                if skill.get("attribute_requirements"):
                    existing.attribute_requirements = skill["attribute_requirements"]
                if skill.get("required_level"):
                    existing.required_level = skill["required_level"]

                updated += 1

            await db.commit()
            logger.info(f"Updated {updated} support gems with effect data")

        # 最终统计
        async with AsyncSession(engine) as db:
            r = await db.execute(text(
                "SELECT count(*) as total, "
                "count(CASE WHEN description IS NOT NULL THEN 1 END) as has_desc, "
                "count(CASE WHEN synergies IS NOT NULL THEN 1 END) as has_syn "
                "FROM game_mechanic WHERE is_active = true AND skill_type = 'support'"
            ))
            row = r.fetchone()
            logger.info(f"Final: {row[1]}/{row[0]} support gems have descriptions, "
                        f"{row[2]}/{row[0]} have synergies")

    finally:
        await scraper.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

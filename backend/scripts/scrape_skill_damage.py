"""批量抓取 POE2DB 技能详情页 — 补全伤害效用/暴击率数据。

针对 GameMechanic 中所有 active 技能，抓取 POE2DB 详情页获取:
  - damage_effectiveness (伤害效用率)
  - base_crit_chance (基础暴击率)
  - cast_time / attack_speed (施放/攻击速度)
  - description (技能描述)
  - mana_cost (魔力消耗)
  - tier (宝石等阶)

使用方式:
  python scripts/scrape_skill_damage.py              # 全部 344 个 active 技能
  python scripts/scrape_skill_damage.py --limit 20   # 先测试 20 个
  python scripts/scrape_skill_damage.py --dry-run    # 预览不写入
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# 确保 backend 在 PYTHONPATH 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.collectors.poe2db_skill_scraper import POE2DBSkillScraper
from app.config import settings
from app.models.base import GameMechanic

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(description="Scrape POE2DB for skill damage data")
    parser.add_argument("--limit", type=int, default=0, help="Only process N skills (0=all)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    parser.add_argument("--concurrency", type=int, default=4, help="Concurrent requests (default: 4)")
    args = parser.parse_args()

    engine = create_async_engine(settings.postgres_url, echo=False)

    async with AsyncSession(engine) as db:
        # 查询所有 active 技能
        result = await db.execute(
            select(GameMechanic.skill_name, GameMechanic.skill_id,
                   GameMechanic.damage_effectiveness, GameMechanic.base_crit_chance,
                   GameMechanic.tags, GameMechanic.weapon_requirements)
            .where(GameMechanic.is_active == True, GameMechanic.skill_type == "active")
        )
        rows = result.all()
        logger.info(f"Found {len(rows)} active skills in DB")

        # 统计当前缺失情况
        missing_dmg = sum(1 for r in rows if r[2] is None)
        missing_crit = sum(1 for r in rows if r[3] is None)
        logger.info(f"  missing damage_effectiveness: {missing_dmg}/{len(rows)}")
        logger.info(f"  missing base_crit_chance: {missing_crit}/{len(rows)}")

        targets = rows
        if args.limit > 0:
            targets = targets[:args.limit]
            logger.info(f"  → processing first {len(targets)} skills")

        # 构建 scraper 输入
        skill_list = [{"name_en": r[0], "skill_id": r[1]} for r in targets]

    scraper = POE2DBSkillScraper(concurrency=args.concurrency)
    try:
        # Phase 2: 抓取详情
        logger.info(f"Starting Phase 2 scrape for {len(skill_list)} skills...")
        detailed = await scraper.scrape_skill_details_batch(skill_list, limit=0)

        # 筛出成功的
        valid = [d for d in detailed if not isinstance(d, Exception) and d.get("detail_found")]
        failed = [d for d in detailed if isinstance(d, Exception) or not d.get("detail_found")]
        logger.info(f"Scrape complete: {len(valid)} success, {len(failed)} failed")

        if args.dry_run:
            # 预览：打印前 20 条结果
            for i, s in enumerate(valid[:20]):
                print(f"\n--- {s['name_en']} ---")
                print(f"  damage_effectiveness: {s.get('damage_effectiveness_min')}-{s.get('damage_effectiveness_max')}%")
                print(f"  base_crit_chance: {s.get('base_crit_chance')}")
                print(f"  cast_time: {s.get('cast_time')}")
                print(f"  attack_speed: {s.get('attack_speed')}")
                print(f"  mana_cost: {s.get('mana_cost_min')}-{s.get('mana_cost_max')}")
                print(f"  display_tags: {s.get('display_tags')}")
                print(f"  tier: {s.get('tier')}")
                print(f"  weapon_requirements: {s.get('weapon_requirements')}")
                desc = s.get('description', '')
                print(f"  description: {desc[:100]}...")
            logger.info(f"DRY RUN: would update {len(valid)} skills, {len(failed)} failures")
            if failed:
                logger.info(f"Failed skills: {[f.get('name_en', str(f)) for f in failed[:10]]}")
            return

        # Phase 3: 写入数据库
        async with AsyncSession(engine) as db:
            updated = 0
            for skill in valid:
                existing = await db.scalar(
                    select(GameMechanic).where(
                        GameMechanic.skill_name == skill["name_en"]
                    )
                )
                if not existing:
                    continue

                # 伤害效用: 攻击技能从页面获取，法术默认 100%
                dmg_eff = skill.get("damage_effectiveness_max") or skill.get("damage_effectiveness")
                if dmg_eff:
                    existing.damage_effectiveness = dmg_eff
                elif not existing.weapon_requirements:
                    # 法术 — POE2 中法术的附加伤害效用默认为 100%
                    existing.damage_effectiveness = 100

                # 暴击率
                if skill.get("base_crit_chance"):
                    existing.base_crit_chance = skill["base_crit_chance"]

                # 描述
                desc = skill.get("description") or ""
                if skill.get("quality_modifier"):
                    desc = f"{desc} | 品质: {skill['quality_modifier']}" if desc else f"品质: {skill['quality_modifier']}"
                if desc:
                    existing.description = desc

                # 协同宝石
                rec = skill.get("recommended_supports", [])
                if rec:
                    existing.synergies = [s["name_en"] for s in rec]

                # 更新属性需求（POE2DB 数据更精确）
                if skill.get("attribute_requirements"):
                    existing.attribute_requirements = skill["attribute_requirements"]
                if skill.get("required_level"):
                    existing.required_level = skill["required_level"]

                updated += 1

            await db.commit()
            logger.info(f"Updated {updated} skills with damage data")

        # 最终统计
        async with AsyncSession(engine) as db:
            r = await db.execute(text(
                "SELECT count(*) as total, "
                "count(CASE WHEN damage_effectiveness > 0 THEN 1 END) as has_dmg, "
                "count(CASE WHEN base_crit_chance > 0 THEN 1 END) as has_crit "
                "FROM game_mechanic WHERE is_active = true AND skill_type = 'active'"
            ))
            row = r.fetchone()
            logger.info(f"Final stats: {row[1]}/{row[0]} have damage_effectiveness, "
                        f"{row[2]}/{row[0]} have base_crit_chance")

    finally:
        await scraper.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

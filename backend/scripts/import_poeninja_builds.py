"""poe.ninja BD 数据导入 — parse_poeninja.py 的 JSON → PostgreSQL。

将 data/builds/poeninja_{league}.json 导入:
  - character (角色基本信息)
  - skill_group (技能宝石)
  - build_meta (BD 元数据/标签)

使用方式:
  python scripts/import_poeninja_builds.py                     # 导入 vaal 赛季数据
  python scripts/import_poeninja_builds.py --league vaal       # 指定赛季
  python scripts/import_poeninja_builds.py --dry-run           # 预览不写入
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── POE2 升华 → 基础职业映射 ────────────────────────────────
# 数据源: poe.ninja class 字典 + POE2 官方 12 职业 36 升华

ASCENDANCY_TO_CLASS: dict[str, str] = {
    # Sorceress
    "Stormweaver": "Sorceress",
    "Chronomancer": "Sorceress",
    # Warrior
    "Titan": "Warrior",
    "Warbringer": "Warrior",
    "Smith of Kitava": "Warrior",
    # Witch
    "Infernalist": "Witch",
    "Blood Mage": "Witch",
    "Lich": "Witch",
    # Ranger
    "Deadeye": "Ranger",
    "Pathfinder": "Ranger",
    # Monk
    "Invoker": "Monk",
    "Acolyte of Chayula": "Monk",
    # Mercenary
    "Tactician": "Mercenary",
    "Gemling Legionnaire": "Mercenary",
    "Witchhunter": "Mercenary",
    # Huntress
    "Amazon": "Huntress",
    "Beastmaster": "Huntress",
    "Ritualist": "Huntress",
    # Druid
    "Shaman": "Druid",
    "Oracle": "Druid",
    # Marauder (Templar-equivalent)
    "Disciple of Varashta": "Templar",
    # Unascended — try to infer from class_id or skill types
    "Unascended": "Unknown",
}

# 伤害类型标签 → 技能名关键词映射 (用于生成 damage_types 标签)
DAMAGE_KEYWORD_MAP: dict[str, str] = {
    "lightning": "Lightning", "spark": "Lightning", "arc": "Lightning",
    "cold": "Cold", "ice": "Cold", "frost": "Cold", "comet": "Cold",
    "fire": "Fire", "flame": "Fire", "ember": "Fire", "ignite": "Fire",
    "physical": "Physical", "bleed": "Physical", "impale": "Physical",
    "chaos": "Chaos", "hex": "Chaos", "poison": "Chaos",
}

# 玩法风格推断
def infer_playstyle(skills: list[str], ascendancy: str) -> str:
    skill_text = " ".join(s.lower() for s in skills)
    if any(k in skill_text for k in ["spark", "arc", "comet", "fireball", "hex blast", "frost"]):
        return "spell_caster"
    if any(k in skill_text for k in ["arrow", "bow", "snipe", "rain of"]):
        return "bow_ranged"
    if any(k in skill_text for k in ["crossbow", "grenade", "galvanic", "explosive shot"]):
        return "crossbow_ranged"
    if any(k in skill_text for k in ["summon", "raging spirit", "skeleton", "zombie"]):
        return "minion_summoner"
    if any(k in skill_text for k in ["strike", "flurry", "palm", "bell"]):
        return "melee_strike"
    if any(k in skill_text for k in ["slam", "hammer", "earthquake", "boneshatter"]):
        return "melee_slam"
    return "any"


def infer_damage_types(skills: list[str]) -> list[str]:
    types: set[str] = set()
    for skill in skills:
        for keyword, dtype in DAMAGE_KEYWORD_MAP.items():
            if keyword in skill.lower():
                types.add(dtype)
    return list(types) if types else ["Physical"]


def infer_tags(skills: list[str], keystones: list[str], ascendancy: str) -> list[str]:
    tags: set[str] = set()
    skill_text = " ".join(s.lower() for s in skills)
    if any(k in skill_text for k in ["lightning", "spark", "arc"]): tags.add("Lightning")
    if any(k in skill_text for k in ["cold", "ice", "frost"]): tags.add("Cold")
    if any(k in skill_text for k in ["fire", "flame", "ember"]): tags.add("Fire")
    if any(k in skill_text for k in ["chaos", "hex", "poison"]): tags.add("Chaos")
    if any(k in skill_text for k in ["projectile", "arrow", "bow"]): tags.add("Projectile")
    if any(k in skill_text for k in ["aoe", "area"]): tags.add("AoE")
    if any(k in skill_text for k in ["crit", "critical"]): tags.add("Critical")
    if any(k in skill_text for k in ["minion", "summon", "skeleton"]): tags.add("Minion")
    if any(k in skill_text for k in ["totem", "ballista"]): tags.add("Totem")
    if any(k in skill_text for k in ["trigger", "cast on"]): tags.add("Trigger")
    if "MoM" in keystones or "Mind Over Matter" in keystones: tags.add("MoM")
    if "CI" in keystones or "Chaos Inoculation" in keystones: tags.add("CI")
    if "EB" in keystones or "Eldritch Battery" in keystones: tags.add("EB")
    return list(tags)


def load_builds(json_path: Path) -> list[dict]:
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)


async def import_builds(
    db: AsyncSession,
    builds: list[dict],
    league: str,
    dry_run: bool = False,
) -> dict[str, int]:
    counts = {"character": 0, "skill_group": 0, "build_meta": 0, "skipped": 0}
    now = datetime.now(timezone.utc)

    for b in builds:
        name = b.get("name", "")
        account = b.get("account", "")
        ascendancy = b.get("ascendancy", "Unknown")
        char_class = ASCENDANCY_TO_CLASS.get(ascendancy, "Unknown")
        level = b.get("level", 1)
        skills = [s["name"] for s in b.get("skills", []) if isinstance(s, dict)]
        keystones = [k["name"] for k in b.get("keystones", []) if isinstance(k, dict)]
        main_skill = b.get("main_skill", skills[0] if skills else "")
        dps_val = b.get("dps", 0)
        ehp_val = b.get("ehp", 0)
        life = b.get("life", 0)
        es = b.get("energy_shield", 0)

        # 跳过重复 (按 account+name+league 去重)
        existing = await db.execute(
            text("SELECT 1 FROM character WHERE account_name = :acc AND character_name = :cn AND league = :lg"),
            {"acc": account, "cn": name, "lg": league},
        )
        if existing.first():
            counts["skipped"] += 1
            continue

        char_id = uuid.uuid4()

        if dry_run:
            counts["character"] += 1
            counts["skill_group"] += len(skills)
            counts["build_meta"] += 1
            continue

        # — Character —
        await db.execute(
            text("""
                INSERT INTO character (id, account_name, character_name, league, level, char_class, ascendancy, last_updated)
                VALUES (:id, :account_name, :character_name, :league, :level, :char_class, :ascendancy, :last_updated)
            """),
            {
                "id": char_id,
                "account_name": account,
                "character_name": name,
                "league": league,
                "level": level,
                "char_class": char_class,
                "ascendancy": ascendancy if ascendancy != "Unascended" else None,
                "last_updated": now,
            },
        )
        counts["character"] += 1

        # — SkillGroup —
        for skill in b.get("skills", []):
            if not isinstance(skill, dict):
                continue
            skill_name = skill.get("name", "")
            skill_id = str(skill.get("id", ""))
            if not skill_name:
                continue

            is_main = skill_name == main_skill
            await db.execute(
                text("""
                    INSERT INTO skill_group (id, character_id, active_skill_id, active_skill_name, trigger_condition, gem_links)
                    VALUES (:id, :character_id, :active_skill_id, :active_skill_name, :trigger, :links)
                """),
                {
                    "id": uuid.uuid4(),
                    "character_id": char_id,
                    "active_skill_id": skill_id,
                    "active_skill_name": skill_name,
                    "trigger": "main" if is_main else None,
                    "links": 5 if is_main else 4,
                },
            )
            counts["skill_group"] += 1

        # — BuildMeta —
        playstyle = infer_playstyle(skills, ascendancy)
        damage_types = infer_damage_types(skills)
        tags = infer_tags(skills, keystones, ascendancy)
        # 简单强度评分: 基于 level + dps 归一化
        power = round((level / 100.0) * 5.0 + min(dps_val / 500_000, 1.0) * 5.0, 1)

        await db.execute(
            text("""
                INSERT INTO build_meta (id, character_id, source, source_url, collected_at, league_version,
                    power_rating, tags, damage_types, playstyle, is_active)
                VALUES (:id, :character_id, :source, :source_url, :collected_at, :league_version,
                    :power_rating, :tags, :damage_types, :playstyle, :is_active)
            """),
            {
                "id": uuid.uuid4(),
                "character_id": char_id,
                "source": "poeninja",
                "source_url": None,
                "collected_at": now,
                "league_version": league,
                "power_rating": power,
                "tags": tags,
                "damage_types": damage_types,
                "playstyle": playstyle,
                "is_active": True,
            },
        )
        counts["build_meta"] += 1

    return counts


async def main():
    parser = argparse.ArgumentParser(description="Import poe.ninja builds to PostgreSQL")
    parser.add_argument("--league", default="vaal", help="League slug (default: vaal)")
    parser.add_argument("--input", help="JSON file path (default: data/builds/poeninja_{league}.json)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    json_path = Path(args.input) if args.input else Path(f"data/builds/poeninja_{args.league}.json")
    if not json_path.exists():
        logger.error(f"JSON file not found: {json_path}")
        return

    builds = load_builds(json_path)
    logger.info(f"Loaded {len(builds)} builds from {json_path}")

    from app.config import settings
    engine = create_async_engine(settings.postgres_url, echo=False)

    async with AsyncSession(engine) as session:
        counts = await import_builds(session, builds, args.league, dry_run=args.dry_run)
        if not args.dry_run:
            await session.commit()
            logger.info(f"Committed. Stats: {counts}")
        else:
            logger.info(f"DRY RUN. Would import: {counts}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

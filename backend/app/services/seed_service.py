"""BD 种子数据 — 已知优秀 POE2 流派骨架。

这些数据作为 LLM 推理的"参考锚点"使用（需求文档 §4.2 Step 2）。
当用户请求生成 BD 时，Agent 从这些锚点中找到最相关的作为推理起点。

数据来源：maxroll.gg, pobb.in, poe.ninja 社区 BD 整理。
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import BuildMeta, Character, SkillGroup

# ── 已知 POE2 流派骨架 ────────────────────────────────

SEED_BUILDS: list[dict] = [
    {
        "name": "Spark Stormweaver (Archmage)",
        "zh_name": "电光火花 风暴编织者（大法师）",
        "class": "Sorceress",
        "ascendancy": "Stormweaver",
        "level": 95,
        "core_skill": "Spark",
        "playstyle": "spell_caster",
        "damage_types": ["Lightning", "Spell", "Projectile"],
        "key_mechanics": [
            "Archmage — 最大魔力转为附加闪电伤害",
            "Arcane Surge — 施法速度与魔力回复",
            "Elemental Overload — 非暴击元素伤害加成",
            "Mana Tempest — 魔力消耗换额外闪电伤害",
        ],
        "budget_range": (30, 200),
        "tags": ["mapper", "all_content", "MoM", "ES"],
    },
    {
        "name": "Lightning Arrow Deadeye",
        "zh_name": "闪电箭 神射手",
        "class": "Ranger",
        "ascendancy": "Deadeye",
        "level": 93,
        "core_skill": "Lightning Arrow",
        "playstyle": "bow_ranged",
        "damage_types": ["Lightning", "Attack", "Projectile", "Bow"],
        "key_mechanics": [
            "Tailwind — 攻速与移速叠加",
            "Far Shot — 远距离伤害加成",
            "Lightning Rod — 闪电箭地面效应叠加",
            "Herald of Thunder — 击杀连锁闪电清图",
        ],
        "budget_range": (10, 100),
        "tags": ["mapper", "speed_farm", "evasion"],
    },
    {
        "name": "Ice Strike Invoker",
        "zh_name": "冰击 祈求者",
        "class": "Monk",
        "ascendancy": "Invoker",
        "level": 94,
        "core_skill": "Ice Strike",
        "playstyle": "melee_strike",
        "damage_types": ["Cold", "Attack", "Melee", "Strike"],
        "key_mechanics": [
            "Elemental Expression — 暴击触发额外元素伤害",
            "Meditate — 叠层爆发机制",
            "Herald of Ice — 冰冻爆炸清图",
            "Chaos Inoculation — 免疫混沌伤，全堆 ES",
        ],
        "budget_range": (20, 150),
        "tags": ["bosser", "mapper", "CI", "ES", "crit"],
    },
    {
        "name": "Minion Infernalist (SRS)",
        "zh_name": "愤怒烈焰 死灵法师（SRS）",
        "class": "Witch",
        "ascendancy": "Infernalist",
        "level": 94,
        "core_skill": "Summon Raging Spirit",
        "playstyle": "minion_summoner",
        "damage_types": ["Fire", "Minion", "Spell"],
        "key_mechanics": [
            "Grim Feast — 击杀回复 ES",
            "Sacrifice — 献祭换伤害",
            "Soul Offering — 召唤物强化",
            "Pain Attunement — 低血更多法术伤害",
        ],
        "budget_range": (15, 120),
        "tags": ["all_content", "minion", "ES", "safe"],
    },
    {
        "name": "Hammer of the Gods Titan",
        "zh_name": "神锤 泰坦",
        "class": "Warrior",
        "ascendancy": "Titan",
        "level": 95,
        "core_skill": "Hammer of the Gods",
        "playstyle": "melee_slam",
        "damage_types": ["Fire", "Attack", "Melee", "Slam", "AoE"],
        "key_mechanics": [
            "Crushing Blows — 眩晕门槛降低",
            "Giant's Blood — 单手 + 盾牌",
            "Herald of Ash — 溢出伤害清图",
            "Seismic Cry — 增伤战吼",
        ],
        "budget_range": (25, 200),
        "tags": ["bosser", "thick", "armour", "life"],
    },
    {
        "name": "Galvanic Shards Tactician",
        "zh_name": "电流碎片 战术家",
        "class": "Mercenary",
        "ascendancy": "Tactician",
        "level": 90,
        "core_skill": "Galvanic Shards",
        "playstyle": "crossbow_ranged",
        "damage_types": ["Lightning", "Attack", "Projectile", "Crossbow"],
        "key_mechanics": [
            "Fresh Clip — 换弹加速与增伤",
            "Voltaic Grenade — 感电覆盖",
            "Herald of Thunder — 清图连锁",
            "Elemental Equilibrium — 切换元素降抗",
        ],
        "budget_range": (10, 80),
        "tags": ["mapper", "speed", "evasion", "hybrid"],
    },
]


async def seed_builds(db: AsyncSession) -> list[str]:
    """将种子 BD 写入数据库。

    返回: 创建的角色 ID 列表。
    """
    ids: list[str] = []

    for data in SEED_BUILDS:
        # 检查是否已存在
        from sqlalchemy import select
        existing = await db.scalar(
            select(Character.id).where(Character.character_name == data["name"])
        )
        if existing:
            ids.append(str(existing))
            continue

        character = Character(
            character_name=data["name"],
            account_name="seed_data",
            league="Unknown",
            level=data["level"],
            char_class=data["class"],
            ascendancy=data["ascendancy"],
            last_updated=datetime.now(timezone.utc),
        )
        db.add(character)
        await db.flush()

        # 核心技能
        skill = SkillGroup(
            character_id=character.id,
            active_skill_name=data["core_skill"],
            active_skill_id=data["core_skill"],
        )
        db.add(skill)

        # 元数据
        meta = BuildMeta(
            character_id=character.id,
            source="seed_data",
            collected_at=datetime.now(timezone.utc),
            league_version="Unknown",
            playstyle=data["playstyle"],
            damage_types=data["damage_types"],
            tags=data["tags"],
            estimated_budget_divines=data["budget_range"][1],
        )
        db.add(meta)

        # 关键机制存入 tags（作为向量搜索参考）
        meta.tags = data["tags"] + [m.split(" —")[0] for m in data["key_mechanics"]]

        ids.append(str(character.id))

    await db.commit()
    return ids

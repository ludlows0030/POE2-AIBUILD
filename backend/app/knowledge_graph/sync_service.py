"""知识图谱同步服务 — PostgreSQL → Neo4j。

从关系数据库提取结构化数据，在 Neo4j 中构建：
  - 技能节点 + 标签关系
  - 基石天赋节点 + 技能关联
  - 升华职业节点 + 技能加成
  - 伤害类型转化链
  - 机制冲突关系
  - BD 共现技能关系

使用方式：
    sync = KnowledgeGraphSync()
    await sync.full_sync(db_session)
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.knowledge_graph.connection import neo4j_manager
from app.models.base import BuildMeta, Character, GameMechanic, SkillGroup

logger = logging.getLogger(__name__)


# ── 已知 POE2 游戏知识（固化在代码中）─────────────────────

# 技能 → 推荐基石天赋
SKILL_KEYSTONE_MAP: dict[str, list[dict[str, str]]] = {
    "Spark": [
        {"keystone": "Mind Over Matter", "effect": "魔力承伤，配合 Archmage 高魔力池", "strength": "strong"},
        {"keystone": "Elemental Overload", "effect": "非暴击更多元素伤害，Spark 低暴击流派适用", "strength": "medium"},
        {"keystone": "Eldritch Battery", "effect": "ES 转魔力，进一步放大魔力池", "strength": "medium"},
    ],
    "Ice Strike": [
        {"keystone": "Chaos Inoculation", "effect": "免疫混沌伤，纯 ES 构建", "strength": "strong"},
        {"keystone": "Elemental Overload", "effect": "更多元素伤害，如果放弃暴击", "strength": "medium"},
        {"keystone": "Ghost Dance", "effect": "闪避转 ES 回复", "strength": "medium"},
    ],
    "Lightning Arrow": [
        {"keystone": "Point Blank", "effect": "近距离更多投射物伤害", "strength": "medium"},
        {"keystone": "Acrobatics", "effect": "闪避可闪避法术", "strength": "medium"},
        {"keystone": "Elemental Equilibrium", "effect": "切换元素降抗", "strength": "medium"},
    ],
    "Summon Raging Spirit": [
        {"keystone": "Pain Attunement", "effect": "低血更多法术伤害（不 CI 时）", "strength": "medium"},
        {"keystone": "Mind Over Matter", "effect": "魔力承伤保护", "strength": "medium"},
        {"keystone": "Necromantic Aegis", "effect": "盾牌属性给召唤物", "strength": "strong"},
    ],
    "Hammer of the Gods": [
        {"keystone": "Giant's Blood", "effect": "单手武器 + 盾牌", "strength": "strong"},
        {"keystone": "Resolute Technique", "effect": "必中但不能暴击", "strength": "medium"},
        {"keystone": "Unwavering Stance", "effect": "免晕但无法闪避", "strength": "medium"},
    ],
    "Galvanic Shards": [
        {"keystone": "Elemental Equilibrium", "effect": "切换元素降抗", "strength": "medium"},
        {"keystone": "Point Blank", "effect": "近距离更多伤害", "strength": "medium"},
        {"keystone": "Acrobatics", "effect": "法术闪避", "strength": "medium"},
    ],
    "Hex Blast": [
        {"keystone": "Chaos Inoculation", "effect": "免疫混沌伤", "strength": "strong"},
        {"keystone": "Pain Attunement", "effect": "低血更多法术伤害", "strength": "medium"},
        {"keystone": "Zealot's Oath", "effect": "生命回复转 ES", "strength": "medium"},
    ],
    "Comet": [
        {"keystone": "Mind Over Matter", "effect": "魔力承伤", "strength": "medium"},
        {"keystone": "Elemental Overload", "effect": "非暴击更多元素伤害", "strength": "medium"},
        {"keystone": "Eldritch Battery", "effect": "ES 转魔力", "strength": "medium"},
    ],
}

# 升华 → 加成的技能
ASCENDANCY_SKILL_MAP: dict[str, list[dict[str, str]]] = {
    "Stormweaver": [
        {"skill": "Spark", "reason": "Arcane Surge 提供施法速度与魔力回复", "power": "strong"},
        {"skill": "Arc", "reason": "感电效果增强，连锁加成", "power": "strong"},
        {"skill": "Comet", "reason": "元素伤害与施法速度加成", "power": "medium"},
        {"skill": "Hex Blast", "reason": "元素伤害泛用加成", "power": "medium"},
    ],
    "Deadeye": [
        {"skill": "Lightning Arrow", "reason": "Tailwind 攻速移速，Far Shot 远距离伤害", "power": "strong"},
        {"skill": "Gas Arrow", "reason": "投射物加成", "power": "medium"},
        {"skill": "Snipe", "reason": "远距离与精准加成", "power": "medium"},
    ],
    "Invoker": [
        {"skill": "Ice Strike", "reason": "Elemental Expression 暴击触发，Meditate 叠层", "power": "strong"},
        {"skill": "Falling Thunder", "reason": "元素伤害与暴击加成", "power": "medium"},
        {"skill": "Shattering Palm", "reason": "冰缓爆炸", "power": "medium"},
        {"skill": "Tempest Bell", "reason": "元素连击协同", "power": "medium"},
    ],
    "Infernalist": [
        {"skill": "Summon Raging Spirit", "reason": "Grim Feast ES 回复，Sacrifice 换伤害", "power": "strong"},
        {"skill": "Detonate Dead", "reason": "火焰与尸体机制", "power": "medium"},
        {"skill": "Ember Fusillade", "reason": "火焰法术加成", "power": "medium"},
    ],
    "Titan": [
        {"skill": "Hammer of the Gods", "reason": "Crushing Blows 眩晕，Giant's Blood 单手+盾", "power": "strong"},
        {"skill": "Earthshatter", "reason": "猛击与眩晕加成", "power": "medium"},
    ],
    "Tactician": [
        {"skill": "Galvanic Shards", "reason": "Fresh Clip 换弹，Voltaic Grenade 感电", "power": "strong"},
    ],
}

# 技能 → 关键装备词缀
SKILL_AFFIX_MAP: dict[str, list[dict[str, Any]]] = {
    "Spark": [
        {"mod": "+1 to Level of all Lightning Spell Skill Gems", "slot": "Weapon", "type": "prefix", "priority": 10},
        {"mod": "% increased Lightning Damage", "slot": "Weapon", "type": "prefix", "priority": 8},
        {"mod": "% increased Cast Speed", "slot": "Weapon", "type": "suffix", "priority": 9},
        {"mod": "+ to maximum Mana", "slot": "BodyArmour", "type": "prefix", "priority": 10},
        {"mod": "% increased Energy Shield", "slot": "BodyArmour", "type": "prefix", "priority": 9},
        {"mod": "% increased Mana Regeneration Rate", "slot": "Amulet", "type": "suffix", "priority": 8},
    ],
    "Ice Strike": [
        {"mod": "Adds # to # Cold Damage", "slot": "Weapon", "type": "prefix", "priority": 10},
        {"mod": "% increased Attack Speed", "slot": "Weapon", "type": "suffix", "priority": 10},
        {"mod": "% increased Critical Strike Chance", "slot": "Weapon", "type": "suffix", "priority": 9},
        {"mod": "+ to Maximum Energy Shield", "slot": "BodyArmour", "type": "prefix", "priority": 10},
    ],
    "Lightning Arrow": [
        {"mod": "Adds # to # Lightning Damage", "slot": "Weapon", "type": "prefix", "priority": 10},
        {"mod": "% increased Attack Speed", "slot": "Weapon", "type": "suffix", "priority": 10},
        {"mod": "+ to Level of all Projectile Skill Gems", "slot": "Weapon", "type": "prefix", "priority": 9},
        {"mod": "% increased Evasion Rating", "slot": "BodyArmour", "type": "prefix", "priority": 8},
    ],
    "Summon Raging Spirit": [
        {"mod": "+1 to Level of all Minion Skill Gems", "slot": "Weapon", "type": "prefix", "priority": 10},
        {"mod": "Minions deal % increased Damage", "slot": "Weapon", "type": "suffix", "priority": 9},
        {"mod": "% increased Energy Shield", "slot": "BodyArmour", "type": "prefix", "priority": 8},
    ],
    "Hammer of the Gods": [
        {"mod": "% increased Physical Damage", "slot": "Weapon", "type": "prefix", "priority": 10},
        {"mod": "Adds # to # Fire Damage", "slot": "Weapon", "type": "prefix", "priority": 8},
        {"mod": "+ to Maximum Life", "slot": "BodyArmour", "type": "prefix", "priority": 10},
        {"mod": "% increased Armour", "slot": "BodyArmour", "type": "prefix", "priority": 8},
    ],
}

# 已知机制冲突
MECHANIC_CONFLICTS: list[dict[str, str]] = [
    {"a": "Chaos Inoculation", "b": "Pain Attunement", "reason": "CI 强制满血，Pain Attunement 需要低血"},
    {"a": "Blood Magic", "b": "Mind Over Matter", "reason": "Blood Magic 消除魔力，MoM 需要魔力承伤"},
    {"a": "Elemental Overload", "b": "Resolute Technique", "reason": "RT 禁止暴击，EO 需要暴击触发"},
    {"a": "Chaos Inoculation", "b": "Blood Magic", "reason": "CI 血量=1，Blood Magic 无法消耗生命"},
    {"a": "Ghost Dance", "b": "Iron Reflexes", "reason": "Iron Reflexes 消除闪避，Ghost Dance 需要闪避"},
]

# 伤害转化链
CONVERSION_CHAINS: list[list[str]] = [
    ["Physical", "Lightning"],
    ["Physical", "Cold"],
    ["Physical", "Fire"],
    ["Lightning", "Cold"],
    ["Cold", "Fire"],
    ["Fire", "Chaos"],
]


class KnowledgeGraphSync:
    """PostgreSQL → Neo4j 知识图谱数据同步器。"""

    def __init__(self):
        self._neo4j = neo4j_manager

    # ── 全量同步 ─────────────────────────────────────────

    async def full_sync(self, db: AsyncSession) -> dict[str, int]:
        """执行完整的知识图谱同步。

        返回: {"skills": N, "keystones": N, "ascendancies": N, "conflicts": N, ...}
        """
        counts: dict[str, int] = {}

        try:
            await self._neo4j.initialize_schema()
        except Exception:
            logger.warning("Schema initialization failed (may already exist)")

        counts["skills"] = await self._sync_skills(db)
        counts["keystones"] = await self._sync_keystones()
        counts["ascendancies"] = await self._sync_ascendancies()
        counts["mechanics"] = await self._sync_mechanics(db)
        counts["damage_types"] = await self._sync_damage_types()
        counts["playstyles"] = await self._sync_playstyles()
        counts["conflicts"] = await self._sync_conflicts()
        counts["conversions"] = await self._sync_conversions()
        counts["affixes"] = await self._sync_affixes()
        counts["co_occurrence"] = await self._sync_co_occurrence(db)
        counts["classes"] = await self._sync_classes()

        logger.info(f"Knowledge graph sync complete: {counts}")
        return counts

    # ── 各节点类型同步 ───────────────────────────────────

    async def _sync_skills(self, db: AsyncSession) -> int:
        """同步技能节点。"""
        result = await db.execute(select(SkillGroup))
        skills = result.scalars().all()

        seen: set[str] = set()
        count = 0
        for s in skills:
            key = s.active_skill_name or s.active_skill_id
            if not key or key in seen:
                continue
            seen.add(key)

            cypher = """
            MERGE (sk:Skill {skill_id: $skill_id})
            ON CREATE SET sk.name = $name,
                          sk.type = $type,
                          sk.tags = $tags,
                          sk.created_at = datetime()
            ON MATCH SET sk.updated_at = datetime()
            """
            await self._neo4j.execute_write(
                cypher,
                skill_id=s.active_skill_id,
                name=key,
                type="Unknown",
                tags=[],
            )
            count += 1

        # 补充种子数据中的技能
        for skill_name, keystone_data in SKILL_KEYSTONE_MAP.items():
            if skill_name not in seen:
                cypher = """
                MERGE (sk:Skill {skill_id: $skill_id})
                ON CREATE SET sk.name = $name, sk.type = $type, sk.tags = [], sk.created_at = datetime()
                """
                await self._neo4j.execute_write(
                    cypher, skill_id=skill_name, name=skill_name, type="Unknown"
                )
                count += 1

        return count

    async def _sync_keystones(self) -> int:
        """同步基石天赋节点 + BENEFITS_FROM 关系。"""
        count = 0
        for skill_name, keystones in SKILL_KEYSTONE_MAP.items():
            for k in keystones:
                cypher = """
                MERGE (keystone:Keystone {name: $name})
                ON CREATE SET keystone.effect = $effect
                MERGE (skill:Skill {skill_id: $skill_name})
                MERGE (skill)-[r:BENEFITS_FROM]->(keystone)
                SET r.synergy_strength = $strength
                """
                await self._neo4j.execute_write(
                    cypher,
                    name=k["keystone"],
                    effect=k["effect"],
                    skill_name=skill_name,
                    strength=k["strength"],
                )
                count += 1
        return count

    async def _sync_ascendancies(self) -> int:
        """同步升华节点 + BOOSTS 关系。"""
        count = 0
        for ascendancy_name, skills in ASCENDANCY_SKILL_MAP.items():
            # 创建升华节点
            base_class = self._ascendancy_to_class(ascendancy_name)
            cypher = """
            MERGE (a:Ascendancy {name: $name})
            ON CREATE SET a.class = $class_name
            """
            await self._neo4j.execute_write(cypher, name=ascendancy_name, class_name=base_class)

            for s in skills:
                cypher = """
                MATCH (a:Ascendancy {name: $asc_name})
                MATCH (sk:Skill {skill_id: $skill_name})
                MERGE (a)-[r:BOOSTS]->(sk)
                SET r.boost_description = $reason,
                    r.boost_power = $power
                """
                await self._neo4j.execute_write(
                    cypher,
                    asc_name=ascendancy_name,
                    skill_name=s["skill"],
                    reason=s["reason"],
                    power=s["power"],
                )
                count += 1
        return count

    async def _sync_mechanics(self, db: AsyncSession) -> int:
        """同步游戏机制节点（来自 game_mechanic 表）。"""
        result = await db.execute(select(GameMechanic))
        mechanics = result.scalars().all()

        count = 0
        for m in mechanics:
            cypher = """
            MERGE (mech:Mechanic {name: $name})
            ON CREATE SET mech.description = $desc,
                          mech.skill_type = $skill_type,
                          mech.tags = $tags
            MERGE (sk:Skill {skill_id: $skill_id})
            MERGE (sk)-[:HAS_MECHANIC]->(mech)
            """
            await self._neo4j.execute_write(
                cypher,
                name=m.skill_name,
                desc=m.description or "",
                skill_type=m.skill_type,
                tags=m.tags or [],
                skill_id=m.skill_id,
            )
            count += 1
        return count

    async def _sync_damage_types(self) -> int:
        """同步伤害类型节点 + 技能 DEALS 关系。"""
        types = ["Fire", "Cold", "Lightning", "Physical", "Chaos", "Elemental"]
        count = 0
        for dt in types:
            cypher = "MERGE (d:DamageType {name: $name})"
            await self._neo4j.execute_write(cypher, name=dt)

        # 关联技能与伤害类型（从种子数据推断）
        skill_damage_map = {
            "Spark": ["Lightning", "Spell", "Projectile"],
            "Arc": ["Lightning", "Spell", "Chain"],
            "Ice Strike": ["Cold", "Attack", "Melee", "Strike"],
            "Lightning Arrow": ["Lightning", "Attack", "Projectile", "Bow"],
            "Summon Raging Spirit": ["Fire", "Minion", "Spell"],
            "Hammer of the Gods": ["Fire", "Attack", "Melee", "Slam", "AoE"],
            "Galvanic Shards": ["Lightning", "Attack", "Projectile", "Crossbow"],
            "Hex Blast": ["Chaos", "Spell", "AoE"],
            "Comet": ["Cold", "Spell", "AoE"],
            "Gas Arrow": ["Chaos", "Attack", "Projectile", "AoE"],
            "Detonate Dead": ["Fire", "Spell", "AoE"],
            "Ember Fusillade": ["Fire", "Spell", "Projectile"],
            "Falling Thunder": ["Lightning", "Attack", "Melee"],
            "Shattering Palm": ["Cold", "Attack", "Melee", "Strike"],
            "Tempest Bell": ["Lightning", "Attack", "Melee", "AoE"],
            "Snipe": ["Physical", "Attack", "Projectile", "Bow"],
        }
        for skill, dmg_types in skill_damage_map.items():
            for dt in dmg_types:
                cypher = """
                MATCH (sk:Skill {skill_id: $skill})
                MATCH (d:DamageType {name: $dtype})
                MERGE (sk)-[:DEALS]->(d)
                """
                await self._neo4j.execute_write(cypher, skill=skill, dtype=dt)
                count += 1
        return count

    async def _sync_playstyles(self) -> int:
        """同步玩法风格节点。"""
        styles = [
            "spell_caster", "bow_ranged", "melee_strike", "melee_slam",
            "minion_summoner", "crossbow_ranged", "trap_mine",
        ]
        for style in styles:
            cypher = "MERGE (p:Playstyle {name: $name})"
            await self._neo4j.execute_write(cypher, name=style)
        return len(styles)

    async def _sync_conflicts(self) -> int:
        """同步机制冲突关系。"""
        count = 0
        for conflict in MECHANIC_CONFLICTS:
            cypher = """
            MERGE (m1:Mechanic {name: $a})
            MERGE (m2:Mechanic {name: $b})
            MERGE (m1)-[r:CONFLICTS_WITH]->(m2)
            SET r.reason = $reason
            """
            await self._neo4j.execute_write(
                cypher,
                a=conflict["a"],
                b=conflict["b"],
                reason=conflict["reason"],
            )
            count += 1
        return count

    async def _sync_conversions(self) -> int:
        """同步伤害转化链。"""
        count = 0
        for chain in CONVERSION_CHAINS:
            cypher = """
            MATCH (d1:DamageType {name: $src})
            MATCH (d2:DamageType {name: $dst})
            MERGE (d1)-[:CONVERTS_TO]->(d2)
            """
            await self._neo4j.execute_write(cypher, src=chain[0], dst=chain[1])
            count += 1
        return count

    async def _sync_affixes(self) -> int:
        """同步技能 → 词缀 SCALES_WITH 关系。"""
        count = 0
        for skill_name, affixes in SKILL_AFFIX_MAP.items():
            for affix in affixes:
                cypher = """
                MATCH (sk:Skill {skill_id: $skill})
                MERGE (m:Modifier {name: $mod})
                ON CREATE SET m.slot = $slot, m.mod_type = $mod_type
                MERGE (sk)-[r:SCALES_WITH]->(m)
                SET r.priority = $priority
                """
                await self._neo4j.execute_write(
                    cypher,
                    skill=skill_name,
                    mod=affix["mod"],
                    slot=affix["slot"],
                    mod_type=affix["type"],
                    priority=affix["priority"],
                )
                count += 1
        return count

    async def _sync_co_occurrence(self, db: AsyncSession) -> int:
        """同步技能共现关系（基于已导入的 BD）。"""
        result = await db.execute(
            select(Character)
        )
        characters = result.scalars().all()

        # 按角色 ID 分组技能
        char_skills: dict[str, set[str]] = {}
        for ch in characters:
            skills_result = await db.execute(
                select(SkillGroup).where(SkillGroup.character_id == ch.id)
            )
            skills = skills_result.scalars().all()
            names = {s.active_skill_name for s in skills if s.active_skill_name}
            if names:
                char_skills[str(ch.id)] = names

        # 创建 PAIRED_WITH 关系
        count = 0
        pairs: dict[tuple[str, str], int] = {}
        for skill_set in char_skills.values():
            skill_list = list(skill_set)
            for i in range(len(skill_list)):
                for j in range(i + 1, len(skill_list)):
                    a, b = sorted([skill_list[i], skill_list[j]])
                    key = (a, b)
                    pairs[key] = pairs.get(key, 0) + 1

        for (a, b), cnt in pairs.items():
            cypher = """
            MATCH (s1:Skill {name: $a})
            MATCH (s2:Skill {name: $b})
            MERGE (s1)-[r:PAIRED_WITH]->(s2)
            SET r.co_occurrence = $count
            """
            await self._neo4j.execute_write(cypher, a=a, b=b, count=cnt)
            count += 1

        return count

    async def _sync_classes(self) -> int:
        """同步职业节点。"""
        classes = [
            {"name": "Sorceress", "ascendancies": ["Stormweaver", "Chronomancer"]},
            {"name": "Ranger", "ascendancies": ["Deadeye", "Pathfinder"]},
            {"name": "Monk", "ascendancies": ["Invoker", "Acolyte of Chayula"]},
            {"name": "Warrior", "ascendancies": ["Titan", "Warbringer"]},
            {"name": "Witch", "ascendancies": ["Infernalist", "Blood Mage"]},
            {"name": "Mercenary", "ascendancies": ["Tactician", "Gemling Legionnaire"]},
        ]
        count = 0
        for c in classes:
            cypher = """
            MERGE (cc:CharClass {name: $name})
            ON CREATE SET cc.ascendancies = $ascendancies
            """
            await self._neo4j.execute_write(cypher, name=c["name"], ascendancies=c["ascendancies"])

            for asc in c["ascendancies"]:
                cypher = """
                MATCH (cc:CharClass {name: $class_name})
                MATCH (a:Ascendancy {name: $asc})
                MERGE (cc)-[:HAS_ASCENDANCY]->(a)
                """
                await self._neo4j.execute_write(cypher, class_name=c["name"], asc=asc)
                count += 1
        return count

    # ── 增量更新 ─────────────────────────────────────────

    async def sync_character(self, db: AsyncSession, character_id: str) -> None:
        """同步单个角色的技能关系到图谱。"""
        result = await db.execute(
            select(SkillGroup).where(SkillGroup.character_id == character_id)
        )
        skills = result.scalars().all()

        for s in skills:
            if s.active_skill_name:
                cypher = """
                MERGE (sk:Skill {skill_id: $skill_id})
                ON CREATE SET sk.name = $name
                """
                await self._neo4j.execute_write(
                    cypher, skill_id=s.active_skill_id, name=s.active_skill_name
                )

    # ── Helpers ──────────────────────────────────────────

    @staticmethod
    def _ascendancy_to_class(ascendancy: str) -> str:
        mapping = {
            "Stormweaver": "Sorceress",
            "Chronomancer": "Sorceress",
            "Deadeye": "Ranger",
            "Pathfinder": "Ranger",
            "Invoker": "Monk",
            "Acolyte of Chayula": "Monk",
            "Titan": "Warrior",
            "Warbringer": "Warrior",
            "Infernalist": "Witch",
            "Blood Mage": "Witch",
            "Tactician": "Mercenary",
            "Gemling Legionnaire": "Mercenary",
        }
        return mapping.get(ascendancy, "Unknown")


knowledge_graph_sync = KnowledgeGraphSync()

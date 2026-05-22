"""M4 Agent 工具集。

需求文档 §4.1 定义的 7 个 Tool Use 工具：
  1. query_builds_db    — 查询历史 BD 数据库
  2. get_skill_mechanics — 查询技能机制
  3. get_passive_graph   — 天赋树相邻节点
  4. calculate_damage    — 伤害估算
  5. validate_build      — BD 可行性验证
  6. search_synergies    — 协同效应搜索
  7. poe2db_lookup       — POE2DB 通用查询（装备/传奇/天赋/怪物等）

每个工具返回结构化 JSON，供 Claude 在推理链中调用。
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import BuildMeta, Character, GameMechanic, SkillGroup

# ── Tool 1: query_builds_db ────────────────────────────


async def query_builds_db(
    db: AsyncSession,
    playstyle: str | None = None,
    damage_type: str | None = None,
    class_name: str | None = None,
    core_skill: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """查询数据库中匹配条件的 BD 参考。

    支持按 playstyle、damage_type、class、skill 过滤。
    返回匹配 BD 的摘要列表（含技能、天赋、标签）。
    """
    query = (
        select(Character, BuildMeta)
        .join(BuildMeta, Character.id == BuildMeta.character_id)
        .where(BuildMeta.is_active == True)  # noqa: E712
    )

    if playstyle:
        query = query.where(BuildMeta.playstyle == playstyle)
    if damage_type and hasattr(BuildMeta, "damage_types"):
        query = query.where(BuildMeta.damage_types.any(damage_type))
    if class_name:
        query = query.where(Character.char_class == class_name)

    query = query.order_by(Character.level.desc()).limit(limit)
    result = await db.execute(query)
    rows = result.all()

    builds: list[dict[str, Any]] = []
    for ch, meta in rows:
        skills_result = await db.execute(
            select(SkillGroup).where(SkillGroup.character_id == ch.id).limit(20)
        )
        skills = skills_result.scalars().all()

        builds.append({
            "id": str(ch.id),
            "name": ch.character_name,
            "class": ch.char_class,
            "ascendancy": ch.ascendancy,
            "level": ch.level,
            "playstyle": meta.playstyle,
            "damage_types": meta.damage_types,
            "tags": meta.tags,
            "skills": [s.active_skill_name for s in skills if s.active_skill_name],
            "power_rating": meta.power_rating,
            "estimated_budget": meta.estimated_budget_divines,
        })

    return builds


# ── Tool 2: get_skill_mechanics ────────────────────────


async def get_skill_mechanics(
    db: AsyncSession, skill_name: str
) -> dict[str, Any] | None:
    """查询技能机制详情（伤害公式、标签、协同效应）。"""
    # 先精确匹配，再模糊匹配
    result = await db.execute(
        select(GameMechanic).where(GameMechanic.skill_name == skill_name)
    )
    mechanic = result.scalar_one_or_none()
    if not mechanic:
        result = await db.execute(
            select(GameMechanic).where(
                GameMechanic.skill_name.ilike(f"%{skill_name}%")
            ).limit(1)
        )
        mechanic = result.scalar_one_or_none()
    if not mechanic:
        return {
            "skill_name": skill_name,
            "found": False,
            "hint": "技能机制数据暂未收录，可从 PoEDB 获取或使用通用知识推理",
        }

    return {
        "skill_name": mechanic.skill_name,
        "skill_id": mechanic.skill_id,
        "type": mechanic.skill_type,
        "damage_formula": mechanic.damage_formula,
        "base_crit_chance": mechanic.base_crit_chance,
        "damage_effectiveness": mechanic.damage_effectiveness,
        "tags": mechanic.tags,
        "synergies": mechanic.synergies,
        "weapon_requirements": mechanic.weapon_requirements,
        "attribute_requirements": mechanic.attribute_requirements,
        "required_level": mechanic.required_level,
        "description": mechanic.description,
    }


# ── Tool 3: get_passive_graph ──────────────────────────


async def get_passive_graph(
    db: AsyncSession, character_id: str
) -> dict[str, Any]:
    """获取某 BD 的天赋树节点列表和关键节点。"""
    result = await db.execute(
        text(
            "SELECT node_ids, keystone_nodes, ascendancy_nodes, mastery_choices "
            "FROM passive_tree WHERE character_id = :cid"
        ),
        {"cid": character_id},
    )
    row = result.fetchone()
    if not row:
        return {"found": False, "character_id": character_id}

    return {
        "found": True,
        "total_nodes": len(row[0]) if row[0] else 0,
        "keystone_nodes": row[1],
        "ascendancy_nodes": row[2],
        "mastery_choices": row[3],
        "node_sample": (row[0] or [])[:30],
    }


# ── Tool 4: calculate_damage ───────────────────────────


async def calculate_damage(
    base_damage: float = 100.0,
    increased_damage: float = 0.0,
    more_multipliers: list[float] | None = None,
    crit_chance: float = 0.05,
    crit_multiplier: float = 1.5,
    cast_rate: float = 2.0,
    resistance_penetration: float = 0.0,
    enemy_resistance: float = 0.0,
) -> dict[str, Any]:
    """简化的 POE2 伤害估算器。

    公式：DPS = Base × (1 + Σinc) × Π(1+more) × (1 + crit_chance × (crit_multi-1)) × cast_rate × res_mult
    其中 res_mult = (1 - enemy_res + penetration)
    """
    inc_mult = 1.0 + increased_damage / 100.0

    more_mult = 1.0
    if more_multipliers:
        for m in more_multipliers:
            more_mult *= 1.0 + m / 100.0

    effective_res = max(-2.0, enemy_resistance - resistance_penetration)
    res_mult = 1.0 - effective_res

    hit_damage = base_damage * inc_mult * more_mult
    avg_hit = hit_damage * (1.0 + crit_chance * (crit_multiplier - 1.0))
    dps = avg_hit * cast_rate * res_mult

    return {
        "base_damage": base_damage,
        "increased_multiplier": round(inc_mult, 3),
        "more_multiplier": round(more_mult, 3),
        "resistance_multiplier": round(res_mult, 3),
        "average_hit": round(avg_hit, 1),
        "estimated_dps": round(dps, 1),
        "assumptions": {
            "crit_chance": crit_chance,
            "crit_multiplier": crit_multiplier,
            "cast_rate": cast_rate,
            "enemy_resistance": enemy_resistance,
            "penetration": resistance_penetration,
        },
    }


# ── Tool 5: validate_build ─────────────────────────────


async def validate_build(build: dict[str, Any]) -> dict[str, Any]:
    """验证 BD 草案的合法性。

    委托给 app.validation.rules.BuildValidator 执行 7 类校验：
    技能、天赋、装备、机制、属性、灵韵、伤害一致性。
    """
    from app.validation.rules import build_validator

    return build_validator.validate(build)


# ── Tool 6: search_synergies ───────────────────────────


async def search_synergies(
    db: AsyncSession, keyword: str, limit: int = 10
) -> list[dict[str, Any]]:
    """搜索与给定关键词有协同效应的技能/机制。

    通过查询已导入 BD 中共现的技能组合来发现协同关系。
    """
    result = await db.execute(
        text(
            "SELECT c.character_name, c.char_class, sg.active_skill_name, "
            "bm.tags, bm.playstyle "
            "FROM character c "
            "JOIN build_meta bm ON bm.character_id = c.id "
            "JOIN skill_group sg ON sg.character_id = c.id "
            "WHERE sg.active_skill_name ILIKE :kw "
            "OR EXISTS (SELECT 1 FROM skill_group sg2 "
            "           WHERE sg2.character_id = c.id AND sg2.active_skill_name ILIKE :kw) "
            "LIMIT :lim"
        ),
        {"kw": f"%{keyword}%", "lim": limit},
    )
    rows = result.fetchall()

    synergies: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        key = f"{row[2]}"
        if key not in seen:
            seen.add(key)
            synergies.append({
                "build_name": row[0],
                "char_class": row[1],
                "skill_name": row[2],
                "tags": row[3],
                "playstyle": row[4],
            })

    return synergies


# ── Tool 7: poe2db_lookup ─────────────────────────────


async def poe2db_lookup(
    term: str,
    lang: str = "cn",
    format: str = "json",  # noqa: A002
) -> dict[str, Any]:
    """查询 POE2DB 任意页面，获取装备/传奇/天赋/怪物/机制等数据。

    这是 Agent 获取 POE2 权威游戏数据的通用入口。当技能机制表 (GameMechanic)
    未覆盖时，通过此工具实时查询 POE2DB。

    Args:
        term: 查询词（英文名），如 "Headhunter", "Passive Skill Tree", "Mind Over Matter"
        lang: 语言（cn=中文, us=英文）
        format: 输出格式（json/markdown）
    """
    from app.collectors.poe2db_lookup import lookup

    return await lookup(term, lang, format)

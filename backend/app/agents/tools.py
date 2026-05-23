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
#
# 基于 PoB2 CalcOffence.lua 的真实 POE2 伤害公式实现。
# 公式来源: backend/data/mechanics_offence.txt


def _product(values: list[float] | None) -> float:
    """计算 Π(1 + v/100) 乘积。"""
    if not values:
        return 1.0
    result = 1.0
    for v in values:
        result *= 1.0 + v / 100.0
    return result


async def calculate_damage(
    # ── 基础伤害 ──
    base_damage_min: float = 100.0,
    base_damage_max: float = 150.0,
    added_damage: float = 0.0,
    added_damage_multiplier: float = 1.0,
    base_multiplier: float = 1.0,
    # ── INC / MORE ──
    increased_damage: float = 0.0,
    more_multipliers: list[float] | None = None,
    more_min_damage: float = 1.0,
    more_max_damage: float = 1.0,
    add_min_damage: float = 0.0,
    add_max_damage: float = 0.0,
    # ── 暴击 ──
    base_crit_chance: float = 0.05,
    increased_crit_chance: float = 0.0,
    more_crit_chance: list[float] | None = None,
    crit_chance_cap: float = 1.0,
    base_crit_multiplier: float = 1.5,
    increased_crit_multiplier: float = 0.0,
    more_crit_multiplier: list[float] | None = None,
    # ── 全局缩放 ──
    double_damage_chance: float = 0.0,
    triple_damage_chance: float = 0.0,
    lucky_hit: bool = False,
    # ── 抗性/穿透 ──
    enemy_resistance: float = 0.0,
    resistance_penetration: float = 0.0,
    # ── 施法速度 ──
    cast_rate: float = 2.0,
    # ── DoT ──
    dot_dps: float = 0.0,
    # ── Impale ──
    impale_stacks: int = 0,
    impale_effect_inc: float = 0.0,
    impale_effect_more: list[float] | None = None,
    impale_chance: float = 0.0,
    enemy_phys_reduction: float = 0.0,
    enemy_impale_phys_reduction: float = 0.0,
    impale_taken_mult: float = 1.0,
    phys_reduction_cap: float = 90.0,
    # ── 斩杀 & 承伤 ──
    cull_multiplier: float = 1.0,
    enemy_taken_inc: float = 0.0,
    enemy_taken_more: list[float] | None = None,
) -> dict[str, Any]:
    """POE2 完整伤害估算器 — 基于 PoB2 CalcOffence.lua。

    公式链:
      基础伤害组装 → 伤害转换 → INC/MORE → 全局缩放(双倍/三倍/Lucky)
      → 暴击期望 → 抗性/穿透 → Impale → DoT → 斩杀

    Returns:
        详细伤害分解，包含 avg_hit, crit_avg, total_dps, 各环节倍率
    """
    # ━━━ 第1步: 基础伤害组装 ━━━
    # baseMin = (sourceMin + bonusMin + addedMin * addedMult) * baseMultiplier
    assembled_min = (base_damage_min + added_damage * added_damage_multiplier) * base_multiplier
    assembled_max = (base_damage_max + added_damage * added_damage_multiplier) * base_multiplier

    # ━━━ 第2步: INC/MORE 修饰 ━━━
    inc_mult = 1.0 + increased_damage / 100.0
    more_mult = _product(more_multipliers)

    # hitMin = round(summedMin * inc * more * moreMinDamage + addMin)
    hit_min = assembled_min * inc_mult * more_mult * more_min_damage + add_min_damage
    hit_max = assembled_max * inc_mult * more_mult * more_max_damage + add_max_damage
    hit_avg = (hit_min + hit_max) / 2.0

    # ━━━ 第3步: 暴击计算 ━━━
    # CritChance = baseCrit * (1 + inc_crit/100) * product(1 + more_crit_i/100)
    crit_chance = base_crit_chance * (1.0 + increased_crit_chance / 100.0) * _product(more_crit_chance)
    crit_chance = min(crit_chance, crit_chance_cap)

    # CritMultiplier = 1 + max(0, (baseCritMulti - 1) * (1 + inc/100) * product(more_i))
    crit_extra_base = base_crit_multiplier - 1.0
    crit_extra = crit_extra_base * (1.0 + increased_crit_multiplier / 100.0) * _product(more_crit_multiplier)
    crit_multiplier = 1.0 + max(0.0, crit_extra)

    # ━━━ 第4步: Lucky Hit ━━━
    if lucky_hit:
        non_crit_avg = hit_min / 3.0 + 2.0 * hit_max / 3.0
    else:
        non_crit_avg = hit_avg

    crit_avg_damage = non_crit_avg * crit_multiplier

    # ━━━ 第5步: 全局缩放 (双倍/三倍伤害) ━━━
    # allMult = 1 + DoubleDamageChance/100 + 2*TripleDamageChance/100
    extra_hit_mult = 1.0 + double_damage_chance / 100.0 + 2.0 * triple_damage_chance / 100.0

    # 暴击也受双倍/三倍加成
    crit_avg_damage *= extra_hit_mult
    non_crit_avg *= extra_hit_mult

    # ━━━ 第6步: StoredCombinedAvg = CritAvg*critChance + HitAvg*(1-critChance) ━━━
    combined_hit_avg = crit_avg_damage * crit_chance + non_crit_avg * (1.0 - crit_chance)

    # ━━━ 第7步: 抗性/穿透 ━━━
    # effMult = (1 + takenInc/100) * takenMore
    taken_mult = (1.0 + enemy_taken_inc / 100.0) * _product(enemy_taken_more)

    # 元素: effMult *= (1 - max(resist - pen, 0) / 100) — 穿透不将正抗性降到0以下
    # 负抗性直接增伤: effMult *= (1 - resist/100) where resist < 0
    if enemy_resistance > 0:
        effective_res = max(enemy_resistance - resistance_penetration, 0.0)
    else:
        effective_res = enemy_resistance
    res_mult = 1.0 - effective_res

    # ━━━ 第8步: Impale ━━━
    impale_modifier = 1.0
    impale_dps = 0.0
    if impale_stacks > 0:
        impale_effect_more_val = _product(impale_effect_more)
        stored_dmg = 0.1 * (1.0 + impale_effect_inc / 100.0) * impale_effect_more_val
        hit_dmg_mod = stored_dmg * impale_stacks
        total_phys_reduction = min(
            enemy_phys_reduction + enemy_impale_phys_reduction,
            phys_reduction_cap,
        )
        impale_resist_mult = 1.0 - total_phys_reduction / 100.0
        dmg_modifier = hit_dmg_mod * impale_resist_mult * impale_chance / 100.0 * impale_taken_mult
        impale_modifier = 1.0 + dmg_modifier
        impale_dps = combined_hit_avg * cast_rate * (impale_modifier - 1.0)

    # ━━━ 第9步: 最终合并 ━━━
    # CombinedDPS = (TotalDPS + TotalDotDPS + ImpaleDPS) * CullMultiplier
    hit_dps = combined_hit_avg * cast_rate * taken_mult * res_mult * impale_modifier
    total_dps = (hit_dps + dot_dps + impale_dps * cast_rate) * cull_multiplier

    return {
        # 基础伤害
        "base_damage_range": [round(assembled_min, 1), round(assembled_max, 1)],
        "hit_damage_range": [round(hit_min, 1), round(hit_max, 1)],
        # 各级倍率
        "increased_multiplier": round(inc_mult, 3),
        "more_multiplier": round(more_mult, 3),
        "taken_multiplier": round(taken_mult, 3),
        "resistance_multiplier": round(res_mult, 3),
        "extra_hit_multiplier": round(extra_hit_mult, 3),
        "cull_multiplier": round(cull_multiplier, 3),
        # 暴击
        "effective_crit_chance": round(crit_chance * 100, 2),
        "effective_crit_multiplier": round(crit_multiplier, 3),
        "crit_average_hit": round(crit_avg_damage, 1),
        "non_crit_average_hit": round(non_crit_avg, 1),
        "combined_average_hit": round(combined_hit_avg, 1),
        # Impale
        "impale_modifier": round(impale_modifier, 3),
        "impale_dps": round(impale_dps, 1),
        # 最终输出
        "hit_dps": round(hit_dps, 1),
        "dot_dps": round(dot_dps, 1),
        "total_dps": round(total_dps, 1),
        # 向后兼容别名 (agent 路由 + format_output 使用)
        "estimated_dps": round(total_dps, 1),
        "average_hit": round(combined_hit_avg, 1),
        # 假设条件
        "assumptions": {
            "lucky_hit": lucky_hit,
            "cast_rate": cast_rate,
            "enemy_resistance": enemy_resistance,
            "penetration": resistance_penetration,
            "effective_resistance": round(effective_res * 100, 1),
            "impale_stacks": impale_stacks,
            "impale_chance": impale_chance,
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


# ── Tool 8: find_compatible_supports ────────────────────


async def find_compatible_supports(
    db: AsyncSession, skill_name: str, limit: int = 30
) -> dict[str, Any]:
    """根据主动技能标签，查找所有兼容的辅助宝石。

    规则：辅助宝石的所有非 support 标签必须 ⊆ 主动技能标签。
    """
    # 获取主动技能标签
    result = await db.execute(
        text(
            "SELECT skill_name, tags FROM game_mechanic "
            "WHERE skill_name = :name AND skill_type = 'active' AND is_active = true LIMIT 1"
        ),
        {"name": skill_name},
    )
    row = result.fetchone()
    if not row:
        # 模糊匹配
        result = await db.execute(
            text(
                "SELECT skill_name, tags FROM game_mechanic "
                "WHERE skill_name ILIKE :name AND skill_type = 'active' AND is_active = true LIMIT 1"
            ),
            {"name": f"%{skill_name}%"},
        )
        row = result.fetchone()
    if not row:
        return {"skill_name": skill_name, "found": False, "compatible_supports": []}

    skill_name_actual = row[0]
    active_tags = row[1] or []

    # 标签匹配查询
    result = await db.execute(
        text("""
            SELECT sg.skill_name, sg.tags,
                   array_remove(sg.tags, 'support') AS required_tags,
                   sg.description
            FROM game_mechanic sg
            WHERE sg.skill_type = 'support' AND sg.is_active = true
            AND array_remove(sg.tags, 'support') <@ :active_tags
            ORDER BY array_length(array_remove(sg.tags, 'support'), 1) DESC NULLS LAST,
                     sg.skill_name
            LIMIT :limit
        """),
        {"active_tags": active_tags, "limit": limit},
    )
    compatible = []
    for r in result:
        desc = (r[3] or "")[:200] if r[3] else None
        compatible.append({
            "name": r[0],
            "required_tags": r[2] or [],
            "description": desc,
        })

    total_result = await db.execute(
        text("""
            SELECT count(*) FROM game_mechanic sg
            WHERE sg.skill_type = 'support' AND sg.is_active = true
            AND array_remove(sg.tags, 'support') <@ :active_tags
        """),
        {"active_tags": active_tags},
    )
    total = total_result.fetchone()[0]

    return {
        "skill_name": skill_name_actual,
        "active_tags": active_tags,
        "total_compatible": total,
        "compatible_supports": compatible,
    }

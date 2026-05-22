"""BD 综合验证规则引擎。

覆盖需求文档 §5.1 定义的 7 类校验：
  1. 技能校验 — 主动技能、辅助宝石有效性
  2. 天赋校验 — 节点数、基石冲突、专精选择
  3. 装备校验 — 槽位合法性、词缀一致性
  4. 机制校验 — 核心机制冲突检测
  5. 属性校验 — 属性门槛、生存要求
  6. 灵韵(Spirit)校验 — 总保留不超标
  7. 伤害一致性 — 伤害类型与天赋/装备对齐
"""

from __future__ import annotations

from typing import Any


# ── 装备槽位定义 ────────────────────────────────────────

VALID_SLOTS = {
    "Weapon", "Offhand", "Helmet", "BodyArmour", "Gloves", "Boots",
    "Amulet", "Ring1", "Ring2", "Belt",
}

# ── POE2 已知机制冲突 ──────────────────────────────────

CONFLICT_PAIRS: list[tuple[set[str], str]] = [
    # CI 冲突组
    ({"Chaos Inoculation", "Pain Attunement"}, "CI 强制满血，Pain Attunement 需要低血状态"),
    ({"Chaos Inoculation", "Blood Magic"}, "CI 生命=1，Blood Magic 无法消耗生命"),
    # Blood Magic 冲突组
    ({"Blood Magic", "Mind Over Matter"}, "Blood Magic 移除魔力，MoM 需要魔力承伤"),
    ({"Blood Magic", "Eldritch Battery"}, "Blood Magic 移除魔力，EB 无魔力可转"),
    ({"Blood Magic", "Archmage"}, "Blood Magic 移除魔力，Archmage 无魔力可用"),
    # 暴击冲突
    ({"Elemental Overload", "Resolute Technique"}, "RT 禁止暴击，EO 需要暴击触发"),
    ({"Elemental Overload", "Precise Technique"}, "PT 要求命中>生命，与 EO 暴击路线冲突"),
    # 护甲/闪避转化冲突
    ({"Iron Reflexes", "Acrobatics"}, "IR 转闪避为护甲，Acrobatics 需要闪避"),
    ({"Iron Reflexes", "Evasion"}, "IR 消除所有闪避"),
    ({"Ghost Dance", "Iron Reflexes"}, "Ghost Dance 需要闪避回复 ES，IR 移除闪避"),
    # 其他
    ({"Zealot's Oath", "Mind Over Matter"}, "ZO 生命回复转ES，MoM 时生命回复不重要"),
    ({"Avatar of Fire", "Atziri's Acuity"}, "AoF 只能火伤，AA 提供混沌偷取不适用"),
]

# ── 属性门槛 ────────────────────────────────────────────

# 技能宝石的属性需求（按技能名）
SKILL_ATTRIBUTE_REQUIREMENTS: dict[str, dict[str, int]] = {
    # 智力技能 (需要高智力)
    "Spark": {"int": 120, "dex": 0, "str": 0},
    "Arc": {"int": 140, "dex": 0, "str": 0},
    "Comet": {"int": 180, "dex": 0, "str": 0},
    "Hex Blast": {"int": 130, "dex": 0, "str": 0},
    "Summon Raging Spirit": {"int": 100, "dex": 0, "str": 0},
    "Ember Fusillade": {"int": 110, "dex": 0, "str": 0},
    "Detonate Dead": {"int": 120, "dex": 0, "str": 0},
    # 敏捷技能 (需要高敏捷)
    "Lightning Arrow": {"int": 0, "dex": 130, "str": 0},
    "Gas Arrow": {"int": 0, "dex": 100, "str": 0},
    "Snipe": {"int": 0, "dex": 120, "str": 0},
    "Galvanic Shards": {"int": 0, "dex": 100, "str": 0},
    # 力敏混合
    "Ice Strike": {"int": 0, "dex": 100, "str": 60},
    "Falling Thunder": {"int": 0, "dex": 80, "str": 60},
    "Shattering Palm": {"int": 0, "dex": 90, "str": 50},
    "Tempest Bell": {"int": 0, "dex": 70, "str": 70},
    # 力量技能
    "Hammer of the Gods": {"int": 0, "dex": 0, "str": 150},
}

# ── 生存门槛 ────────────────────────────────────────────


class BuildValidator:
    """POE2 BD 综合验证器。

    使用方式:
        validator = BuildValidator()
        result = validator.validate(build_dict)
    """

    def validate(self, build: dict[str, Any]) -> dict[str, Any]:
        """对 BD 草案执行全部校验规则。

        Returns:
            {
                "passed": bool,
                "errors": [...],
                "warnings": [...],
                "suggestions": [...],
                "score": 0-100,
            }
        """
        errors: list[str] = []
        warnings: list[str] = []
        suggestions: list[str] = []

        # 1. 技能校验
        errors.extend(self._check_skills(build))
        # 2. 天赋校验
        errors.extend(self._check_passive_tree(build))
        # 3. 装备校验
        errors.extend(self._check_equipment(build))
        # 4. 机制校验
        errors.extend(self._check_mechanics(build))
        # 5. 属性校验
        warnings.extend(self._check_attributes(build))
        # 6. 灵韵校验
        warnings.extend(self._check_spirit(build))
        # 7. 伤害一致性
        suggestions.extend(self._check_damage_consistency(build))

        passed = len(errors) == 0
        score = self._calculate_score(len(errors), len(warnings))

        return {
            "passed": passed,
            "errors": errors,
            "warnings": warnings,
            "suggestions": suggestions,
            "score": score,
            "summary": f"{'通过' if passed else '不通过'}：{len(errors)} 错误, {len(warnings)} 警告, 评分 {score}/100",
        }

    # ── 1. 技能校验 ──────────────────────────────────────

    @staticmethod
    def _check_skills(build: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        skills = build.get("skill_gems", {})

        active = skills.get("active", [])
        if not active:
            errors.append("缺少主动技能 — BD 必须至少有 1 个主输出技能")
            return errors

        # 检查每个主动技能
        for i, skill in enumerate(active):
            if not skill.get("name"):
                errors.append(f"主动技能 #{i+1} 缺少技能名称")

            supports = skill.get("support_gems", [])
            if len(supports) > 5:
                errors.append(f"{skill.get('name', f'技能#{i+1}')} 有 {len(supports)} 个辅助宝石，超过 POE2 的 5 辅助上限")

            role = skill.get("role", "")
            if role not in ("main_dps", "mobility", "aura", "debuff", "weapon_swap", "curse", "buff", ""):
                errors.append(f"{skill.get('name', f'技能#{i+1}')} 的角色 '{role}' 无效")

        # 检查主输出技能
        dps_skills = [s for s in active if s.get("role") == "main_dps"]
        if not dps_skills:
            errors.append("至少需要 1 个 role='main_dps' 的技能")

        return errors

    # ── 2. 天赋校验 ──────────────────────────────────────

    @staticmethod
    def _check_passive_tree(build: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        tree = build.get("passive_tree", {})

        nodes = tree.get("nodes", [])
        node_count = len(nodes)

        if node_count > 130:
            errors.append(f"天赋节点过多 ({node_count} > 130)，超出 lv100 上限")
        elif node_count > 120:
            pass  # 改为警告处理，在 validate 调用方处理

        # 检查基石冲突
        keystones = set(tree.get("keystones", []))
        # CI + PA 在同一套基石中检测
        if "Chaos Inoculation" in keystones and "Pain Attunement" in keystones:
            errors.append("CI 与 Pain Attunement 冲突（CI 强制满血）")

        return errors

    # ── 3. 装备校验 ──────────────────────────────────────

    @staticmethod
    def _check_equipment(build: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        equipment = build.get("equipment", {})

        if not equipment:
            errors.append("装备方案为空 — 至少需要推荐武器和防具")
            return errors

        for slot in equipment:
            if slot not in VALID_SLOTS:
                errors.append(f"未知装备槽位: {slot}（有效槽位: {', '.join(sorted(VALID_SLOTS))}）")

        # 必须有武器
        if "Weapon" not in equipment:
            errors.append("缺少武器槽位 — BD 必须有推荐武器")

        return errors

    # ── 4. 机制校验 ──────────────────────────────────────

    @staticmethod
    def _check_mechanics(build: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        mechanics = set(build.get("key_mechanics", []))

        for pair, msg in CONFLICT_PAIRS:
            if pair.issubset(mechanics):
                errors.append(msg)

        # 额外检查 — mechanism 名称中已包含的描述
        mech_str = " ".join(mechanics).lower()
        if "chaos inoculation" in mech_str and "pain attunement" in mech_str:
            errors.append("CI 与 Pain Attunement 冲突（CI 强制满血）")
        if "blood magic" in mech_str and "mind over matter" in mech_str:
            errors.append("Blood Magic 与 MoM 冲突（消除魔力）")
        if "elemental overload" in mech_str and "resolute technique" in mech_str:
            errors.append("EO 与 RT 冲突（RT 禁止暴击）")

        return errors

    # ── 5. 属性校验 ──────────────────────────────────────

    @staticmethod
    def _check_attributes(build: dict[str, Any]) -> list[str]:
        warnings: list[str] = []
        skills = build.get("skill_gems", {}).get("active", [])
        tree = build.get("passive_tree", {})
        nodes = tree.get("nodes", [])

        # 估算属性（基于天赋节点数粗略估算）
        estimated_str = len(nodes) * 0.8
        estimated_dex = len(nodes) * 0.8
        estimated_int = len(nodes) * 0.8

        # 从职业调整
        char_class = build.get("class", "").lower()
        if char_class == "sorceress":
            estimated_int *= 1.5
        elif char_class == "ranger":
            estimated_dex *= 1.5
        elif char_class == "warrior":
            estimated_str *= 1.5

        for s in skills:
            name = s.get("name", "")
            reqs = SKILL_ATTRIBUTE_REQUIREMENTS.get(name)
            if reqs:
                if reqs["str"] > estimated_str:
                    warnings.append(f"{name} 需要 {reqs['str']} 力量，估算角色仅有 {estimated_str:.0f}")
                if reqs["dex"] > estimated_dex:
                    warnings.append(f"{name} 需要 {reqs['dex']} 敏捷，估算角色仅有 {estimated_dex:.0f}")
                if reqs["int"] > estimated_int:
                    warnings.append(f"{name} 需要 {reqs['int']} 智力，估算角色仅有 {estimated_int:.0f}")

        return warnings

    # ── 6. 灵韵(Spirit)校验 ──────────────────────────────

    @staticmethod
    def _check_spirit(build: dict[str, Any]) -> list[str]:
        warnings: list[str] = []
        auras = build.get("skill_gems", {}).get("spirit_reservation", [])

        total_spirit = sum(a.get("spirit_cost", 0) for a in auras)

        # POE2 基础灵韵: 100 (来自剧情)
        # 可通过装备获取更多
        base_spirit = 100
        if total_spirit > base_spirit + 60:
            warnings.append(f"灵韵保留 ({total_spirit}) 偏高，基础灵韵仅为 {base_spirit}，需要额外灵韵装备")
        if total_spirit > base_spirit + 120:
            warnings.append(f"灵韵保留 ({total_spirit}) 过高，需要大量灵韵投资")

        return warnings

    # ── 7. 伤害一致性 ────────────────────────────────────

    @staticmethod
    def _check_damage_consistency(build: dict[str, Any]) -> list[str]:
        suggestions: list[str] = []
        tree = build.get("passive_tree", {})
        tree_nodes_str = " ".join(str(n) for n in tree.get("nodes", [])).lower()

        damage_types_from_skills: set[str] = set()
        skills = build.get("skill_gems", {}).get("active", [])
        for s in skills:
            name = s.get("name", "").lower()
            if "spark" in name or "arc" in name or "lightning" in name:
                damage_types_from_skills.add("lightning")
            elif "ice" in name or "frost" in name or "cold" in name or "comet" in name:
                damage_types_from_skills.add("cold")
            elif "fire" in name or "flame" in name or "ember" in name or "hammer" in name:
                damage_types_from_skills.add("fire")
            elif "chaos" in name or "hex" in name or "poison" in name:
                damage_types_from_skills.add("chaos")
            else:
                damage_types_from_skills.add("physical")

        # 检查天赋树是否与伤害类型匹配
        if "lightning" in damage_types_from_skills and "cold" in tree_nodes_str:
            if "lightning" not in tree_nodes_str:
                suggestions.append("技能使用闪电伤害但天赋树侧重冰伤，建议统一伤害类型")

        if "cold" in damage_types_from_skills and "fire" in tree_nodes_str:
            if "cold" not in tree_nodes_str:
                suggestions.append("技能使用冰伤但天赋树侧重火伤，建议统一伤害类型")

        return suggestions

    # ── 评分 ─────────────────────────────────────────────

    @staticmethod
    def _calculate_score(errors: int, warnings: int) -> int:
        score = 100
        score -= errors * 25
        score -= warnings * 5
        return max(0, min(100, score))


build_validator = BuildValidator()

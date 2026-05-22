"""BD 输出格式化器。

将内部 BuildCard dict 转换为:
  1. Markdown 格式 — 人类可读的 BD 卡片
  2. PoB XML 格式 — 可导入 Path of Building 2
  3. API JSON 格式 — 前端消费的标准 JSON
"""

from __future__ import annotations

import json
from typing import Any


class BuildFormatter:
    """BD 输出格式化器。"""

    # ── Markdown ──────────────────────────────────────────

    @staticmethod
    def to_markdown(build: dict[str, Any]) -> str:
        """将 BuildCard 转为可读的 Markdown。"""
        lines: list[str] = []

        # 标题
        name = build.get("build_name", "Unnamed Build")
        cls = build.get("class", "Unknown")
        asc = build.get("ascendancy", "")
        budget = build.get("estimated_budget_divines", "N/A")
        confidence = build.get("confidence", 0)
        dps = build.get("estimated_dps", "N/A")

        lines.append(f"# {name}")
        if asc:
            lines.append(f"**{asc} {cls}** | Budget: {budget}d | Confidence: {confidence:.0%} | DPS: {dps}")
        else:
            lines.append(f"**{cls}** | Budget: {budget}d | Confidence: {confidence:.0%} | DPS: {dps}")
        lines.append("")

        # 核心概念
        concept = build.get("core_concept", "")
        if concept:
            lines.append("## Core Concept")
            lines.append(concept)
            lines.append("")

        # 升华
        asc_nodes = build.get("ascendancy_nodes", [])
        if asc_nodes:
            lines.append("## Ascendancy")
            for i, node in enumerate(asc_nodes, 1):
                lines.append(f"{i}. {node}")
            lines.append("")

        # 技能
        skills = build.get("skill_gems", {})
        active = skills.get("active", [])
        if active:
            lines.append("## Skill Gems")
            for skill in active:
                name = skill.get("name", "Unknown")
                role = skill.get("role", "")
                supports = skill.get("support_gems", [])
                role_tag = f" *({role})*" if role else ""
                lines.append(f"### {name}{role_tag}")
                if supports:
                    lines.append("Support gems: " + " → ".join(supports))
                lines.append("")
            lines.append("")

        # 灵韵
        auras = skills.get("spirit_reservation", [])
        if auras:
            lines.append("## Spirit Reservation")
            for aura in auras:
                a_name = aura.get("name", "Unknown")
                cost = aura.get("spirit_cost", 0)
                lines.append(f"- **{a_name}** — {cost} Spirit")
            lines.append("")

        # 天赋
        tree = build.get("passive_tree", {})
        keystones = tree.get("keystones", [])
        nodes = tree.get("nodes", [])
        if nodes:
            lines.append(f"## Passive Tree ({len(nodes)} nodes)")
            for n in nodes[:15]:
                lines.append(f"- {n}")
            if len(nodes) > 15:
                lines.append(f"- ... and {len(nodes) - 15} more nodes")
            lines.append("")

        if keystones:
            lines.append("### Keystones")
            for k in keystones:
                lines.append(f"- {k}")
            lines.append("")

        # 装备
        equipment = build.get("equipment", {})
        if equipment:
            lines.append("## Equipment")
            for slot, item in equipment.items():
                if item:
                    lines.append(f"- **{slot}**: {item}")
            lines.append("")

        # 关键机制
        mechanics = build.get("key_mechanics", [])
        if mechanics:
            lines.append("## Key Mechanics")
            for m in mechanics:
                lines.append(f"- {m}")
            lines.append("")

        # 玩法说明
        notes = build.get("playstyle_notes", "")
        if notes:
            lines.append("## Playstyle Notes")
            lines.append(notes)
            lines.append("")

        # 优缺点
        strengths = build.get("strengths", [])
        weaknesses = build.get("weaknesses", [])
        if strengths or weaknesses:
            lines.append("## Summary")
            if strengths:
                lines.append("### Strengths")
                for s in strengths:
                    lines.append(f"- {s}")
            if weaknesses:
                lines.append("### Weaknesses")
                for w in weaknesses:
                    lines.append(f"- {w}")
            lines.append("")

        # 伤害分解
        dmg_breakdown = build.get("damage_breakdown", {})
        if dmg_breakdown:
            lines.append("## Damage Breakdown")
            lines.append(f"- Average Hit: {dmg_breakdown.get('average_hit', 'N/A')}")
            lines.append(f"- Estimated DPS: {dmg_breakdown.get('estimated_dps', 'N/A')}")
            assumptions = dmg_breakdown.get("assumptions", {})
            if assumptions:
                lines.append("- Assumptions:")
                for k, v in assumptions.items():
                    lines.append(f"  - {k}: {v}")
            lines.append("")

        # 验证状态
        validation = build.get("validation", {})
        if validation:
            passed = "PASSED" if validation.get("passed") else "FAILED"
            lines.append(f"## Validation: {passed}")
            for err in validation.get("errors", []):
                lines.append(f"- [ERROR] {err}")
            for warn in validation.get("warnings", []):
                lines.append(f"- [WARN] {warn}")
            lines.append("")

        return "\n".join(lines)

    # ── PoB XML ───────────────────────────────────────────

    @staticmethod
    def to_pob_xml(build: dict[str, Any]) -> str:
        """将 BuildCard 转为简化的 PoB2 XML 格式。

        Note: 这不是完整的 PoB2 XML — 仅包含推理出的技能和天赋信息。
        完整导入仍需真实 PoB XML。
        """
        name = build.get("build_name", "Unnamed Build")
        cls = build.get("class", "Unknown")
        asc = build.get("ascendancy", "")

        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            "<PathOfBuilding2>",
            f'  <Build level="100" className="{cls}" ascendClassName="{asc}" title="{name}">',
        ]

        # 技能
        skills = build.get("skill_gems", {}).get("active", [])
        lines.append("    <Skills>")
        for skill in skills:
            name = skill.get("name", "Unknown")
            lines.append("      <Skill>")
            lines.append(f'        <Gem nameSpec="{name}" enabled="true"/>')
            for i, support in enumerate(skill.get("support_gems", [])[:5]):
                lines.append(f'        <Gem nameSpec="{support}" enabled="true" slot="{i+2}"/>')
            lines.append("      </Skill>")
        lines.append("    </Skills>")

        # 天赋（节点转 IDs）
        tree = build.get("passive_tree", {})
        nodes = tree.get("nodes", [])
        if nodes:
            # 简化为注释形式的节点列表
            lines.append(f"    <!-- Passive nodes: {', '.join(nodes[:20])} -->")

        lines.append("  </Build>")
        lines.append("</PathOfBuilding2>")

        return "\n".join(lines)

    # ── API JSON ──────────────────────────────────────────

    @staticmethod
    def to_api_response(build: dict[str, Any]) -> dict[str, Any]:
        """转为前端 API 消费的标准 JSON 结构。"""
        return {
            "build_card": {
                "name": build.get("build_name"),
                "core_concept": build.get("core_concept"),
                "class": build.get("class"),
                "ascendancy": build.get("ascendancy"),
                "ascendancy_nodes": build.get("ascendancy_nodes", []),
            },
            "skills": build.get("skill_gems", {}),
            "passive_tree": build.get("passive_tree", {}),
            "equipment": build.get("equipment", {}),
            "mechanics": {
                "key_interactions": build.get("key_mechanics", []),
                "playstyle": build.get("playstyle_notes", ""),
            },
            "meta": {
                "confidence": build.get("confidence", 0),
                "budget_divines": build.get("estimated_budget_divines"),
                "budget_tier": build.get("budget_tier"),
                "estimated_dps": build.get("estimated_dps"),
                "game_version": build.get("game_version"),
                "reference_count": build.get("reference_builds_count", 0),
            },
            "validation": build.get("validation", {}),
            "damage_breakdown": build.get("damage_breakdown", {}),
            "strengths": build.get("strengths", []),
            "weaknesses": build.get("weaknesses", []),
        }

    # ── Summary Card ──────────────────────────────────────

    @staticmethod
    def to_summary(build: dict[str, Any]) -> str:
        """生成一句话概括。"""
        name = build.get("build_name", "Build")
        cls = build.get("ascendancy", "") or build.get("class", "")
        budget = build.get("estimated_budget_divines", "?")
        dps = build.get("estimated_dps", "?")
        confidence = build.get("confidence", 0)
        return (
            f"**{name}** — {cls}, Budget: {budget}d, "
            f"DPS: {dps}, Confidence: {confidence:.0%}"
        )


build_formatter = BuildFormatter()

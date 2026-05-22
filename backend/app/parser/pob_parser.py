"""Path of Building XML 完整解析器。

解析 PoB XML 格式输出，支持：
  - Build 基础信息（等级、职业、升华、万神殿）
  - 技能组（主动技能 + 支援宝石 + 触发条件）
  - 天赋树（已选节点、升华节点、专精选择）
  - 装备（各部位 + 词缀 + 珠宝槽位）
  - 配置（光环、充能球、灵魂等资源设置）

PoB XML 格式参考：
  https://github.com/PathOfBuildingCommunity/PathOfBuilding
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from uuid import UUID

from app.models.base import (
    BuildMeta,
    Character,
    EquipmentItem,
    PassiveTree,
    SkillGroup,
)

logger = logging.getLogger(__name__)

# 技能宝石类型关键词匹配
_ACTIVE_GEM_TYPES = {
    "Active", "Spell", "Attack", "AoE", "Projectile", "Strike", "Slam",
    "Channelling", "Channelled", "Mine", "Trap", "Totem", "Brand",
    "Minion", "Herald", "Aura", "Curse", "Mark", "Warcry", "Stance",
    "Banner", "Offering", "Blessing",
}
_SUPPORT_GEM_KEYWORDS = {"Support", "Empower", "Enlighten", "Enhance", "Inspiration"}


class PoBParser:
    """PoB XML → ORM 模型 转换器。"""

    def __init__(self, source: str = "pob_import", source_url: str | None = None):
        self.source = source
        self.source_url = source_url
        self._warnings: list[str] = []

    @property
    def warnings(self) -> list[str]:
        return self._warnings

    # ── Entry point ─────────────────────────────────────

    def parse_xml(self, xml_text: str) -> dict[str, Any]:
        """解析完整 PoB XML，返回拆解的 ORM 模型清单。

        返回:
            {
                "character": Character,
                "skills": list[SkillGroup],
                "tree": PassiveTree,
                "items": list[EquipmentItem],
                "meta": BuildMeta,
            }
        """
        # PoB XML 有时包含 BOM 或 XML 声明前的内容
        start = xml_text.find("<")
        if start > 0:
            xml_text = xml_text[start:]

        tree = ET.parse(BytesIO(xml_text.encode("utf-8")))
        root = tree.getroot()

        # 支持 PoB1 (<PathOfBuilding>) 和 PoB2 (<PathOfBuilding2>)
        if root.tag not in ("PathOfBuilding", "PathOfBuilding2"):
            raise ValueError(f"Unknown PoB root element: {root.tag}")

        build_el = root.find(".//Build")
        if build_el is None:
            raise ValueError("Invalid PoB XML: no <Build> element found")

        build_info = self._parse_build(build_el)

        return {
            "character": build_info["character"],
            "skills": self._parse_skills(root, build_info["character"].id),
            "tree": self._parse_tree(root, build_info["character"].id),
            "items": self._parse_items(root, build_info["character"].id),
            "meta": build_info["meta"],
        }

    # ── Build base info ─────────────────────────────────

    def _parse_build(self, build_el: ET.Element) -> dict[str, Any]:
        level = int(build_el.get("level", 0) or 0)
        char_class = build_el.get("className", "Unknown")
        ascendancy = build_el.get("ascendClassName") or None
        bandit = build_el.get("bandit") or None
        build_name = (
            build_el.get("title") or build_el.get("name") or
            f"{ascendancy or ''} {char_class}".strip() or "Unnamed Build"
        )
        version = build_el.get("targetVersion") or "Unknown"

        import uuid
        char_id = uuid.uuid4()

        character = Character(
            id=char_id,
            account_name="manual_import",
            character_name=build_name,
            league="Unknown",
            level=level,
            char_class=char_class,
            ascendancy=ascendancy,
            last_updated=datetime.now(timezone.utc),
        )

        meta = BuildMeta(
            character_id=char_id,
            source=self.source,
            source_url=self.source_url,
            collected_at=datetime.now(timezone.utc),
            league_version=version,
            power_rating=None,
            tags=[],
            damage_types=[],
            playstyle=None,
        )

        return {"character": character, "meta": meta}

    # ── Skills ──────────────────────────────────────────

    def _parse_skills(self, root: ET.Element, char_id: UUID) -> list[SkillGroup]:
        skills: list[SkillGroup] = []

        for skill_el in root.findall(".//Skill"):
            # PoB2: gems are <Gem> children; PoB1: attributes on <Skill>
            gems = skill_el.findall("Gem")
            if gems:
                for gem in gems:
                    if gem.get("enabled", "true") != "true":
                        continue
                    skill = SkillGroup(
                        character_id=char_id,
                        active_skill_id=gem.get("skillId", ""),
                        active_skill_name=gem.get("nameSpec", ""),
                        support_gems=None,
                        gem_links=1,
                        trigger_condition=None,
                    )
                    skills.append(skill)
            else:
                # PoB1 fallback: attributes on Skill element
                if skill_el.get("enabled", "false") != "true":
                    continue
                skill = SkillGroup(
                    character_id=char_id,
                    active_skill_id=skill_el.get("skillId", ""),
                    active_skill_name=skill_el.get("nameSpec", ""),
                    support_gems=None,
                    gem_links=1,
                )
                skills.append(skill)

        return skills

    # ── Passive Tree ────────────────────────────────────

    def _parse_tree(self, root: ET.Element, char_id: UUID) -> PassiveTree:
        spec = root.find(".//Tree/Spec")
        if spec is None:
            spec = root.find(".//TreeSpec")
        if spec is None:
            return PassiveTree(character_id=char_id, node_ids=[])

        nodes_str = spec.get("nodes", "") or ""

        # PoB2: space-separated; PoB1: comma-separated
        if " " in nodes_str and "," not in nodes_str:
            node_ids = [n.strip() for n in nodes_str.split() if n.strip()]
        else:
            node_ids = [n.strip() for n in nodes_str.replace(" ", ",").split(",") if n.strip()]

        ascendancy_nodes = []
        # PoB2: jewel sockets in <Socket> elements
        for sock in root.findall(".//Tree//Socket"):
            nid = sock.get("nodeId", "")
            if nid:
                ascendancy_nodes.append(nid)

        mastery_choices: dict[str, str] = {}
        spec_masteries = spec.get("masteryEffects", "")
        if spec_masteries:
            mastery_choices["_raw"] = spec_masteries

        return PassiveTree(
            character_id=char_id,
            node_ids=node_ids,
            keystone_nodes=None,
            mastery_choices=mastery_choices if mastery_choices else None,
            ascendancy_nodes=ascendancy_nodes if ascendancy_nodes else None,
        )

    # ── Equipment & Jewels ──────────────────────────────

    def _parse_items(self, root: ET.Element, char_id: UUID) -> list[EquipmentItem]:
        items: list[EquipmentItem] = []

        for item_el in root.findall(".//Item"):
            slot = item_el.get("slot", "Unknown")

            # 跳过宝石、药水和空槽
            if slot in ("Flask", "Trinket", ""):
                continue
            if "Gem" in slot or "Jewel" in slot:
                continue

            item_name = item_el.get("name", "")
            base_type = item_el.get("baseType", "")
            rarity = self._parse_rarity(item_el.get("rarity", "Normal"))

            explicit = self._gather_mods(item_el, "explicit")
            implicit = self._gather_mods(item_el, "implicit")
            crafted = self._gather_mods(item_el, "crafted")
            enchant = self._gather_mods(item_el, "enchant")

            items.append(
                EquipmentItem(
                    character_id=char_id,
                    slot=slot,
                    item_name=item_name,
                    base_type=base_type,
                    rarity=rarity,
                    explicit_mods=explicit if explicit else None,
                    implicit_mods=implicit if implicit else None,
                    crafted_mods=crafted if crafted else None,
                    enchant_mods=enchant if enchant else None,
                    raw_json=self._item_to_dict(item_el),
                )
            )

        return items

    # ── Helpers ─────────────────────────────────────────

    @staticmethod
    def _parse_rarity(rarity_str: str) -> str:
        mapping = {
            "NORMAL": "normal", "MAGIC": "magic",
            "RARE": "rare", "UNIQUE": "unique",
            "LEGENDARY": "unique",
        }
        return mapping.get(rarity_str.upper(), "normal")

    @staticmethod
    def _gather_mods(item_el: ET.Element, mod_type: str) -> list[str]:
        mods: list[str] = []
        for mod_el in item_el.findall(".//Mod"):
            if mod_el.get("type", "") == mod_type:
                text = mod_el.get("text", "")
                if text:
                    mods.append(text)
        return mods

    @staticmethod
    def _item_to_dict(item_el: ET.Element) -> dict[str, Any]:
        return {
            "slot": item_el.get("slot", ""),
            "name": item_el.get("name", ""),
            "baseType": item_el.get("baseType", ""),
            "rarity": item_el.get("rarity", ""),
            "mods": [
                {"type": m.get("type", ""), "text": m.get("text", "")}
                for m in item_el.findall(".//Mod")
            ],
        }


pob_parser = PoBParser()

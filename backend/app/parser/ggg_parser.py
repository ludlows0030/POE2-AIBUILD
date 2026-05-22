"""将 GGG API 原始 JSON 转换为数据库 ORM 模型。"""

from datetime import datetime, timezone

from app.models.base import (
    BuildMeta,
    Character,
    EquipmentItem,
    PassiveTree,
    SkillGroup,
)


def parse_character(api_entry: dict, league: str) -> Character:
    """从 Ladder API entry 解析 Character 模型。"""
    char_data = api_entry["character"]
    return Character(
        account_name=api_entry["account"]["name"],
        character_name=char_data["name"],
        league=league,
        level=char_data["level"],
        char_class=char_data.get("class", "Unknown"),
        ascendancy=char_data.get("ascendancy"),
        last_updated=datetime.now(timezone.utc),
    )


def parse_equipment(character_id: str, items_data: dict) -> list[EquipmentItem]:
    """从 /character/items API 响应解析装备列表。"""
    equipment: list[EquipmentItem] = []

    for item in items_data.get("items", []):
        equipment.append(
            EquipmentItem(
                character_id=character_id,
                slot=item.get("inventoryId", "Unknown"),
                item_name=item.get("name", ""),
                base_type=item.get("baseType", ""),
                rarity=item.get("frameType", "normal"),
                explicit_mods=item.get("explicitMods", []),
                implicit_mods=item.get("implicitMods", []),
                crafted_mods=item.get("craftedMods", []),
                enchant_mods=item.get("enchantMods", []),
                sockets=len(item.get("sockets", [])),
                links=item.get("maxSockets", None),
                raw_json=item,
            )
        )

    return equipment


def parse_passives(character_id: str, passives_data: dict) -> PassiveTree:
    """从 /character/passives API 响应解析天赋树。"""
    return PassiveTree(
        character_id=character_id,
        node_ids=passives_data.get("hashes", []),
        keystone_nodes=[
            h for h in passives_data.get("hashes", [])
            if _is_keystone(h, passives_data)
        ],
        ascendancy_nodes=passives_data.get("ascendancyHashes", []),
        mastery_choices=passives_data.get("masteryEffects", {}),
    )


def _is_keystone(node_hash: str, passives_data: dict) -> bool:
    """判断节点是否为关键天赋（keystone）。"""
    # GGG API 不直接标注 keystone，需要通过节点数据判断
    # 这里做简单启发式标记，后续由知识图谱模块精确识别
    return node_hash in passives_data.get("keystoneHashes", [])


def parse_skill_groups(character_id: str, items_data: dict) -> list[SkillGroup]:
    """从装备数据中解析技能组（技能宝石映射到各装备槽位）。"""
    skills: list[SkillGroup] = []

    for item in items_data.get("items", []):
        for socketed in item.get("socketedItems", []):
            if socketed.get("type") == "gem":
                skills.append(
                    SkillGroup(
                        character_id=character_id,
                        active_skill_id=socketed.get("id", ""),
                        active_skill_name=socketed.get("name", ""),
                        support_gems=[],  # 支援宝石需额外解析 gem links
                        trigger_condition=None,
                        gem_links=len(item.get("sockets", [])),
                    )
                )

    return skills


def parse_build_meta(
    character_id: str,
    source: str = "ggg_api",
    league_version: str = "Standard",
) -> BuildMeta:
    """创建 BD 元数据记录。"""
    return BuildMeta(
        character_id=character_id,
        source=source,
        collected_at=datetime.now(timezone.utc),
        league_version=league_version,
        power_rating=None,
        tags=[],
        damage_types=[],
    )

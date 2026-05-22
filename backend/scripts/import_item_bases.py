"""POE2 装备底材导入 — 从游戏知识编译底材列表，逐条验证 POE2DB，增量导入。

POE2 底材数量远少于 POE1（~200 件），手动维护可行。
每个底材会通过 POE2DB 验证是否存在。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.poe2db_lookup import lookup
from app.models.base import ItemBase

logger = logging.getLogger(__name__)

# ── POE2 装备底材列表（来源：游戏知识 + POE2DB 验证）─────────

POE2_ITEM_BASES: list[dict] = [
    # ═══ 弓 (Bows) ═══
    {"name_en": "Crude Bow", "name_zh": "粗糙弓", "item_class": "Bow"},
    {"name_en": "Shortbow", "name_zh": "短弓", "item_class": "Bow"},
    {"name_en": "Longbow", "name_zh": "长弓", "item_class": "Bow"},
    {"name_en": "Composite Bow", "name_zh": "复合弓", "item_class": "Bow"},
    {"name_en": "Recursive Bow", "name_zh": "递归弓", "item_class": "Bow"},
    {"name_en": "Warden Bow", "name_zh": "守望者弓", "item_class": "Bow"},
    # ═══ 十字弩 (Crossbows) ═══
    {"name_en": "Makeshift Crossbow", "name_zh": "临时十字弩", "item_class": "Crossbow"},
    {"name_en": "Tense Crossbow", "name_zh": "紧绷十字弩", "item_class": "Crossbow"},
    {"name_en": "Stout Crossbow", "name_zh": "结实十字弩", "item_class": "Crossbow"},
    {"name_en": "Repeating Crossbow", "name_zh": "连发十字弩", "item_class": "Crossbow"},
    {"name_en": "Siege Crossbow", "name_zh": "攻城十字弩", "item_class": "Crossbow"},
    # ═══ 细杖/节杖 (Quarterstaves) ═══
    {"name_en": "Crackling Quarterstaff", "name_zh": "噼啪细杖", "item_class": "Quarterstaff"},
    {"name_en": "Sturdy Quarterstaff", "name_zh": "结实细杖", "item_class": "Quarterstaff"},
    {"name_en": "Mystic Quarterstaff", "name_zh": "神秘细杖", "item_class": "Quarterstaff"},
    {"name_en": "War Quarterstaff", "name_zh": "战斗细杖", "item_class": "Quarterstaff"},
    # ═══ 单手锤 (One Hand Maces) ═══
    {"name_en": "Wooden Club", "name_zh": "木棍", "item_class": "One Hand Mace"},
    {"name_en": "Spiked Club", "name_zh": "钉刺木棍", "item_class": "One Hand Mace"},
    {"name_en": "Slim Mace", "name_zh": "细锤", "item_class": "One Hand Mace"},
    {"name_en": "Warpick", "name_zh": "战镐", "item_class": "One Hand Mace"},
    {"name_en": "Flanged Mace", "name_zh": "凸缘锤", "item_class": "One Hand Mace"},
    {"name_en": "Execratus Hammer", "name_zh": "惩戒锤", "item_class": "One Hand Mace"},
    # ═══ 双手锤 (Two Hand Maces) ═══
    {"name_en": "Cultist Greathammer", "name_zh": "邪教徒巨锤", "item_class": "Two Hand Mace"},
    {"name_en": "Totemic Great hammer", "name_zh": "图腾巨锤", "item_class": "Two Hand Mace"},
    # ═══ 战矛 (Spears) ═══
    {"name_en": "Short Spear", "name_zh": "短矛", "item_class": "Spear"},
    {"name_en": "Long Spear", "name_zh": "长矛", "item_class": "Spear"},
    {"name_en": "War Spear", "name_zh": "战矛", "item_class": "Spear"},
    # ═══ 长杖 (Staves) ═══
    {"name_en": "Wooden Staff", "name_zh": "木杖", "item_class": "Staff"},
    {"name_en": "Iron Staff", "name_zh": "铁杖", "item_class": "Staff"},
    {"name_en": "Royal Staff", "name_zh": "皇室杖", "item_class": "Staff"},
    # ═══ 法杖 (Wands) ═══
    {"name_en": "Wooden Wand", "name_zh": "木制法杖", "item_class": "Wand"},
    {"name_en": "Crystal Wand", "name_zh": "水晶法杖", "item_class": "Wand"},
    {"name_en": "Bone Wand", "name_zh": "骨制法杖", "item_class": "Wand"},
    # ═══ 权杖 (Sceptres) ═══
    {"name_en": "Wooden Sceptre", "name_zh": "木制权杖", "item_class": "Sceptre"},
    {"name_en": "Iron Sceptre", "name_zh": "铁制权杖", "item_class": "Sceptre"},
    {"name_en": "Royal Sceptre", "name_zh": "皇室权杖", "item_class": "Sceptre"},
    # ═══ 双手剑 (Two Hand Swords) ═══
    {"name_en": "Rusted Greatsword", "name_zh": "生锈钢剑", "item_class": "Two Hand Sword"},
    # ═══ 双手斧 (Two Hand Axes) ═══
    {"name_en": "Woodsplitter", "name_zh": "劈木斧", "item_class": "Two Hand Axe"},
    # ═══ 护符 (Talismans) ═══
    {"name_en": "Wolf Pelt", "name_zh": "狼皮", "item_class": "Talisman"},
    # ── 防具 ──
    # 头盔 (Helmets)
    {"name_en": "Iron Hat", "name_zh": "铁帽", "item_class": "Helmet"},
    {"name_en": "Leather Hood", "name_zh": "皮革兜帽", "item_class": "Helmet"},
    {"name_en": "Silk Hood", "name_zh": "丝绸兜帽", "item_class": "Helmet"},
    {"name_en": "Soldier Helmet", "name_zh": "士兵头盔", "item_class": "Helmet"},
    {"name_en": "Plague Mask", "name_zh": "瘟疫面具", "item_class": "Helmet"},
    {"name_en": "Night Hood", "name_zh": "夜色兜帽", "item_class": "Helmet"},
    # 胸甲 (Body Armours)
    {"name_en": "Plate Vest", "name_zh": "板甲背心", "item_class": "Body Armour"},
    {"name_en": "Leather Vest", "name_zh": "皮背心", "item_class": "Body Armour"},
    {"name_en": "Silk Robe", "name_zh": "丝绸长袍", "item_class": "Body Armour"},
    {"name_en": "Chainmail", "name_zh": "锁子甲", "item_class": "Body Armour"},
    {"name_en": "Scale Doublet", "name_zh": "鱼鳞紧身衣", "item_class": "Body Armour"},
    {"name_en": "Hexers Robe", "name_zh": "咒术长袍", "item_class": "Body Armour"},
    # 手套 (Gloves)
    {"name_en": "Iron Gauntlets", "name_zh": "铁制护手", "item_class": "Gloves"},
    {"name_en": "Leather Gloves", "name_zh": "皮手套", "item_class": "Gloves"},
    {"name_en": "Silk Gloves", "name_zh": "丝绸手套", "item_class": "Gloves"},
    # 靴子 (Boots)
    {"name_en": "Iron Greaves", "name_zh": "铁制胫甲", "item_class": "Boots"},
    {"name_en": "Leather Boots", "name_zh": "皮靴", "item_class": "Boots"},
    {"name_en": "Silk Slippers", "name_zh": "丝绸拖鞋", "item_class": "Boots"},
    {"name_en": "Feathered Sandals", "name_zh": "羽毛凉鞋", "item_class": "Boots"},
    # 盾牌 (Shields)
    {"name_en": "Wooden Shield", "name_zh": "木盾", "item_class": "Shield"},
    {"name_en": "Buckler", "name_zh": "小圆盾", "item_class": "Shield"},
    {"name_en": "Spirit Shield", "name_zh": "灵魂盾", "item_class": "Shield"},
    {"name_en": "Tower Shield", "name_zh": "塔盾", "item_class": "Shield"},
    # 法器 (Foci)
    {"name_en": "Wooden Focus", "name_zh": "木制法器", "item_class": "Focus"},
    {"name_en": "Crystal Focus", "name_zh": "水晶法器", "item_class": "Focus"},
    # ── 饰品 ──
    # 项链 (Amulets)
    {"name_en": "Amber Amulet", "name_zh": "琥珀护身符", "item_class": "Amulet"},
    {"name_en": "Jade Amulet", "name_zh": "翡翠护身符", "item_class": "Amulet"},
    {"name_en": "Lapis Amulet", "name_zh": "青金石护身符", "item_class": "Amulet"},
    {"name_en": "Gold Amulet", "name_zh": "金护身符", "item_class": "Amulet"},
    # 戒指 (Rings)
    {"name_en": "Iron Ring", "name_zh": "铁戒指", "item_class": "Ring"},
    {"name_en": "Silver Ring", "name_zh": "银戒指", "item_class": "Ring"},
    {"name_en": "Gold Ring", "name_zh": "金戒指", "item_class": "Ring"},
    {"name_en": "Amethyst Ring", "name_zh": "紫晶戒指", "item_class": "Ring"},
    {"name_en": "Two Stone Ring", "name_zh": "双石戒指", "item_class": "Ring"},
    # 腰带 (Belts)
    {"name_en": "Rustic Sash", "name_zh": "乡村腰带", "item_class": "Belt"},
    {"name_en": "Linen Belt", "name_zh": "亚麻腰带", "item_class": "Belt"},
    {"name_en": "Heavy Belt", "name_zh": "重腰带", "item_class": "Belt"},
]


def content_hash(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


async def import_item_bases(
    db: AsyncSession,
    game_version: str = "3.26",
    delay: float = 4.0,
    dry_run: bool = False,
) -> dict:
    """逐条验证并导入装备底材。

    Args:
        db: 数据库会话
        game_version: 游戏版本
        delay: 每个底材之间的延迟（秒）
        dry_run: 仅统计不写入

    Returns:
        {inserted, updated, skipped, errors, verified, not_found}
    """
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0, "verified": 0, "not_found": 0}

    for i, item in enumerate(POE2_ITEM_BASES):
        name_en = item["name_en"]
        slug = name_en.replace(" ", "_")

        try:
            # POE2DB 验证
            r = await lookup(slug, "us", format="json")
            if r.get("found"):
                stats["verified"] += 1
            else:
                stats["not_found"] += 1
                logger.debug(f"Not on POE2DB: {name_en}")

            # 构建数据
            data = {
                "name_en": name_en,
                "name_zh": item.get("name_zh"),
                "item_class": item["item_class"],
            }
            hval = content_hash(data)

            existing = await db.scalar(
                select(ItemBase).where(ItemBase.name_en == name_en)
            )
            if existing:
                if existing.content_hash == hval:
                    stats["skipped"] += 1
                else:
                    for k, v in data.items():
                        if hasattr(existing, k) and k not in ("id", "content_hash"):
                            setattr(existing, k, v)
                    existing.content_hash = hval
                    existing.game_version = game_version
                    existing.updated_at = datetime.now(timezone.utc)
                    stats["updated"] += 1
            else:
                if not dry_run:
                    db.add(ItemBase(
                        **data,
                        content_hash=hval,
                        game_version=game_version,
                    ))
                stats["inserted"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.warning(f"Error on {name_en}: {e}")

        # 延迟 + 定期提交
        if (i + 1) % 10 == 0:
            if not dry_run:
                await db.commit()
            logger.info(
                f"Item base progress: {i + 1}/{len(POE2_ITEM_BASES)} | "
                f"ins={stats['inserted']} ver={stats['verified']} nf={stats['not_found']}"
            )
        if i < len(POE2_ITEM_BASES) - 1:
            await asyncio.sleep(delay)

    if not dry_run:
        await db.commit()

    logger.info(f"Item base import complete: ins={stats['inserted']}, upd={stats['updated']}, "
                f"skip={stats['skipped']}, ver={stats['verified']}, nf={stats['not_found']}")
    return stats

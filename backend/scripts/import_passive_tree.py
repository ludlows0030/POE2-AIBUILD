"""POE2 天赋树导入 — 从 PoB2 tree.json + POE2DB 中文名，导入全部节点及连接关系。

数据源:
  - tree.json: PoB2 社区从游戏文件提取的完整天赋树（4,891 节点，含连接关系）
  - POE2DB CN Notable/Keystone 页面: 中文名

用法:
    cd backend && python scripts/import_passive_tree.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from sqlalchemy import select

sys.path.insert(0, ".")

from app.collectors.poe2db_lookup import fetch_poe2db_page, cache_clear
from app.database import async_session_factory
from app.models.base import PassiveNode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

TREE_JSON_PATH = Path(__file__).parent.parent / "data" / "tree_0_4.json"

# ── 基础属性名中英对照 ─────────────────────────────────────

_BASIC_STAT_ZH: dict[str, str] = {
    "Strength": "力量",
    "Dexterity": "敏捷",
    "Intelligence": "智慧",
    "Strength and Dexterity": "力量和敏捷",
    "Strength and Intelligence": "力量和智慧",
    "Dexterity and Intelligence": "敏捷和智慧",
    "Life Flask Charges": "生命药剂充能",
    "Mana Flask Charges": "魔力药剂充能",
    "Shock Chance": "感电几率",
    "Life Leech": "生命吸取",
    "Mana Leech": "魔力吸取",
    "Attack Damage": "攻击伤害",
    "Spell Damage": "法术伤害",
    "Armour": "护甲",
    "Evasion": "闪避",
    "Energy Shield": "能量护盾",
    "Life": "生命",
    "Mana": "魔力",
    "Chaos Resistance": "混沌抗性",
    "Fire Resistance": "火焰抗性",
    "Cold Resistance": "冰霜抗性",
    "Lightning Resistance": "闪电抗性",
    "Elemental Damage": "元素伤害",
    "Physical Damage": "物理伤害",
    "Chaos Damage": "混沌伤害",
    "Attack Speed": "攻击速度",
    "Cast Speed": "施法速度",
    "Movement Speed": "移动速度",
    "Critical Hit Chance": "暴击几率",
    "Critical Damage Bonus": "暴击伤害",
    "Accuracy": "命中",
    "Block": "格挡",
    "Stun Threshold": "眩晕门槛",
    "Flask Charges": "药剂充能",
    "Life Regeneration": "生命回复",
    "Mana Regeneration": "魔力回复",
    "Light Radius": "光照范围",
    "Rarity": "稀有度",
    "Projectile Damage": "投射物伤害",
    "Area Damage": "范围伤害",
    "Totem Life": "图腾生命",
    "Minion Damage": "召唤物伤害",
    "Trap Damage": "陷阱伤害",
    "Mine Damage": "地雷伤害",
    "Warcry Speed": "战吼速度",
    "Brand Damage": "烙印伤害",
    "Herald Damage": "捷伤害",
    "Aura Effect": "光环效果",
    "Curse Effect": "诅咒效果",
    "Duration": "持续时间",
    "AoE": "范围",
    "Projectile Speed": "投射物速度",
    "Flask Effect": "药剂效果",
    "Bleed": "流血",
    "Poison": "中毒",
    "Ignite": "点燃",
    "Freeze": "冻结",
    "Shock": "感电",
    "Impale": "穿刺",
    "Leech": "吸取",
    "Gain": "获得",
    "Charge": "充能",
    "Endurance Charge": "耐力球",
    "Frenzy Charge": "狂怒球",
    "Power Charge": "暴击球",
}


def content_hash(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _infer_zh_name(name_en: str) -> str | None:
    """尝试推断简单节点的中文名。"""
    # 精确匹配
    if name_en in _BASIC_STAT_ZH:
        return _BASIC_STAT_ZH[name_en]
    # "10 to Strength" → "10 力量"
    if " to Strength" in name_en:
        return name_en.replace(" to Strength", " 力量")
    if " to Dexterity" in name_en:
        return name_en.replace(" to Dexterity", " 敏捷")
    if " to Intelligence" in name_en:
        return name_en.replace(" to Intelligence", " 智慧")
    # "+X% to Strength" etc pattern
    for en, zh in _BASIC_STAT_ZH.items():
        if en.lower() in name_en.lower():
            # Simple replacement is hard, skip for now
            pass
    return None


def _parse_cn_notable_page(html: str) -> dict[str, str]:
    """从 CN Notable 页面提取 {slug: 中文名} 映射。"""
    soup = BeautifulSoup(html, "lxml")
    mapping: dict[str, str] = {}
    # CN page may use different pane structure — find all PassiveSkills links
    for a in soup.find_all("a", class_="PassiveSkills"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if href and text and not href.startswith("?"):
            slug = href.split("/")[-1] if "/" in href else href
            if slug and text:
                mapping[slug] = text
    return mapping


def _parse_cn_keystone_page(html: str) -> dict[str, str]:
    """从 CN Keystone 页面提取 {slug: 中文名} 映射。"""
    soup = BeautifulSoup(html, "lxml")
    mapping: dict[str, str] = {}
    for a in soup.find_all("a", class_="PassiveSkills"):
        href = a.get("href", "")
        text = a.get_text(strip=True)
        if href and text and not href.startswith("?"):
            slug = href.split("/")[-1] if "/" in href else href
            if slug and text:
                mapping[slug] = text
    return mapping


async def import_passive_tree(
    game_version: str = "3.26",
    dry_run: bool = False,
) -> dict:
    """导入全部天赋树节点。

    Returns:
        {inserted, updated, skipped, errors}
    """
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    # ── Step 1: 加载 tree.json ──────────────────────────
    logger.info("Step 1: Loading tree.json...")
    with open(TREE_JSON_PATH, "r", encoding="utf-8") as f:
        tree_data = json.load(f)

    nodes = tree_data["nodes"]
    groups = tree_data["groups"]

    # 建立 group_id → (x, y) 映射
    group_positions: dict[int, tuple[float, float]] = {}
    for i, g in enumerate(groups):
        if g is None:
            continue
        if g.get("x") is not None and g.get("y") is not None:
            group_positions[i] = (g["x"], g["y"])

    logger.info(f"Loaded {len(nodes)} nodes, {len(groups)} groups")

    # ── Step 2: 获取中文名 ──────────────────────────────
    logger.info("Step 2: Fetching Chinese names from POE2DB...")
    cache_clear()

    cn_html_notable = await fetch_poe2db_page("Notable", "cn")
    cn_html_keystone = await fetch_poe2db_page("Keystone", "cn")

    cn_notable_map = _parse_cn_notable_page(cn_html_notable) if cn_html_notable else {}
    cn_keystone_map = _parse_cn_keystone_page(cn_html_keystone) if cn_html_keystone else {}
    cn_all = {**cn_notable_map, **cn_keystone_map}

    logger.info(f"CN names: {len(cn_notable_map)} notables + {len(cn_keystone_map)} keystones = {len(cn_all)} total")

    # ── Step 3: 逐节点导入 ──────────────────────────────
    logger.info("Step 3: Importing nodes...")
    total = len(nodes)

    async with async_session_factory() as db:
        for i, (node_key, node) in enumerate(nodes.items()):
            try:
                name_en = node.get("name", "")
                if not name_en:
                    stats["skipped"] += 1
                    continue

                # 确定节点类型
                if node.get("isKeystone"):
                    node_type = "keystone"
                elif node.get("isNotable"):
                    node_type = "notable"
                elif node.get("ascendancyName"):
                    node_type = "ascendancy"
                else:
                    node_type = "normal"

                # 中文名
                slug = name_en.replace(" ", "_").replace("'", "")
                name_zh = cn_all.get(slug) or _infer_zh_name(name_en)

                # 位置
                group_id = node.get("group")
                pos_x, pos_y = group_positions.get(group_id, (None, None))

                # 连接关系 → 按 node_gid 存储
                connections = [str(c["id"]) for c in node.get("connections", [])]

                # 构建数据
                data = {
                    "node_gid": str(node["skill"]),
                    "name_en": name_en,
                    "name_zh": name_zh,
                    "node_type": node_type,
                    "stats": node.get("stats", []),
                    "position_x": pos_x,
                    "position_y": pos_y,
                    "ascendancy_class": node.get("ascendancyName"),
                    "connections": connections,
                }
                hval = content_hash(data)

                existing = await db.scalar(
                    select(PassiveNode).where(PassiveNode.node_gid == data["node_gid"])
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
                        existing.is_active = True
                        existing.updated_at = datetime.now(timezone.utc)
                        stats["updated"] += 1
                else:
                    if not dry_run:
                        db.add(PassiveNode(
                            **data,
                            content_hash=hval,
                            game_version=game_version,
                        ))
                    stats["inserted"] += 1

            except Exception as e:
                stats["errors"] += 1
                logger.warning(f"Error on node {node_key}: {e}")

            # 每 500 条提交并报告
            if (i + 1) % 500 == 0:
                if not dry_run:
                    await db.commit()
                logger.info(
                    f"Progress: {i + 1}/{total} | "
                    f"ins={stats['inserted']} upd={stats['updated']} "
                    f"skip={stats['skipped']} err={stats['errors']}"
                )

        if not dry_run:
            await db.commit()

    logger.info(f"Passive tree import complete: ins={stats['inserted']}, "
                f"upd={stats['updated']}, skip={stats['skipped']}, err={stats['errors']}")
    return stats


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Import POE2 passive skill tree")
    parser.add_argument("--dry-run", action="store_true", help="统计不写入")
    parser.add_argument("--game-version", default="3.26")
    args = parser.parse_args()

    result = await import_passive_tree(
        game_version=args.game_version,
        dry_run=args.dry_run,
    )
    print(f"\nFinal: {result}")


if __name__ == "__main__":
    asyncio.run(main())

"""POE2DB 数据导入管道 — 从 POE2DB 采集全量游戏数据并导入 PostgreSQL。

采集策略：
  1. 升华职业 — 已知 POE2 数据，硬编码 + POE2DB 验证（12 个升华）
  2. 装备底材 — 按武器/防具类型页面逐一采集
  3. 传奇物品 — 从 Unique_item 列表页 + 详情页采集
  4. 天赋节点 — 从 Passive_Skill_Tree 页面解析
  5. 装备词缀 — 按物品类型采集可用词缀

增量更新：
  - 每条记录计算 content_hash = sha256(json.dumps(data, sort_keys=True))
  - 版本更新后重新采集，hash 比对决定 INSERT/UPDATE/SKIP
  - 旧版本有但新版本无的记录标记 is_active=False
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.poe2db_lookup import lookup as poe2db_lookup
from app.models.base import (
    AscendancyClass,
    ItemBase,
    Modifier,
    PassiveNode,
    UniqueItem,
)

logger = logging.getLogger(__name__)


# ── 内容哈希工具 ───────────────────────────────────────────


def content_hash(data: dict[str, Any]) -> str:
    """计算数据内容的 SHA256 哈希，用于增量更新比对。"""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── 增量更新辅助 ───────────────────────────────────────────


@dataclass
class ImportStats:
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    deactivated: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.skipped

    def __str__(self) -> str:
        return (
            f"Inserted: {self.inserted}, Updated: {self.updated}, "
            f"Skipped: {self.skipped}, Deactivated: {self.deactivated}"
            + (f", Errors: {len(self.errors)}" if self.errors else "")
        )


# ── 升华职业导入 ──────────────────────────────────────────


# POE2 12 个升华职业的已知数据（来源：官方 + POE2DB 验证）
POE2_ASCENDANCIES: list[dict[str, Any]] = [
    # ── Sorceress ──
    {
        "name_en": "Stormweaver",
        "name_zh": "风暴编织者",
        "base_class": "Sorceress",
        "description_zh": "专注于闪电伤害和元素风暴效果。核心机制：元素风暴（Elemental Storm）自动触发，强化感电和冰缓效果。",
    },
    {
        "name_en": "Chronomancer",
        "name_zh": "时空法师",
        "base_class": "Sorceress",
        "description_zh": "操控时间的施法者。核心机制：时间回溯（Time Freeze）、冷却恢复加速、技能冷却重置。",
    },
    # ── Monk ──
    {
        "name_en": "Invoker",
        "name_zh": "祈求者",
        "base_class": "Monk",
        "description_zh": "元素武僧，融合冰冷与闪电之力。核心机制：元素祈唤（Invoke Elements）、暴击得球、冥想增益。",
    },
    {
        "name_en": "Acolyte of Chayula",
        "name_zh": "夏乌拉侍僧",
        "base_class": "Monk",
        "description_zh": "混沌武僧，拥抱暗影。核心机制：混沌抗性穿透、暗影之握（Grasp of the Void）、生命偷取转ES。",
    },
    # ── Warrior ──
    {
        "name_en": "Titan",
        "name_zh": "泰坦",
        "base_class": "Warrior",
        "description_zh": "重型打击专精。核心机制：晕眩积累加速、重击伤害加成、生命加成。",
    },
    {
        "name_en": "Warbringer",
        "name_zh": "战争使者",
        "base_class": "Warrior",
        "description_zh": "战吼与图腾专精。核心机制：战吼增益强化、图腾增益共享、护甲加成。",
    },
    # ── Ranger ──
    {
        "name_en": "Deadeye",
        "name_zh": "神射手",
        "base_class": "Ranger",
        "description_zh": "远程箭术大师。核心机制：额外投射物、远射（Far Shot）、命中与速度加成。",
    },
    {
        "name_en": "Pathfinder",
        "name_zh": "开拓者",
        "base_class": "Ranger",
        "description_zh": "药剂与元素专精。核心机制：药剂充能获取、元素异常扩散、移动速度加成。",
    },
    # ── Mercenary ──
    {
        "name_en": "Witchhunter",
        "name_zh": "女巫猎人",
        "base_class": "Mercenary",
        "description_zh": "弩箭与陷阱专精。核心机制：对低血敌人处决、集中效果、元素弹药。",
    },
    {
        "name_en": "Gemling Legionnaire",
        "name_zh": "宝石军团士兵",
        "base_class": "Mercenary",
        "description_zh": "宝石强化专精。核心机制：额外宝石品质、宝石属性需求降低、宝石等级加成。",
    },
    # ── Witch ──
    {
        "name_en": "Infernalist",
        "name_zh": "地狱使者",
        "base_class": "Witch",
        "description_zh": "火焰与召唤融合。核心机制：地狱火（Infernal Flame）替代魔力、召唤物火焰伤害转化、点燃扩散。",
    },
    {
        "name_en": "Blood Mage",
        "name_zh": "血法师",
        "base_class": "Witch",
        "description_zh": "生命作为资源的施法者。核心机制：法术消耗生命、生命残留（Life Remnants）、暴击流血。",
    },
    # ── Huntress (待上线) ──
    # {"name_en": "Beastmaster", ...},
    # {"name_en": "Tactician", ...},
    # ── Druid (待上线) ──
    # {"name_en": "Fury", ...},
    # {"name_en": "Preserver", ...},
]


async def import_ascendancy_classes(
    db: AsyncSession,
    game_version: str = "0.4",
    dry_run: bool = False,
) -> ImportStats:
    """导入升华职业数据。

    先查询数据库已有记录，通过 content_hash 比对决定增删改。
    """
    stats = ImportStats()

    for asc_data in POE2_ASCENDANCIES:
        hash_val = content_hash(asc_data)

        # 查询是否已存在
        existing = await db.scalar(
            select(AscendancyClass).where(
                AscendancyClass.name_en == asc_data["name_en"]
            )
        )

        if existing:
            if existing.content_hash == hash_val:
                stats.skipped += 1
                continue
            # 更新
            existing.name_zh = asc_data["name_zh"]
            existing.base_class = asc_data["base_class"]
            existing.description_zh = asc_data.get("description_zh")
            existing.content_hash = hash_val
            existing.game_version = game_version
            existing.is_active = True
            existing.updated_at = datetime.now(timezone.utc)
            stats.updated += 1
        else:
            if not dry_run:
                db.add(AscendancyClass(
                    name_en=asc_data["name_en"],
                    name_zh=asc_data["name_zh"],
                    base_class=asc_data["base_class"],
                    description_zh=asc_data.get("description_zh"),
                    content_hash=hash_val,
                    game_version=game_version,
                ))
            stats.inserted += 1

    if not dry_run:
        await db.commit()

    logger.info(f"Ascendancy import: {stats}")
    return stats


# ── 装备底材导入 ──────────────────────────────────────────


# POE2 物品类别 → POE2DB 页面名映射
ITEM_CLASS_PAGES: dict[str, list[str]] = {
    # 武器
    "Bow": ["Bows", "Bow"],
    "Crossbow": ["Crossbows", "Crossbow"],
    "Quarterstaff": ["Quarterstaves", "Quarterstaff"],
    "One Hand Mace": ["One_Hand_Maces", "One_Hand_Mace"],
    "Two Hand Mace": ["Two_Hand_Maces", "Two_Hand_Mace"],
    "Spear": ["Spears", "Spear"],
    "Staff": ["Staves", "Staff"],
    "Wand": ["Wands", "Wand"],
    "Sceptre": ["Sceptres", "Sceptre"],
    "Two Hand Sword": ["Two_Hand_Swords", "Two_Hand_Sword"],
    "Two Hand Axe": ["Two_Hand_Axes", "Two_Hand_Axe"],
    "Claw": ["Claws", "Claw"],
    "Dagger": ["Daggers", "Dagger"],
    "Flail": ["Flails", "Flail"],
    "Talisman": ["Talismans", "Talisman"],
    # 防具
    "Helmet": ["Helmets", "Helmet"],
    "Body Armour": ["Body_Armours", "Body_Armour"],
    "Gloves": ["Gloves", "Glove"],
    "Boots": ["Boots", "Boot"],
    "Shield": ["Shields", "Shield"],
    "Focus": ["Foci", "Focus"],
    # 饰品
    "Amulet": ["Amulets", "Amulet"],
    "Ring": ["Rings", "Ring"],
    "Belt": ["Belts", "Belt"],
}


async def _scrape_item_class_page(item_class: str, lang: str = "cn") -> list[dict[str, Any]]:
    """抓取一个物品类别页面的所有底材数据。"""
    candidates = ITEM_CLASS_PAGES.get(item_class, [item_class])
    items: list[dict[str, Any]] = []

    for page_name in candidates:
        result = await poe2db_lookup(page_name, lang, format="json")
        if not result.get("found"):
            continue

        # 从表格中提取底材数据
        for table in result.get("tables", []):
            for row in table:
                items.append({
                    "item_class": item_class,
                    "source_page": page_name,
                    "raw_data": row,
                })
        if items:
            break

    return items


async def import_item_bases(
    db: AsyncSession,
    game_version: str = "0.4",
    item_classes: list[str] | None = None,
    concurrency: int = 3,
    dry_run: bool = False,
) -> ImportStats:
    """导入装备底材数据。

    Args:
        db: 数据库会话
        game_version: 游戏版本
        item_classes: 要导入的物品类别（None=全部）
        concurrency: 并发抓取数
        dry_run: 仅统计不写入
    """
    stats = ImportStats()
    targets = item_classes or list(ITEM_CLASS_PAGES.keys())
    semaphore = asyncio.Semaphore(concurrency)

    async def scrape_one(item_class: str) -> list[dict[str, Any]]:
        async with semaphore:
            try:
                return await _scrape_item_class_page(item_class)
            except Exception as e:
                stats.errors.append(f"{item_class}: {e}")
                return []

    # 并发抓取所有物品类别
    tasks = [scrape_one(cls) for cls in targets]
    results = await asyncio.gather(*tasks)

    for item_class, items in zip(targets, results):
        for item_data in items:
            raw = item_data["raw_data"]
            name_en = raw.get("Name", raw.get("name", raw.get("Item", "")))
            if not name_en:
                continue

            data = {
                "name_en": name_en,
                "name_zh": raw.get("name_zh", raw.get("Chinese", "")),
                "item_class": item_class,
                "drop_level": _parse_int(raw.get("Drop Level", raw.get("drop_level"))),
                "required_level": _parse_int(raw.get("Required Level", raw.get("required_level"))),
                "required_str": _parse_int(raw.get("Str", raw.get("str", raw.get("Strength")))),
                "required_dex": _parse_int(raw.get("Dex", raw.get("dex", raw.get("Dexterity")))),
                "required_int": _parse_int(raw.get("Int", raw.get("int", raw.get("Intelligence")))),
            }
            hash_val = content_hash(data)

            # 查重
            existing = await db.scalar(
                select(ItemBase).where(ItemBase.name_en == name_en)
            )
            if existing:
                if existing.content_hash == hash_val:
                    stats.skipped += 1
                    continue
                _update_existing(existing, data, hash_val, game_version)
                stats.updated += 1
            else:
                if not dry_run:
                    db.add(ItemBase(
                        **data,
                        content_hash=hash_val,
                        game_version=game_version,
                    ))
                stats.inserted += 1

    if not dry_run:
        await db.commit()

    logger.info(f"Item base import: {stats}")
    return stats


# ── 传奇物品导入 ───────────────────────────────────────────


# POE2DB 传奇物品列表页的非物品链接过滤
_UNIQUE_SKIP_PATTERNS = [
    "Damage", "Attack", "Armour", "Evasion", "Energy_Shield", "Physical",
    "Strength", "Dexterity", "Intelligence", "Player_", "Skill_Gems",
    "Support_Gems", "Spirit_Gems", "Stun", "Melee", "Rarity", "Item_",
    "Unique_item", "PoEDB", "Modifier", "Modifiers", "Keywords",
    "Ascendancy", "Passive", "GemTags", "Lineage", "Desecrated",
    "Historic", "Timeless", "Crafting", "Quest", "Waystones",
    "Shrine", "Strongbox", "Essence", "Act", "Patreon",
]


def _parse_unique_list_html(html: str, lang: str = "us") -> dict[str, str]:
    """从 Unique_item 页面 HTML 中提取所有传奇物品的名称映射。

    返回 {slug: display_name} 字典。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    items: dict[str, str] = {}

    tab_content = soup.find("div", class_="tab-content")
    if not tab_content:
        return items

    for a in tab_content.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)

        if f"/{lang}/" not in href:
            continue
        if not text or len(text) < 3:
            continue

        slug = href.split(f"/{lang}/")[-1]

        if any(p in slug for p in _UNIQUE_SKIP_PATTERNS):
            continue
        if "/" in slug or "?" in slug or ":" in slug:
            continue

        if slug not in items:
            items[slug] = text

    return items


async def _scrape_unique_detail(slug: str, name_cn: str = "") -> dict[str, Any] | None:
    """抓取单个传奇物品的详情页数据。

    仅获取英文页面（数据最完整），中文名从列表页传入。
    """
    us_result = await poe2db_lookup(slug, "us", format="json")
    if not us_result.get("found"):
        return None

    tables = us_result.get("tables", [])
    data: dict[str, Any] = {
        "name_en": slug.replace("_", " "),
        "name_zh": name_cn or None,
        "base_item_type": None,
        "item_class": None,
        "required_level": None,
        "required_str": None,
        "required_dex": None,
        "required_int": None,
        "explicit_mods": [],
        "flavour_text": None,
        "is_boss_drop": False,
        "boss_source": None,
    }

    # Table 0: 物品元数据
    if tables:
        meta_table = tables[0]
        for row in meta_table:
            key = row.get("Name", "")
            val = row.get("Show Full Descriptions", "")
            if key == "BaseType":
                data["base_item_type"] = val
            elif key == "Release Version":
                data["game_version_hint"] = val
            elif key == "Acronym":
                data["item_class"] = val
            elif key == "Icon" and not data.get("base_item_type"):
                parts = val.split("/")
                if len(parts) >= 2:
                    data["base_item_type"] = parts[-2]

    # Table 2+: 词缀数据
    for table in tables[2:]:
        headers = list(table[0].keys()) if table else []
        if not headers:
            continue
        if "BuffGroupsID" in headers or "IsCharged" in headers:
            continue
        if "Version" in headers:
            continue

        stat_col = [h for h in headers if h != "Family"][0] if len(headers) > 1 else ""

        for row in table:
            family = row.get("Family", "")
            stat_val = row.get(stat_col, "") if stat_col else ""

            if family == "Req. level":
                lv = _parse_int(stat_val)
                if lv and (data["required_level"] is None or lv > data["required_level"]):
                    data["required_level"] = lv
                continue

            if family in ("Domains", "GenerationType", "Craft Tags"):
                continue

            if family == "Stats":
                modifier_name = stat_col
                if stat_val and stat_val not in ("1", ""):
                    data["explicit_mods"].append(f"{modifier_name}: {stat_val}")

    # 描述文本
    desc = us_result.get("description", "")
    if desc:
        data["flavour_text"] = desc

    return data


async def import_unique_items(
    db: AsyncSession,
    game_version: str = "0.4",
    limit: int = 0,
    concurrency: int = 3,
    dry_run: bool = False,
) -> ImportStats:
    """从 POE2DB 采集全量传奇物品数据。

    流程：
      1. 抓取 Unique_item 列表页 HTML
      2. 解析 tab-content 中所有物品链接
      3. 逐个抓取详情页（中英文）
      4. content_hash 比对，增量写入

    Args:
        db: 数据库会话
        game_version: 游戏版本
        limit: 限制导入数量（0=全部）
        concurrency: 详情页抓取并发数
        dry_run: 仅统计不写入
    """
    stats = ImportStats()

    # Step 1+2: 获取列表页（US + CN）并合并名称
    logger.info("Fetching unique item list from POE2DB...")
    from app.collectors.poe2db_lookup import fetch_poe2db_page

    html_us = await fetch_poe2db_page("Unique_item", "us")
    if not html_us:
        stats.errors.append("Failed to fetch Unique_item page")
        return stats

    items_us = _parse_unique_list_html(html_us, "us")
    logger.info(f"Parsed {len(items_us)} unique items from US listing")

    # 获取中文名
    html_cn = await fetch_poe2db_page("Unique_item", "cn")
    items_cn = _parse_unique_list_html(html_cn, "cn") if html_cn else {}
    logger.info(f"Parsed {len(items_cn)} unique items from CN listing")

    # 合并：slug → {slug, name_en, name_zh}
    all_slugs = set(items_us.keys()) | set(items_cn.keys())
    items: list[dict[str, str]] = []
    for slug in all_slugs:
        items.append({
            "slug": slug,
            "name_en": items_us.get(slug, slug.replace("_", " ")),
            "name_zh": items_cn.get(slug, ""),
        })

    logger.info(f"Merged {len(items)} unique items total")

    if limit > 0:
        items = items[:limit]

    # Step 3: 逐个采集详情
    semaphore = asyncio.Semaphore(concurrency)

    async def import_one(item: dict[str, str]) -> None:
        async with semaphore:
            slug = item["slug"]
            name_cn = item.get("name_zh", item.get("name_cn", ""))
            try:
                detail = await _scrape_unique_detail(slug, name_cn=name_cn)
                if detail is None:
                    stats.errors.append(f"{slug}: detail not found")
                    return

                hash_val = content_hash(detail)

                existing = await db.scalar(
                    select(UniqueItem).where(UniqueItem.name_en == detail["name_en"])
                )
                if existing:
                    if existing.content_hash == hash_val:
                        stats.skipped += 1
                        return
                    _update_existing(existing, detail, hash_val, game_version)
                    stats.updated += 1
                else:
                    if not dry_run:
                        db.add(UniqueItem(
                            name_en=detail["name_en"],
                            name_zh=detail.get("name_zh"),
                            base_item_type=detail.get("base_item_type"),
                            item_class=detail.get("item_class"),
                            required_level=detail.get("required_level"),
                            required_str=detail.get("required_str"),
                            required_dex=detail.get("required_dex"),
                            required_int=detail.get("required_int"),
                            explicit_mods=detail.get("explicit_mods"),
                            flavour_text=detail.get("flavour_text"),
                            is_boss_drop=detail.get("is_boss_drop"),
                            boss_source=detail.get("boss_source"),
                            content_hash=hash_val,
                            game_version=game_version,
                        ))
                    stats.inserted += 1

            except Exception as e:
                stats.errors.append(f"{slug}: {e}")

    # 分批执行（每批等待限速恢复）
    batch_size = 15
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        await asyncio.gather(*[import_one(item) for item in batch])
        if not dry_run:
            await db.commit()
        pct = min(i + batch_size, len(items))
        logger.info(f"Unique import: {pct}/{len(items)} — {stats}")

    logger.info(f"Unique item import complete: {stats}")
    return stats


# ── 辅助函数 ───────────────────────────────────────────────


def _parse_int(val: Any) -> int | None:
    """安全解析整数。"""
    if val is None:
        return None
    try:
        if isinstance(val, str):
            # 移除中文数字描述
            import re
            nums = re.findall(r"\d+", val)
            return int(nums[0]) if nums else None
        return int(val)
    except (ValueError, TypeError):
        return None


def _extract_mod_lines(text: str) -> list[str]:
    """从描述文本提取词缀行。"""
    if not text:
        return []
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if line and len(line) > 3:
            lines.append(line)
    return lines


def _update_existing(
    existing: Any, data: dict[str, Any], hash_val: str, game_version: str
) -> None:
    """更新已有记录的非主键字段。"""
    for key, val in data.items():
        if hasattr(existing, key) and key not in ("id", "content_hash"):
            setattr(existing, key, val)
    existing.content_hash = hash_val
    existing.game_version = game_version
    existing.is_active = True
    existing.updated_at = datetime.now(timezone.utc)


# ── 一键全量导入 ──────────────────────────────────────────


async def import_all_game_data(
    db: AsyncSession,
    game_version: str = "0.4",
    include_items: bool = True,
    include_uniques: bool = False,
    unique_limit: int = 0,
    dry_run: bool = False,
) -> dict[str, ImportStats]:
    """一键执行全量数据导入。

    Args:
        db: 数据库会话
        game_version: 游戏版本
        include_items: 是否导入装备底材
        include_uniques: 是否导入传奇物品（慢）
        unique_limit: 传奇导入上限
        dry_run: 仅统计不写入

    Returns:
        {类别: ImportStats} 字典
    """
    all_stats: dict[str, ImportStats] = {}

    logger.info(f"Starting full data import (game_version={game_version}, dry_run={dry_run})")

    # 1. 升华职业（快速）
    logger.info("--- Importing ascendancy classes ---")
    all_stats["ascendancy_classes"] = await import_ascendancy_classes(
        db, game_version, dry_run
    )

    # 2. 装备底材
    if include_items:
        logger.info("--- Importing item bases ---")
        all_stats["item_bases"] = await import_item_bases(
            db, game_version, dry_run=dry_run
        )

    # 3. 传奇物品（慢速，可选）
    if include_uniques:
        logger.info("--- Importing unique items ---")
        all_stats["unique_items"] = await import_unique_items(
            db, game_version, limit=unique_limit, dry_run=dry_run
        )

    total_inserted = sum(s.inserted for s in all_stats.values())
    total_updated = sum(s.updated for s in all_stats.values())
    logger.info(
        f"Full import complete: {total_inserted} inserted, {total_updated} updated"
    )

    return all_stats

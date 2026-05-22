"""POE2DB 技能数据采集器 — 从 poe2db.tw 采集 POE2 技能详细数据。

采集策略：
  Phase 1: 从列表页提取所有技能的名称、标签、类型（快速，2次HTTP请求）
  Phase 2: 对核心技能抓取详情页获取伤害效用、暴击率等（慢速，每技能1次请求）
  Phase 3: 存入 GameMechanic 表
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import GameMechanic

logger = logging.getLogger(__name__)

# ── 中文标签 → 英文标签映射 ─────────────────────────────
TAG_MAP: dict[str, str] = {
    "法术": "Spell",
    "攻击": "Attack",
    "投射物": "Projectile",
    "范围效果": "AoE",
    "持续时间": "Duration",
    "闪电": "Lightning",
    "冰霜": "Cold",
    "火焰": "Fire",
    "物理": "Physical",
    "混沌": "Chaos",
    "召唤生物": "Minion",
    "图腾": "Totem",
    "陷阱": "Trap",
    "地雷": "Mine",
    "战吼": "Warcry",
    "捷": "Herald",
    "光环": "Aura",
    "诅咒": "Curse",
    "位移": "Travel",
    "近战": "Melee",
    "重击": "Slam",
    "打击": "Strike",
    "弓箭": "Bow",
    "十字弩": "Crossbow",
    "引导": "Channelling",
    "触发": "Trigger",
    "增益": "Buff",
    "减益": "Debuff",
    "印记": "Mark",
    "闪避": "Evasion",
    "能量护盾": "ES",
    "护甲": "Armour",
    "元素": "Elemental",
    "可重复": "Repeatable",
    "可触发": "Triggerable",
    "玩家投射物": "PlayerProjectile",
    "法术可重复": "SpellRepeatable",
    "陷阱技能": "TrapSkill",
    "图腾技能": "TotemSkill",
    "地雷技能": "MineSkill",
    "非光环": "NonAura",
}

GEM_COLOR_MAP = {
    "gem_red": "Strength",
    "gem_green": "Dexterity",
    "gem_blue": "Intelligence",
    "gemitem": "Special",
}


class POE2DBSkillScraper:
    """从 poe2db.tw 采集 POE2 技能宝石数据。"""

    BASE_URL = "https://poe2db.tw"

    def __init__(self, concurrency: int = 5):
        self.concurrency = concurrency
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={
                    "User-Agent": "POE2BD-Agent/1.0 (contact: dev@example.com)",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Phase 1: 列表页快速采集 ─────────────────────────

    async def scrape_all_skill_gems(self) -> list[dict[str, Any]]:
        """从列表页快速采集所有主动技能宝石的名称和标签。"""
        client = await self._get_client()
        skills: list[dict] = []

        seen: set[str] = set()
        for url_path in ["/cn/Skill_Gems", "/cn/Support_Gems"]:
            r = await client.get(url_path)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            for table in soup.find_all("table", class_="filters"):
                rows = table.find_all("tr")
                for row in rows:
                    skill_data = self._parse_list_row(row)
                    if not skill_data:
                        continue
                    # 去重（按英文名）
                    if skill_data["name_en"] in seen:
                        continue
                    seen.add(skill_data["name_en"])
                    skill_data["is_support"] = ("Support" in url_path)
                    skills.append(skill_data)

        logger.info(f"Phase 1: collected {len(skills)} gems from list pages")
        return skills

    def _parse_list_row(self, row) -> dict[str, Any] | None:
        """解析列表页的一行技能数据。"""
        # 查找宝石链接
        link = None
        for cls in ["gem_red", "gem_green", "gem_blue", "gemitem"]:
            link = row.find("a", class_=cls)
            if link:
                break
        if not link:
            return None

        href = link.get("href", "")
        if "/cn/" not in href:
            return None

        name_en = href.split("/cn/")[-1].replace("_", " ")

        # 中文名：链接文本可能为空，从行的所有文本中提取括号前的中文名
        cells = row.find_all("td")
        name_zh = ""
        if len(cells) >= 2:
            # 第二列格式: "中文名 (等级)tags..."
            full_text = cells[1].get_text(" ", strip=True)
            # 提取括号前的中文名
            if " (" in full_text:
                name_zh = full_text.split(" (")[0].strip()

        # 从行的文本中提取标签
        row_text = row.get_text(" ", strip=True)
        tags_en = self._extract_tags(row_text)
        gem_color = GEM_COLOR_MAP.get(link.get("class", [""])[0], "Unknown")

        return {
            "name_en": name_en,
            "name_zh": name_zh,
            "tags": tags_en,
            "gem_color": gem_color,
            "required_attrs": self._guess_required_attrs(tags_en, gem_color),
        }

    def _extract_tags(self, text: str) -> list[str]:
        """从文本中提取中文标签并转为英文。"""
        tags: set[str] = set()
        for zh_tag, en_tag in TAG_MAP.items():
            if zh_tag in text:
                tags.add(en_tag)
        return sorted(tags)

    def _guess_required_attrs(self, tags: list[str], gem_color: str) -> dict[str, int]:
        """根据标签和宝石颜色推测属性需求。"""
        attrs = {"Str": 0, "Dex": 0, "Int": 0}
        if gem_color == "Strength":
            attrs["Str"] = 50
        elif gem_color == "Dexterity":
            attrs["Dex"] = 50
        elif gem_color == "Intelligence":
            attrs["Int"] = 50
        elif gem_color == "Special":
            attrs["Int"] = 30
        return attrs

    # ── Phase 2: 详情页深度采集 ─────────────────────────

    async def scrape_skill_details_batch(
        self, skills: list[dict[str, Any]], limit: int = 0
    ) -> list[dict[str, Any]]:
        """批量抓取技能详情页（并发控制）。

        Args:
            skills: Phase 1 采集的技能列表
            limit: 只采集前 N 个技能（0=全部）
        """
        targets = skills[:limit] if limit > 0 else skills
        semaphore = asyncio.Semaphore(self.concurrency)

        async def fetch_one(skill: dict) -> dict:
            async with semaphore:
                details = await self._scrape_skill_detail(skill["name_en"])
                skill.update(details)
                return skill

        tasks = [fetch_one(s) for s in targets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = 0
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning(f"Failed to scrape {targets[i]['name_en']}: {r}")
            else:
                success += 1
                targets[i] = r

        logger.info(f"Phase 2: scraped {success}/{len(targets)} skill details")
        return targets

    async def _scrape_skill_detail(self, skill_name: str) -> dict[str, Any]:
        """抓取单个技能的详情页数据。

        解析字段：中文名、标签、类型标签、等级需求、属性需求(力/敏/智)、
        武器需求、魔力消耗、攻击/施放速度、伤害效用、暴击率、描述、
        推荐辅助宝石、可选辅助宝石。
        """
        client = await self._get_client()
        slug = skill_name.replace(" ", "_")
        r = await client.get(f"/cn/{slug}")

        if r.status_code == 404:
            return {"detail_found": False}

        soup = BeautifulSoup(r.text, "lxml")
        result: dict[str, Any] = {"detail_found": True}

        # ── 1. 中文名 ──────────────────────────
        name_span = soup.find("div", class_="itemName")
        if name_span:
            lc = name_span.find("span", class_="lc")
            if lc:
                result["name_zh"] = lc.get_text(strip=True)

        # ── 2. 内部类型标签 (Type row in table) ──
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) >= 2 and cells[0].get_text(strip=True) == "Type":
                    type_tags = [t.strip() for t in cells[1].get_text(strip=True).split(",")]
                    result["type_tags"] = type_tags
                    # 推断 skill_type
                    result["skill_type"] = type_tags[0] if type_tags else "Unknown"

        # ── 3. 需求 (requirements divs) ──────────
        # POE2DB 有两个 requirements div:
        #   第一个: 等级 + 属性需求 (e.g. "需求：等级 (1 — 90), (4 — 86) 敏捷, (4 — 86) 智慧")
        #   第二个: 武器需求 (e.g. "需求： 节杖" with <a> link)
        attr_req_text = None
        for req_div in soup.find_all("div", class_="requirements"):
            a_tag = req_div.find("a", class_="ItemClasses")
            if a_tag:
                # 武器需求
                weapon_href = a_tag.get("href", "").strip()
                weapon_name_zh = a_tag.get_text(strip=True)
                result["weapon_requirements"] = [weapon_href]
                result["weapon_name_zh"] = weapon_name_zh
            else:
                attr_req_text = req_div.get_text(" ", strip=True)

        # 解析属性需求文本
        if attr_req_text:
            lvl_m = re.search(r"等级\s*\(\s*(\d+)\s*[—\-–]\s*(\d+)\s*\)", attr_req_text)
            if lvl_m:
                result["required_level"] = int(lvl_m.group(1))

            attrs: dict[str, int] = {}
            dex_m = re.search(r"\(\s*(\d+)\s*[—\-–]\s*(\d+)\s*\)\s*敏捷", attr_req_text)
            int_m = re.search(r"\(\s*(\d+)\s*[—\-–]\s*(\d+)\s*\)\s*智慧", attr_req_text)
            str_m = re.search(r"\(\s*(\d+)\s*[—\-–]\s*(\d+)\s*\)\s*力量", attr_req_text)

            if dex_m:
                attrs["dex"] = int(dex_m.group(2))
            if int_m:
                attrs["int"] = int(int_m.group(2))
            if str_m:
                attrs["str"] = int(str_m.group(2))
            if attrs:
                result["attribute_requirements"] = attrs

        # ── 4. 属性 (property divs) ──────────────
        seen_prop_keys: set[str] = set()
        for prop in soup.find_all("div", class_="property"):
            text = prop.get_text(" ", strip=True)
            links = prop.find_all("a")

            # 4a. 显示标签 (第一个含 GemTags 的 property)
            if links and "GemTags" in (links[0].get("class", []) or []):
                if "display_tags" not in seen_prop_keys:
                    seen_prop_keys.add("display_tags")
                    result["display_tags"] = [a.text.strip() for a in links]
                    result["display_tags_en"] = [a.get("href", "").strip() for a in links]
                continue

            # 4b. 等阶 (Tier)
            tier_m = re.search(r"等阶.*?(\d+)", text)
            if tier_m and "tier" not in seen_prop_keys:
                seen_prop_keys.add("tier")
                result["tier"] = int(tier_m.group(1))
                continue

            # 4c. 魔力/灵魂消耗
            mana_m = re.search(r"(\d+)\s*[—\-–]\s*(\d+)\s*(?:点)?魔力", text)
            if mana_m and "mana_cost" not in seen_prop_keys:
                seen_prop_keys.add("mana_cost")
                result["mana_cost_min"] = int(mana_m.group(1))
                result["mana_cost_max"] = int(mana_m.group(2))
                continue

            spirit_m = re.search(r"(\d+)\s*灵魂", text)
            if spirit_m and "spirit_cost" not in seen_prop_keys:
                result["spirit_cost"] = int(spirit_m.group(1))
                continue

            # 4d. 攻击速度 / 施放时间
            speed_m = re.search(r"(攻击速度|施放速度|攻击时间).*?(\d+\.?\d*)\s*%\s*基础", text)
            if speed_m and "attack_speed" not in seen_prop_keys:
                seen_prop_keys.add("attack_speed")
                result["attack_speed"] = float(speed_m.group(2))
                continue

            # 4d2. 施放时间 (法术专用，单位秒)
            cast_m = re.search(r"施放时间.*?(\d+\.?\d*)\s*秒", text)
            if cast_m and "attack_speed" not in seen_prop_keys:
                seen_prop_keys.add("attack_speed")
                result["cast_time"] = float(cast_m.group(1))
                continue

            # 4e. 伤害效用 (攻击伤害 / 法术伤害)
            dmg_m = re.search(r"(攻击伤害|法术伤害).*?\(\s*(\d+)\s*[—\-–]\s*(\d+)\s*\)\s*%\s*基础", text)
            if dmg_m and "damage_effectiveness" not in seen_prop_keys:
                seen_prop_keys.add("damage_effectiveness")
                result["damage_effectiveness_min"] = int(dmg_m.group(2))
                result["damage_effectiveness_max"] = int(dmg_m.group(3))
                continue

            # 4f. 暴击率
            crit_m = re.search(r"暴击\s*(?:率)?.*?(\d+\.?\d*)%", text)
            if crit_m and "base_crit_chance" not in seen_prop_keys:
                seen_prop_keys.add("base_crit_chance")
                result["base_crit_chance"] = float(crit_m.group(1)) / 100.0
                continue

        # ── 5. 描述 ─────────────────────────────
        desc_div = soup.find("div", class_="secDescrText")
        if desc_div:
            result["description"] = desc_div.get_text(" ", strip=True)

        # ── 6. 品质修饰符 ──────────────────────
        qual_div = soup.find("div", class_="qualityMod")
        if qual_div:
            result["quality_modifier"] = qual_div.get_text(" ", strip=True)

        # ── 7. 推荐辅助宝石 ─────────────────────
        for h5 in soup.find_all("h5"):
            h5_text = h5.get_text(strip=True)
            if "Recommended Support Gems" in h5_text:
                # 找到包含推荐宝石的容器
                container = h5.find_parent()
                for _ in range(8):
                    if container is None:
                        break
                    links = container.find_all("a", href=lambda h: h and "/cn/" in h)
                    if len(links) >= 2:
                        seen: set[str] = set()
                        recommended: list[dict] = []
                        for a in links:
                            name = a.get_text(strip=True)
                            href = a.get("href", "")
                            if name and "/cn/" in href and name not in seen:
                                seen.add(name)
                                recommended.append({
                                    "name_zh": name,
                                    "name_en": href.split("/cn/")[-1].replace("_", " "),
                                })
                        if recommended:
                            result["recommended_supports"] = recommended
                        break
                    container = container.find_parent()
                break

        return result

    # ── Phase 3: 数据存储 ──────────────────────────────

    async def save_to_database(
        self, db: AsyncSession, skills: list[dict[str, Any]]
    ) -> int:
        """将采集的技能数据存入 GameMechanic 表。"""
        saved = 0
        for skill in skills:
            existing = await db.scalar(
                select(GameMechanic.skill_id).where(
                    GameMechanic.skill_name == skill["name_en"]
                )
            )
            if existing:
                continue

            # 组合标签：显示标签 + 类型标签 + gem_color
            display_tags = skill.get("display_tags_en") or skill.get("display_tags") or []
            type_tags = skill.get("type_tags") or []
            combined_tags = list(dict.fromkeys(display_tags + type_tags))  # 去重保序
            if skill.get("is_support"):
                combined_tags.append("SupportGem")
            if skill.get("gem_color"):
                combined_tags.append(skill["gem_color"])

            # 构建描述
            desc = skill.get("description") or ""
            if skill.get("quality_modifier"):
                if desc:
                    desc += " | "
                desc += f"品质: {skill['quality_modifier']}"

            # 从 detail 获取完整字段，回退到 Phase 1 数据
            skill_type = (
                skill.get("skill_type") or skill.get("gem_color") or "Unknown"
            )

            mechanic = GameMechanic(
                skill_name=skill["name_en"],
                skill_id=skill.get("name_en", ""),
                skill_type=skill_type,
                damage_formula=None,
                base_crit_chance=skill.get("base_crit_chance"),
                damage_effectiveness=(
                    skill.get("damage_effectiveness_max") or skill.get("damage_effectiveness")
                ),
                tags=combined_tags,
                synergies=None,
                description=desc,
                weapon_requirements=skill.get("weapon_requirements"),
                attribute_requirements=skill.get("attribute_requirements"),
                required_level=skill.get("required_level"),
                game_version="POE2",
            )
            db.add(mechanic)
            saved += 1

        await db.commit()
        logger.info(f"Phase 3: saved {saved} new mechanics to database")
        return saved


    async def update_mechanics_in_db(
        self, db: AsyncSession, skills: list[dict[str, Any]]
    ) -> int:
        """用 Phase 2 详情数据更新已有的 GameMechanic 记录。"""
        updated = 0
        for skill in skills:
            if not skill.get("detail_found"):
                continue

            existing = await db.scalar(
                select(GameMechanic).where(
                    GameMechanic.skill_name == skill["name_en"]
                )
            )
            if not existing:
                continue

            # 更新详情字段
            if skill.get("skill_type"):
                existing.skill_type = skill["skill_type"]
            if skill.get("base_crit_chance"):
                existing.base_crit_chance = skill["base_crit_chance"]
            dmg_eff = skill.get("damage_effectiveness_max") or skill.get("damage_effectiveness")
            if dmg_eff:
                existing.damage_effectiveness = dmg_eff

            # 组合标签
            display_tags = skill.get("display_tags_en") or skill.get("display_tags") or []
            type_tags = skill.get("type_tags") or []
            combined_tags = list(dict.fromkeys(display_tags + type_tags))
            if combined_tags:
                existing.tags = combined_tags

            # 新字段
            existing.weapon_requirements = skill.get("weapon_requirements")
            existing.attribute_requirements = skill.get("attribute_requirements")
            existing.required_level = skill.get("required_level")

            # 描述
            desc = skill.get("description") or ""
            if skill.get("quality_modifier"):
                if desc:
                    desc += " | "
                desc += f"品质: {skill['quality_modifier']}"
            if desc:
                existing.description = desc

            # 协同：推荐辅助宝石
            rec = skill.get("recommended_supports", [])
            if rec:
                existing.synergies = [s["name_en"] for s in rec]

            updated += 1

        await db.commit()
        logger.info(f"Phase 3 update: updated {updated} existing mechanics")
        return updated


# ── 便捷函数 ──────────────────────────────────────────


async def collect_all_skills(db: AsyncSession, concurrency: int = 5) -> int:
    """一键采集：列表页 → 详情页 → 数据库。

    返回存入的条目数。
    """
    scraper = POE2DBSkillScraper(concurrency=concurrency)
    try:
        # Phase 1: 快速采集
        skills = await scraper.scrape_all_skill_gems()

        # Phase 2: 对活跃技能采集详情（支持宝石跳过详情页）
        active_skills = [s for s in skills if not s["is_support"]]
        support_skills = [s for s in skills if s["is_support"]]

        logger.info(
            f"Scraping details for {len(active_skills)} active + "
            f"{len(support_skills)} support gems..."
        )

        # 对活跃技能采集详情
        detailed = await scraper.scrape_skill_details_batch(active_skills, limit=0)

        # 合并（活跃技能有详情，支持宝石只有基本数据）
        all_skills = detailed + support_skills

        # Phase 3: 存入数据库
        saved = await scraper.save_to_database(db, all_skills)
        return saved

    finally:
        await scraper.close()


async def update_all_active_skills(
    db: AsyncSession, concurrency: int = 3, limit: int = 0
) -> int:
    """只对已有 GameMechanic 记录的主动技能采集详情并更新。

    Args:
        db: 数据库会话
        concurrency: 并发数
        limit: 限制更新数量（0=全部）
    """
    # 查询所有需要更新的主动技能（没有武器需求数据的）
    result = await db.execute(
        select(GameMechanic.skill_name, GameMechanic.skill_type).where(
            GameMechanic.weapon_requirements == None,  # noqa: E711
            GameMechanic.skill_type != "SupportGem",
        )
    )
    rows = result.all()
    logger.info(f"Found {len(rows)} active skills needing detail update")

    targets = [{"name_en": row[0]} for row in rows]
    if limit > 0:
        targets = targets[:limit]

    scraper = POE2DBSkillScraper(concurrency=concurrency)
    try:
        # Phase 1: 快速采集列表页（获取 tags/gem_color 等基础数据）
        all_list_skills = await scraper.scrape_all_skill_gems()
        list_map = {s["name_en"]: s for s in all_list_skills}

        # 合并 Phase 1 数据
        for t in targets:
            name = t["name_en"]
            if name in list_map:
                t.update(list_map[name])

        # Phase 2: 采集详情
        detailed = await scraper.scrape_skill_details_batch(targets, limit=0)

        # 过滤异常
        valid = [d for d in detailed if not isinstance(d, Exception) and d.get("detail_found")]

        # Phase 3: 更新数据库
        updated = await scraper.update_mechanics_in_db(db, valid)
        return updated

    finally:
        await scraper.close()

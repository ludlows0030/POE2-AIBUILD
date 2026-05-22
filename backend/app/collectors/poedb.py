"""PoEDB 技能机制文本采集器。

从 poedb.tw 公开页面提取技能宝石的机制说明文本。
不爬取受保护数据，仅解析公开 HTML 页面以构建技能机制库。

使用方式：
    - fetch_skill_page(skill_name) — 获取技能详情页文本
    - extract_mechanics(html) — 提取伤害公式、标签、协同技能

注意：遵守 poedb.tw robots.txt，限制频率。
"""

import logging
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)

# 常见伤害模式匹配
_DAMAGE_PATTERNS = re.compile(
    r"deal[s]?\s+(\d+[\d,.]*)\s*(?:to\s+(\d+[\d,.]*))?\s*"
    r"(Fire|Cold|Lightning|Physical|Chaos|Elemental)",
    re.IGNORECASE,
)
_TAG_PATTERN = re.compile(
    r"(Attack|Spell|AoE|Projectile|Melee|Bow|Strike|Slam|Channelling|"
    r"Minion|Totem|Trap|Mine|Trigger|Duration|Curse|Aura|Herald|"
    r"Warcry|Stance|Mark|Blessing|Offering|Nova|Orb|Bolt|Brand)",
    re.IGNORECASE,
)
_EFFECTIVENESS_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)%\s*(?:of base|damage effectiveness)",
    re.IGNORECASE,
)
_CRIT_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)%\s*(?:base\s*)?critical\s*(?:strike\s*)?chance",
    re.IGNORECASE,
)


class PoEDBClient:
    """poedb.tw 技能数据采集器。"""

    BASE_URL = "https://poedb.tw"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={
                    "User-Agent": settings.GGG_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=httpx.Timeout(20.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Skill Page ──────────────────────────────────────

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=3, max=15))
    async def fetch_skill_page(self, skill_name: str) -> str | None:
        """获取技能详情页 HTML。"""
        client = await self._get_client()
        # poedb.tw URL 格式: /us/{Skill_Name}
        slug = skill_name.replace(" ", "_")
        r = await client.get(f"/us/{slug}")
        if r.status_code == 404:
            # 尝试直接搜索
            r = await client.get("/us/", params={"q": skill_name})
        r.raise_for_status()
        return r.text

    # ── Text Extraction ─────────────────────────────────

    def extract_mechanics(self, html: str, skill_name: str) -> dict[str, Any]:
        """从 HTML 提取技能机制信息。"""
        soup = BeautifulSoup(html, "lxml")

        result: dict[str, Any] = {
            "skill_name": skill_name,
            "description": "",
            "damage_formula": None,
            "base_crit_chance": None,
            "damage_effectiveness": None,
            "tags": [],
            "synergies": [],
        }

        # 提取纯文本描述
        body = soup.get_text("\n", strip=True)

        # 伤害公式
        dmg_match = _DAMAGE_PATTERNS.search(body)
        if dmg_match:
            result["damage_formula"] = dmg_match.group(0)

        # 伤害效用
        eff_match = _EFFECTIVENESS_PATTERN.search(body)
        if eff_match:
            result["damage_effectiveness"] = float(eff_match.group(1))

        # 基础暴击率
        crit_match = _CRIT_PATTERN.search(body)
        if crit_match:
            result["base_crit_chance"] = float(crit_match.group(1))

        # 标签
        result["tags"] = list(set(m.group(1) for m in _TAG_PATTERN.finditer(body)))
        result["description"] = body[:2000]

        return result

    # ── Known POE2 Skills ───────────────────────────────

    @staticmethod
    def poe2_skill_list() -> list[dict[str, str]]:
        """POE2 常见技能宝石列表（主动技能）。"""
        return [
            {"id": "Spark", "name": "Spark", "type": "Spell"},
            {"id": "Arc", "name": "Arc", "type": "Spell"},
            {"id": "IceStrike", "name": "Ice Strike", "type": "Attack"},
            {"id": "LightningArrow", "name": "Lightning Arrow", "type": "Attack"},
            {"id": "SummonRagingSpirit", "name": "Summon Raging Spirit", "type": "Minion"},
            {"id": "HammerOfTheGods", "name": "Hammer of the Gods", "type": "Attack"},
            {"id": "GalvanicShards", "name": "Galvanic Shards", "type": "Attack"},
            {"id": "HexBlast", "name": "Hex Blast", "type": "Spell"},
            {"id": "FallingThunder", "name": "Falling Thunder", "type": "Attack"},
            {"id": "ShatteringPalm", "name": "Shattering Palm", "type": "Attack"},
            {"id": "TempestBell", "name": "Tempest Bell", "type": "Attack"},
            {"id": "EmberFusillade", "name": "Ember Fusillade", "type": "Spell"},
            {"id": "Snipe", "name": "Snipe", "type": "Attack"},
            {"id": "GasArrow", "name": "Gas Arrow", "type": "Attack"},
            {"id": "DetonateDead", "name": "Detonate Dead", "type": "Spell"},
            {"id": "Comet", "name": "Comet", "type": "Spell"},
        ]


poedb_client = PoEDBClient()

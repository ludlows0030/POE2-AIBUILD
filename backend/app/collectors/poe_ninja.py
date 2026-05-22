"""poe.ninja 社区数据采集器。

poe.ninja 汇总 GGG OAuth API 的角色数据，是 POE2 BD 数据的主要社区来源。
经济数据有内部 API（/api/data/*），BD 数据需从网页提取。

参考: https://poe.ninja/posts/dawn-of-the-hunt
"""

import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)


class PoeNinjaClient:
    """poe.ninja 数据客户端。

    提供两个层面的数据：
    1. 经济数据 — 内部 API（货币/装备/技能宝石价格）
    2. BD 数据 — 网页抓取（builds 页面，需要 Playwright/BS4）
    """

    BASE_URL = "https://poe.ninja"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={
                    "User-Agent": settings.GGG_USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Economy API (semi-public) ───────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def _get_data(self, path: str, league: str) -> dict[str, Any]:
        client = await self._get_client()
        r = await client.get(f"/api/data/{path}", params={"league": league, "type": league})
        r.raise_for_status()
        return r.json()

    async def fetch_currency_overview(self, league: str) -> dict[str, Any]:
        """获取货币市场价格概览。"""
        return await self._get_data("currencyoverview", league)

    async def fetch_item_overview(self, league: str) -> dict[str, Any]:
        """获取物品市场价格概览。"""
        return await self._get_data("itemoverview", league)

    async def fetch_skill_gem_overview(self, league: str) -> dict[str, Any]:
        """获取技能宝石价格概览。"""
        return await self._get_data("skillgemoverview", league)

    async def fetch_unique_accessory_overview(self, league: str) -> dict[str, Any]:
        """获取暗金饰品价格。"""
        return await self._get_data("uniqueaccessoryoverview", league)

    # ── Builds URL (UI scraping needed) ─────────────────

    @staticmethod
    def builds_url(league: str | None = None, sort: str = "dps") -> str:
        """获取 BD 列表页面的 URL（需浏览器渲染）。"""
        league_slug = league or "Challenge"
        return f"https://poe.ninja/poe2/builds?league={league_slug}&sort={sort}"

    @staticmethod
    def passive_tree_url() -> str:
        """POE2 天赋树 SVG URL。"""
        return "https://poe.ninja/poe2/passive-skill-tree"


poe_ninja_client = PoeNinjaClient()

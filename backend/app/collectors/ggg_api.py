"""POE2 GGG API 异步客户端。

注意：截至 2026-05，POE2 官方 API 覆盖有限：
  - /league 支持 realm=poe2（联赛元数据）
  - Ladder API 为 PoE1 only，POE2 暂无
  - 角色详情需 OAuth 或 POESESSID（非官方路径）
  - Trade API 支持 POE2

参考：
  https://www.pathofexile.com/developer/docs/reference
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)


class GGGClient:
    """POE2 数据采集客户端。

    三层接入：
    1. api.pathofexile.com — 公共 API（league 元数据、trade）
    2. www.pathofexile.com — Web API（需 POESESSID Cookie，角色详情）
    3. poe.ninja / pobb.in — 社区数据源（Ladder 替代方案）
    """

    def __init__(self) -> None:
        self._pub_client: httpx.AsyncClient | None = None   # api.pathofexile.com
        self._web_client: httpx.AsyncClient | None = None   # www.pathofexile.com

    async def close(self) -> None:
        for c in (self._pub_client, self._web_client):
            if c:
                await c.aclose()
        self._pub_client = None
        self._web_client = None

    # ── Client builders ──────────────────────────────────

    async def _get_pub_client(self) -> httpx.AsyncClient:
        if self._pub_client is None:
            self._pub_client = httpx.AsyncClient(
                base_url="https://api.pathofexile.com",
                headers={
                    "User-Agent": settings.GGG_USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=httpx.Timeout(30.0),
            )
        return self._pub_client

    async def _get_web_client(self) -> httpx.AsyncClient:
        if self._web_client is None:
            cookies = {"POESESSID": settings.GGG_POESESSID} if settings.GGG_POESESSID else None
            self._web_client = httpx.AsyncClient(
                base_url=settings.GGG_WEB_BASE_URL,
                headers={
                    "User-Agent": settings.GGG_USER_AGENT,
                    "Accept": "application/json",
                },
                cookies=cookies,
                timeout=httpx.Timeout(30.0),
            )
        return self._web_client

    # ── Request helpers (with retry) ─────────────────────

    @asynccontextmanager
    async def _pub_request(self, path: str, **params: Any) -> AsyncIterator[dict]:
        client = await self._get_pub_client()

        @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
        async def _do() -> dict:
            r = await client.get(path, params=params)
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After", "5")
                logger.warning(f"Rate limited (pub), retry after {retry_after}s")
                raise httpx.HTTPStatusError("Rate limited", request=r.request, response=r)
            r.raise_for_status()
            return r.json()

        yield await _do()

    @asynccontextmanager
    async def _web_request(self, path: str, **params: Any) -> AsyncIterator[dict]:
        client = await self._get_web_client()

        @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30), reraise=True)
        async def _do() -> dict:
            r = await client.get(path, params=params)
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After", "5")
                logger.warning(f"Rate limited (web), retry after {retry_after}s")
                raise httpx.HTTPStatusError("Rate limited", request=r.request, response=r)
            r.raise_for_status()
            return r.json()

        yield await _do()

    # ── League Metadata (POE2, public) ───────────────────

    async def fetch_leagues(self) -> list[dict[str, Any]]:
        """获取 POE2 联赛列表。realm=poe2 返回 POE2 专属数据。"""
        async with self._pub_request("/league", realm="poe2") as data:
            return data if isinstance(data, list) else []

    async def get_current_league(self) -> str | None:
        """获取当前活跃 POE2 挑战联赛名称。"""
        leagues = await self.fetch_leagues()
        for lg in leagues:
            rules = lg.get("rules", []) if isinstance(lg.get("rules"), list) else []
            for rule in rules:
                if rule.get("id") == "Challenge" and not lg.get("endAt"):
                    return lg["id"]
        # Fallback: 第一个未结束的联赛
        for lg in leagues:
            if not lg.get("endAt"):
                return lg["id"]
        return None

    # ── Character Data (via POESESSID, development fallback) ──

    async def fetch_character_items(
        self, account_name: str, character_name: str
    ) -> dict[str, Any] | None:
        """获取角色装备（需 POESESSID）。"""
        try:
            async with self._web_request(
                "/character-window/get-items",
                accountName=account_name,
                character=character_name,
            ) as data:
                return data
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403, 404):
                return None
            raise

    async def fetch_character_passives(
        self, account_name: str, character_name: str
    ) -> dict[str, Any] | None:
        """获取角色天赋（需 POESESSID）。"""
        try:
            async with self._web_request(
                "/character-window/get-passive-skills",
                accountName=account_name,
                character=character_name,
            ) as data:
                return data
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403, 404):
                return None
            raise

    async def fetch_character_full(
        self, account_name: str, character_name: str
    ) -> dict[str, Any]:
        """并行获取角色装备 + 天赋。"""
        items_t = asyncio.create_task(self.fetch_character_items(account_name, character_name))
        passives_t = asyncio.create_task(self.fetch_character_passives(account_name, character_name))
        items, passives = await asyncio.gather(items_t, passives_t)
        return {
            "account_name": account_name,
            "character_name": character_name,
            "items": items,
            "passives": passives,
        }

    # ── Trade API (POE2, public) ─────────────────────────

    async def fetch_trade_leagues(self) -> list[dict[str, Any]]:
        """获取 Trade API 支持的联赛列表。"""
        async with self._pub_request("/trade/data/leagues", realm="poe2") as data:
            return data.get("result", [])

    async def fetch_trade_items(self) -> dict[str, Any]:
        """获取物品/词缀基础数据。"""
        async with self._pub_request("/trade/data/items") as data:
            return data

    async def fetch_trade_stats(self) -> dict[str, Any]:
        """获取物品静态统计数据。"""
        async with self._pub_request("/trade/data/stats") as data:
            return data


ggg_client = GGGClient()

"""pobb.in 社区数据采集器。

pobb.in 是 Path of Building 配置分享平台。
玩家可以上传 XML 格式的 PoB 配置获得分享链接。

参考: https://pobb.in
"""

import logging
import xml.etree.ElementTree as ET
from io import BytesIO
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings

logger = logging.getLogger(__name__)


class PobbInClient:
    """pobb.in PoB 配置分享客户端。

    支持的功能：
    - 获取热门/最近分享的 BD 列表
    - 下载 PoB XML 并解析出技能/天赋/装备
    """

    BASE_URL = "https://pobb.in"

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.BASE_URL,
                headers={
                    "User-Agent": settings.GGG_USER_AGENT,
                    "Accept": "application/json, text/plain, */*",
                },
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Build Share ─────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
    async def fetch_build_xml(self, build_id: str) -> str | None:
        """获取 PoB XML 配置内容。"""
        client = await self._get_client()
        # pobb.in 直接用 /{build_id} 提供 raw XML 或重定向
        r = await client.get(f"/{build_id}/raw", follow_redirects=True)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.text

    async def parse_build_xml(self, xml_content: str) -> dict[str, Any]:
        """解析 PoB XML 为结构化数据。"""
        tree = ET.parse(BytesIO(xml_content.encode("utf-8")))
        root = tree.getroot()

        build_data: dict[str, Any] = {
            "skills": [],
            "tree_nodes": [],
            "items": [],
            "stats": {},
            "bandit_choice": None,
            "ascendancy": None,
            "class": None,
            "level": None,
        }

        # 基础信息
        for elem in root.findall(".//Build"):
            build_data["level"] = int(elem.get("level", 0))
            build_data["class"] = elem.get("className", "")
            build_data["ascendancy"] = elem.get("ascendClassName", "")
            build_data["bandit_choice"] = elem.get("bandit", "")

        # 技能宝石
        for skill in root.findall(".//Skill"):
            build_data["skills"].append({
                "name": skill.get("nameSpec", ""),
                "skill_id": skill.get("skillId", ""),
                "gem_group": skill.get("gemGroup", ""),
                "enabled": skill.get("enabled", "false") == "true",
                "slot": skill.get("slot", ""),
            })

        # 天赋节点
        for spec in root.findall(".//TreeSpec"):
            nodes_str = spec.get("nodes", "")
            if nodes_str:
                build_data["tree_nodes"] = [
                    n for n in nodes_str.split(",") if n.strip()
                ]

        # 装备物品
        for item in root.findall(".//Item"):
            build_data["items"].append({
                "name": item.get("name", ""),
                "base_type": item.get("baseType", ""),
                "slot": item.get("slot", ""),
            })

        return build_data


pobb_client = PobbInClient()

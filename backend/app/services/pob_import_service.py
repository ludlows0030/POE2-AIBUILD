"""PoB/pobb.in BD 导入服务。

支持两种导入方式：
  1. pobb.in 链接 — 自动拉取 XML → 解析 → 存储
  2. PoB 粘贴码 — 直接解析 XML 文本 → 存储
"""

import base64
import logging
import re
import zlib
from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.models.base import Character, SkillGroup
from app.parser.pob_parser import PoBParser

logger = logging.getLogger(__name__)

# pobb.in URL 格式: https://pobb.in/{build_id}
_POBB_URL_RE = re.compile(r"pobb\.in/([a-zA-Z0-9_-]+)")


class PoBImportService:
    """PoB BD 导入编排器。"""

    def __init__(self, source: str = "pobb.in_import"):
        self.parser = PoBParser(source=source)

    # ── pobb.in 导入 ────────────────────────────────────

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def _fetch_pobb_xml(self, build_id: str) -> str | None:
        """从 pobb.in 获取并解码 PoB XML。"""
        url = f"https://pobb.in/{build_id}/raw"
        async with httpx.AsyncClient(
            headers={"User-Agent": settings.GGG_USER_AGENT},
            follow_redirects=True,
            timeout=httpx.Timeout(15.0),
        ) as client:
            r = await client.get(url)
            if r.status_code == 404 or "not found" in r.text.lower():
                return None
            r.raise_for_status()
            raw = r.text.strip()

        # 判定是否为 base64 压缩格式（不以 < 开头）
        if not raw.startswith("<") and not raw.startswith("<?xml"):
            raw = "".join(raw.split())
            try:
                pad = 4 - len(raw) % 4 if len(raw) % 4 else 0
                decoded = base64.urlsafe_b64decode(raw + "=" * pad)
                return zlib.decompress(decoded).decode("utf-8")
            except Exception:
                logger.exception("Failed to decode PoB base64+zlib")
                return None

        return raw

    async def import_from_pobb_url(self, db: AsyncSession, url: str) -> UUID | None:
        """从 pobb.in URL 导入 BD。

        返回 character.id，失败返回 None。
        """
        match = _POBB_URL_RE.search(url)
        if not match:
            logger.error(f"Invalid pobb.in URL: {url}")
            return None

        build_id = match.group(1)
        logger.info(f"Importing pobb.in build: {build_id}")

        xml = await self._fetch_pobb_xml(build_id)
        if not xml:
            logger.warning(f"Build {build_id} not found or private")
            return None

        return await self.import_from_xml(
            db, xml, source_url=f"https://pobb.in/{build_id}"
        )

    async def import_from_pobb_id(self, db: AsyncSession, build_id: str) -> UUID | None:
        """从 pobb.in build ID 导入。"""
        return await self.import_from_pobb_url(
            db, f"https://pobb.in/{build_id}"
        )

    # ── PoB XML 直接导入 ────────────────────────────────

    async def import_from_xml(
        self, db: AsyncSession, xml_text: str, source_url: str | None = None
    ) -> UUID | None:
        """从 PoB XML 文本导入 BD。

        完整流程：解析 XML → 创建 Character + Skills + Tree + Items + Meta → 提交到 DB。
        """
        parser = PoBParser(source="pob_import", source_url=source_url)

        try:
            parsed = parser.parse_xml(xml_text)
        except Exception:
            logger.exception("Failed to parse PoB XML")
            return None

        if parser.warnings:
            for w in parser.warnings:
                logger.warning(f"PoB parse warning: {w}")

        character = parsed["character"]
        skills = parsed["skills"]
        tree = parsed["tree"]
        items = parsed["items"]
        meta = parsed["meta"]

        db.add(character)
        await db.flush()

        char_id = character.id

        # 关联外键
        for skill in skills:
            skill.character_id = char_id
        tree.character_id = char_id
        for item in items:
            item.character_id = char_id
        meta.character_id = char_id

        db.add_all(skills)
        db.add(tree)
        db.add_all(items)
        db.add(meta)

        await db.commit()

        logger.info(
            f"Imported build '{character.character_name}' ({character.char_class}) "
            f"lvl{character.level} — {len(skills)} skills, {len(tree.node_ids)} nodes, "
            f"{len(items)} items"
        )
        return char_id

    # ── 查询 ────────────────────────────────────────────

    async def list_imported_builds(
        self, db: AsyncSession, limit: int = 20
    ) -> list[dict]:
        """列出已导入的 BD 摘要。"""
        result = await db.execute(
            select(Character)
            .where(Character.account_name.like("%import%"))
            .order_by(Character.created_at.desc())
            .limit(limit)
        )
        characters = result.scalars().all()

        summaries: list[dict] = []
        for ch in characters:
            skill_count = await db.scalar(
                select(func.count(SkillGroup.id)).where(
                    SkillGroup.character_id == ch.id
                )
            )
            summaries.append({
                "id": str(ch.id),
                "name": ch.character_name,
                "class": ch.char_class,
                "ascendancy": ch.ascendancy,
                "level": ch.level,
                "skills": skill_count or 0,
                "league": ch.league,
            })
        return summaries


pob_import_service = PoBImportService()

"""M1→M2 数据采集流水线服务。

数据来源策略（按优先级）：
  1. GGG OAuth API — 官方角色数据（需注册 oauth@grindinggear.com）
  2. poe.ninja Builds — 社区聚合 BD（Web UI，需 Playwright 渲染）
  3. pobb.in — 玩家分享的 PoB 配置（XML 解析）
  4. PoEDB — 技能/词缀机制文本（HTML 解析）

当前开发阶段：数据模型和采集框架就绪，等待 OAuth 注册或使用社区源。
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.ggg_api import ggg_client
from app.collectors.pobb_in import pobb_client
from app.models.base import (
    BuildMeta,
    Character,
    EquipmentItem,
    PassiveTree,
    SkillGroup,
)
from app.parser.ggg_parser import (
    parse_build_meta,
    parse_character,
    parse_equipment,
    parse_passives,
    parse_skill_groups,
)

logger = logging.getLogger(__name__)


async def collect_league_info() -> list[dict]:
    """获取 POE2 联赛基础信息。"""
    return await ggg_client.fetch_leagues()


async def collect_from_pobb_build(db: AsyncSession, build_id: str) -> UUID | None:
    """从 pobb.in 导入单个 BD 配置。

    返回创建的 BuildMeta ID，失败返回 None。
    """
    xml = await pobb_client.fetch_build_xml(build_id)
    if not xml:
        logger.warning(f"Build {build_id} not found on pobb.in")
        return None

    build_data = await pobb_client.parse_build_xml(xml)
    if not build_data["skills"]:
        logger.warning(f"Build {build_id} has no skill data")
        return None

    # Create minimal character record
    character = Character(
        account_name=f"pobb.in/{build_id}",
        character_name=build_id,
        league="Unknown",
        level=build_data.get("level") or 1,
        char_class=build_data.get("class") or "Unknown",
        ascendancy=build_data.get("ascendancy"),
        last_updated=datetime.now(timezone.utc),
    )
    db.add(character)
    await db.flush()

    # Equipment from PoB items
    for item_data in build_data.get("items", []):
        item = EquipmentItem(
            character_id=character.id,
            slot=item_data.get("slot", "Unknown"),
            item_name=item_data.get("name", ""),
            base_type=item_data.get("base_type", ""),
            rarity="unique" if "unique" in item_data.get("name", "").lower() else "rare",
            raw_json=item_data,
        )
        db.add(item)

    # Skill groups from PoB skills
    for skill_data in build_data.get("skills", []):
        if skill_data.get("enabled"):
            skill = SkillGroup(
                character_id=character.id,
                active_skill_id=skill_data.get("skill_id", ""),
                active_skill_name=skill_data.get("name", ""),
                gem_group=skill_data.get("gem_group", ""),
            )
            db.add(skill)

    # Passive tree
    if build_data.get("tree_nodes"):
        passives = PassiveTree(
            character_id=character.id,
            node_ids=build_data["tree_nodes"],
        )
        db.add(passives)

    # Build metadata
    meta = BuildMeta(
        character_id=character.id,
        source="pobb.in",
        source_url=f"https://pobb.in/{build_id}",
        collected_at=datetime.now(timezone.utc),
        league_version="Unknown",
        tags=[],
        damage_types=[],
    )
    db.add(meta)

    await db.commit()
    logger.info(f"Imported build {build_id} from pobb.in → character {character.id}")
    return character.id


async def get_existing_character_ids(db: AsyncSession) -> set[UUID]:
    result = await db.execute(select(Character.id))
    return set(row[0] for row in result.fetchall())

"""M5 — BD 生成 API 端点。

POST /api/builds/generate  — 提交 BD 生成请求
POST /api/builds/validate  — 验证 BD 草案
POST /api/builds/format    — 格式化 BD 输出
GET  /api/builds/{id}      — 查询已生成的 BD
GET  /api/builds/           — 列出已生成的 BD
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.build_agent import build_agent
from app.config import settings
from app.database import get_db
from app.models.base import GeneratedBuild

router = APIRouter(prefix="/api/builds", tags=["builds"])


# ── Request/Response Schemas ─────────────────────────────


class GenerateRequest(BaseModel):
    user_request: str = Field(
        ...,
        min_length=5,
        max_length=1000,
        description="自然语言 BD 需求描述",
        examples=["我想玩一个电系法师，中等预算，能刷图也能打王"],
    )
    game_version: str = Field(default="3.26", description="POE2 版本号")


class BuildCardResponse(BaseModel):
    id: str | None = None
    build_name: str
    core_concept: str
    class_name: str = Field(alias="class")
    ascendancy: str
    ascendancy_nodes: list[str]
    skill_gems: dict[str, Any]
    passive_tree: dict[str, Any]
    equipment: dict[str, Any]
    key_mechanics: list[str]
    playstyle_notes: str
    estimated_dps: float | str
    estimated_budget_divines: float
    budget_tier: str
    confidence: float
    strengths: list[str]
    weaknesses: list[str]
    validation: dict[str, Any]
    damage_breakdown: dict[str, Any]
    reference_builds_count: int
    game_version: str

    model_config = {"populate_by_name": True}


class BuildListResponse(BaseModel):
    builds: list[dict[str, Any]]
    total: int


# ── Endpoints ────────────────────────────────────────────


@router.post("/generate", response_model=BuildCardResponse)
async def generate_build(
    req: GenerateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """生成 POE2 BD 方案。

    此端点调用 M4 Agent 推理引擎，执行完整的 6 步推理链：
    需求理解 → 参考搜索 → 协同分析 → BD 草案 → 验证 → 输出
    """
    result = await build_agent.generate(
        db=db,
        user_request=req.user_request,
        game_version=req.game_version,
    )

    if result.get("error") and result.get("confidence", 0) <= 0:
        raise HTTPException(status_code=500, detail=result.get("error", "Generation failed"))

    # 持久化生成的 BD
    try:
        saved = await _save_generated_build(db, req, result)
        result["id"] = str(saved.id)
    except Exception:
        # 存储失败不影响返回结果
        pass

    return result


@router.get("/{build_id}", response_model=BuildCardResponse)
async def get_generated_build(
    build_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """获取之前生成的 BD。"""
    build = await db.get(GeneratedBuild, build_id)
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")

    return {
        "id": str(build.id),
        "build_name": build.build_name,
        "class": "N/A",
        "core_concept": build.user_request or "",
        "ascendancy": "",
        "ascendancy_nodes": [],
        "skill_gems": build.skill_gems,
        "passive_tree": build.passive_tree,
        "equipment": build.equipment,
        "key_mechanics": [],
        "playstyle_notes": "",
        "estimated_dps": "N/A",
        "estimated_budget_divines": build.estimated_cost_max or 0,
        "budget_tier": "medium",
        "confidence": build.confidence,
        "strengths": [],
        "weaknesses": [],
        "validation": {
            "passed": build.validation_passed,
            "errors": build.validation_errors or [],
            "warnings": [],
        },
        "damage_breakdown": {},
        "reference_builds_count": 0,
        "game_version": build.game_version,
    }


@router.get("/", response_model=BuildListResponse)
async def list_generated_builds(
    db: AsyncSession = Depends(get_db),
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """列出已生成的 BD 历史。"""
    total = await db.scalar(select(func.count(GeneratedBuild.id)))
    result = await db.execute(
        select(GeneratedBuild)
        .order_by(GeneratedBuild.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    builds = result.scalars().all()

    return {
        "total": total or 0,
        "builds": [
            {
                "id": str(b.id),
                "build_name": b.build_name,
                "core_skill": b.core_skill,
                "confidence": b.confidence,
                "game_version": b.game_version,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in builds
        ],
    }


class ValidateRequest(BaseModel):
    build: dict[str, Any] = Field(..., description="BD 草案 JSON")


class FormatRequest(BaseModel):
    build: dict[str, Any] = Field(..., description="BD 数据")
    format: str = Field(default="json", description="输出格式: json / markdown / pob_xml")


@router.post("/validate")
async def validate_build_endpoint(req: ValidateRequest) -> dict[str, Any]:
    """验证 BD 草案的合法性（7 类校验规则）。"""
    from app.validation.rules import build_validator
    return build_validator.validate(req.build)


@router.post("/format")
async def format_build(req: FormatRequest) -> dict[str, Any]:
    """将 BD 数据格式化为指定输出格式。"""
    from app.validation.formatter import build_formatter

    fmt = req.format.lower()
    build = req.build

    if fmt == "markdown":
        content = build_formatter.to_markdown(build)
    elif fmt == "pob_xml":
        content = build_formatter.to_pob_xml(build)
    elif fmt == "summary":
        content = build_formatter.to_summary(build)
    else:
        content = build_formatter.to_api_response(build)

    if isinstance(content, str):
        return {"format": fmt, "content": content}
    return {"format": fmt, "content": content}


# ── Helpers ──────────────────────────────────────────────


async def _save_generated_build(
    db: AsyncSession,
    req: GenerateRequest,
    result: dict[str, Any],
) -> GeneratedBuild:
    """将生成的 BD 持久化到数据库。"""
    import json
    from datetime import datetime, timezone

    build = GeneratedBuild(
        build_name=result.get("build_name", "Unnamed"),
        user_request=req.user_request,
        core_skill=result.get("skill_gems", {}).get("active", [{}])[0].get("name", "Unknown")
        if result.get("skill_gems", {}).get("active")
        else "Unknown",
        damage_types=[result.get("class", "")],
        reasoning_chain=result,
        confidence=result.get("confidence", 0.5),
        estimated_cost_min=result.get("estimated_budget_divines", 0),
        estimated_cost_max=result.get("estimated_budget_divines", 0),
        skill_gems=result.get("skill_gems", {}),
        passive_tree=result.get("passive_tree", {}),
        equipment=result.get("equipment", {}),
        validation_passed=result.get("validation", {}).get("passed"),
        validation_errors=result.get("validation", {}).get("errors"),
        model_used=settings.DEEPSEEK_MODEL if settings.LLM_PROVIDER == "deepseek" else settings.ANTHROPIC_MODEL,
        game_version=req.game_version,
    )
    db.add(build)
    await db.commit()
    await db.refresh(build)
    return build

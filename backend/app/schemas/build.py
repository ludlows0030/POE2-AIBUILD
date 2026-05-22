from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class BuildSearchRequest(BaseModel):
    keyword: str | None = None
    playstyle: str | None = None
    damage_type: str | None = None
    min_level: int | None = None
    max_budget_divines: float | None = None
    limit: int = 10


class BuildGenerateRequest(BaseModel):
    """BD 生成请求 — 对应文档 §4.2 Step 1 意图解析。"""

    playstyle: str
    budget: str | None = None
    damage_type: str | None = None
    class_preference: str | None = None
    additional_notes: str | None = None


class BuildSummary(BaseModel):
    id: UUID
    build_name: str
    core_skill: str
    damage_types: list[str]
    confidence: float
    estimated_cost_min: float | None
    estimated_cost_max: float | None
    created_at: datetime


class BuildDetail(BuildSummary):
    skill_gems: dict
    passive_tree: dict
    equipment: dict
    reasoning_chain: dict
    validation_passed: bool | None
    validation_errors: list[str] | None


class ValidationResult(BaseModel):
    passed: bool
    errors: list[str] = []
    warnings: list[str] = []

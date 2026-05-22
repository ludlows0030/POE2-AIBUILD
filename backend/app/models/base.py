import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Character(Base):
    __tablename__ = "character"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    character_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    league: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    level: Mapped[int] = mapped_column(nullable=False)
    char_class: Mapped[str] = mapped_column(String(32), nullable=False)
    ascendancy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SkillGroup(Base):
    __tablename__ = "skill_group"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    character_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    active_skill_id: Mapped[str] = mapped_column(String(128), nullable=False)
    active_skill_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    support_gems: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    trigger_condition: Mapped[str | None] = mapped_column(String(512), nullable=True)
    gem_links: Mapped[int] = mapped_column(default=4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PassiveTree(Base):
    __tablename__ = "passive_tree"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    character_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    node_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    keystone_nodes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    mastery_choices: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ascendancy_nodes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    bandit_choice: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EquipmentItem(Base):
    __tablename__ = "equipment_item"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    character_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    slot: Mapped[str] = mapped_column(String(32), nullable=False)
    item_name: Mapped[str] = mapped_column(String(256), nullable=False)
    base_type: Mapped[str] = mapped_column(String(128), nullable=False)
    rarity: Mapped[str] = mapped_column(String(16), nullable=False)
    explicit_mods: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    implicit_mods: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    crafted_mods: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    enchant_mods: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    sockets: Mapped[int] = mapped_column(default=0)
    links: Mapped[int | None] = mapped_column(nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BuildMeta(Base):
    __tablename__ = "build_meta"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    character_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, unique=True, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    league_version: Mapped[str] = mapped_column(String(32), nullable=False)
    power_rating: Mapped[float | None] = mapped_column(nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    damage_types: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    playstyle: Mapped[str | None] = mapped_column(String(64), nullable=True)
    estimated_budget_divines: Mapped[float | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GameMechanic(Base):
    __tablename__ = "game_mechanic"

    id: Mapped[uuid.UUID] = mapped_column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    skill_name: Mapped[str] = mapped_column(String(256), nullable=False)
    skill_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    damage_formula: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    base_crit_chance: Mapped[float | None] = mapped_column(nullable=True)
    damage_effectiveness: Mapped[float | None] = mapped_column(nullable=True)
    trigger_conditions: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    synergies: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    game_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), onupdate=func.now(), nullable=True)


class GeneratedBuild(Base):
    __tablename__ = "generated_build"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    build_name: Mapped[str] = mapped_column(String(256), nullable=False)
    user_request: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    core_skill: Mapped[str] = mapped_column(String(128), nullable=False)
    damage_types: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    reasoning_chain: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    estimated_cost_min: Mapped[float | None] = mapped_column(nullable=True)
    estimated_cost_max: Mapped[float | None] = mapped_column(nullable=True)
    skill_gems: Mapped[dict] = mapped_column(JSONB, nullable=False)
    passive_tree: Mapped[dict] = mapped_column(JSONB, nullable=False)
    equipment: Mapped[dict] = mapped_column(JSONB, nullable=False)
    validation_passed: Mapped[bool | None] = mapped_column(nullable=True)
    validation_errors: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    model_used: Mapped[str] = mapped_column(String(64), nullable=False)
    game_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

"""M4 LangGraph 状态机 — POE2 BD 生成推理图。

实现需求文档 §4.2-§4.3 定义的 6 步推理链：
  understand → search_references → analyze_synergies → draft → validate → output

与标准 ReAct agent 的区别：
  - 推理步骤是结构化的，不是自由式的 tool-use 循环
  - 每步都有明确的输入/输出契约
  - validate 失败时自动回退到 draft 修正（最多 2 次）
"""

from __future__ import annotations

import json
from typing import Any, Literal

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from app.agents.llm_client import llm_client
from app.config import settings


# ── Agent State ──────────────────────────────────────────


class AgentState(BaseModel):
    """BD Agent 全局状态。流转图上的所有节点读写此状态。"""

    model_config = {"arbitrary_types_allowed": True}

    # 用户输入
    user_request: str = ""
    game_version: str = "Unknown"

    # Step 1 产出
    requirements: dict[str, Any] = Field(default_factory=dict)

    # Step 2 产出
    reference_builds: list[dict[str, Any]] = Field(default_factory=list)

    # Step 3 产出
    skill_mechanics: dict[str, Any] | None = None
    synergies: list[dict[str, Any]] = Field(default_factory=list)

    # Step 4 产出
    draft_build: dict[str, Any] = Field(default_factory=dict)

    # Step 5 产出
    validation_result: dict[str, Any] | None = None
    damage_result: dict[str, Any] | None = None

    # Step 6 产出
    final_output: str = ""
    confidence: float = 0.0

    # 控制字段
    retry_count: int = 0
    errors: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    poe2db_lookup_results: list[dict[str, Any]] = Field(default_factory=list)


# ── 节点定义 ─────────────────────────────────────────────


class BuildAgentNodes:
    """BD Agent 的 6 个推理节点 + 工具调用节点。

    LLM 调用通过 llm_client (DeepSeek/Anthropic 双后端) 统一接口。
    """

    # ── Node 1: Understand Requirements ──────────────────

    async def understand_requirements(self, state: AgentState) -> dict[str, Any]:
        """用 LLM 从用户自然语言中提取结构化约束。"""
        prompt = f"""从以下 POE2 玩家需求中提取结构化约束。只输出有效 JSON，不要解释。

玩家需求："{state.user_request}"

返回包含以下字段的 JSON：
- playstyle: 玩法风格，可选值 [spell_caster, bow_ranged, melee_strike, melee_slam, minion_summoner, crossbow_ranged, spear_melee, talisman_shapeshift, trap_mine, any]
- class_name: 职业英文名，可选值 [Sorceress, Ranger, Monk, Warrior, Witch, Mercenary, any]
- ascendancy: 建议的升华职业英文名，或 null
- damage_type: 伤害类型，可选值 [Fire, Cold, Lightning, Physical, Chaos, any]
- budget: 预算层级，可选值 [low, medium, high, unlimited]
- goal: 目标，可选值 [mapper, bosser, all_content, speed_farm, hardcore]
- core_skill_hint: 提到的技能英文名，无则为 null
- special_constraints: 特殊限制的字符串列表（如 SSF、特定传奇等）

示例输出：
{{"playstyle": "spell_caster", "class_name": "Sorceress", "ascendancy": "Stormweaver", "damage_type": "Lightning", "budget": "medium", "goal": "all_content", "core_skill_hint": "Spark", "special_constraints": []}}"""

        text = await llm_client.messages_create(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.3,
        )
        # 提取 JSON 块
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        try:
            requirements = json.loads(text)
        except json.JSONDecodeError:
            requirements = {
                "playstyle": "any",
                "class_name": "any",
                "damage_type": "any",
                "budget": "medium",
                "goal": "all_content",
                "core_skill_hint": None,
                "special_constraints": [],
            }

        return {"requirements": requirements}

    # ── Node 2: Search Reference Builds ───────────────────

    async def search_references(self, state: AgentState) -> dict[str, Any]:
        """查询参考 BD 锚点。此节点记录需要调用的工具及其参数，
        实际 DB 调用由 build_agent 编排器在节点间执行。"""
        req = state.requirements

        tool_call = {
            "tool": "query_builds_db",
            "args": {
                "playstyle": req.get("playstyle") if req.get("playstyle") != "any" else None,
                "damage_type": req.get("damage_type") if req.get("damage_type") != "any" else None,
                "class_name": req.get("class_name") if req.get("class_name") != "any" else None,
                "core_skill": req.get("core_skill_hint"),
                "limit": settings.MAX_REFERENCE_BUILDS,
            },
        }

        return {"tool_calls": [tool_call]}

    # ── Node 3: Analyze Synergies ─────────────────────────

    async def analyze_synergies(self, state: AgentState) -> dict[str, Any]:
        """分析候选技能的协同关系（PG 表 + Neo4j 图谱 + POE2DB 实时查询）。"""
        tool_calls: list[dict[str, Any]] = []

        # 从参考 BD 中提取候选技能
        candidate_skills: set[str] = set()
        for ref in state.reference_builds:
            for skill in ref.get("skills", []):
                candidate_skills.add(skill)

        # 如果用户指定了技能，优先查询
        hint = state.requirements.get("core_skill_hint")
        if hint:
            candidate_skills.add(hint)

        for skill in list(candidate_skills)[:5]:
            # PostgreSQL 工具
            tool_calls.append({
                "tool": "get_skill_mechanics",
                "args": {"skill_name": skill},
            })
            tool_calls.append({
                "tool": "search_synergies",
                "args": {"keyword": skill, "limit": 5},
            })
            # POE2DB 实时查询（获取权威技能数据作为补充）
            tool_calls.append({
                "tool": "poe2db_lookup",
                "args": {"term": skill, "lang": "cn"},
            })
            # Neo4j KG 工具
            tool_calls.append({
                "tool": "query_skill_synergies",
                "args": {"skill_name": skill, "limit": 10},
            })
            tool_calls.append({
                "tool": "query_keystone_for_skill",
                "args": {"skill_name": skill},
            })
            tool_calls.append({
                "tool": "query_ascendancy_for_skill",
                "args": {"skill_name": skill},
            })
            tool_calls.append({
                "tool": "query_affixes_for_skill",
                "args": {"skill_name": skill},
            })

        # 查询用户提到的特殊约束（传奇装备、核心天赋等）
        special_constraints = state.requirements.get("special_constraints", [])
        for constraint in special_constraints[:3]:
            # 尝试将约束作为 POE2DB 查询词
            tool_calls.append({
                "tool": "poe2db_lookup",
                "args": {"term": constraint, "lang": "cn"},
            })

        return {"tool_calls": tool_calls}

    # ── Node 4: Draft Build ───────────────────────────────

    async def draft_build(self, state: AgentState) -> dict[str, Any]:
        """LLM 综合所有信息，生成 BD 草案。"""
        # 组装上下文
        poe2db_data = state.poe2db_lookup_results or []
        context = {
            "requirements": state.requirements,
            "reference_builds": self._summarize_refs(state.reference_builds),
            "skill_mechanics": state.skill_mechanics,
            "synergies": state.synergies[:10] if state.synergies else [],
            "poe2db_data": poe2db_data,
            "previous_errors": state.validation_result.get("errors", []) if state.validation_result else [],
            "retry_count": state.retry_count,
        }

        prompt = f"""你正在设计一个 POE2 角色 BD。请基于以下上下文信息，创建一个完整的 BD 草案。

## 用户需求
{json.dumps(context['requirements'], ensure_ascii=False, indent=2)}

## 参考 BD（经过验证的原型）
{json.dumps(context['reference_builds'], ensure_ascii=False, indent=2)}

## 技能机制数据
{json.dumps(context['skill_mechanics'], ensure_ascii=False, indent=2) if context['skill_mechanics'] else '无机��制数据——请使用通用 POE2 知识。'}

## POE2DB 权威数据（实时查询结果）
{json.dumps(context['poe2db_data'], ensure_ascii=False, indent=2) if context['poe2db_data'] else '无 POE2DB 补充数据'}

## 已知协同关系
{json.dumps(context['synergies'], ensure_ascii=False, indent=2)}

## 上一次验证失败的错误（如果是重试阶段）
{json.dumps(context['previous_errors'], ensure_ascii=False, indent=2) if context['previous_errors'] else '无'}

---

请输出完整的 BD 草案 JSON。严格按照以下结构：

```json
{{
  "build_name": "有创意的 BD 名称（中文）",
  "core_concept": "2-3 句中文说明，解释该 BD 的核心机制和为什么成立",
  "class": "职业英文名 ClassName",
  "ascendancy": "升华职业英文名 AscendancyName",
  "ascendancy_nodes": ["升华节点1", "升华节点2", "升华节点3", "升华节点4"],
  "skill_gems": {{
    "active": [
      {{
        "name": "技能英文名 SkillName",
        "support_gems": ["辅助宝石1", "辅助宝石2", "辅助宝石3", "辅助宝石4", "辅助宝石5"],
        "role": "main_dps / mobility / aura / debuff / weapon_swap"
      }}
    ],
    "spirit_reservation": [
      {{"name": "光环或捷英文名", "spirit_cost": 30}}
    ]
  }},
  "passive_tree": {{
    "nodes": [
      "列出 5-10 个关键天赋群或重要节点（中文描述）",
      "不需要列出全部 120 点——只列出重要的路径决策"
    ],
    "keystones": ["核心天赋1（中文名+英文名）如果适用"],
    "mastery_choices": {{"天赋群名称（中文）": "专精选择（中文）"}}
  }},
  "equipment": {{
    "Weapon": "推荐武器类型及关键词缀（中文）",
    "Offhand": "盾牌/法器 或 null",
    "Helmet": "推荐传奇或稀有词缀（中文）",
    "BodyArmour": "推荐传奇或稀有词缀（中文）",
    "Gloves": "推荐传奇或稀有词缀（中文）",
    "Boots": "推荐传奇或稀有词缀（中文）",
    "Amulet": "推荐传奇或稀有词缀（中文）",
    "Ring1": "推荐传奇或稀有词缀（中文）",
    "Ring2": "推荐传奇或稀有词缀（中文）",
    "Belt": "推荐传奇或稀有词缀（中文）"
  }},
  "key_mechanics": [
    "核心机制 1 — 中文解释其交互原理",
    "核心机制 2 — 中文解释其交互原理"
  ],
  "playstyle_notes": "操作说明（中文）：技能循环、站位、增益维持、防御层利用",
  "estimated_budget_divines": 50,
  "strengths": ["优势 1（中文）", "优势 2（中文）"],
  "weaknesses": ["劣势 1（中文）", "劣势 2（中文）"]
}}
```

**关键规则（必须遵守）：**
1. **仅使用数据库中的真实技能**：技能名和辅助宝石名必须来自上方"技能机制数据"中列出的技能。如果某个技能不在数据库中，不要使用它。这是 POE2——不能使用 POE1 的技能。
2. **POE2 武器类型（根据 POE2DB 真实数据）**：
   技能决定武器需求，武器有属性需求但无职业限制。以下是当前版本(3.26)的武器→技能映射：
   **主流武器（技能完整）：**
   - 节杖/细杖(Quarterstaves) — 21个技能，需求敏捷+智慧，武僧打击技能（冰击 Ice Strike、风暴乱舞 Tempest Flurry、雷霆坠落 Falling Thunder 等）
   - 弓(Bows) — 24个技能，需求敏捷，游侠箭术技能（闪电箭矢 Lightning Arrow、毒爆箭 Poisonburst Arrow 等）
   - 十字弩(Crossbows) — 27个技能，需求敏捷+力量，佣兵弹药/榴弹技能
   - 单手锤(One Hand Maces) — 14个技能，需求力量，战士重击技能（七伤破 Boneshatter、震地 Earthquake 等）
   - 战矛(Spears) — 21个技能，需求敏捷，女猎手近战/投掷技能
   - 护符(Talismans) — 16个技能，需求力量+智力，德鲁伊变形技能
   **有物品但技能未完全实现（当前版本无进阶技能宝石）：**
   - 双手剑(Two Hand Swords) — 仅有 Sword Slash 基础攻击，剑的物品存在于 POE2 但无对应进阶主动技能宝石
   - 双手斧(Two Hand Axes) — 仅有 Axe Slash 基础攻击
   - 双手锤(Two Hand Maces) — 仅 2 个技能（Mace Strike + Supercharged Slam）
   - 爪(Claws)、匕首(Daggers)、连枷(Flails) — 仅有基础攻击，无进阶技能
   **施法武器（法术不限制武器类型）：**
   - 法杖(Wand)、权杖(Sceptre)、长杖(Staff) — 施法者属性武器。POE2 中法术技能只有属性需求（智力等），不绑定具体武器类型
   - 法器(Focus)、盾牌(Shield) — 副手
   **重要**: Quarterstaff(节杖) ≠ Staff(长杖)，这是两种完全不同的武器！Monk 用 Quarterstaff，施法者用 Staff。
3. **仅限 POE2 机制**：绝不引用 POE1 独有系统（神圣祝福 Divine Blessing、保留效能 reservation efficiency 等不存在于 POE2）。
4. **独立宝石连接**：POE2 中每个主动技能有自己独立的 5 个辅助宝石插槽。
5. **灵魂(Spirit)资源**：光环/捷/增益消耗灵魂，必须在 spirit_reservation 中列出。
6. **预算现实**：参考锚点 BD 的预算范围给出合理的神圣石估算。
7. **如果是重试**：务必修正上面列出的验证错误。
8. **描述用中文**：技能名用英文，但所有描述性文字、装备说明、天赋路径使用简体中文。
"""

        text = await llm_client.messages_create(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=settings.LLM_TEMPERATURE,
        )
        # 提取 JSON 块
        if "```" in text:
            parts = text.split("```")
            for i, part in enumerate(parts):
                if part.startswith("json") or part.startswith("{") and i % 2 == 1:
                    text = part
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip()
                    break

        try:
            draft = json.loads(text)
        except json.JSONDecodeError:
            # 尝试找到 JSON 边界
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                text = text[start:end]
            draft = json.loads(text)

        return {"draft_build": draft}

    # ── Node 5: Validate ─────────────────────────────────

    async def validate(self, state: AgentState) -> dict[str, Any]:
        """验证 BD 草案合法性 + 伤害估算。"""
        tool_calls: list[dict[str, Any]] = [
            {"tool": "validate_build", "args": {"build": state.draft_build}},
            {
                "tool": "calculate_damage",
                "args": {
                    "base_damage": self._estimate_base_damage(state),
                    "increased_damage": self._estimate_increased(state),
                    "more_multipliers": self._estimate_more_mults(state),
                    "crit_chance": self._estimate_crit(state),
                    "crit_multiplier": 3.0,
                    "cast_rate": self._estimate_cast_rate(state),
                    "resistance_penetration": self._estimate_pen(state),
                },
            },
        ]
        return {"tool_calls": tool_calls}

    # ── Node 6: Output ───────────────────────────────────

    async def format_output(self, state: AgentState) -> dict[str, Any]:
        """格式化最终 BuildCard 输出。"""
        draft = state.draft_build
        validation = state.validation_result or {}
        damage = state.damage_result or {}

        # 综合计算置信度
        confidence = 0.7  # 基准
        if state.reference_builds:
            confidence += 0.1  # 有参考锚点
        if validation.get("passed"):
            confidence += 0.1
        else:
            confidence -= 0.3
        if state.skill_mechanics:
            confidence += 0.05
        if state.retry_count > 0:
            confidence -= 0.1 * state.retry_count
        confidence = max(0.1, min(1.0, confidence))

        # 构建 BuildCard
        build_card = {
            "build_name": draft.get("build_name", "Unnamed Build"),
            "core_concept": draft.get("core_concept", ""),
            "class": draft.get("class", "Unknown"),
            "ascendancy": draft.get("ascendancy", ""),
            "ascendancy_nodes": draft.get("ascendancy_nodes", []),
            "skill_gems": draft.get("skill_gems", {}),
            "passive_tree": draft.get("passive_tree", {}),
            "equipment": draft.get("equipment", {}),
            "key_mechanics": draft.get("key_mechanics", []),
            "playstyle_notes": draft.get("playstyle_notes", ""),
            "estimated_dps": damage.get("estimated_dps", "N/A"),
            "estimated_budget_divines": draft.get("estimated_budget_divines", 50),
            "budget_tier": self._budget_tier(draft.get("estimated_budget_divines", 50)),
            "confidence": round(confidence, 2),
            "strengths": draft.get("strengths", []),
            "weaknesses": draft.get("weaknesses", []),
            "validation": {
                "passed": validation.get("passed", False),
                "errors": validation.get("errors", []),
                "warnings": validation.get("warnings", []),
            },
            "damage_breakdown": {
                "average_hit": damage.get("average_hit"),
                "estimated_dps": damage.get("estimated_dps"),
                "assumptions": damage.get("assumptions"),
            },
            "reference_builds_count": len(state.reference_builds),
            "game_version": state.game_version,
        }

        return {
            "final_output": json.dumps(build_card, ensure_ascii=False, indent=2),
            "confidence": confidence,
        }

    # ── Helpers ───────────────────────────────────────────

    @staticmethod
    def _summarize_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """精简参考 BD 输出，减少 prompt token。"""
        return [
            {
                "name": r.get("name"),
                "class": r.get("class"),
                "ascendancy": r.get("ascendancy"),
                "level": r.get("level"),
                "playstyle": r.get("playstyle"),
                "damage_types": r.get("damage_types"),
                "skills": r.get("skills", [])[:5],
                "tags": r.get("tags", []),
                "power_rating": r.get("power_rating"),
                "estimated_budget": r.get("estimated_budget"),
            }
            for r in refs
        ]

    @staticmethod
    def _budget_tier(divines: float) -> str:
        if divines <= 20:
            return "low"
        elif divines <= 100:
            return "medium"
        elif divines <= 300:
            return "high"
        return "unlimited"

    # ── 伤害参数估算 ──────────────────────────────────────

    @staticmethod
    def _estimate_base_damage(state: AgentState) -> float:
        draft = state.draft_build
        skills = draft.get("skill_gems", {}).get("active", [])
        if not skills:
            return 100.0
        skill_name = skills[0].get("name", "").lower()

        # 技能基础伤害参考值（POE2 approximate）
        skill_bases = {
            "spark": 200, "arc": 350, "lightning arrow": 250,
            "ice strike": 300, "hammer of the gods": 800,
            "summon raging spirit": 150, "galvanic shards": 180,
            "comet": 600, "hex blast": 400, "snipe": 500,
            "gas arrow": 280, "detonate dead": 350,
            "falling thunder": 320, "shattering palm": 250,
            "tempest bell": 220, "ember fusillade": 350,
        }
        for key, val in skill_bases.items():
            if key in skill_name:
                return val
        return 250.0

    @staticmethod
    def _estimate_increased(state: AgentState) -> float:
        draft = state.draft_build
        tree_nodes = len(draft.get("passive_tree", {}).get("nodes", []))
        # 粗略估算：每 10 个天赋节点 ~40% inc damage
        return tree_nodes * 4.0 + 200.0  # 200% base from gear

    @staticmethod
    def _estimate_more_mults(state: AgentState) -> list[float]:
        draft = state.draft_build
        skills = draft.get("skill_gems", {}).get("active", [])
        if not skills:
            return [20, 15]
        support_count = len(skills[0].get("support_gems", []))
        # 每个辅助宝石 ~25% more
        return [25.0] * min(support_count, 5)

    @staticmethod
    def _estimate_crit(state: AgentState) -> float:
        draft = state.draft_build
        tags = draft.get("key_mechanics", [])
        tags_str = " ".join(tags).lower()
        if "crit" in tags_str or "critical" in tags_str:
            return 0.45
        mechanic = state.skill_mechanics
        if mechanic and isinstance(mechanic, dict):
            base_crit = mechanic.get("base_crit_chance")
            if base_crit:
                return float(base_crit) / 100.0 * 3.5  # scaled with tree
        return 0.25

    @staticmethod
    def _estimate_cast_rate(state: AgentState) -> float:
        draft = state.draft_build
        playstyle = draft.get("playstyle", "").lower()
        if "bow" in playstyle or "crossbow" in playstyle:
            return 4.0  # attack builds
        if "strike" in playstyle or "slam" in playstyle:
            return 2.0
        if "minion" in playstyle:
            return 3.0
        return 3.0  # default spell cast rate

    @staticmethod
    def _estimate_pen(state: AgentState) -> float:
        draft = state.draft_build
        mechanics = " ".join(draft.get("key_mechanics", [])).lower()
        skill_gems = draft.get("skill_gems", {}).get("active", [])
        support_names = " ".join(
            g for s in skill_gems for g in s.get("support_gems", [])
        ).lower()
        if "penetration" in mechanics or "penetration" in support_names:
            return 0.25
        if "exposure" in mechanics or "exposure" in support_names:
            return 0.15
        return 0.0


# ── 状态图构建 ───────────────────────────────────────────


def create_build_agent_graph() -> CompiledStateGraph:
    """创建 BD 生成 LangGraph 状态图。

    节点顺序:
      understand_requirements → search_references → analyze_synergies
      → draft_build → validate → [conditional] → format_output → END

    validate 失败时: → draft_build (最多循环 2 次)
    """
    nodes = BuildAgentNodes()

    graph = StateGraph(AgentState)

    # 注册节点
    graph.add_node("understand_requirements", nodes.understand_requirements)
    graph.add_node("search_references", nodes.search_references)
    graph.add_node("analyze_synergies", nodes.analyze_synergies)
    graph.add_node("draft_build", nodes.draft_build)
    graph.add_node("validate", nodes.validate)
    graph.add_node("format_output", nodes.format_output)

    # 定义边
    graph.set_entry_point("understand_requirements")
    graph.add_edge("understand_requirements", "search_references")
    graph.add_edge("search_references", "analyze_synergies")
    graph.add_edge("analyze_synergies", "draft_build")
    graph.add_edge("draft_build", "validate")
    graph.add_conditional_edges(
        "validate",
        _route_after_validate,
        {
            "draft_build": "draft_build",
            "format_output": "format_output",
        },
    )
    graph.add_edge("format_output", END)

    return graph.compile()


def _route_after_validate(state: AgentState) -> Literal["draft_build", "format_output"]:
    """验证失败时回退到 draft_build 修正，最多重试 2 次。"""
    if state.retry_count >= 2:
        return "format_output"

    validation = state.validation_result
    if validation and not validation.get("passed", False):
        return "draft_build"

    # 置信度过低也重试一次
    if state.retry_count == 0 and len(state.reference_builds) == 0:
        return "draft_build"

    return "format_output"


# ── 模块级实例 ───────────────────────────────────────────

build_agent_graph = create_build_agent_graph()

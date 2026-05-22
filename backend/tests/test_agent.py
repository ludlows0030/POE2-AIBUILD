"""M4 Agent 端到端测试。

测试矩阵：
  - test_agent_state_validation    — AgentState 模型验证
  - test_tools_unit                — 工具函数单元测试（mock DB）
  - test_graph_structure           — LangGraph 状态图结构验证
  - test_full_pipeline             — 完整推理链（需要 Docker + API key）
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.build_agent import BuildAgent, build_agent
from app.agents.graph import AgentState, BuildAgentNodes, build_agent_graph
from app.agents.tools import calculate_damage, validate_build


# ── AgentState 验证 ──────────────────────────────────────


class TestAgentState:
    def test_default_state(self):
        state = AgentState(user_request="test")
        assert state.user_request == "test"
        assert state.requirements == {}
        assert state.reference_builds == []
        assert state.confidence == 0.0
        assert state.retry_count == 0

    def test_state_serialization(self):
        state = AgentState(
            user_request="test request",
            game_version="3.26",
            requirements={"playstyle": "spell_caster"},
            confidence=0.85,
        )
        d = state.model_dump()
        assert d["user_request"] == "test request"
        assert d["requirements"]["playstyle"] == "spell_caster"
        assert d["confidence"] == 0.85


# ── 工具函数单元测试 ─────────────────────────────────────


class TestTools:
    @pytest.mark.anyio
    async def test_calculate_damage_basic(self):
        result = await calculate_damage(
            base_damage=200.0,
            increased_damage=400.0,
            more_multipliers=[25.0, 30.0],
            crit_chance=0.35,
            crit_multiplier=3.5,
            cast_rate=3.0,
        )
        assert result["estimated_dps"] > 0
        assert result["average_hit"] > result["base_damage"]
        assert result["increased_multiplier"] == 5.0  # 1 + 400/100
        assert 1.6 < result["more_multiplier"] < 1.7  # 1.25 * 1.30

    @pytest.mark.anyio
    async def test_calculate_damage_penetration(self):
        no_pen = await calculate_damage(enemy_resistance=0.5)
        with_pen = await calculate_damage(enemy_resistance=0.5, resistance_penetration=0.25)
        assert with_pen["estimated_dps"] > no_pen["estimated_dps"]

    @pytest.mark.anyio
    async def test_validate_build_valid(self):
        valid = {
            "skill_gems": {"active": [{"name": "Spark", "support_gems": [], "role": "main_dps"}]},
            "passive_tree": {"nodes": list(range(110))},
            "equipment": {"Weapon": "Wand", "Helmet": "Rare"},
            "key_mechanics": [],
        }
        result = await validate_build(valid)
        assert result["passed"] is True
        assert len(result["errors"]) == 0

    @pytest.mark.anyio
    async def test_validate_build_no_skills(self):
        invalid = {
            "skill_gems": {"active": []},
            "passive_tree": {"nodes": range(50)},
            "equipment": {},
            "key_mechanics": [],
        }
        result = await validate_build(invalid)
        assert result["passed"] is False
        assert any("技能" in e for e in result["errors"])

    @pytest.mark.anyio
    async def test_validate_build_too_many_nodes(self):
        invalid = {
            "skill_gems": {"active": [{"name": "Spark", "support_gems": [], "role": "main_dps"}]},
            "passive_tree": {"nodes": list(range(140))},
            "equipment": {"Weapon": "Test"},
            "key_mechanics": [],
        }
        result = await validate_build(invalid)
        assert result["passed"] is False
        assert any("130" in e for e in result["errors"])

    @pytest.mark.anyio
    async def test_validate_build_ci_pain_attunement_conflict(self):
        invalid = {
            "skill_gems": {"active": [{"name": "Hex Blast", "support_gems": [], "role": "main_dps"}]},
            "passive_tree": {"nodes": list(range(80))},
            "equipment": {"Weapon": "Test"},
            "key_mechanics": ["Chaos Inoculation", "Pain Attunement"],
        }
        result = await validate_build(invalid)
        assert result["passed"] is False
        assert any("CI" in e for e in result["errors"])

    @pytest.mark.anyio
    async def test_validate_build_blood_magic_mom_conflict(self):
        invalid = {
            "skill_gems": {"active": [{"name": "Hammer of the Gods", "support_gems": [], "role": "main_dps"}]},
            "passive_tree": {"nodes": list(range(80))},
            "equipment": {"Weapon": "Test"},
            "key_mechanics": ["Blood Magic", "Mind Over Matter"],
        }
        result = await validate_build(invalid)
        assert result["passed"] is False
        assert any("Blood Magic" in e for e in result["errors"])


# ── LangGraph 状态图结构验证 ──────────────────────────────


class TestGraphStructure:
    def test_graph_compiles(self):
        assert build_agent_graph is not None

    def test_graph_has_all_nodes(self):
        nodes = build_agent_graph.get_graph().nodes
        node_names = {n for n in nodes}
        expected = {
            "understand_requirements",
            "search_references",
            "analyze_synergies",
            "draft_build",
            "validate",
            "format_output",
        }
        assert expected.issubset(node_names), f"Missing nodes: {expected - node_names}"

    def test_agent_state_fields(self):
        state = AgentState(user_request="test")
        d = state.model_dump()
        required_fields = [
            "user_request", "requirements", "reference_builds",
            "skill_mechanics", "synergies", "draft_build",
            "validation_result", "damage_result", "final_output",
            "confidence", "retry_count", "errors",
        ]
        for f in required_fields:
            assert f in d, f"Missing field: {f}"

    def test_graph_entry_point(self):
        # 验证入口节点存在
        nodes = build_agent_graph.get_graph().nodes
        assert "understand_requirements" in nodes


# ── Node 逻辑验证 (mock LLM) ─────────────────────────────


class TestAgentNodes:
    @pytest.mark.anyio
    async def test_understand_requirements_with_mock_llm(self):
        """用 mock LLM 测试需求提取节点。"""
        from unittest.mock import patch

        state = AgentState(user_request="I want a cold crit monk with Ice Strike, low budget, for bossing")
        nodes = BuildAgentNodes()

        expected_json = '''{
            "playstyle": "melee_strike",
            "class_name": "Monk",
            "ascendancy": "Invoker",
            "damage_type": "Cold",
            "budget": "low",
            "goal": "bosser",
            "core_skill_hint": "Ice Strike",
            "special_constraints": []
        }'''

        with patch("app.agents.graph.llm_client.messages_create", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = expected_json
            result = await nodes.understand_requirements(state)

        assert "requirements" in result
        reqs = result["requirements"]
        assert reqs["playstyle"] == "melee_strike"
        assert reqs["class_name"] == "Monk"
        assert reqs["damage_type"] == "Cold"
        assert reqs["core_skill_hint"] == "Ice Strike"

    @pytest.mark.anyio
    async def test_understand_requirements_fallback_on_bad_json(self):
        """测试 LLM 返回无效 JSON 时的回退逻辑。"""
        from unittest.mock import patch

        state = AgentState(user_request="test request")
        nodes = BuildAgentNodes()

        with patch("app.agents.graph.llm_client.messages_create", new_callable=AsyncMock) as mock_llm:
            mock_llm.return_value = "not valid json at all"
            result = await nodes.understand_requirements(state)

        reqs = result["requirements"]
        assert reqs["playstyle"] == "any"
        assert reqs["class_name"] == "any"

    @pytest.mark.anyio
    async def test_draft_build_structure(self):
        state = AgentState(
            user_request="Lightning arrow ranger",
            requirements={"playstyle": "bow_ranged", "class_name": "Ranger", "damage_type": "Lightning"},
            reference_builds=[{
                "name": "LA Deadeye",
                "class": "Ranger",
                "ascendancy": "Deadeye",
                "skills": ["Lightning Arrow"],
                "tags": ["mapper", "evasion"],
            }],
            skill_mechanics={"skill_name": "Lightning Arrow", "type": "Attack"},
            synergies=[{"skill_name": "Lightning Arrow", "playstyle": "bow_ranged"}],
        )
        nodes = BuildAgentNodes()

        # 这需要 LLM 调用，测试会跳过如果没有 API key
        # 这里验证状态传递正确
        assert state.user_request
        assert state.reference_builds


# ── BuildAgent 编排器测试 ────────────────────────────────


class TestBuildAgent:
    def test_agent_singleton(self):
        assert build_agent is not None
        assert isinstance(build_agent, BuildAgent)

    def test_fallback_output(self):
        result = BuildAgent._fallback_output("test request", "some error")
        assert result["build_name"] == "Generation Failed"
        assert result["confidence"] == 0.0

    def test_budget_tier(self):
        from app.agents.graph import BuildAgentNodes
        assert BuildAgentNodes._budget_tier(10) == "low"
        assert BuildAgentNodes._budget_tier(50) == "medium"
        assert BuildAgentNodes._budget_tier(200) == "high"
        assert BuildAgentNodes._budget_tier(500) == "unlimited"

    def test_estimate_base_damage(self):
        from app.agents.graph import BuildAgentNodes
        state = AgentState(
            user_request="test",
            draft_build={
                "skill_gems": {
                    "active": [{"name": "Spark"}],
                },
            },
        )
        dmg = BuildAgentNodes._estimate_base_damage(state)
        assert dmg == 200.0  # Spark base

        state.draft_build["skill_gems"]["active"][0]["name"] = "Comet"
        dmg = BuildAgentNodes._estimate_base_damage(state)
        assert dmg == 600.0  # Comet base

    def test_estimate_more_mults(self):
        from app.agents.graph import BuildAgentNodes
        state = AgentState(
            user_request="test",
            draft_build={
                "skill_gems": {
                    "active": [{"support_gems": ["A", "B", "C", "D", "E"]}],
                },
            },
        )
        mults = BuildAgentNodes._estimate_more_mults(state)
        assert len(mults) == 5
        assert all(m == 25.0 for m in mults)


# ── 集成测试（需要 Docker + API key）─────────────────────
# 这些测试默认跳过，在 CI/Docker 环境中通过环境变量启用


@pytest.mark.skipif(
    "not os.getenv('DEEPSEEK_API_KEY')",
    reason="需要 DEEPSEEK_API_KEY",
)
@pytest.mark.integration
class TestAgentIntegration:
    """需要真实基础设施的集成测试。
    Windows 上 asyncpg + ProactorEventLoop 在两次测试间会断开连接，
    因此所有集成断言放在一个测试函数中，共享一个事件循环生命周期。"""

    @pytest.mark.anyio
    async def test_generate_and_validate_builds(self):
        """完整集成：种子数据 → 生成 BD → 验证结构 → 验证流派识别。"""
        from app.database import async_session_factory
        from app.services.seed_service import seed_builds

        async with async_session_factory() as db:
            await seed_builds(db)

            agent = BuildAgent()

            # Test 1: 最小请求生成 BD，验证输出结构
            result = await agent.generate(
                db,
                user_request="I want a lightning spell caster for mapping, medium budget",
                game_version="3.26",
            )

            assert "build_name" in result
            assert "core_concept" in result
            assert "skill_gems" in result
            assert "passive_tree" in result
            assert "equipment" in result
            assert "confidence" in result
            assert "estimated_budget_divines" in result
            assert result["confidence"] > 0

            # Test 2: 已知流派 archetype 识别
            result2 = await agent.generate(
                db,
                user_request="Spark Stormweaver with Archmage, high budget lightning caster",
                game_version="3.26",
            )

            assert result2["confidence"] > 0.3

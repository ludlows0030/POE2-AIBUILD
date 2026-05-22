"""M4 BD 推理引擎 — 主编排器。

协调 LangGraph 状态图与 6 个 Tool Use 工具的交互：
  1. 用户请求 → 初始化 AgentState
  2. 逐节点执行推理图
  3. 在节点间执行 tool_calls（查询数据库 / 计算伤害 / 验证）
  4. 将工具结果写回状态
  5. 返回最终 BuildCard

使用方式:
    agent = BuildAgent()
    result = await agent.generate(db, "我想玩一个电系法师，中等预算")
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import AgentState, build_agent_graph
from app.agents.kg_tools import (
    detect_mechanic_conflicts,
    query_affixes_for_skill,
    query_ascendancy_for_skill,
    query_conversion_chain,
    query_keystone_for_skill,
    query_skill_synergies,
)
from app.agents.tools import (
    calculate_damage,
    get_passive_graph,
    get_skill_mechanics,
    poe2db_lookup,
    query_builds_db,
    search_synergies,
    validate_build,
)

logger = logging.getLogger(__name__)


class BuildAgent:
    """POE2 BD 生成 Agent。

    编排 LangGraph 推理图与工具调用，返回结构化的 BD 方案。
    """

    def __init__(self):
        self.graph = build_agent_graph

    # ── Public API ────────────────────────────────────────

    async def generate(
        self,
        db: AsyncSession,
        user_request: str,
        game_version: str = "3.26",
    ) -> dict[str, Any]:
        """主入口：根据用户请求生成 BD。

        Args:
            db: 异步数据库会话
            user_request: 用户的自然语言请求
            game_version: POE2 版本号

        Returns:
            BuildCard dict，包含完整的 BD 方案
        """
        logger.info(f"BuildAgent.generate: '{user_request[:80]}...'")

        # 初始化状态
        state = AgentState(
            user_request=user_request,
            game_version=game_version,
        )

        # 执行推理图（逐步推进，在节点间执行工具调用）
        try:
            result = await self._run_graph(db, state)
        except Exception:
            logger.exception("Build agent failed")
            return self._fallback_output(user_request, str(getattr(state, 'errors', [])))

        return result

    async def generate_from_character(
        self,
        db: AsyncSession,
        character_id: str,
        variation_request: str = "优化此 BD",
    ) -> dict[str, Any]:
        """基于已有角色的天赋树生成变体 BD。

        Args:
            db: 异步数据库会话
            character_id: 已有角色 UUID
            variation_request: 变体需求描述

        Returns:
            BuildCard dict
        """
        # 获取该角色的天赋树作为参考
        tree = await get_passive_graph(db, character_id)

        prompt = (
            f"Based on this existing character (ID: {character_id}), "
            f"create a build variation. Existing tree: {tree.get('node_sample', [])}. "
            f"Variation request: {variation_request}"
        )

        return await self.generate(db, prompt)

    # ── Graph Runner ──────────────────────────────────────

    async def _run_graph(
        self, db: AsyncSession, state: AgentState
    ) -> dict[str, Any]:
        """逐步运行 LangGraph 状态图，在节点间执行工具调用。"""

        # 获取当前状态的快照
        current_state = state.model_dump()

        # 状态图预编译，但我们需要手动控制节点间工具执行
        # LangGraph 的 invoke/astream 不直接支持异步工具调用注入
        # 因此采用逐步推进 + 工具执行的方式

        # ── Step 1: Understand Requirements ──
        current_state = await self._invoke_node("understand_requirements", current_state)

        # ── Step 2: Search Reference Builds ──
        current_state = await self._invoke_node("search_references", current_state)
        current_state = await self._execute_tools(db, current_state)
        # 工具结果 → reference_builds
        ref_builds = current_state.get("_tool_results", [])
        if ref_builds:
            current_state["reference_builds"] = ref_builds[0] if isinstance(ref_builds[0], list) else ref_builds

        # ── Step 3: Analyze Synergies ──
        current_state = await self._invoke_node("analyze_synergies", current_state)
        current_state = await self._execute_tools(db, current_state)
        tool_results = current_state.get("_tool_results", [])
        # 分配 get_skill_mechanics, search_synergies 和 poe2db_lookup 结果
        mechanics_results = []
        synergy_results = []
        poe2db_results = []
        for r in tool_results:
            if isinstance(r, dict) and "damage_formula" in r:
                mechanics_results.append(r)
            elif isinstance(r, list):
                synergy_results.extend(r)
            elif isinstance(r, dict) and "skill_id" in r:
                mechanics_results.append(r)
            elif isinstance(r, dict) and r.get("found") and "sections" in r:
                # poe2db_lookup 结果
                poe2db_results.append(r)
            elif isinstance(r, dict):
                mechanics_results.append(r)

        if mechanics_results:
            current_state["skill_mechanics"] = mechanics_results[0]
        if synergy_results:
            current_state["synergies"] = synergy_results
        if poe2db_results:
            current_state["poe2db_lookup_results"] = poe2db_results

        # ── Step 4: Draft Build ──
        current_state = await self._invoke_node("draft_build", current_state)

        # ── Step 5: Validate (with retry loop) ──
        max_retries = 2
        for retry in range(max_retries + 1):
            current_state["retry_count"] = retry
            current_state = await self._invoke_node("validate", current_state)
            current_state = await self._execute_tools(db, current_state)
            vt_results = current_state.get("_tool_results", [])
            # 分配验证和伤害结果
            for r in vt_results:
                if isinstance(r, dict):
                    if "passed" in r:
                        current_state["validation_result"] = r
                    elif "estimated_dps" in r:
                        current_state["damage_result"] = r

            validation = current_state.get("validation_result", {})
            if validation.get("passed") or retry >= max_retries:
                break

            # 回退到 draft_build 修正
            logger.info(f"Build validation failed, retrying ({retry + 1}/{max_retries})")
            current_state = await self._invoke_node("draft_build", current_state)

        # ── Step 6: Format Output ──
        current_state = await self._invoke_node("format_output", current_state)

        # 解析最终输出
        final = current_state.get("final_output", "{}")
        import json
        try:
            return json.loads(final)
        except json.JSONDecodeError:
            return {"error": "Failed to parse final output", "raw": final}

    # ── Node & Tool execution ─────────────────────────────

    async def _invoke_node(
        self, node_name: str, state_dict: dict[str, Any]
    ) -> dict[str, Any]:
        """调用单个图的节点。"""
        from app.agents.graph import BuildAgentNodes

        nodes = BuildAgentNodes()
        node_fn = getattr(nodes, node_name, None)
        if node_fn is None:
            logger.error(f"Unknown node: {node_name}")
            return state_dict

        state = AgentState(**state_dict)
        result = await node_fn(state)
        state_dict.update(result)
        return state_dict

    async def _execute_tools(
        self, db: AsyncSession, state_dict: dict[str, Any]
    ) -> dict[str, Any]:
        """执行 state 中待处理的 tool_calls，结果写入 _tool_results。"""
        tool_calls: list[dict[str, Any]] = state_dict.pop("tool_calls", [])
        if not tool_calls:
            return state_dict

        results: list[Any] = []
        for tc in tool_calls:
            tool_name = tc["tool"]
            args = tc.get("args", {})

            try:
                result = await self._dispatch_tool(db, tool_name, args)
                results.append(result)
            except Exception:
                logger.exception(f"Tool {tool_name} failed with args {args}")
                results.append({"error": f"Tool {tool_name} failed"})

        state_dict["_tool_results"] = results
        return state_dict

    async def _dispatch_tool(
        self, db: AsyncSession, tool_name: str, args: dict[str, Any]
    ) -> Any:
        """将工具名分发到对应的 async 函数。"""
        match tool_name:
            case "query_builds_db":
                return await query_builds_db(
                    db,
                    playstyle=args.get("playstyle"),
                    damage_type=args.get("damage_type"),
                    class_name=args.get("class_name"),
                    core_skill=args.get("core_skill"),
                    limit=args.get("limit", 5),
                )
            case "get_skill_mechanics":
                return await get_skill_mechanics(db, skill_name=args["skill_name"])
            case "get_passive_graph":
                return await get_passive_graph(db, character_id=args["character_id"])
            case "calculate_damage":
                return await calculate_damage(
                    base_damage=args.get("base_damage", 100.0),
                    increased_damage=args.get("increased_damage", 0.0),
                    more_multipliers=args.get("more_multipliers"),
                    crit_chance=args.get("crit_chance", 0.05),
                    crit_multiplier=args.get("crit_multiplier", 1.5),
                    cast_rate=args.get("cast_rate", 2.0),
                    resistance_penetration=args.get("resistance_penetration", 0.0),
                    enemy_resistance=args.get("enemy_resistance", 0.0),
                )
            case "validate_build":
                return await validate_build(args["build"])
            case "search_synergies":
                return await search_synergies(
                    db, keyword=args["keyword"], limit=args.get("limit", 10)
                )
            # KG tools (Neo4j)
            case "query_skill_synergies":
                return await query_skill_synergies(
                    skill_name=args["skill_name"], limit=args.get("limit", 10)
                )
            case "query_keystone_for_skill":
                return await query_keystone_for_skill(skill_name=args["skill_name"])
            case "query_ascendancy_for_skill":
                return await query_ascendancy_for_skill(skill_name=args["skill_name"])
            case "query_affixes_for_skill":
                return await query_affixes_for_skill(
                    skill_name=args["skill_name"], slot=args.get("slot")
                )
            case "detect_mechanic_conflicts":
                return await detect_mechanic_conflicts(
                    mechanics=args.get("mechanics", [])
                )
            case "query_conversion_chain":
                return await query_conversion_chain(damage_type=args["damage_type"])
            case "poe2db_lookup":
                return await poe2db_lookup(
                    term=args["term"],
                    lang=args.get("lang", "cn"),
                    format=args.get("format", "json"),
                )
            case _:
                raise ValueError(f"Unknown tool: {tool_name}")

    # ── Fallback ──────────────────────────────────────────

    @staticmethod
    def _fallback_output(user_request: str, errors: str) -> dict[str, Any]:
        return {
            "build_name": "Generation Failed",
            "core_concept": f"Unable to generate build for: '{user_request}'",
            "errors": errors,
            "confidence": 0.0,
        }


# ── 模块级实例 ───────────────────────────────────────────

build_agent = BuildAgent()

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 语言偏好

**所有回复使用简体中文。** 包括代码注释、文档、解释、提问等一切与用户的交流。

## Project Overview

POE2 AI BD Agent — Path of Exile 2 智能流派生成系统。从社区数据源采集真实玩家 BD 数据，利用 LLM 推理引擎生成新的可行 BD 方案。

## Commands

```bash
# 基础设施
cd docker && docker compose up -d      # 启动 PG16 + Neo4j5 + Redis7 + Qdrant
cd docker && docker compose down       # 停止容器
cd docker && docker compose down -v    # 停止并清除数据卷

# 数据库迁移 (在 backend/ 目录执行)
cd backend
python -m alembic revision --autogenerate -m "描述"   # 生成迁移
python -m alembic upgrade head                        # 应用迁移
python -m alembic downgrade -1                        # 回滚一步

# 验证导入 (必须 cd 到 backend 或设置 PYTHONPATH)
cd backend && python -c "from app.config import settings; from app.models import Base; print('OK')"

# 测试
cd backend && python -m pytest tests/ -v
```

Python 路径：`/c/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe`

**重要：运行脚本时的路径**
项目路径含中文（`POE2BD搭建器`），部分工具可能因编码问题找不到文件。始终 `cd` 到对应目录再执行，或设置 `PYTHONPATH`：
```bash
export PYTHONPATH="/c/aPrivate/Work/Project/POE2BD搭建器/backend"
cd "/c/aPrivate/Work/Project/POE2BD搭建器/backend"
```

## Architecture: 5 Modules

| 模块 | 位置 | 职责 | 就绪状态 |
|------|------|------|----------|
| M1 数据采集 | `backend/app/collectors/` + `scripts/` | poe.ninja protobuf + POE2DB 爬虫 + PoB2 文件 | 就绪 (GGG OAuth 待注册) |
| M2 数据解析 | `backend/scripts/` | JSON → ORM 模型 → PostgreSQL | 就绪 |
| M3 知识图谱 | `backend/app/knowledge_graph/` | Neo4j 技能/词缀/天赋/职业关系图 | **就绪** |
| M4 BD 推理引擎 | `backend/app/agents/` | LangGraph + LLM 6 步推理链 + 14 个工具 | **就绪** |
| M5 输出与验证 | `backend/app/validation/` + `api/` | 规则校验 + FastAPI 输出 | 就绪 |

**数据流**：M1(采集原始数据) → M2(解析为结构化模型 → PostgreSQL) → M3(构建关系 → Neo4j) → M4(用户请求 → LLM推理 ← 知识图谱) → M5(校验 → 格式化输出)

## 数据导入与 KG 同步

```bash
cd backend

# 导入 poe.ninja BD 数据 (从 data/builds/poeninja_{league}.json)
python scripts/import_poeninja_builds.py --league vaal
python scripts/import_poeninja_builds.py --dry-run          # 预览不写入

# POE2DB 技能数据补全
python scripts/scrape_skill_damage.py --limit 20 --dry-run  # 主动技能详情（伤害效能/暴击/施放时间）
python scripts/scrape_support_gems.py --limit 20 --dry-run  # 辅助宝石详情（Implicit 数值效果/标签）
python scripts/scrape_support_gems.py                       # 全部 511 个辅助宝石

# 传奇物品词缀清洗
python scripts/clean_unique_mods.py                         # 清洗 explicit_mods 原始格式 → 可读格式

# 同步 Neo4j 知识图谱 (从 PostgreSQL 全量同步)
python -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from app.config import settings
from app.knowledge_graph.sync_service import knowledge_graph_sync

async def main():
    engine = create_async_engine(settings.postgres_url)
    async with AsyncSession(engine) as db:
        print(await knowledge_graph_sync.full_sync(db))
    await engine.dispose()
asyncio.run(main())
"

# 端到端 BD 生成测试
python -c "
import asyncio, json
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from app.config import settings
from app.agents.build_agent import build_agent

async def main():
    engine = create_async_engine(settings.postgres_url)
    async with AsyncSession(engine) as db:
        result = await build_agent.generate(db, 'Spark Lightning caster Stormweaver')
        print(json.dumps(result, indent=2, ensure_ascii=False))
    await engine.dispose()
asyncio.run(main())
"
```

## 关键数据统计 (2026-05-24)

| 数据 | 数量 | 来源 |
|------|------|------|
| PostgreSQL BD (真实玩家) | 100 | poe.ninja Fate of the Vaal 赛季 |
| PostgreSQL 技能机制 | 901 | PoB2 Gems.lua + POE2DB 爬虫补全 |
| 　├ 主动技能 | 344 | 其中 288 有伤害效能, 76 有基础暴击率 |
| 　└ 辅助宝石 | 511 | 全部有描述 (含 Implicit 表数值效果) |
| PostgreSQL 天赋节点 | 4,701 | PoB2 tree_0_4.json |
| PostgreSQL 装备底材 | 1,122 | PoB2 BaseItemTypes |
| PostgreSQL 传奇物品 | 454 | POE2DB 爬虫, 已清洗词缀格式 |
| PostgreSQL 装备词缀 | 6,944 | PoB2 Mods.dat |
| Neo4j 技能节点 | 1,066 | SkillGroup + GameMechanic |
| Neo4j PAIRED_WITH 关系 | 1,244 | 100 BD 的技能共现统计 |
| Neo4j 升华加成关系 | 17 | 固化游戏知识 |

## Key Design Decisions

### 预算系统 (已移除)

POE2 物价实时变动，预算估算没有参考价值。整个 Agent 推理链中已移除：
- 需求提取不再包含 `budget` 字段
- 草案模板不再输出 `estimated_budget_divines`
- System prompt 不再提及预算层级
- 数据库字段保留 (仅用于外部数据导入，不影响推理)

### POE2 API 限制（重要）

GGG 官方 API 对 POE2 覆盖有限，设计时务必注意：

- **Ladder API 为 PoE1 only** — POE2 没有官方排行榜接口
- **Leagues API 支持 `realm=poe2`** — 可获取 POE2 联赛元数据
- **角色详情需 OAuth 2.1** — 向 `oauth@grindinggear.com` 注册应用后方可使用
- **POESESSID Cookie** — 非官方备选方案，可在 `www.pathofexile.com` 上查询角色装备/天赋

### 数据采集四层策略

1. **PoB2 社区仓库** — Lua/JSON 文件解析（最完整、离线可用）
2. **POE2DB** (`poedb.tw`) — HTML 爬虫补充中文译名、传奇装备、技能数值效果
3. **poe.ninja** — Protobuf API 逆向（100 条 BD/次，列式存储）
4. **pobb.in** — PoB XML 格式，直接可解析

### POE2DB 爬虫要点

**辅助宝石数值效果提取**：POE2DB 将辅助宝石的数值效果存储在 `<table class="filters">` 的 Implicit/Explicit/Gem/Effect 表格中，格式为 `<span class='mod-value'>30%</span> more Elemental Damage`。爬虫在 `poe2db_skill_scraper.py` 中专门解析这些表格，将数值效果拼接到 `description` 字段中。

**正则适配**：
- 中文用词差异：POE2DB 使用"施放间隔"而非"施放时间"
- 破折号：POE2DB HTML 使用 U+2014 (EM DASH)，正则中慎用字符类，优先用 `\D+` 匹配分隔符
- 法术伤害效能默认值：POE2DB 不展示法术技能的伤害效能（默认为 100%），仅展示攻击技能。爬虫脚本中已处理此逻辑

**请求限制**：POE2DB 对并发请求敏感，`POE2DBSkillScraper` 默认 `concurrency=5`，过高会被 Cloudflare 限流。

### poe.ninja Protobuf 逆向要点

详见 `backend/docs/poeninja-protobuf.md`。关键发现：
- 字典 API 使用位置索引（非 varint ID）— ID = 字段排列序号
- Build 数据使用列式存储（11 列 × 100 条），Field 5 为核心
- 技能列表使用 packed varint 编码
- Windows `requests` 库连 poe.ninja 会 SSL EOF，使用 `curl` 替代
- 字典 hash 硬编码在 `parse_poeninja.py` 中，赛季更新时需重新提取

### Agent 推理链 (M4)

6 步结构化推理：`understand → search_references → analyze_synergies → draft → validate → output`

验证失败时自动回退到 draft 修正（最多 2 次）。

**14 个工具函数：**

PostgreSQL 工具 (8):
1. `query_builds_db` — 查询历史 BD 参考锚点
2. `get_skill_mechanics` — 查询技能机制详情
3. `get_passive_graph` — 获取天赋树节点
4. `calculate_damage` — PoB2 公式 9 步伤害估算
5. `validate_build` — BD 可行性校验
6. `search_synergies` — 技能协同搜索
7. `poe2db_lookup` — POE2DB 通用查询
8. `find_compatible_supports` — 辅助宝石标签兼容性匹配

Neo4j 知识图谱工具 (6，位于 `app/agents/kg_tools.py`):
9. `query_skill_synergies` — PAIRED_WITH 技能共现关系
10. `query_keystone_for_skill` — BENEFITS_FROM 基石推荐
11. `query_ascendancy_for_skill` — BOOSTS 升华加成
12. `query_affixes_for_skill` — SCALES_WITH 装备词缀
13. `detect_mechanic_conflicts` — CONFLICTS_WITH 机制冲突
14. `query_conversion_chain` — CONVERTS_TO 伤害转化

**工具调用链**：`graph.py` (node → tool_calls) → `build_agent.py` (_execute_tools → _dispatch_tool) → `kg_tools.py` / `tools.py` → PostgreSQL / Neo4j / curl

### Neo4j 知识图谱 Schema

**节点类型**：Skill, Keystone, Ascendancy, Mechanic, DamageType, Playstyle, CharClass, Modifier

**关系类型**：
- `(Skill)-[:PAIRED_WITH]-(Skill)` — BD 技能共现频率 (co_occurrence)
- `(Skill)-[:BENEFITS_FROM]->(Keystone)` — 基石天赋搭配 (synergy_strength)
- `(Ascendancy)-[:BOOSTS]->(Skill)` — 升华加成 (boost_power)
- `(Skill)-[:SCALES_WITH]->(Modifier)` — 装备词缀优先度 (priority)
- `(Skill)-[:DEALS]->(DamageType)` — 技能伤害类型
- `(Mechanic)-[:CONFLICTS_WITH]-(Mechanic)` — 机制冲突 (reason)
- `(DamageType)-[:CONVERTS_TO]->(DamageType)` — 伤害转化链
- `(CharClass)-[:HAS_ASCENDANCY]->(Ascendancy)` — 职业-升华关系
- `(Skill)-[:HAS_MECHANIC]->(Mechanic)` — 技能机制关联

**重要坑**：`_sync_co_occurrence` 使用 `MATCH {name: $a}` 而非 `{skill_id: $a}`。因为从 poe.ninja 导入的技能 `skill_id` 是数字 ID，但共现统计的键是技能英文名。混用会导致 PAIRED_WITH 关系创建失败。

### 辅助宝石兼容性匹配

Agent 通过 `find_compatible_supports` 工具自动筛选每个主动技能可用的辅助宝石。

**匹配规则**：辅助宝石的所有非 `support` 标签必须 ⊆ 主动技能标签。使用 PostgreSQL 数组操作：

```sql
array_remove(sg.tags, 'support') <@ :active_tags
```

结果按匹配标签数降序排列（多标签匹配优先，通用辅助宝石垫底）。

**实例**：Spark 标签 `[duration, lightning, projectile, spell, ...]` → 159 兼容辅助宝石
- 2 标签匹配优先：Arcane Surge `[spell, duration]`、Lightning Exposure `[lightning, duration]`
- Lightning Attunement `[attack, lightning]` 正确被排除（Spark 没有 `attack` 标签）

**重要坑**：标签大小写敏感。Gems.lua 标签是小写（`lightning`），POE2DB 爬虫标签是首字母大写（`Lightning`）。`<@` 运算符区分大小写。解决方法：将所有标签统一 lowercase。
```sql
UPDATE game_mechanic SET tags = (
  SELECT array_agg(lower(tag)) FROM unnest(tags) AS tag
) WHERE skill_type IN ('active', 'support');
```

### 数据库

- 13 张表（`backend/app/models/base.py`）：Character, SkillGroup, PassiveTree, EquipmentItem, BuildMeta, GameMechanic, GeneratedBuild, ItemBase, UniqueItem, Modifier, PassiveNode, AscendancyClass, ClusterJewelBase
- 使用 SQLAlchemy async + PostgreSQL 16
- Alembic 管理迁移，`target_metadata = Base.metadata` 已在 `alembic/env.py` 配置

### 配置管理

`backend/app/config.py` 使用 pydantic-settings，所有环境变量集中管理。`.env` 文件放在 `backend/` 目录。LLM 默认使用 DeepSeek (`deepseek-chat`)。

### 伤害计算

基于 PoB2 `CalcOffence.lua` 的真实 9 步伤害链：
`基础伤害 → INC → MORE → 暴击 → Lucky → 全局缩放 → 抗性/穿透 → Impale → 斩杀`

详见 `backend/docs/mechanics-formulas.md`。

## 已知限制

- **版本号**：当前 POE2 版本为 **0.4**（Early Access）。所有 `game_version` 默认值和 prompt 中的版本引用已统一为 0.4。注意与 PoE1 的 3.x 版本号体系区分。
- **PAIRED_WITH 关系不完整**：仅依赖当前 100 条 BD 的技能共现，未覆盖所有技能组合。更多 BD 数据导入可改善。
- **poe.ninja class_id=0**：未升华角色无法推断基础职业，`ascendancy_to_class` 映射靠手工维护。
- **中文路径编码**：项目目录名含中文可能导致 `ModuleNotFoundError`，始终使用 `PYTHONPATH` 或 `cd` 解决。
- **伤害估算为近似值**：`calculate_damage` 的 INC%、MORE%、暴击率等参数由 `graph.py` 中的启发式方法估算，不精确。
- **poe.ninja API 每次仅 100 条**：无法翻页，需修改排序/筛选条件获取更多数据。
- **POE2DB 暴击率覆盖率低**：仅 76/344 主动技能有 `base_crit_chance` 数据。POE2DB 页面并非所有技能都展示暴击率，法术默认 5% 暴击率的补全逻辑暂未实现。
- **辅助宝石兼容性受限于标签完整性**：如果主动技能或辅助宝石的标签不完整（Gems.lua 缺少某些标签），匹配结果会有遗漏。当前标签主要来自 PoB2 Gems.lua 和 POE2DB 页面爬取。

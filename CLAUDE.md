# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

# 验证导入
cd backend && python -c "from app.config import settings; from app.models import Base; print('OK')"

# 测试
cd backend && python -m pytest tests/ -v
```

Python 路径：`/c/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe`

## Architecture: 5 Modules

| 模块 | 位置 | 职责 | 就绪状态 |
|------|------|------|----------|
| M1 数据采集 | `backend/app/collectors/` | GGG API + poe.ninja + pobb.in | 框架就绪，GGG OAuth 待注册 |
| M2 数据解析 | `backend/app/parser/` | API JSON → ORM 模型转换 | 就绪 |
| M3 知识图谱 | `backend/app/knowledge_graph/` | Neo4j 技能/词缀/天赋关系图 | 待开发 |
| M4 BD 推理引擎 | `backend/app/agents/` | LangGraph + Claude SDK 多步推理 | 待开发 |
| M5 输出与验证 | `backend/app/validation/` + `api/` | 规则校验 + FastAPI 输出 | 待开发 |

**数据流**：M1(采集原始数据) → M2(解析为结构化模型 → PostgreSQL) → M3(构建关系 → Neo4j) → M4(用户请求 → LLM推理 ← 知识图谱) → M5(校验 → 格式化输出)

## Key Design Decisions

### POE2 API 限制（重要）

GGG 官方 API 对 POE2 覆盖有限，设计时务必注意：

- **Ladder API 为 PoE1 only** — POE2 没有官方排行榜接口
- **Leagues API 支持 `realm=poe2`** — 可获取 POE2 联赛元数据
- **角色详情需 OAuth 2.1** — 向 `oauth@grindinggear.com` 注册应用后方可使用
- **POESESSID Cookie** — 非官方备选方案，可在 `www.pathofexile.com` 上查询角色装备/天赋

### 数据采集三层策略

1. **GGG OAuth API**（`ggg_api.py`）— 公共 API host 是 `api.pathofexile.com`，Web API host 是 `www.pathofexile.com`，两者不同！
2. **poe.ninja**（`poe_ninja.py`）— 经济数据有内部 API；BD 数据在 `/poe2/builds` 页面，需 Playwright 渲染
3. **pobb.in**（`pobb_in.py`）— PoB XML 格式，直接可解析

### 数据库

- 7 张核心表（见 `backend/app/models/base.py`）：Character, SkillGroup, PassiveTree, EquipmentItem, BuildMeta, GameMechanic, GeneratedBuild
- 使用 SQLAlchemy async + PostgreSQL 16
- Alembic 管理迁移，`target_metadata = Base.metadata` 已在 `alembic/env.py` 配置

### 配置管理

`backend/app/config.py` 使用 pydantic-settings，所有环境变量集中管理。`.env` 文件放在项目根目录。LLM 当前使用 `ANTHROPIC_MODEL` 配置模型。

# POE2 数据采集管道文档

记录项目中所有数据源的采集方式、导入脚本和目标数据库表。

**最后更新**: 2026-05-24

---

## 数据源总览

```
┌──────────────────────────────────────────────────────────────┐
│                      数据采集三层架构                          │
├──────────────┬──────────────────┬─────────────────────────────┤
│ 层级         │ 数据源            │ 采集方式                     │
├──────────────┼──────────────────┼─────────────────────────────┤
│ 游戏文件层   │ PoB2 社区仓库     │ Lua 解析 + JSON 解析          │
│ 社区数据库层  │ POE2DB           │ HTML 爬虫 + BeautifulSoup    │
│ 玩家数据层   │ poe.ninja         │ Protobuf API 逆向            │
│              │ GGG 官方 API      │ OAuth 2.1 (待注册)           │
│              │ pobb.in           │ PoB XML 导入                 │
└──────────────┴──────────────────┴─────────────────────────────┘
```

---

## 一、游戏文件层 — PoB2 社区仓库

PoB2 (Path of Building 2) 是 POE2 社区维护的 BD 模拟器。其源码中包含从游戏客户端提取的完整数据。

### 数据文件 (存放在 `backend/data/`)

| 文件 | 大小 | 内容 | 对应导入脚本 |
|------|------|------|-------------|
| `Gems.lua` | 471 KB | 902 条技能/辅助宝石（含标签、需求、伤害倍率） | `scripts/import_gems.py` |
| `tree_0_4.json` | 1.8 MB | 4,891 个天赋树节点（含坐标、连接关系、属性） | `scripts/import_passive_tree.py` |
| `CalcOffence.lua` | 334 KB | 伤害计算公式（9 步链） | 手动提取 → `docs/mechanics-formulas.md` |
| `CalcDefence.lua` | 234 KB | 防御计算公式（15 子系统） | 手动提取 → `data/mechanics_defence.txt` |
| `ModCache.lua` | 979 KB | 装备词缀缓存 | `scripts/import_modifiers.py` |
| `ModItem.lua` | 702 KB | 物品词缀定义 | — |
| `ModItemExclusive.lua` | 1.6 MB | 专属词缀 | — |
| `ModJewel.lua` | 135 KB | 珠宝词缀 | `scripts/import_jewels.py` |
| `ClusterJewels.lua` | 35 KB | 星团珠宝 | — |
| `CalcPerform.lua` | 164 KB | 性能计算工具 | — |
| `CalcTools.lua` | 10 KB | 计算辅助函数 | — |
| `Common.lua` | 28 KB | 公共常量/函数 | — |
| `TimelessJewelData/` | 目录 | 永恒珠宝数据 | — |
| `Uniques/` | 目录 | 传奇物品定义 | — |
| `Bases/` | 目录 | 底材定义 | — |

### 导入脚本详解

#### `import_gems.py`
- **数据源**: `data/Gems.lua`
- **目标表**: `game_mechanic` (ORM: `GameMechanic`)
- **方式**: 正则解析 Lua 表，提取 gem 名称、类型、需求属性、标签、伤害倍率
- **数量**: 902 条
- **用法**: `python scripts/import_gems.py`

#### `import_passive_tree.py`
- **数据源**: `data/tree_0_4.json`
- **目标表**: `passive_node` (ORM: `PassiveNode`)
- **方式**: JSON 解析节点列表 + POE2DB 补充中文名
- **数量**: 4,891 个节点
- **用法**: `python scripts/import_passive_tree.py`

#### `import_item_bases.py` / `import_item_bases_v2.py`
- **数据源**: 硬编码的游戏知识列表 + POE2DB 逐条验证
- **目标表**: `item_base` (ORM: `ItemBase`)
- **方式**: 维护 `POE2_ITEM_BASES` 列表 → 每条通过 POE2DB 验证存在性
- **数量**: ~200 件底材
- **用法**: `python scripts/import_item_bases.py`

---

## 二、社区数据库层 — POE2DB

`poe2db.tw` 是 POE 中文维基，提供 HTML 页面，可被 BeautifulSoup 解析。

### 采集器 (存放在 `app/collectors/`)

| 文件 | 职责 |
|------|------|
| `poe2db_lookup.py` | 基础 HTTP 获取 + 缓存（`fetch_poe2db_page()`） |
| `poe2db_skill_scraper.py` | 技能详情页批量爬取（860 条技能 → `skill_list.json` 等） |
| `poe2db_data_importer.py` | 全量数据导入器（统一入口） |
| `poedb.py` | POEDB (POE1 版本) 适配器 |

### 导入脚本

#### `import_uniques.py`
- **数据源**: POE2DB Unique 分类页面
- **目标表**: `unique_item` (ORM: `UniqueItem`)
- **方式**: 遍历 POE2DB 传奇列表 → 逐条抓取详情页 → 解析属性
- **延迟控制**: `--delay 4.0` (秒) 避免封 IP
- **用法**: `python scripts/import_uniques.py --limit 100 --delay 4.0`

#### `import_modifiers.py`
- **数据源**: `data/ModCache.lua` → 已验证 + POE2DB 补充
- **目标表**: `modifier`
- **用法**: `python scripts/import_modifiers.py`

#### `import_jewels.py`
- **数据源**: `data/ModJewel.lua`
- **用法**: `python scripts/import_jewels.py`

---

## 三、玩家数据层 — poe.ninja

详见 `docs/poeninja-protobuf.md` 的完整逆向文档。

### 数据源
- **API**: `https://poe.ninja/poe2/api/builds/{snapshotId}/search?overview={league}`
- **格式**: Protobuf 二进制，100 条/次，列式存储
- **字典**: 独立 API 端点提供 ID→名称 映射

### 解析器
- **脚本**: `scripts/parse_poeninja.py`
- **输出**: `data/builds/poeninja_{league}.json`
- **用法**: `python scripts/parse_poeninja.py --league vaal --limit 100`

### 已有采集数据
- `data/builds/poeninja_vaal.json` — Fate of the Vaal 赛季前 100 名 BD

### 旧版 Playwright 方案 (已弃用)
- `scripts/scrape_poeninja.py` — 使用 Playwright 渲染 SPA 页面，不稳定（~30% 成功率）
- `scripts/decode_poeninja.py` — 通用 protobuf 解码器（研究阶段使用）

---

## 四、其他数据源

### GGG 官方 API
- **采集器**: `app/collectors/ggg_api.py`
- **状态**: 框架就绪，OAuth 2.1 待向 `oauth@grindinggear.com` 注册
- **能力**: 角色装备/天赋查询（需 OAuth）、联赛元数据（`realm=poe2`）
- **注意**: Ladder API 仅 POE1，POE2 无官方排行榜

### pobb.in
- **采集器**: `app/collectors/pobb_in.py`
- **格式**: PoB XML（结构化，易解析）
- **用途**: 导入手工制作的 PoB BD 配置

### poe.ninja 经济数据
- **采集器**: `app/collectors/poe_ninja.py`
- **用途**: 物品价格、通货汇率

---

## 数据库表映射

| ORM 模型 | 表名 | 数据来源 | 记录数 |
|----------|------|----------|--------|
| `Character` | `character` | GGG API / poe.ninja | — |
| `SkillGroup` | `skill_group` | 角色技能关联 | — |
| `PassiveTree` | `passive_tree` | 角色天赋关联 | — |
| `EquipmentItem` | `equipment_item` | 角色装备关联 | — |
| `BuildMeta` | `build_meta` | BD 元数据 | — |
| `GameMechanic` | `game_mechanic` | `import_gems.py` | 902 |
| `ItemBase` | `item_base` | `import_item_bases.py` | ~200 |
| `UniqueItem` | `unique_item` | `import_uniques.py` | — |
| `PassiveNode` | `passive_node` | `import_passive_tree.py` | 4,891 |
| `Modifier` | `modifier` | `import_modifiers.py` | — |
| `GeneratedBuild` | `generated_build` | M4 推理引擎输出 | — |

---

## 知识图谱 (M3)

- **脚本**: `scripts/sync_kg.py`
- **数据库**: Neo4j 5 (Docker)
- **同步方向**: PostgreSQL → Neo4j
- **节点类型**: 技能、天赋、词缀、装备
- **关系**: HAS_SKILL, SUPPORTS, REQUIRES_ATTR, GRANTS_MOD 等

---

## Bootstrap 一次性初始化

`scripts/bootstrap.py` 用于新环境的一键初始化：
1. 检查 PostgreSQL 连接
2. 运行 Alembic 迁移建表
3. 依次运行数据导入脚本
4. 同步 Neo4j 知识图谱

---

## 采集的 JSON 缓存文件

根目录下的 JSON 文件是各采集阶段的中间产物，可用于调试：

| 文件 | 来源 | 内容 |
|------|------|------|
| `all_gems_phase1.json` | POE2DB | 全量宝石数据 |
| `skill_list.json` | POE2DB | 主动技能列表 |
| `support_list.json` | POE2DB | 辅助宝石列表 |
| `skill_weapons.json` | POE2DB | 技能-武器映射 |
| `weapon_types.json` | POE2DB | 武器类型 |
| `spark_page.json` | POE2DB | Spark 详情页 (调试) |
| `ice_strike_detail.json` | POE2DB | Ice Strike 详情页 (调试) |
| `poe2db_nav.json` | POE2DB | 网站导航结构 |
| `db_mechanics_sample.json` | PoB2 | 机制数据样本 |
| `build_result.json` / `build_v2.json` | M4 | 生成 BD 输出样本 |
| `data/dict_ascendancy.json` | poe.ninja | 升华职业字典缓存 |

---

## 维护检查清单

当游戏版本更新或数据过期时：

- [ ] 更新 PoB2 数据文件 (`data/*.lua`, `data/tree_*.json`)
- [ ] 重新运行 `import_gems.py`（宝石可能有变动）
- [ ] 重新运行 `import_passive_tree.py`（天赋树可能调整）
- [ ] 检查新传奇物品 → `import_uniques.py --limit 999`
- [ ] 重新运行 `parse_poeninja.py --league {new_league}` 采集新赛季 BD
- [ ] 检查字典 hash 是否变化（见 `poeninja-protobuf.md`）
- [ ] 运行 `sync_kg.py` 刷新知识图谱
- [ ] 运行 Alembic migration 如果 Schema 有变更

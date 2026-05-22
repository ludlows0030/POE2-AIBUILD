# POE2 数据导入指南

## 概览

所有游戏数据从两个来源获取：

| 来源 | 类型 | 更新方式 |
|------|------|----------|
| **PoB2 社区仓库** (GitHub) | 技能宝石、装备底材、词缀、天赋树、珠宝 | 下载最新 .lua/.json 文件 → 运行脚本导入 |
| **POE2DB** (poe2db.tw) | 传奇装备、中文名称翻译 | 运行爬虫脚本直接采集 |

## 数据文件清单

### 需要从 PoB2 GitHub 下载的文件

仓库地址：`https://github.com/PathOfBuildingCommunity/PathOfBuilding-PoE2` 分支 `dev`

| 文件 | 大小 | 用途 |
|------|------|------|
| `src/Data/Gems.lua` | 471 KB | 技能/辅助宝石（901条） |
| `src/Data/ModItem.lua` | 702 KB | 装备词缀-常规（前缀/后缀） |
| `src/Data/ModItemExclusive.lua` | 1.6 MB | 装备词缀-专属（传奇/基底/腐化） |
| `src/Data/ModJewel.lua` | 135 KB | 珠宝词缀（361 条） |
| `src/Data/ClusterJewels.lua` | 35 KB | 星团珠宝底材 |
| `src/Data/Bases/*.lua` | ~370 KB (29文件) | 全部装备底材（1137条） |
| `src/Data/Uniques/jewel.lua` | 3 KB | 传奇珠宝（7条） |
| `src/Data/Spectres.lua` | 可选 | 灵体数据 |
| `tree_0_4.json` (从 PoB2 根目录) | 1.8 MB | 完整天赋树（4891节点） |

### 下载命令

```bash
# 设置数据目录
DATA_DIR="backend/data"
RAW_BASE="https://raw.githubusercontent.com/PathOfBuildingCommunity/PathOfBuilding-PoE2/dev/src/Data"

# 核心文件
curl -L -o "$DATA_DIR/Gems.lua" "$RAW_BASE/Gems.lua"
curl -L -o "$DATA_DIR/ModItem.lua" "$RAW_BASE/ModItem.lua"
curl -L -o "$DATA_DIR/ModItemExclusive.lua" "$RAW_BASE/ModItemExclusive.lua"
curl -L -o "$DATA_DIR/ModJewel.lua" "$RAW_BASE/ModJewel.lua"
curl -L -o "$DATA_DIR/ClusterJewels.lua" "$RAW_BASE/ClusterJewels.lua"

# 底材文件 (29个)
mkdir -p "$DATA_DIR/Bases"
for slot in amulet axe belt body boots bow claw crossbow dagger fishing \
            flail flask focus gloves helmet incursionlimb jewel mace \
            quiver ring sceptre shield soulcore spear staff sword \
            talisman traptool wand; do
    curl -L -o "$DATA_DIR/Bases/${slot}.lua" "$RAW_BASE/Bases/${slot}.lua"
done

# 传奇珠宝
mkdir -p "$DATA_DIR/Uniques"
curl -L -o "$DATA_DIR/Uniques/jewel.lua" "$RAW_BASE/Uniques/jewel.lua"

# 天赋树 (从 PoB2 运行时目录导出)
# 需要先运行一次 PoB2 让它生成 tree_0_4.json，或从 PoB2 安装目录复制
cp "PathOfBuilding-PoE2/tree_0_4.json" "$DATA_DIR/"
```

## 导入脚本执行顺序

所有脚本在 `backend/` 目录下执行，使用项目 Python 环境。

```bash
cd backend
```

### 第 1 步：技能宝石

```bash
python scripts/import_gems.py
```

- **脚本**: `scripts/import_gems.py`
- **数据源**: `data/Gems.lua` (PoB2)
- **目标表**: `game_mechanic`
- **覆盖策略**: 按 `mechanic_id` (技能英文ID) 匹配，内容哈希比对增量更新
- **结果**: 901 条（主动 344 + 辅助 519 + 元技能 38）
- **耗时**: ~5秒

### 第 2 步：装备底材

```bash
python scripts/import_item_bases_v2.py --clear-old
```

- **脚本**: `scripts/import_item_bases_v2.py`
- **数据源**: `data/Bases/*.lua` (PoB2, 29个文件)
- **目标表**: `item_base`
- **覆盖策略**: 按 `name_en` 匹配，首次导入用 `--clear-old` 清空旧数据
- **结果**: 1,122 条（武器 542 + 防具 549 + 护符 13 + 药剂 18）
- **耗时**: ~5秒

### 第 3 步：装备词缀

```bash
python scripts/import_modifiers.py
```

- **脚本**: `scripts/import_modifiers.py`
- **数据源**: `data/ModItem.lua` + `data/ModItemExclusive.lua` + `data/ModJewel.lua` (PoB2)
- **目标表**: `modifier`
- **覆盖策略**: 按 `stat_id` (唯一键) 匹配，内容哈希比对增量更新
- **结果**: 6,944 条（prefix 982 + suffix 738 + exclusive 2,444 + unique 2,221 + implicit 198 + jewel 361）
- **耗时**: ~60秒（ModItemExclusive.lua 文件较大）

### 第 4 步：天赋树

```bash
python scripts/import_passive_tree.py
```

- **脚本**: `scripts/import_passive_tree.py`
- **数据源**: `data/tree_0_4.json` (PoB2) + POE2DB（中文名）
- **目标表**: `passive_node` + `ascendancy_class`
- **覆盖策略**: 按 `node_gid` (GGG 节点ID) 匹配
- **结果**: 
  - 4,701 天赋节点（normal 3,344 + notable 1,122 + ascendancy 202 + keystone 33）
  - 12 升华职业（含中文名）
- **耗时**: ~5分钟（需要爬 POE2DB 获取中文名）

### 第 5 步：传奇装备

```bash
python scripts/import_uniques.py
```

- **脚本**: `scripts/import_uniques.py`
- **数据源**: POE2DB 网页爬取 (`poe2db.tw`)
- **目标表**: `unique_item`
- **覆盖策略**: 按 `name_en` 匹配
- **结果**: 452 条传奇装备
- **耗时**: ~15分钟（大量 HTTP 请求 + 延迟控制）
- **注意**: 需要网络访问 POE2DB，每次运行会重新爬取

### 第 6 步：珠宝数据

```bash
python scripts/import_jewels.py
```

- **脚本**: `scripts/import_jewels.py`
- **数据源**: `data/ModJewel.lua` + `data/Uniques/jewel.lua` + `data/ClusterJewels.lua` (PoB2)
- **目标表**: `modifier` + `unique_item` + `cluster_jewel_base`
- **覆盖策略**: 按复合键匹配
- **结果**: 
  - 361 珠宝词缀 → `modifier`
  - 7 传奇珠宝 → `unique_item`
  - 3 星团珠宝底材 → `cluster_jewel_base`
- **耗时**: ~3秒

> 注：第 3 步 `import_modifiers.py` 已包含 ModJewel.lua 导入，无需重复执行珠宝词缀部分。

## 快速更新流程

游戏版本更新后，完整重导流程：

```bash
cd backend

# 1. 下载最新数据文件（见上方下载命令）

# 2. 重新导入（按依赖顺序）
python scripts/import_gems.py
python scripts/import_item_bases_v2.py
python scripts/import_modifiers.py
python scripts/import_passive_tree.py
python scripts/import_uniques.py
python scripts/import_jewels.py

# 3. 验证
python -c "
import asyncio
from app.database import async_session_factory
from sqlalchemy import text

async def check():
    async with async_session_factory() as db:
        tables = ['game_mechanic','modifier','item_base','unique_item',
                   'cluster_jewel_base','passive_node','ascendancy_class']
        for t in tables:
            cnt = await db.scalar(text(f'SELECT count(*) FROM {t}'))
            print(f'{t:25s} {cnt:6d}')

asyncio.run(check())
"
```

## 数据库表汇总

| 表 | 当前数量 | 说明 |
|------|------|------|
| `game_mechanic` | 901 | 技能/辅助宝石 |
| `item_base` | 1,122 | 装备底材 |
| `modifier` | 6,944 | 装备/珠宝词缀 |
| `unique_item` | 454 | 传奇物品 |
| `passive_node` | 4,701 | 天赋树节点 |
| `ascendancy_class` | 12 | 升华职业 |
| `cluster_jewel_base` | 3 | 星团珠宝底材 |

## 增量更新机制

所有导入脚本共享同一套增量更新逻辑：

1. 每条记录计算 `content_hash = SHA256(JSON.dumps(data, sort_keys=True))`
2. 导入时逐条比对：
   - 哈希相同 → 跳过
   - 哈希不同 → UPDATE + 更新 `updated_at`
   - 新增记录 → INSERT
3. 不再存在于新数据的旧记录标记 `is_active = False`（软删除）
4. 每条记录存储 `game_version` 字段用于版本追踪

## 注意事项

1. **POE2DB 爬虫依赖网络** — `import_uniques.py` 和 `import_passive_tree.py` 需要访问 poe2db.tw
2. **文件完整性** — 下载的 .lua 文件须以 `}` 结尾，否则可能被截断；`import_gems.py` 已内置截断检测
3. **首次导入** — 使用 `--clear-old` 参数清空旧数据（非 POE2DB 源）
4. **Python 路径** — `/c/Users/Administrator/AppData/Local/Programs/Python/Python312/python.exe`

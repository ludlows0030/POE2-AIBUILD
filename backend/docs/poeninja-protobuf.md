# poe.ninja POE2 Build API 逆向文档

## 概述

poe.ninja 的 POE2 builds 页面 (`/poe2/builds/{league}`) 使用 React SPA 渲染，但底层数据通过 protobuf API 提供。本文档记录逆向后的 API 结构和数据格式。

**最后更新**: 2026-05-24 (赛季: Fate of the Vaal / 0.4)

## API 端点

### 1. 索引状态 (JSON)
```
GET https://poe.ninja/poe2/api/data/index-state
```
返回所有联赛的 snapshot 版本号。`buildLeagues` 数组中每个条目包含 `url` 和 `version`。

### 2. 字典 API (Protobuf)
```
GET https://poe.ninja/poe2/api/builds/dictionary/{hash}
```
返回 ID→名称 映射。不同字典有不同的 hash：

| 字典 | Hash (SHA1) |
|------|------------|
| class (职业/升华) | `e198d88dc5417779bb6f556f66ad24ef525024c1` |
| gem (技能宝石) | `b52d0885e529d358542ac5909386aee200cb64b6` |
| keypassive (核心天赋) | `b239765917bccf3943d954b7c70c2048de049958` |
| weaponmode (武器模式) | `591bbe3bae28a64ea0ebf2d3f85cc9c9bddbcf14` |
| item (装备) | `ec77cc690489d14f455b7f1e815c09822c718b30` |
| skillmode | `0ff147d2358ef2ab12a1142d970ca61050f13110` |
| anointed (涂油) | `2185928e3412622ef94a0acbe8cdbc394605a479` |

**字典格式**:
```protobuf
message Dictionary {
  string type_name = 1;          // "class", "gem", ...
  repeated string names = 2;     // 名字列表，ID = 列表中位置 (1-indexed)
}
```
没有 varint ID 字段！ID 就是字段 2 中名字的排列序号。

### 3. Build 数据 (Protobuf)
```
GET https://poe.ninja/poe2/api/builds/{snapshotId}/search?overview={league}
```
每次返回 100 条 build（当前排序下的前 100）。暂未发现分页参数。

## Protobuf 数据结构

### 顶层包装

```
outer_message {
  bytes field_1 = the entire inner message (length-delimited)
}
```

所有数据嵌套在 field 1 的一个 length-delimited 字段中。

### 内层消息结构

| Field | 内容 | 格式 |
|-------|------|------|
| 1 | 总记录数 (124,092) | varint |
| 2 | 8 个字典 (class, weaponmode, items, skills, skillmodes, keypassives, anointed, allskills) | repeated message |
| 3 | 35 个属性元数据 (name, min/max range) | repeated message |
| 4 | 12 个搜索过滤器元数据 | repeated message |
| **5** | **核心：列式 BD 数据 (11 列 × 100 条)** | repeated message |
| 6 | 7 个字典 schema (含 hash) | repeated message |
| 7 | 7 个列元数据定义 (UI 表头) | repeated message |
| 8 | 9 个字典实例定义 | repeated message |
| 9 | 37 个额外列定义 | repeated message |
| 10 | 7 个列名字符串 | repeated string |
| 11 | 211 个技能 DPS 头部 (含 min/max) | repeated message |

### Field 5: 列式数据（核心）

**这是整个 protobuf 最关键的字段**，使用列式存储（而非行式）。

```
message ColumnarData {
  // 11 列, 每列包含 100 条 build 的该属性值
  repeated Column columns = 5;
}

message Column {
  string name = 1;              // 列名: "name", "account", "class", ...
  repeated Value values = 2;    // 100 个值, 每个对应一条 build
}

message Value {
  oneof {
    string text = 1;            // 字符串值 (名字、EHP显示)
    int64 number = 2;           // 数值 (等级、生命、职业ID)
    bytes packed_data = 3;      // packed varint 列表 (技能列表、天赋列表、DPS)
  }
}
```

**11 列的具体内容**:

| 索引 | 列名 | 类型 | 说明 |
|------|------|------|------|
| 0 | name | field 1 (string) | 角色名 |
| 1 | account | field 1 (string) | 账户名 |
| 2 | class | field 2 (varint) | 职业 ID (0=未升华) |
| 3 | skills | field 3 (packed varint) | 技能宝石 ID 列表 |
| 4 | keypassives | field 3 (packed varint) | 核心天赋 ID 列表 |
| 5 | level | field 2 (varint) | 等级 |
| 6 | life | field 2 (varint) | 生命值 |
| 7 | energyshield | field 2 (varint) | 能量护盾 |
| 8 | ehp | field 1 (string) | EHP 显示字符串 ("29k", "15k") |
| 9 | dps | mixed (field 1+2+3) | DPS 显示字符串 + 计数 + packed 数据 |
| 10 | ehp | field 1 (string) | EHP 重复列 (备用) |

### Field 11: DPS 头部

```
message DpsHeader {
  string skill_name = 1;     // "dps", "dps-Herald of Ice", "dps-Tornado Shot"...
  double min_dps = 2;        // 所有 build 中该技能的最低 DPS
  double max_dps = 3;        // 所有 build 中该技能的最高 DPS
}
```

### 技能列表 (packed varint 编码)

技能列 (column 3) 使用 protobuf packed repeated 编码：
- 外层: field 3, wire type 2 (length-delimited)
- 内层: 连续的 varint 值, 每个是一个 gem 字典 ID
- ID=0 表示空位
- 第一个非零 ID 通常是主技能

### DPS 列 (column 9) 结构

```
message BuildDps {
  string total_display = 1;    // "783k", "27M"
  int32 skill_count = 2;       // 有 DPS 数据的技能数
  bytes per_skill_data = 3;    // packed varint (格式待进一步确认)
}
```

## 关键注意事项

1. **字典和 build 数据中的 ID 是同一个命名空间**：gem 字典 ID 38 = "Tectonic Slams"，build 数据中的技能 ID 38 也是这个技能。

2. **class_id=0 表示未升华**：空字节 `b''` 或 varint 0 都表示角色尚未选择升华职业。

3. **EHP 和 DPS 使用缩写字符串**："29k" = 29,000，"27M" = 27,000,000。需要解析后才能用于计算。

4. **API 每次返回 100 条**：这是当前排序下的前 100 条。无法通过 URL 参数翻页，需修改排序/筛选条件获取更多数据。

5. **curl 而非 requests**：Windows 上 Python 的 requests 库连接 poe.ninja 会报 SSL EOF 错误，但 curl 正常工作。所有 HTTP 调用使用 `subprocess.run(["curl", ...])`。

6. **字典 hash 硬编码**：字典 hash 来自 build 数据 protobuf 的 field 6。这些 hash 在 poe.ninja 更新时可能会变化，届时需要重新提取。

## 数据文件位置

- 解析器: `backend/scripts/parse_poeninja.py`
- 输出数据: `backend/data/builds/poeninja_{league}.json`
- 字典缓存: `backend/data/dict_ascendancy.json`

## 使用方式

```bash
# 从 API 获取 (Fate of the Vaal)
python scripts/parse_poeninja.py --league vaal --limit 100

# 从本地 .pb 文件解析
python scripts/parse_poeninja.py --from-file /path/to/data.pb --limit 50

# 指定输出路径
python scripts/parse_poeninja.py --league vaal -o data/builds/vaal_top100.json
```

## 更新维护

当 poe.ninja 更新或新赛季到来时：

1. **检查字典 hash**：抓一份 build 数据 protobuf，检查 field 6 中的 hash 是否有变化
2. **更新 DICT_HASHES**：在 `parse_poeninja.py` 中更新对应的 hash 值
3. **检查列结构**：运行一次 `--limit 5`，验证 field 5 的列数和列名是否变化
4. **检查新赛季**：在 `LEAGUES` 字典中添加新赛季的 slug→overview 映射

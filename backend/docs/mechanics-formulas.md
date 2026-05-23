# POE2 伤害与防御计算公式文档

从 Path of Building 2 (PoB2) 源码提取的真实 POE2 计算公式。

**最后更新**: 2026-05-23
**数据源**: PoB2 `CalcOffence.lua` + `CalcDefence.lua`

## 伤害计算 (CalcOffence) — 9 步链

```
基础伤害 → 增伤 → MORE → 暴击 → Lucky → 全局 → 抗性/穿透 → 穿刺 → DoT → 斩杀
```

### 1. 基础伤害组装
- `base_damage = weapon_damage + added_damage`
- `combined_damage = (base_min..base_max) × base_multiplier`

### 2. 增伤 (INC) 阶段
- `inc_multiplier = 1 + (sum of all increased/reduced modifiers) / 100`
- 包含: 伤害类型 INC, 全局 INC, 武器 INC, 元素 INC, 物理 INC 等

### 3. MORE 阶段
- `more_multiplier = product(1 + more_i / 100)` — 所有 MORE 相乘
- 来源: 辅助宝石、升华天赋、装备词缀

### 4. 暴击阶段
- `effective_crit_chance = min(base_crit × crit_chance_mult, crit_cap)`
- `crit_multiplier = base_crit_multi × crit_multi_mult`
- `lucky_crit` 效果: 两次判定取较高者
- double/triple damage chance 独立判定

### 5. Lucky 机制
- Lucky hit 对非暴击伤害: 两次随机取高值
- 有效增伤 ≈ 33% for uniform distribution

### 6. 全局伤害倍率
- `global_damage_multiplier` — 特定机制 (如 Pain Attunement)

### 7. 抗性与穿透
- `effective_resistance = enemy_resistance - penetration`
- `pen_multiplier = 1 - effective_resistance / 100` (如果抗性 ≥ 0)
- 诅咒/曝露 先降低敌方抗性

### 8. 穿刺 (Impale)
- `impale_damage = physical_damage × impale_effect × impale_stacks × impale_chance`
- 穿刺存储 10% 物理伤害，后续 5 次击中释放

### 9. 斩杀 (Cull)
- 目标生命 < cull_threshold 时直接击杀
- `cull_multiplier = 1 / (1 - cull_percent)` 等效增伤

## 防御计算 (CalcDefence) — 15 个子系统

### 护甲 (Armour)
```
PDR% = Armour / (Armour + Raw_Damage × ArmourRatio)
```
- ArmourRatio 默认 5 (POE2 可能不同)
- 元素伤害减免使用更低的护甲效果

### 闪避 (Evasion)
```
HitChance% = clamp(Accuracy × 1.25 / (Accuracy + Evasion × 0.3) × 100, 5, 100)
```
- 闪避熵系统: 使用伪随机避免连续命中
- 致盲: 降低敌方命中率

### 能量护盾 (ES)
```
RechargeRate = ES × RechargeBase × (1 + inc_recharge/100) × more_recharge
RechargeDelay = base_delay / (1 + faster_start/100)
```
- 默认充能延迟: 4 秒 (受伤后重置)
- Chaos Inoculation: ES 免疫混沌伤害

### 格挡 (Block)
- 攻击格挡和法术格挡独立计算
- 格挡上限 75% (Glancing Blows 翻倍但只减免部分伤害)
- 格挡恢复时间因职业而异

### 抗性
```
EffectiveRes = base_res + sum(res_mods) - sum(penetrations)
```
- 元素抗性上限 75% (可提升至 90%)
- 混沌抗性上限 75%
- 最大抗性提升词缀增加上限

### 伤害转化 (Damage Taken As)
```
FinalDamage = Physical × (1 - %_as_fire - %_as_cold - %_as_light) + Fire_Damage + ...
```
- 伤害按转化比例重新分配
- 可配合高抗性类型使用

### Mind Over Matter (MoM)
```
DamageToMana = raw_damage × MoM_percent
```
- 40% 伤害从魔力扣除 (POE2 值)

### Ward
- 一层额外护盾，承受第一次命中后消失
- 充能后恢复

### 异常状态避免
```
Avoidance% = sum(avoid_chance) - sum(inflict_chance)
```
- 元素异常: 冰冻、感电、点燃
- 非元素: 中毒、流血

### 眩晕门槛
```
StunThreshold = max_life × (1 + stun_threshold_mod/100)
```
- ES 角色使用 ES 替代生命计算

### 偏转 (Deflect)
- POE2 新机制，类似格挡但效果不同
- 减免部分伤害而非完全免疫

### EHP / MaxHit
```
EHP = Life / (1 - mitigation_rate)
MaxHit = pool / (1 - max_res/100) / (1 - other_mitigation)
```

### 伤害减免链 (Mitigation Chain)
1. 格挡/偏转 (完全减免)
2. 护甲/抗性 (百分比减免)
3. 吸收 (Ward, Molten Shell)
4. MoM (分流到魔力)
5. 最终 Life/ES 扣除

## 代码位置

- 伤害公式实现: `backend/app/agents/tools.py` → `calculate_damage()`
- 估算辅助函数: `backend/app/agents/graph.py` → `BuildAgentNodes`
- 机制文档: `backend/data/mechanics_defence.txt`

## 技能伤害数据

`graph.py` 中的 `_SKILL_DAMAGE_TABLE` 包含 40+ 技能的实测基础伤害范围、暴击率和施法时间，用于 BD 生成时的伤害估算。

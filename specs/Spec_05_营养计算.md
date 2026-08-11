# Spec_05_营养计算

## 一句话目标

> 计算并保存每条菜谱整份配方的 9 项营养，并按用户档案和餐次提供单人单餐营养目标，供后续菜单编排使用。

## 数据模型

### 菜谱克重与营养

`recipe_ingredients` 新增字段：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| resolved_quantity_g | decimal | 必填；通常大于 0，仅营养排除项允许为 0 |
| is_quantity_estimated | boolean | 必填；是否经过估算或单位换算 |
| is_nutrition_excluded | boolean | 必填；仅纯水及其明确别名为 true，此时 resolved_quantity_g 必须为 0 |

保留 `quantity_g` 表示原始文本直接给出的克重，不改变其现有语义。

`RecipeComplete.json` 中每道菜增加 `ingredient_quantity_resolutions`，按标准食材名保存正式导入所需的最终结果：

| 字段 | 约束 |
| --- | --- |
| resolved_quantity_g | 必填；非营养排除项必须大于 0 |
| is_quantity_estimated | 必填；单位换算、统计或无参考估算为 true |
| is_nutrition_excluded | 必填；仅纯水允许为 true |
| calculation_path | 必填；从原始用量到最终克重的完整取值路径 |
| reference_source | 必填；原菜谱、内部严格样本、官方资料或“无权威来源估算”的明确说明 |
| ingredient_weight_distribution | 必填；同食材最终克重的样本数、分位数、均值和常见值；纯水记录为营养排除分布 |

`ingredient_quantity_resolutions` 必须与 `ingredients` 一一对应，不得缺少、重复或包含额外食材。正式导入直接校验并使用该最终结果，不再读取单位规则或菜谱覆盖中间文件。

**recipe_nutrition**（每条菜谱一行）

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| recipe_id | integer | 主键、外键，唯一对应 recipes.id |
| energy_kcal | decimal | 必填，非负，整份配方能量 |
| protein_g / fat_g / carbohydrate_g / fiber_g | decimal | 必填，非负，整份配方克数 |
| sodium_mg / calcium_mg / iron_mg / cholesterol_mg | decimal | 必填，非负，整份配方毫克数 |

### 用户单餐营养目标

`user_profiles` 新增 `is_menstruating: boolean/null`：女性 50–64 岁必填，其他用户必须为 null。

**profile_dri_targets**（每个用户 × 餐次 × 营养素一行）

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| profile_id | integer | 外键，关联 user_profiles.id |
| meal_period | string | 早餐、午餐、晚餐 |
| nutrient | string | 9 项营养字段之一 |
| status | string | available、not_established |
| unit | string | kcal、g、mg |
| target_value | decimal/null | 推荐目标；没有单值目标时为 null |
| lower_bound / upper_bound | decimal/null | 规划下限、上限；未建立时为 null |
| target_basis / lower_basis / upper_basis | string/null | EER、RNI、AI、AMDR、PI、UL |

目标语义固定如下：

| 营养素 | target | lower / upper |
| --- | --- | --- |
| 能量 | EER | null / null |
| 蛋白质 | RNI | AMDR / AMDR |
| 脂肪、碳水 | null | AMDR / AMDR |
| 膳食纤维 | null | AI / AI |
| 钠 | AI | null / PI |
| 钙、铁 | RNI | null / UL |
| 胆固醇 | null | null / null；status=not_established |

## 确定性计算规则

- 菜谱营养按 `Σ(食材每100g营养 × resolved_quantity_g ÷ 100)` 计算，不考虑烹饪损耗、吸油、出成率或可食部变化。
- 明确质量及其求和、明确括号克重为非估算；范围取中点，约数取标示值，体积、勺、计数单位、内部统计和无权威来源估算均标记为估算。
- 纯水保留菜谱关联但标记 `is_nutrition_excluded=true`、克重为 0，并且不参与营养汇总；椰子水、汤汁、饮料等不得排除。
- 禁止全局 `ml=1g`、统一勺重或统一单个重量；最终克重必须保存其实际采用的食材、单位、规格、统计或菜谱级取值路径。
- 外部参考、内部统计及无权威来源估算必须保存完整计算路径和来源说明，并始终标记为估算；不得表述为当前菜谱精确质量。无权威来源估算允许参与后续计算，但必须在数据和比赛材料中明确披露。
- 每条菜谱使用的食材必须同时具备 `resolved_quantity_g` 和完整 9 项每 100g 营养；任一缺失时整批导入失败。
- 中间计算使用 Decimal 且不舍入；派生克重、菜谱营养和单餐目标最终使用 `ROUND_HALF_UP` 保留两位小数。
- DRI 使用《中国居民膳食营养素参考摄入量（2023版）》表 9–18、20–23；能量按 `1 MJ=239 kcal`，蛋白质/碳水按 4 kcal/g、脂肪按 9 kcal/g 换算 AMDR。
- 早餐、午餐、晚餐分别取每日参考值的 30%、40%、30%；所有非 null 目标和上下界均按餐次比例换算。
- 支持 18–29、30–49、50–64、65–74、75+ 岁成人，以及 18–49 岁孕早期（1–12 周）、孕中期（13–27 周）、孕晚期（28–42 周）和哺乳期用户。
- 劳动强度低、中、高依次对应 PAL I、II、III；65 岁及以上没有 PAL III 官方值，因此高劳动强度不支持计算。

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况（状态码） |
| --- | --- | --- | --- |
| 扩展 import_basic_data | 含最终食材克重证据的 RecipeComplete.json、食材营养 CSV、用户档案 JSON、DRI CSV | 在同一事务写入基础表和派生表，返回各表数量 | 400：文件、克重、证据路径、营养或档案无法计算；409：键冲突；500：数据库失败 |
| NutritionService.get_recipe_nutrition | 非空、无重复的 recipe_names | 按输入顺序返回 RecipeNutrition[] | 400：输入非法；404：任一菜谱不存在；500：数据库失败 |
| NutritionService.get_meal_nutrition_targets | 正整数 profile_id；早餐/午餐/晚餐之一 | 返回该用户该餐次的 9 项 NutrientTarget | 400：输入非法；404：档案不存在；500：数据库失败或已存在档案缺少完整的预计算目标 |

FDC API 只用于生成和审核静态数据；正式导入和查询不访问外部网络，也不读取 FDC API Key。

正式运行只读取最终静态数据，不读取数据准备阶段的单位规则、菜谱覆盖或网络来源。每条估算值通过 `is_quantity_estimated`、`calculation_path` 和 `reference_source` 保留可追溯性。

## 边界（每条之后会变成一条测试）

- 精确质量、质量求和和明确括号克重不标估算；范围、约数、体积、勺、计数和人工覆盖标估算。
- 任一菜谱食材缺少最终克重、取值路径、参考来源、分布或任一营养值时，整批导入回滚。
- 9 项菜谱营养按整份配方汇总并统一保留两位小数。
- 菜谱名列表为空、含空字符串或重复值返回 400；任一名称不存在时不返回部分结果，整体返回 404。
- 普通成人、50–64 岁女性、65 岁以上、孕早/中/晚期和哺乳期分别命中对应 DRI 规则。
- 女性 50–64 岁缺少经期状态、非法孕周、非女性妊娠、妊娠与哺乳并存、65 岁以上高劳动强度返回 400。
- 早餐、午餐、晚餐分别返回每日参考值的 30%、40%、30%；下午茶返回 400。
- DRI 结果始终包含 9 项；胆固醇状态为 not_established，目标和上下界均为 null。
- 数据库查询或写入失败返回 500，不静默返回空结果。
- 不支持计算的档案组合在导入阶段返回 400 并整批回滚；运行时档案存在但缺少完整预计算目标属于数据完整性错误，返回 500。

## 明确不做

- 不判断营养是否达标，不按营养过滤、选择或排序菜谱。
- 不执行 CP-SAT 菜单编排、整数缩放、软硬约束或权重计算。
- 不推导菜谱人数、单人份或成品每 100g 营养；本 Spec 输出整份配方，配方比例留给 Spec06。
- 不根据疾病、体检指标、健康目标或减重需求修改基础 DRI。
- 不处理下午茶营养目标，不调用运行时外部营养 API，不提供 fallback。

---
**三条自检**：①规则均可直接转成测试。②正文可在 2 分钟内审完。③营养计算与菜单规划边界已划定。

# Spec_03_约束整合

## 一句话目标

> 整合健康档案约束与单轮对话约束，生成供后续菜品过滤和菜单编排使用的统一结构。

## 数据模型

### IntegratedConstraints

| 字段                   | 类型                 | 约束                                               |
| ---------------------- | -------------------- | -------------------------------------------------- |
| profile_id             | integer              | 来自 Spec_01                                      |
| dialogue_id            | integer              | 来自 Spec_02                                      |
| meal_periods           | string[]             | 保持 Spec_02 的值和顺序                            |
| diner_count            | integer/null         | 保持 Spec_02 的值                                  |
| max_total_time_minutes | integer/null         | 保持 Spec_02 的值                                  |
| available_ingredients  | string[]             | 保持 Spec_02 的值和顺序                            |
| allergens              | string[]             | 保持 Spec_01 的值和顺序，作为全餐硬排除词          |
| dishes                 | IntegratedDish[]     | 至少一项，顺序与 Spec_02 一致                      |
| has_conflicts          | boolean              | 等于 conflicts 是否非空                            |
| conflicts              | ConstraintConflict[] | 无冲突时为 []                                      |

### IntegratedDish

| 字段                 | 类型                    | 约束                                      |
| -------------------- | ----------------------- | ----------------------------------------- |
| count                | integer/null            | 保持 Spec_02 的值                         |
| dish_type            | string                  | 保持 Spec_02 的值                         |
| taste_preferences    | object<string, boolean> | 档案口味与当前 Dish 口味合并后的最终值   |
| cuisines             | string[]                | 保持 Spec_02 的值和顺序                   |
| effects              | string[]                | 保持 Spec_02 的值和顺序                   |
| special_populations  | string[]                | 档案人群在前，当前 Dish 的对话人群在后   |
| required_ingredients | IngredientRequirement[] | 保持 Spec_02 的值和顺序                   |

### ConstraintConflict

| 字段                | 类型                  | 约束                                                         |
| ------------------- | --------------------- | ------------------------------------------------------------ |
| code                | string                | 固定为 allergen_required_ingredient                          |
| dish_index          | integer               | 冲突 Dish 的零基索引                                         |
| profile_path        | string                | 格式为 allergens[n]                                          |
| dialogue_path       | string                | 格式为 dishes[n].required_ingredients[m].value               |
| allergen            | string                | 冲突的过敏词                                                 |
| required_ingredient | IngredientRequirement | 冲突的完整必需食材约束                                       |
| dialogue_evidence   | string                | dialogue_path 在 Spec_02 evidence 中对应的用户原文            |

## 接口

| 动作                                   | 输入                                           | 成功返回              | 失败情况 |
| -------------------------------------- | ---------------------------------------------- | --------------------- | -------- |
| ConstraintIntegrationService.integrate | Spec_01、Spec_02 的已校验输出                  | IntegratedConstraints | 400：输入不符合对应 Spec |

## 整合规则

- 档案口味作为默认值，当前 Dish 明确表达的同名口味优先；档案人群与 Dish 人群合并去重。
- allergens 是全餐硬排除约束；与 required_ingredients.value 完全同名时保留双方并记录冲突。
- 存在冲突的结果必须先完成用户确认，才能进入后续过滤和编排。

## 边界

- 空档案约束不改变对话业务约束。
- 某个 Dish 的口味覆盖不影响其他 Dish。
- “海鲜”与“虾”、“坚果”与“花生”等非同名关系不记录冲突。
- available_ingredients 与 allergens 同名不记录冲突，因为可用食材不表示必须使用。
- 冲突所需的 evidence 路径或原文缺失时，输入视为不符合 Spec_02，返回 400。

## 明确不做

- 不分类或展开过敏词，不处理知识图谱关系。
- 不执行菜品过滤、菜单编排、冲突确认或多轮约束更新。

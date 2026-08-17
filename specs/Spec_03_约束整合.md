# Spec_03_约束整合

## 一句话目标

> 整合健康档案约束与已校验的单轮或多轮对话约束，生成供后续菜品过滤和菜单编排使用的统一结构。

## 数据模型

### IntegratedConstraints

| 字段                   | 类型                 | 约束                                               |
| ---------------------- | -------------------- | -------------------------------------------------- |
| profile_id             | integer              | 来自 Spec_01                                      |
| dialogue_id            | integer              | 来自 Spec_02 或 Spec_08                            |
| meal_periods           | string[]             | 保持对话约束的值和顺序                             |
| diner_count            | integer/null         | 保持对话约束的值                                   |
| total_dish_count       | integer/null         | Spec_08 输入原值透传；Spec_02 输入补 null          |
| max_total_time_minutes | integer/null         | 保持对话约束的值                                   |
| max_difficulty         | string/null          | Spec_08 输入原值透传，只允许简单、中等、null；Spec_02 输入补 null |
| available_ingredients  | string[]             | 保持对话约束的值和顺序                             |
| allergens              | string[]             | 保持 Spec_01 的值和顺序，作为全餐硬排除词          |
| dishes                 | IntegratedDish[]     | 至少一项，顺序与对话约束一致                       |
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
| dialogue_evidence   | string                | dialogue_path 在对应 Spec evidence 中的用户原文                |

## 接口

| 动作                                   | 输入                                           | 成功返回              | 失败情况 |
| -------------------------------------- | ---------------------------------------------- | --------------------- | -------- |
| ConstraintIntegrationService.integrate | Spec_01 与 Spec_02 或 Spec_08 的已校验输出     | IntegratedConstraints | 400：输入不精确符合其中一种对话约束结构 |

本服务是纯整合层，不访问数据库。对话输入必须精确匹配 Spec_02 旧字段集合或 Spec_08 `merged_constraints` 新字段集合之一,不得缺少字段、携带未知字段或只携带 total_dish_count、max_difficulty 中的一部分。食材标准名和非空食材类别由对应提取阶段根据数据库完成动态校验；本服务只复核可独立验证的结构、枚举、证据和概念值，并信任已经校验的动态食材值。

## 整合规则

- 档案口味作为默认值，当前 Dish 明确表达的同名口味优先；档案人群与 Dish 人群合并去重。
- Spec_08 的 total_dish_count、max_difficulty 原值透传；Spec_02 不增加字段，其输入在整合输出中统一补为 null。
- allergens 是全餐硬排除约束；与 required_ingredients.value 完全同名时保留双方并记录冲突。
- 存在冲突的结果必须先完成用户确认，才能进入后续过滤和编排。

## 边界

- 空档案约束不改变对话业务约束。
- 完整 Spec_02 输入成功整合且输出 total_dish_count=null、max_difficulty=null；完整 Spec_08 输入原样透传两个字段。
- Spec_02 字段集合额外混入一个新字段、Spec_08 字段集合缺少任一新字段或出现未知字段时均返回 400。
- 某个 Dish 的口味覆盖不影响其他 Dish。
- “海鲜”与“虾”、“坚果”与“花生”等非同名关系不记录冲突。
- available_ingredients 与 allergens 同名不记录冲突，因为可用食材不表示必须使用。
- 冲突所需的 evidence 路径或原文缺失时，输入视为不符合对应对话 Spec，返回 400。

## 明确不做

- 不分类或展开过敏词，不处理知识图谱关系。
- 不执行菜品过滤、菜单编排、冲突确认或多轮约束更新；不修改 Spec_02 的输出契约。

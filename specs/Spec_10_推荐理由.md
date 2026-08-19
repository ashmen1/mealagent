# Spec_10 推荐理由

## 一句话目标

> 合并最终选菜、标签命中、整桌营养评分和已应用健康约束，为每道入选菜及整桌菜单生成固定模板、结构化且可追溯的推荐理由；不解释未入选菜品。

## 数据模型

服务接收同一业务链路中的菜品筛选结果和菜单规划结果。调用方保证二者属于同一次规划；服务不尝试证明该前提，只按菜品组索引和菜名完成回溯。两份结果允许包含其他字段，服务只校验和读取下表字段。

### 输入字段

| 来源 | 字段 | 约束 |
|---|---|---|
| 菜品筛选结果 | `dishes` | 数组；每个元素为一个候选数组 |
| 候选 | `recipe_name` | 非空字符串 |
| 候选 | `matched_tags` | 不重复的非空字符串数组，允许 `[]` |
| 候选 | `matched_groups` | 不重复的非空字符串数组，允许 `[]`；支持范围在关联阶段校验 |
| 菜单规划结果 | `profile_id / dialogue_id` | 正整数，不接受布尔值 |
| 菜单规划结果 | `selected_dishes` | 非空数组；菜名不得重复 |
| 最终菜品 | `dish_constraint_index` | 非负整数，不接受布尔值 |
| 最终菜品 | `recipe_name` | 非空字符串；其重复携带的 `matched_tags` 不读取、不比较 |
| 菜单规划结果 | `nutrition_score` | 0～16 的整数，不接受布尔值 |
| 菜单规划结果 | `nutrient_grades` | 至少完整包含8个计分营养项；其他项忽略 |
| 营养等级 | `actual_value / grade / score` | 非负 `Decimal`；等级与分数只能为 excellent/2、normal/1、bad/0 |
| 菜单规划结果 | `applied_health_constraints` | 不重复的非空字符串数组；无则 `[]`；支持范围在组装阶段校验 |

8个计分营养项及固定顺序为：`energy_kcal`（能量）、`protein_g`（蛋白质）、`fat_g`（脂肪）、`carbohydrate_g`（碳水化合物）、`fiber_g`（膳食纤维）、`sodium_mg`（钠）、`calcium_mg`（钙）、`iron_mg`（铁）。胆固醇和其他额外营养字段不进入推荐理由。

### 输出字段

| 类型 | 必需字段与约束 |
|---|---|
| `RecommendationReasonResult` | `profile_id`、`dialogue_id`、`dish_recommendations: DishRecommendation[]`、`menu_reasons: MenuReason[]` |
| `DishRecommendation` | `dish_constraint_index`、`recipe_name`、`reasons: TagMatchReason[]` |
| `TagMatchReason` | `reason_type="tag_match"`、`matched_group`、`matched_tags`、`sources`、`text` |
| `HealthConstraintReason` | `reason_type="health_constraint"`、`constraint`、`rule`、`sources`、`text` |
| `NutritionSummaryReason` | `reason_type="nutrition_summary"`、`nutrition_score`、`max_score=16`、`nutrient_details`、`sources`、`text` |
| `NutrientDetail` | `nutrient`、中文 `label`、`menu_total_value`、`unit`、`grade`、`grade_label`、`score`、`source` |
| `ReasonSource` | `component` 为 `dish_filtering` 或 `menu_planning`；`paths` 为至少一项的输入相对字段路径 |

营养明细保留整桌实际值的原始精度。单位固定为：能量 kcal；蛋白质、脂肪、碳水化合物、膳食纤维 g；钠、钙、铁 mg。等级文案固定映射为 excellent→优秀区间、normal→正常区间、bad→正常区间外。

### 来源路径

- 标签理由固定包含两项来源：`menu_planning` 的 paths 为 `selected_dishes[{最终菜序号}].dish_constraint_index`、`selected_dishes[{最终菜序号}].recipe_name`；`dish_filtering` 的 paths 为 `dishes[{菜品组索引}][{候选序号}].matched_tags`、`dishes[{菜品组索引}][{候选序号}].matched_groups`。
- 健康理由来源为 `menu_planning` 的 `applied_health_constraints[{约束序号}]`。
- 营养摘要来源为 `menu_planning` 的 `nutrition_score`；每项营养明细来源为 `menu_planning` 的 `nutrient_grades.{营养字段名}`。
- 路径中的序号均使用输入数组的零基索引，不因输出排序重新编号。

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| `RecommendationReasonService.build` | `dish_filtering_result: object`、`menu_planning_result: object` | `RecommendationReasonResult` | 400：所需字段缺失、类型、范围或重复性非法；500：链路数据无法回溯、标签关系非法、缺少固定模板或营养总分不一致 |

服务构造函数不接收依赖；`build` 只读取两个输入对象，不访问数据库、知识图谱、求解器、LLM或外部网络，也不修改输入。

## 边界（每条之后会变成一条测试）

- 最终菜品只在 `dishes[dish_constraint_index]` 中按菜名查找；找到0个或多个候选均返回500，不跨组搜索。
- 未选中候选只读取 `recipe_name` 用于定位，不校验或解释其标签字段。
- 标签理由以候选的 `matched_tags / matched_groups` 为唯一依据；复用菜品筛选契约中的 `TAG_TO_GROUP` 将标签归组。未知标签、未知标签组、组内无对应标签或标签所属组未声明均返回500。
- 每个命中组生成一条理由；菜品顺序保持最终选择顺序，理由顺序固定为餐次、口味、菜系、功效、人群，组内标签保持原顺序并用 `、` 连接。无标签菜返回 `reasons=[]`。
- 标签模板固定为：`{菜名}适合本次{餐次}。`、`{菜名}符合本次{口味}口味偏好。`、`{菜名}符合本次{菜系}偏好。`、`{菜名}匹配本次提出的{功效}功效标签。`、`{菜名}匹配本次提出的{人群}人群标签。`
- 健康理由保持输入顺序并先于营养摘要。高血压固定映射 `sodium_upper_bound`，文案为“考虑高血压需求，本桌菜单规划已将钠摄入上限作为必须满足的条件。”；高血糖固定映射 `macronutrient_energy_ratio`，文案为“考虑高血糖需求，本桌菜单规划已将蛋白质、脂肪和碳水化合物的供能比范围作为必须满足的条件。”；其他已应用约束返回500。
- 8项营养分数之和必须等于 `nutrition_score`，否则返回500；0分项保留在明细中，但不进入正向摘要。
- 营养文案先固定输出“本桌菜单按8项营养指标评分，满分16分，本桌得{总分}分。”；再按固定营养顺序分别汇总优秀项和正常项，文案为“{名称}处于优秀区间（每项2分）”和“{名称}处于正常区间（每项1分）”，两类均有时用 `；` 连接并以 `。` 结尾；某类为空则省略，二者均为空则只输出总分句。
- 没有健康约束时，`menu_reasons` 只包含一条营养摘要；相同输入重复调用必须得到相同结构、顺序、路径和文案。

## 明确不做（划出范围，防 AI 自由发挥）

- 不修改或重新执行菜品筛选、菜单规划及营养评分，不提供fallback。
- 不为未入选菜、菜品类型、食材、制作时间、难度、过敏排除或未应用健康标签生成理由。
- 不比较多份菜单，不声称“优于其他菜单”，不把整桌营养表现归因到单道菜。
- 不输出具体评分上下界、人均营养或个人实际摄入量，不作疗效和医学适用性承诺。
- 不新增HTTP端点、界面、存储、完整链路编排、LLM文案或外部依赖。

---
**三条自检**：①每条输入、关联、排序、模板和错误规则均可写成确定断言。②正文只保留接口、固定映射和不可推导边界，可在2分钟内完成审阅。③证据来源、错误处理和明确不做均已封闭，不允许猜测或降级。

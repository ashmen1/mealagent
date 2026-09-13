# Spec_10 推荐理由

## 一句话目标

> 合并本次实际生效的筛选条件、最终选菜、规划过程、整桌营养评分和已应用健康约束，为每道入选菜及整桌菜单生成固定模板、结构化且可追溯的推荐理由；不解释未执行的档案条件或未入选菜品。

## 数据模型

服务接收同一业务链路中的菜品筛选结果、菜单规划结果和推荐决策上下文。调用方保证三者属于同一次规划；服务不重新执行筛选或规划，只按菜品组索引、菜名、生效约束和实际候选尝试完成回溯。三份输入允许包含其他字段，服务只校验和读取下表字段。

### 输入字段

| 来源 | 字段 | 约束 |
|---|---|---|
| 菜品筛选结果 | `dishes` | 数组；每个元素为一个候选数组 |
| 候选 | `recipe_name` | 非空字符串 |
| 候选 | `matched_tags` | 不重复的非空字符串数组，允许 `[]` |
| 候选 | `matched_groups` | 不重复的非空字符串数组，允许 `[]`；支持范围在关联阶段校验 |
| 菜单规划结果 | `profile_id / dialogue_id` | 正整数，不接受布尔值 |
| 菜单规划结果 | `diner_count` | 正整数，不接受布尔值；用于说明按人数计算的默认菜数 |
| 菜单规划结果 | `selected_dishes` | 非空数组；菜名不得重复 |
| 最终菜品 | `dish_constraint_index` | 非负整数，不接受布尔值 |
| 最终菜品 | `recipe_name` | 非空字符串；其重复携带的 `matched_tags` 不读取、不比较 |
| 菜单规划结果 | `nutrition_score` | 0～16 的整数，不接受布尔值 |
| 菜单规划结果 | `nutrient_grades` | 至少完整包含8个计分营养项；其他项忽略 |
| 营养等级 | `actual_value / grade / score` | 非负 `Decimal`；等级与分数只能为 excellent/2、normal/1、bad/0 |
| 菜单规划结果 | `applied_health_constraints` | 不重复的非空字符串数组；无则 `[]`；支持范围在组装阶段校验 |
| 推荐决策上下文 | `effective_constraints` | 本次传给菜品筛选的完整生效约束副本；不得使用原始档案或未生效字段替代 |
| 推荐决策上下文 | `candidate_attempts` | 本次实际规划尝试，顺序、candidate_limit、outcome和nutrition_score与统一推荐结果一致 |

8个计分营养项及固定顺序为：`energy_kcal`（能量）、`protein_g`（蛋白质）、`fat_g`（脂肪）、`carbohydrate_g`（碳水化合物）、`fiber_g`（膳食纤维）、`sodium_mg`（钠）、`calcium_mg`（钙）、`iron_mg`（铁）。胆固醇和其他额外营养字段不进入推荐理由。

### 输出字段

| 类型 | 必需字段与约束 |
|---|---|
| `RecommendationReasonResult` | `profile_id`、`dialogue_id`、`dish_recommendations: DishRecommendation[]`、`filtering_reasons: FilteringReason[]`、`planning_reasons: PlanningReason[]`、`menu_reasons: MenuReason[]` |
| `DishRecommendation` | `dish_constraint_index`、`recipe_name`、`reasons: TagMatchReason[]` |
| `TagMatchReason` | `reason_type="tag_match"`、`matched_group`、`matched_tags`、`sources`、`text` |
| `FilteringReason` | `reason_type="filtering_rule"`、`rule`、`details`、`affected_recipe_names`、`dish_constraint_indexes`、`sources`、`text` |
| `PlanningReason` | `reason_type="planning_rule"`、`rule`、`details`、`affected_recipe_names`、`dish_constraint_indexes`、`sources`、`text` |
| `HealthConstraintReason` | `reason_type="health_constraint"`、`constraint`、`rule`、`sources`、`text` |
| `NutritionSummaryReason` | `reason_type="nutrition_summary"`、`nutrition_score`、`max_score=16`、`nutrient_details`、`sources`、`text` |
| `NutrientDetail` | `nutrient`、中文 `label`、`menu_total_value`、`unit`、`grade`、`grade_label`、`score`、`source` |
| `ReasonSource` | `component` 为 `constraint_integration`、`dish_filtering`、`menu_planning` 或 `menu_recommendation`；`paths` 为至少一项的输入相对字段路径 |

营养明细保留整桌实际值的原始精度。单位固定为：能量 kcal；蛋白质、脂肪、碳水化合物、膳食纤维 g；钠、钙、铁 mg。等级文案固定映射为 excellent→优秀区间、normal→正常区间、bad→正常区间外。

### 来源路径

- 标签理由固定包含两项来源：`menu_planning` 的 paths 为 `selected_dishes[{最终菜序号}].dish_constraint_index`、`selected_dishes[{最终菜序号}].recipe_name`；`dish_filtering` 的 paths 为 `dishes[{菜品组索引}][{候选序号}].matched_tags`、`dishes[{菜品组索引}][{候选序号}].matched_groups`。
- 筛选理由以 `effective_constraints` 中的非空条件和筛选结果中的入选候选为来源；主食来源理由分别回溯到`dishes[n].required_staple_ingredients`和`dishes[n].excluded_staple_ingredients`；推荐资格门禁与候选顺序分别回溯到 `dish_filtering` 的固定规则标识，不伪造不存在的用户输入路径。
- 规划理由以菜单规划结果、`effective_constraints` 中的数量字段和 `candidate_attempts` 为来源；固定配方、菜名去重、选优顺序和最优性分别回溯到 `menu_planning` 的固定规则标识。
- 健康理由来源为 `menu_planning` 的 `applied_health_constraints[{约束序号}]`。
- 营养摘要来源为 `menu_planning` 的 `nutrition_score`；每项营养明细来源为 `menu_planning` 的 `nutrient_grades.{营养字段名}`。
- 路径中的序号均使用输入数组的零基索引，不因输出排序重新编号。

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| `RecommendationReasonService.build` | `dish_filtering_result: object`、`menu_planning_result: object`、`decision_context: object` | `RecommendationReasonResult` | 400：所需字段缺失、类型、范围或重复性非法；500：链路数据无法回溯、标签关系非法、缺少固定模板或营养总分不一致 |

服务构造函数不接收依赖；`build` 只读取三个输入对象，不访问数据库、知识图谱、求解器、LLM或外部网络，也不修改输入。

## 边界（每条之后会变成一条测试）

- 最终菜品只在 `dishes[dish_constraint_index]` 中按菜名查找；找到0个或多个候选均返回500，不跨组搜索。
- 未选中候选只读取 `recipe_name` 用于定位，不校验或解释其标签字段。
- 标签理由以候选的 `matched_tags / matched_groups` 为唯一依据；复用菜品筛选契约中的 `TAG_TO_GROUP` 将标签归组。未知标签、未知标签组、组内无对应标签或标签所属组未声明均返回500。
- 每个命中组生成一条理由；菜品顺序保持最终选择顺序，理由顺序固定为餐次、口味、菜系、功效、人群，组内标签保持原顺序并用 `、` 连接。无标签菜返回 `reasons=[]`。
- 标签模板固定为：`{菜名}适合本次{餐次}。`、`{菜名}符合本次{口味}口味偏好。`、`{菜名}符合本次{菜系}偏好。`、`{菜名}匹配本次提出的{功效}功效标签。`、`{菜名}匹配本次提出的{人群}人群标签。`
- `filtering_reasons` 只描述本次实际执行的规则，顺序固定为：入选标签匹配、否定口味、菜品类型、时间上限、难度上限、必需食材组、必需主食来源、排除主食来源、可用食材、过敏排除、推荐资格门禁、候选稳定排序。空值对应的条件不生成理由；推荐资格门禁和候选稳定排序每次固定生成。
- 入选标签匹配按 `matched_group + matched_tags` 合并；同一组合影响多道菜时只生成一条整桌理由，`affected_recipe_names` 按最终菜品顺序列出，`dish_constraint_indexes` 去重后保持首次出现顺序。不同标签组合不得错误合并。
- 否定口味说明排除了命中任一否定标签的候选；菜品类型说明精确限定；时间与难度说明采用上限；必需食材保持组间AND及组内all/any语义；可用食材说明只约束核心食材；过敏说明使用档案中过滤成功的原始过敏项，不展开或罗列内部标准食材成员。
- 必需主食来源的 `rule` 固定为 `required_staple_ingredients`，details 原样保存 match 和 items；排除主食来源的 `rule` 固定为 `excluded_staple_ingredients`，details 原样保存排除数组。两者均不得复用普通必需食材 rule。
- 主食项保持约束输入顺序。all 单项文案为“本次主食来源限定为{食材1}。”；all 2项用“和”，3项及以上用“、”分隔前项并在末项前用“和”，文案为“本次主食来源需同时包含{连接结果}。”；any 2项用“或”，3项及以上用“、”分隔前项并在末项前用“或”，文案为“本次主食来源限定为{连接结果}。”。主食来源理由不得写成“菜谱包含”。
- 排除主食来源固定文案为“本次排除以{排除项}作为主食来源的菜谱。”；多个排除项保持输入顺序并用“、”连接。文本保留用户约束中的主食词，不展开罗列内部米饭族成员。
- 过敏固定文案为“已按档案中的{过敏项}过敏信息，在候选筛选阶段排除含相关标准食材的菜谱。”；不得声称治愈、绝对安全或不存在交叉污染。
- 推荐资格固定文案为“本次只从允许推荐的菜谱中选择。”；候选顺序固定文案为“符合条件的候选先按标签命中数从多到少排列，命中数相同时按菜名稳定排序。”
- `planning_reasons` 顺序固定为：菜品数量、菜名去重与固定配方、候选阶段、选优优先级、最优性。实际应用的高血压和高血糖理由仍保留在 `menu_reasons`，最终回答在规划依据中展示，不在 `planning_reasons` 重复生成。
- 菜品数量理由必须区分：显式总菜数、各组显式数量、以及未指定总数时按人数得到的默认菜数；count=null 的组说明至少选择一道，不能写成平均分配。
- 固定规划文案必须披露同名菜不重复，以及“菜谱配方和整份营养按库中固定值计算，本次不调整食材克重”。
- 候选阶段只说明实际结果：首次优先候选达到8分时写“本次在优先候选范围内找到达到营养目标的可行菜单。”；经过扩展后成功写“本次扩大候选范围后找到可行菜单。”；全量候选低于8分但可行时写“本次使用全量候选得到可行菜单，营养得分未达到8分目标。”，不得在面向用户的文本中出现100或300。
- 选优固定文案为“在满足约束的菜单中，依次按营养得分高、正常区间外营养项少、标签命中多、候选顺序靠前进行选择。”；最优性固定文案为“本次返回的是在上述规则下已证明最优的菜单。”
- 健康理由保持输入顺序并先于营养摘要。高血压固定映射 `sodium_upper_bound`，文案为“考虑高血压需求，本桌菜单规划已将钠摄入上限作为必须满足的条件。”；高血糖固定映射 `macronutrient_energy_ratio`，文案为“考虑高血糖需求，本桌菜单规划已将蛋白质、脂肪和碳水化合物的供能比范围作为必须满足的条件。”；其他已应用约束返回500。
- 8项营养分数之和必须等于 `nutrition_score`，否则返回500；0分项保留在明细中，但不进入正向摘要。
- 营养文案先固定输出“本桌菜单按8项营养指标评分，满分16分，本桌得{总分}分。”；再按固定营养顺序分别汇总优秀项和正常项，文案为“{名称}处于优秀区间（每项2分）”和“{名称}处于正常区间（每项1分）”，两类均有时用 `；` 连接并以 `。` 结尾；某类为空则省略，二者均为空则只输出总分句。
- 没有健康约束时，`menu_reasons` 只包含一条营养摘要；未应用健康标签、健康需求、体检指标及其他未进入本次筛选或规划的档案字段不得出现在任一理由中；相同输入重复调用必须得到相同结构、顺序、路径和文案。
- “玉米或红薯作为主食来源”的理由必须使用独立主食rule和文案；普通“想吃带玉米的食物”仍使用required_ingredient_groups理由。

## 明确不做（划出范围，防 AI 自由发挥）

- 不修改或重新执行菜品筛选、菜单规划及营养评分，不提供fallback。
- 不为未入选菜生成理由，不解释空值对应的未启用条件，不解释未应用健康标签、健康需求或体检指标。
- 不比较多份菜单，不声称“优于其他菜单”，不把整桌营养表现归因到单道菜。
- 不输出具体评分上下界、人均营养或个人实际摄入量，不作疗效和医学适用性承诺。
- 不新增HTTP端点、界面、存储、完整链路编排、LLM文案或外部依赖。

---
**三条自检**：①每条输入、关联、合并、排序、模板和错误规则均可写成确定断言。②结构化依据与最终文案一一对应，审核时无需推测隐藏规则。③证据来源、错误处理和明确不做均已封闭，不允许猜测或降级。

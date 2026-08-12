# Spec_06_菜单规划

## 一句话目标

> 将健康约束、菜品候选和营养目标整理为统一输入，使用 CP-SAT 选择一份满足硬约束且营养评分最高的固定配方菜单。

## 数据模型

进入 CP-SAT 前，必须先按菜谱名合并菜品候选与整份营养，形成唯一输入 `MenuPlanningInput`；求解器不接收彼此分散的中间结果。

### MenuPlanningInput

| 字段 | 类型 | 约束（必填？范围？默认？）|
|---|---|---|
| profile_id | integer | 必填，正整数 |
| dialogue_id | integer | 必填，正整数 |
| meal_period | string | 必填，只允许早餐、午餐、晚餐 |
| diner_count | integer/null | 正整数；null 按 1 人 |
| special_populations | string[] | 用户的特殊人群标签；无则 [] |
| dishes | PlanningDish[] | 至少一项，保持用户菜品要求的顺序 |
| nutrient_targets | object<string, NutrientTarget> | 必须完整包含九项单人单餐营养目标 |
| unmatched_allergens | string[] | 正常必须为 [] |

### PlanningDish

| 字段 | 类型 | 约束 |
|---|---|---|
| count | integer/null | 明确数量时为正整数，否则为 null |
| dish_type | string | 菜、汤、主食、小菜、未指定 |
| candidates | PlanningCandidate[] | 已完成过敏、口味、菜系、功效、人群、时间和食材过滤的候选 |

### PlanningCandidate

| 字段 | 类型 | 约束 |
|---|---|---|
| recipe_name | string | 菜谱唯一名 |
| recipe_type | string/null | 菜谱类型 |
| matched_tags | string[] | 本次实际命中的正向标签 |
| nutrition | NutritionValues | 该菜谱固定整份的九项营养 |

`NutrientTarget` 保留 `status`、`target_value`、`lower_bound`、`upper_bound`、`target_basis`、`lower_basis`、`upper_basis`。`NutritionValues` 固定包含能量、蛋白质、脂肪、碳水化合物、膳食纤维、钠、钙、铁、胆固醇。

### MenuPlanningResult

| 字段 | 类型 | 约束 |
|---|---|---|
| profile_id / dialogue_id / meal_period / diner_count | 对应输入类型 | 返回实际采用的值 |
| selected_dishes | PlannedDish[] | 唯一最优菜单，菜名不得重复 |
| total_nutrition | NutritionValues | 选中菜谱整份营养之和 |
| per_person_nutrition | NutritionValues | 整桌营养除以人数，保留两位小数 |
| nutrient_grades | object<string, NutrientGrade> | 九项营养的实际值、等级和分数 |
| nutrition_score | integer | 八项得分之和，范围 0～16 |
| applied_health_constraints | string[] | 实际启用的健康硬约束 |
| unapplied_health_constraints | string[] | 本版未处理的专项标签 |

`PlannedDish` 返回菜品要求索引、菜谱名、菜谱类型、命中标签和固定整份营养。`NutrientGrade.grade` 为 excellent、normal、bad 或 null；对应分数为 2、1、0 或 null。

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况（状态码）|
|---|---|---|---|
| MenuPlanningService.plan | MenuPlanningInput | MenuPlanningResult | 400：输入结构或餐次非法；422：过敏安全、候选数量或营养硬约束无法满足；500：求解器内部错误；503：10 秒内未证明最优 |

本服务只执行输入校验、菜单求解和结果评分，不访问数据库、知识图谱、LLM 或外部网络。

## 边界（每条之后会变成一条测试）

- 输入整合时，所有候选必须已带有同名菜谱的完整九项营养；缺少、重复或错配返回 400，不进入求解。
- `unmatched_allergens` 非空表示过敏硬约束尚未落实，返回 422，并给出未解析词。
- 有效人数为 `diner_count ?? 1`；1～3 人默认分别选 1～3 道，4 人及以上默认选 `人数-1` 道。
- 明确的 count 必须严格满足。存在 count=null 的菜品要求时，每项至少一道，剩余默认名额由求解器分配；全部 count 明确时不再使用默认总数。
- 同一菜谱即使出现在多个候选列表中，整份菜单也只能选择一次。
- 菜谱食材分量和整份营养固定，不允许缩放；整桌营养为选中菜谱营养之和，单人营养目标与范围乘人数后参与比较。
- 正常人的全部营养目标只用于评分，不作为菜单可行性的硬约束。
- 高血压才将钠 PI 升级为硬约束；采用[《成人高血压食养指南（2023年版）》](https://www.nhc.gov.cn/sps/c100088/202301/f01895a06c5349ef999f25da833c166d/files/1732844468193_68545.pdf)的全天钠低于 2000mg 口径，并按餐次和人数换算。
- 高血糖严格满足蛋白质、脂肪、碳水供能比分别为 15%～20%、20%～35%、45%～60%，依据[《成人糖尿病食养指南（2023年版）》](https://www.nhc.gov.cn/sps/c100088/202301/f01895a06c5349ef999f25da833c166d/files/1732844468278_20318.pdf)。多种特殊人群规则同时存在时取交集。
- 高尿酸、备孕本版不增加专项约束，继续使用输入营养目标，并在 `unapplied_health_constraints` 中披露。
- 八项评分如下；胆固醇为 not_established，只展示、不评分：

| 营养素 | excellent（2分） | normal（1分） | bad（0分） |
|---|---|---|---|
| 能量 | EER 90%～110% | EER 80%～120% 中的其余部分 | 其余 |
| 蛋白质 | max(RNI, AMDR下界)～AMDR上界 | 优秀下界80%～优秀上界120%中的其余部分 | 其余 |
| 脂肪、碳水、纤维 | 各自目标范围 | 下界80%～上界120%中的其余部分 | 其余 |
| 钠 | ≤AI | AI～PI | >PI；仅高血压时不可行 |
| 钙、铁 | ≥RNI且≤UL | RNI的80%～100% | <RNI的80%或>UL |

- 多个可行菜单依次按总分高、bad 项少、命中标签多、候选原顺序靠前确定唯一结果。
- 只有已证明最优的菜单可以成功返回；10 秒内未证明最优返回 503，不返回当前次优结果。
- 空候选、候选不足、菜名去重后数量不足或健康硬约束无解均返回 422，不放宽约束重试。

## 明确不做（划出范围，防 AI 自由发挥）

- 不支持下午茶、多餐次、跨天菜单或多位用户分别建档。
- 不返回多份备选菜单，不保存菜单。
- 不缩放配方，不修改食材克重，不推导分食比例。
- 不实现高尿酸、备孕专项规则，不新增 GI、嘌呤、果糖、酒精或叶酸数据。
- 不根据 BMI、体检指标或健康目标临时修改输入营养目标。
- 不重复执行候选菜谱的过敏、口味、菜系、功效、人群、时间或食材过滤。
- 不提供 fallback，不在无解或超时时放宽硬约束。

---
**三条自检**：①规则均可直接转成测试。②正文可在 2 分钟内审完。③输入整合、硬约束、评分和不支持范围均已划定。

# Spec_12 统一推荐链路

## 一句话目标

> 以持久化会话ID为唯一入口，将档案、统一对话约束、确认、整合、筛选、营养规划和推荐理由串成一条确定性生产链路，并用结构化状态区分可推荐与真实业务阻塞。

## 数据模型

### 下游食材约束

统一对话输出中的 `required_ingredient_groups` 原样进入约束整合和菜品筛选，不再接受 `required_ingredients`。

- Dish内各组之间固定为AND。
- `match=all`时菜谱必须满足组内全部项。
- `match=any`时菜谱必须满足组内至少一项。
- ingredient按标准食材名匹配；category按食材类别匹配；concept按概念关系匹配。
- 档案过敏原仍对整道菜做全局硬排除，不因any组存在安全选项而放宽。

### CandidateAttempt

| 字段 | 类型 | 约束 |
|---|---|---|
| candidate_limit | integer/null | 100、300或null；null表示本次已使用全量候选 |
| candidate_counts | integer[] | 各菜品组本次候选数，顺序与Dish一致 |
| outcome | string | infeasible、below_target或accepted |
| nutrition_score | integer/null | 规划成功时为0～16；不可行时为null |

### QualityWarning

| 字段 | 类型 | 约束 |
|---|---|---|
| code | `"nutrition_score_below_target"` | 固定值 |
| nutrition_score | integer | 全量候选下最终得分，0～7 |
| target_score | integer | 固定为8 |

### MenuGenerationResult

所有字段固定存在；当前状态不适用的对象为null、数组为`[]`。

| 字段 | 类型 | 约束 |
|---|---|---|
| session_id | integer | 输入会话ID |
| profile_id | integer | 会话所属档案ID |
| dialogue_id | integer | 等于会话中约束的dialogue_id；尚无首轮约束时等于session_id |
| status | string | in_progress、needs_confirmation、constraint_conflict、unmatched_allergen、empty_candidate、planning_infeasible或recommended |
| confirmation_state | object | 本次读取到的完整确认状态 |
| conflicts | object[] | 约束冲突；无冲突为[] |
| unmatched_allergens | string[] | 图谱无法解析的过敏词；无则[] |
| empty_dish_indexes | integer[] | 全量筛选仍无候选的Dish索引 |
| dish_filtering_result | object/null | 已执行筛选时为全量筛选结果，否则null |
| candidate_attempts | CandidateAttempt[] | 实际规划尝试，按执行顺序 |
| menu_planning_result | object/null | 仅recommended时存在 |
| recommendation_reason_result | object/null | 仅recommended时存在 |
| quality_warnings | QualityWarning[] | 仅全量候选得分仍低于8时一项，否则[] |

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| `MenuRecommendationService.generate` | session_id正整数 | MenuGenerationResult | 400：输入或会话非法；500：配置、数据库、图谱、营养、规划或理由契约异常 |

依赖在Service创建阶段注入，`generate`只接收`session_id`。应用容器公开同一个`MenuRecommendationService`实例及其共享依赖。

## 业务流程

1. 读取确认状态；尚无成功轮次返回in_progress，需要确认餐次返回needs_confirmation。
2. 使用会话绑定的profile_id读取档案并整合约束；冲突返回constraint_conflict。
3. 构造不持久化的生效约束副本：将确认后的唯一餐次写入meal_periods，再执行全量筛选；不得修改会话状态或输入对象。
4. 未解析过敏词返回unmatched_allergen；任一Dish全量候选为空返回empty_candidate。
5. 一次性加载全量候选的营养数据和本餐目标，按筛选结果原顺序构造100、300、全量候选阶段；若某阶段已经等于全量，标记candidate_limit=null且不再重复阶段。
6. 规划返回422时记录infeasible并扩展；规划成功但nutrition_score小于8时记录below_target并扩展；分数达到8时记录accepted并立即结束。
7. 全量仍不可行返回planning_infeasible。全量成功但低于8时仍返回recommended，并附唯一质量警告；不得放宽过敏、健康、数量或其他硬约束。
8. 对最终规划结果调用推荐理由服务；理由使用全量筛选结果回溯，保持既有理由契约。

## 过敏冲突

- 整合层只比较档案标准过敏名与`kind=ingredient`项目的精确同名，不访问图谱或做类别推导。
- all组中任一项目精确冲突即返回对应冲突条目。
- any组仅在全部项目均精确冲突时返回各项目对应的冲突条目；仍有安全选项时整合层不报冲突，由筛选层全局排除含过敏食材的菜谱。
- 冲突中的dialogue_path使用`dishes[i].required_ingredient_groups[j].items[k].value`，证据取该路径对应的会话证据。

## 边界

- 单项all组与任意食材示例使用同一通用逻辑，不为具体食材写特殊规则。
- all/any为空、数量非法、未知match、未知kind或重复项目时整合返回400；ingredient值是否为数据库标准食材已由统一对话服务保证，整合层不重复访问数据库。
- 旧`required_ingredients`输入返回400，不做兼容转换。
- 确认展示中all组用“和”连接、any组用“或”连接、组间用“；”连接。
- 推定餐次只写入本次生效副本，不回写会话；筛选命中的餐次标签可以生成推荐理由。
- 多组候选分别截取前100或前300，不随机采样，不跨组移动候选。
- 相邻阶段候选完全相同时只规划一次；首次就是全量时candidate_limit为null。
- unmatched_allergens、全量空候选和全量规划不可行必须准确区分，不能互相归类。
- 0分和7分产生质量警告；8分和16分不产生质量警告。
- 同一会话状态与相同数据重复调用，状态、候选阶段、菜单和理由完全一致。

## 明确不做

- 不保留旧单轮或旧多轮下游输入，不提供profile_id加任意state的生成入口。
- 不修改菜单规划和推荐理由的既有输入输出契约。
- 不在本阶段增加菜谱推荐资格字段、清理菜谱标签或修改基础数据。
- 不使用随机抽样、LLM文案、规则放宽、候选替换或其他fallback。
- 不保存生成结果、候选阶段或理由，不新增HTTP端点。

---

**三条自检**

1. 通过：每个字段、状态、分组关系、阶段条件与错误分支均有唯一可断言结果。
2. 通过：审核只需定位统一输入、七种状态、三阶段扩展和过敏冲突四部分。
3. 通过：范围止于现有数据上的统一推荐链路；菜谱质量与数据重建明确留到下一阶段。

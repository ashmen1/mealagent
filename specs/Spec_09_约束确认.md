# Spec_09_约束确认

## 一句话目标

> 每次收到或读取对话状态时，计算餐次、人数和菜品数量的生效值；餐次无法确定时固定追问，否则展示全部已知约束并允许进入规划。

## 数据模型

### ConfirmationState

| 字段 | 类型 | 约束 |
|---|---|---|
| status | string | `in_progress`、`needs_confirmation`、`ready_for_planning` |
| merged_constraints | object/null | 原始对话约束，不写入默认值或时间判断结果 |
| planning_context | object/null | 初始会话为 null；其余包含下表全部字段 |
| known_constraints | KnownConstraint[] | 初始会话为 `[]` |
| confirmation | object/null | 仅待确认时非 null |
| message | string/null | 初始会话为 null |

### PlanningContext

| 字段 | 类型 | 约束 |
|---|---|---|
| meal_period | string/null | 早餐、午餐、晚餐；无法确定时为 null |
| meal_period_source | string/null | `explicit`、`current_time` 或 null |
| diner_count | integer | 正整数 |
| diner_count_source | string | `explicit` 或 `default` |
| total_dish_count | integer | 正整数 |
| total_dish_count_source | string | `explicit`、`dish_counts` 或 `default` |

`KnownConstraint` 固定包含 `path、label、value、source`；四项均为字符串，source 只允许 `explicit、current_time、default、derived`。

`confirmation` 固定为：

```json
{
  "reason": "餐次解析返回的原因",
  "options": ["早餐", "午餐", "晚餐"],
  "question": "请确认这次要安排早餐、午餐还是晚餐？"
}
```

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| create_session | profile_id: 正整数 | session_id | 保留底层状态码和错误信息 |
| submit_turn | session_id: 正整数、user_message: 非空字符串 | session_id、turn_number、ConfirmationState | 保留底层状态码和错误信息 |
| get_session | session_id: 正整数 | session_id、profile_id、ConfirmationState | 保留底层状态码和错误信息 |

会话服务和餐次解析服务在创建确认服务时注入。依赖无效返回 500；底层异常统一转换为确认服务异常，并保留原 `status_code` 和错误信息。

## 生效规则

| 维度 | 优先级与默认值 |
|---|---|
| 餐次 | 用户明确值优先；否则每次调用时按当前时间判断。无法唯一确定则待确认 |
| 人数 | 用户值优先；缺失时为 1 |
| 菜品数量 | 明确总数优先；否则全部分组数量明确时取合计；仍有未定量组时取 `max(人数默认菜数, 明确数量之和 + 未定量组数)` |
| 人数默认菜数 | 1～3 人等于人数；4 人及以上等于人数减 1 |

时间判断结果不保存；跨时段再次调用时重新判断。用户明确餐次后不受时间变化影响。

## 固定展示

展示顺序为：餐次、人数、菜品数量、最长制作时间、难度、现有食材，再按原顺序展示各菜品组的数量、类型、口味、菜系、功效、适用人群和必需食材。

- null、空容器、“未指定”、证据、编号和变更声明不展示。
- 数组使用 `、` 连接并保留原顺序。
- 口味按甜、清淡、辣、咸、酸排序，分别显示甜/不甜、清淡/不清淡、辣/不辣、咸/不咸、酸/不酸。
- 人数、菜数、组数量和时间分别显示为 `N人`、`N道`、`N道`、`N分钟以内`。
- 菜品组标签固定为 `菜品组N数量、菜品组N类型、菜品组N口味、菜品组N菜系、菜品组N功效、菜品组N适用人群、菜品组N必需食材`。
- 来源后缀：explicit 无后缀；current_time 为 `（根据当前时间）`；default 为 `（默认）`；derived 为 `（根据各菜品数量合计）`。

每条展示行为：

```text
- {label}：{value}{来源后缀}
```

待确认文案：

```text
已确定：
{展示行}
还需要确认：
请确认这次要安排早餐、午餐还是晚餐？
```

可规划文案：

```text
已确定：
{展示行}
可以开始规划。
```

## 边界

- 初始会话返回 `in_progress、merged_constraints=null、planning_context=null、known_constraints=[]、confirmation=null、message=null`。
- 时间窗口外、多餐次和不支持餐次均返回同一句问题；其他已知约束继续展示。
- 用户回答餐次后重新合并；无关回答仍待确认。
- “两菜一汤”得到 3 道，来源为 `dish_counts`。
- 1 人要求面和小菜两个未定量组时，默认 2 道。
- 2 人要求三道主菜和一个未定量汤组时，默认 4 道。
- 条件齐备后直接返回可规划，不要求再次确认；后续消息仍可修改约束。
- 问题和展示文案不得调用 LLM 生成。

## 明确不做

- 不修改现有会话与餐次解析的公开契约。
- 不保存问题、展示文案、默认值或时间判断结果。
- 不读取健康档案，不调用菜品筛选、营养计算或菜单规划。
- 不引入数据库字段、新依赖或缓存。

---

**三条自检**：①所有规则均有确定输入与输出，可直接测试。②正文以四张表和关键边界为主，可在 2 分钟内审完。③存储、健康档案、后续规划和自由文案均已排除。

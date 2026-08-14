# Spec_07_餐次解析

## 一句话目标

> 优先采用用户明确餐次；未明确时按上海当前时间解析早餐、午餐或晚餐，无法确定则返回结构化待确认结果，供未来智能体与用户确认。

## 数据模型

### MealPeriodResolution

| 字段 | 类型 | 约束（必填？范围？默认？）|
|---|---|---|
| status | string | 必填；resolved 或 needs_confirmation |
| meal_period | string/null | resolved 时为早餐、午餐或晚餐；待确认时为 null |
| source | string | 必填；用户明确时为 explicit，按时间判断时为 current_time |
| reason | string/null | resolved 时为 null；待确认时为 outside_meal_window、multiple_meal_periods 或 unsupported_meal_period |
| options | string[] | resolved 时为 []；待确认时固定为 [早餐, 午餐, 晚餐] |

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况（状态码）|
|---|---|---|---|
| MealPeriodResolutionService.resolve | meal_periods: string[] | MealPeriodResolution | 400：输入不是数组、包含重复或未知餐次；500：时钟、时区或内部执行失败 |

时钟和业务时区在 Service 创建阶段注入，当前业务时区为 `Asia/Shanghai`。服务不访问数据库、Neo4j、LLM 或网络，也不生成确认文案。

## 边界（每条之后会变成一条测试）

- 单个早餐、午餐或晚餐直接返回 resolved/explicit，当前时间不得覆盖。
- 空数组按上海本地时间解析：05:00～10:00 为早餐，11:00～14:00 为午餐，17:00～21:00 为晚餐。
- 时间比较精确到分钟并包含端点，秒和微秒不参与判断。
- 空数组且当前时间不在三个饭点时，返回 needs_confirmation/current_time/outside_meal_window。
- 多个不同餐次返回 needs_confirmation/explicit/multiple_meal_periods；单个下午茶返回 needs_confirmation/explicit/unsupported_meal_period。
- 待确认属于正常业务结果，不抛异常；未来调用方确认餐次后以明确餐次重新解析。
- 重复值、未知餐次或非法时钟结果按接口错误返回，不用当前时间兜底。

## 明确不做（划出范围，防 AI 自由发挥）

- 不修改约束整合、菜品筛选或菜单规划契约，不直接调用这些服务。
- 不生成对话文案、不保存多轮状态、不实现智能体确认流程。
- 不支持下午茶营养目标、夜宵、跨天菜单或按用户档案配置时区。

---
**三条自检**：①每条规则均可直接转成测试。②正文可在 2 分钟内审完。③显式优先、时间窗口、待确认和不支持范围均已划定。

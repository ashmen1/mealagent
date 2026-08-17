# Spec_08_多轮约束提取

## 一句话目标

> 在多轮会话中结合上一轮结构化状态提取并合并菜单约束，记录轮次与最终状态；餐次可唯一解析时允许进入规划。

## 数据模型

### dialogue_sessions 与 dialogue_turns（扩展 Spec_00）

| 字段 | 类型 | 约束 |
|---|---|---|
| dialogue_sessions.id | bigint | 主键，数据库生成 |
| dialogue_sessions.profile_id | bigint | 必填，外键关联 user_profiles.id |
| dialogue_sessions.status | string | in_progress、needs_confirmation、ready_for_planning |
| dialogue_sessions.merged_constraints | JSON/null | 首轮前为 null；此后保存下述九个约束字段，不含 change_actions |
| dialogue_turns.id | bigint | 主键，数据库生成 |
| dialogue_turns.session_id | bigint | 必填，外键关联 dialogue_sessions.id |
| dialogue_turns.turn_number | integer | 正整数；(session_id, turn_number) 唯一 |
| dialogue_turns.user_message | string | 必填，非空 |

### 多轮提取器输出

输入为当前 user_message 和上一状态 merged_constraints，不传历史原文。约束状态严格继承 Spec_02 的 dialogue_id、meal_periods、diner_count、max_total_time_minutes、available_ingredients、dishes、evidence 及全部嵌套结构，并增加：

| 字段 | 类型 | 约束 |
|---|---|---|
| total_dish_count | integer/null | 用户明确的整桌菜品总数；正整数或 null |
| max_difficulty | string/null | 简单、中等或 null，表示菜谱难度上限 |

上述九个约束字段全部必填并写入 merged_constraints；Spec_02 的字段及嵌套枚举不作修改。每轮 LLM 输出在九字段之后增加必填 change_actions；首轮或无变化时为 []。

没有明确分类时返回 Spec_02 的默认 Dish，但该结构占位组不表示用户确认了一个菜品组。`total_dish_count` 是整桌总数，`len(dishes)` 是查询组数，`Dish.count` 是组内明确数量，三者不得混用。

`ChangeAction` 固定包含 field、dish_index、action、evidence：action 为 add、replace、remove；顶层变更填写 field，Dish 变更填写上一状态 dish_index；新增组时两者均为 null 且 action=add。field 只允许 meal_periods、diner_count、total_dish_count、max_total_time_minutes、max_difficulty、available_ingredients；evidence 必须是本轮原文连续片段。

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| MultiTurnConstraintService.create_session | profile_id:正整数 | session_id | 400：输入非法；409：profile 不存在 |
| MultiTurnConstraintService.submit_turn | session_id:正整数、user_message:非空 | session_id、turn_number、status、merged_constraints、missing_requirements | 400：输入或会话非法；500：数据库或 LLM 配置错误；502：LLM 输出、证据或演化非法；503：LLM 不可用 |
| MultiTurnConstraintService.get_session | session_id:正整数 | session_id、profile_id、status、merged_constraints、missing_requirements | 400：会话不存在 |

missing_requirements 由 merged_constraints 实时推导，不落库。SessionFactory、LLM 提取器和 MealPeriodResolutionService 在 Service 创建阶段注入，业务参数只在方法调用时传入。LLM 输出校验失败重试一次，再次失败返回 502；失败轮次不落库。

## 归一与演化规则

| 主题 | 规则 |
|---|---|
| 口味 | 微辣、香辣、麻辣→is_spicy=true；不辣→false；咸鲜→is_salty=true；清爽、别太抢味→is_light=true；别太甜→is_sweet=false。口味键支持新增、覆盖和删除。 |
| 口味冲突 | 一人要求、另一人拒绝同一布尔口味时拆成两个真实查询组，分别保存 true/false，两个 count 均为 null；人数不得写入 count，拆组不表示两套菜单。 |
| 标签 | 暖胃、胃口不好、养胃、健胃消食、便秘→养胃健胃消食；补气血→贫血；公司、上班、下班→上班族；夜宵→晚餐；仪式感、稍微正式点、正式一点→西餐风味。 |
| 难度 | 家常一点、家常菜、简单、简单点→简单；别整得太难做、别太难做、别太复杂、太麻烦不行→中等；难度不限、麻烦点也行、复杂点也能接受→remove 为 null；“复杂”单独出现忽略。max_difficulty 只允许 replace/remove，add 返回 502。 |
| 食材与类型 | “面”保存为 concept，不展开；一桌菜、主菜→dish_type=菜；available_ingredients 只限制核心食材，不要求盐、油、水等辅料齐备，也不表示必须用完。 |
| 人数与时间 | diner_count、max_total_time_minutes 的绝对值用 replace；相对增加要求旧值非空并用 add；解除约束用 remove 置 null。 |
| 整桌数量 | “四个菜”→total_dish_count=4，默认 Dish.count 仍为 null；不得按人数推测总数。数值明确时 replace，已有明确总数后的相对增加用 add，解除限制用 remove。 |
| 指定组数量 | 绝对数量 replace 该组 count。相对增加要求旧 count 非空；若 total_dish_count 非空，同时增加总数和该组 count；旧 count 为 null 时不能相对 add，只接受新的绝对数量。 |
| 未指定组追加 | 已有明确总数时，“再加一个菜”只增加 total_dish_count，不修改任一 count；组内未分配数量由 Spec_06 求解。旧总数为 null 时不能相对 add，只接受新的绝对总数。 |
| 数组与 Dish | 数组 add 追加去重、remove 删除、replace 整体替换；Dish 可 replace、remove，或在末尾 add 新组。适用于所有组的限制复制到每个 Dish。 |
| 明确忽略 | 周末、平时、适合夏天、热乎、牙口不好，以及“大部分食材共用”“不想分开做两套”等跨组组合优化描述不产生字段。 |

`total_dish_count` 非空时，显式 count 之和加上 null 组数不得超过总数；全部 count 明确时，其和必须等于总数，否则返回 502。

## 重放、证据与完整性

- 后续轮逐条重放 change_actions 后必须等于完整新状态；未声明字段和 Dish 必须不变，同一顶层字段或同一旧 Dish 每轮最多一条声明。
- 数值 add 要求旧值非空且新值增大，remove 后为 null；数组 add/remove 必须保留旧元素相对顺序；Dish add 指向旧组时要求 count 增大，新增组追加到末尾；违反时返回 502。
- 首轮 change_actions 必须为 []，evidence 必须精确覆盖所有非空叶子；后续轮只提交本轮新增或变更证据，未变更证据继承。
- evidence 路径包括顶层非空字段及 `dishes[i]` 内 count、非默认 dish_type、口味键、数组元素和 required_ingredients[j].value；dialogue_id、null、空容器和默认未指定组无需证据。
- 每条 evidence 与 ChangeAction.evidence 都必须是对应轮 user_message 的连续片段，否则返回 502。
- 餐次经 Spec_07 唯一解析为早餐、午餐或晚餐时 status=ready_for_planning；多餐次、下午茶或时间窗外为 needs_confirmation；新轮次先回到 in_progress 再重新判定。
- missing_requirements 不阻塞规划，固定按“人数、明确菜品类型”返回：diner_count=null 缺人数；所有 Dish 均未指定类型时缺明确菜品类型。

## 边界（每条之后会变成一条测试）

- profile 不存在返回 409；会话不存在或消息为空返回 400，且不调用 LLM。
- 首轮成功后只保存合并状态、轮次和原文；不保存 change_actions、Prompt 或 LLM 原始响应。
- “四个菜”得到 total_dish_count=4、默认 count=null；口味冲突后保持总数 4、形成两个 count=null 的组。
- 两组 count=null、总数 4 时，“再加一个菜”只把总数改为 5；不得默认分给第一组。
- 已知总数和已知汤组 count 时，“汤再加一道”必须同时增加总数与汤组 count，并分别声明变更。
- 难度归一、解除难度和非法 max_difficulty add 分别得到简单、中等、null 和 502。
- 数量不一致、未声明改动、声明与输出不一致、非法 add、重复声明或证据不命中原文均返回 502。
- 餐次可唯一解析时 ready_for_planning；需澄清时 needs_confirmation；新轮次重新判定。
- 提取或持久化失败时会话不变；turn_number 严格递增，并发提交以行锁和唯一约束串行。

## 明确不做（划出范围，防 AI 自由发挥）

- 不修改 Spec_02 单轮提取逻辑，不把单轮调用方迁移到本服务。
- 不生成追问文案、不处理自由闲聊、不调用 Spec_03 及以后服务。
- 不增加 per-diner 字段，不自动均分组数量，不把第一组视为默认组。
- 不处理跨组食材复用或多套菜单组合优化。
- 不引入新依赖、Redis 或全局缓存；状态只存业务数据库。
- 不保存历史原文到 merged_constraints，不记录 Prompt 与 LLM 请求或响应。

---
**三条自检**：①每条规则均能转成确定输入、输出或错误测试。②正文以契约表和唯一边界为主，可快速审阅。③演化、完整性与不支持范围均已划定。

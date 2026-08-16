# Spec_08_多轮约束提取

## 一句话目标

> 在一段多轮会话中逐步提取并合并菜单约束:每轮由 LLM 结合已有约束状态识别新增、修改与删除,会话与轮次落库;约束满足硬门槛后进入可规划状态,供下游规划使用。

## 数据模型

### 会话表 dialogue_sessions(扩展 Spec_00)

| 字段 | 类型 | 约束(必填?范围?默认?)|
|---|---|---|
| id | bigint | 主键,数据库生成 |
| profile_id | bigint | 必填,外键关联 user_profiles.id |
| status | string | 必填;只允许 in_progress、needs_confirmation、ready_for_planning |
| merged_constraints | JSON/null | 首轮提交前为 null,此后必填;保存多轮提取器输出的七个约束字段(dialogue_id、meal_periods、diner_count、max_total_time_minutes、available_ingredients、dishes、evidence),不包含 change_actions;dialogue_id 等于会话 id |

### 轮次表 dialogue_turns

| 字段 | 类型 | 约束(必填?范围?默认?)|
|---|---|---|
| id | bigint | 主键,数据库生成 |
| session_id | bigint | 必填,外键关联 dialogue_sessions.id |
| turn_number | integer | 必填,正整数;(session_id, turn_number) 联合唯一 |
| user_message | string | 必填,非空 |

### 多轮提取器输入与输出

每轮调用输入:当前轮 user_message + 当前 merged_constraints(上一状态的结构化约束字典,不含历史原文)。

每轮调用输出 = 完整更新后的约束 + change_actions:

| 字段 | 类型 | 约束 |
|---|---|---|
| dialogue_id | integer | 必填,等于会话 id |
| meal_periods | string[] | 必填;只允许下午茶、晚餐、早餐、午餐,不允许重复;无餐次时为 [] |
| diner_count | integer/null | 必填;明确人数时为正整数,否则为 null |
| max_total_time_minutes | integer/null | 必填;明确最长时间时为正整数分钟,否则为 null |
| available_ingredients | string[] | 必填;保存"家里只剩"等可用核心食材限制,每个值必须命中数据库标准食材名,不允许重复;无限制时为 [] |
| dishes | Dish[] | 必填,至少一项,不允许重复;每项对应一次菜品查询 |
| evidence | object<string, string> | 必填;键为字段路径,值为用户原文连续片段;规则见"证据规则" |
| change_actions | ChangeAction[] | 必填;本轮对上一状态的增删改声明;无变化时为 [] |

### Dish

| 字段 | 类型 | 约束 |
|---|---|---|
| count | integer/null | 必填;明确数量时为正整数,否则为 null |
| dish_type | string | 必填;只允许菜、汤、主食、小菜、未指定 |
| taste_preferences | object<string, boolean> | 必填;键只允许 is_sweet、is_light、is_spicy、is_salty、is_sour,值只允许布尔;无要求时为 {} |
| cuisines | string[] | 必填;只允许西餐风味、东北菜、粤菜、川湘菜、江浙菜,不允许重复;无要求时为 [] |
| effects | string[] | 必填;只允许助眠、减脂、养胃健胃消食、贫血、哺乳,不允许重复;无要求时为 [] |
| special_populations | string[] | 必填;只允许上班族、儿童、老人、更年期,不允许重复;无要求时为 [] |
| required_ingredients | IngredientRequirement[] | 必填;保存标准食材名、食材类别或已配置概念,不允许重复;无要求时为 [] |

### IngredientRequirement

| 字段 | 类型 | 约束 |
|---|---|---|
| kind | string | 必填;只允许 ingredient、category、concept |
| value | string | 必填;分别命中数据库标准食材名、数据库非空食材类别或当前已配置概念"面" |

没有明确菜品分类时,dishes 返回一项 count: null、dish_type: "未指定" 的菜品,并将口味、菜系、功效、人群和必需食材直接放入该项。存在多个菜品组时,适用于所有组的限制复制到每个 Dish 中,不使用共享层或继承规则。

### ChangeAction

| 字段 | 类型 | 约束 |
|---|---|---|
| field | string/null | 作用于顶层字段时为 meal_periods、diner_count、max_total_time_minutes、available_ingredients 之一;作用于 Dish 时为 null |
| dish_index | integer/null | 作用于 Dish 时填上一状态 Dish 索引;新增全新菜品组时为 null;作用于顶层字段时为 null;field 与 dish_index 必须恰好一个非空,唯一例外是新增全新菜品组(action=add)时两者均为 null |
| action | string | 必填;只允许 add、replace、remove |
| evidence | string | 必填,本轮原文连续片段 |

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况(状态码)|
|---|---|---|---|
| MultiTurnConstraintService.create_session | profile_id:正整数 | session_id | 400:输入非法;409:profile 不存在 |
| MultiTurnConstraintService.submit_turn | session_id:正整数、user_message:非空字符串 | 会话状态对象(session_id、turn_number、status、merged_constraints、missing_requirements;missing_requirements 由 merged_constraints 实时推导,不落库) | 400:输入非法或会话不存在;500:数据库 Session 或 LLM 配置错误;502:LLM 结构化输出或演化校验失败;503:LLM 服务不可用 |
| MultiTurnConstraintService.get_session | session_id:正整数 | 会话状态对象(session_id、profile_id、status、merged_constraints、missing_requirements;missing_requirements 由 merged_constraints 实时推导,不落库) | 400:会话不存在 |

SessionFactory、多轮 LLM 提取器、MealPeriodResolutionService(含时钟)在 Service 创建阶段注入,业务参数在方法调用阶段传入。LLM 结构化输出或演化校验失败重试一次,再次失败仍按 502 抛出。Prompt、原文与 LLM 原始响应不写入日志。

## 归一规则

- 微辣、香辣、麻辣归一为 is_spicy=true;不辣归一为 is_spicy=false;咸鲜归一为 is_salty=true。
- 暖胃、胃口不好、养胃、健胃消食、便秘归一为养胃健胃消食;夜宵归一为晚餐;公司、上班、下班归一为上班族。
- 仪式感、稍微正式点、正式一点归一为西餐风味;清爽、别太抢味归一为 is_light=true。
- 补气血归一为贫血;减脂保留为减脂;别太甜归一为 is_sweet=false。
- "面"保留为 kind=concept、value=面,不提前展开;一桌菜、主菜等说法归一为 dish_type=菜。
- 周末、平时等没有既定映射的时间表达一律忽略,不填入 meal_periods。
- 家常一点、家常菜、简单、复杂、适合夏天、热乎、牙口不好等没有既定映射的描述一律忽略,不得填入任何字段。
- available_ingredients 只限制核心食材,不要求盐、油、水等辅料也在可用列表中,也不表示列表中的食材必须全部使用。
- 暂不支持的描述直接忽略,不因此拒绝整条输入。

## 合并演化规则

- 所有约束字段均支持增、改、删三种演化,由 LLM 结合约束状态与原文判断,输出完整更新后的约束:
  - 标量(diner_count、max_total_time_minutes):增=旧值累加("再加一个人" 2→3);改=新值覆盖("改成三个人");删=解除约束置 null("人数不限")。
  - 数组(meal_periods、available_ingredients 及 Dish 内 cuisines、effects、special_populations、required_ingredients):增=追加元素,去重保序;删=移除元素("不要土豆了");改=整体替换。
  - 口味(taste_preferences):增=新增键;改=同名键新值覆盖(改口);删=移除键(放开该口味)。
  - dishes:增=同类型 count 累加("再加一个菜")或新增菜品组("来个甜品");删=移除整个 Dish("汤不要了");改=替换 count 或修改 Dish 内字段("汤清淡点")。
- 变更声明与重放校验:对上一状态逐条应用 change_actions 后,必须等于本轮输出的新状态;未出现在声明中的顶层字段或 Dish 必须原样保留;任一不满足返回 502。
  - 首轮(上一状态为空)不参与重放校验:change_actions 必须为 [],evidence 路径必须与所有非空约束精确对应,任一不满足返回 502。
  - 标量 add:旧值必须非空且输出值必须大于旧值;旧值为空时使用 replace。remove:输出必须为 null。
  - 数组 add:输出必须包含旧数组全部元素且相对顺序不变;remove:输出必须是旧数组的子集且相对顺序不变。
  - Dish add:指向旧 Dish 时旧 count 必须非空且新 count 必须大于旧值;旧 count 为空时使用 replace;dish_index 为 null 时视为新增菜品组,新组位于输出 dishes 末尾。
  - Dish remove:该 Dish 必须从输出中消失。
  - 同一顶层字段或同一 Dish 不得出现多条声明。
- 证据规则:
  - 叶子路径格式:meal_periods[i]、diner_count、max_total_time_minutes、available_ingredients[i]、dishes[i].count、dishes[i].dish_type、dishes[i].taste_preferences.is_x、dishes[i].cuisines[j]、dishes[i].effects[j]、dishes[i].special_populations[j]、dishes[i].required_ingredients[j].value。
  - 首轮所有字段均为新增:evidence 路径必须与所有非空约束的叶子路径精确对应(不多不少),每条片段必须是本轮原文的连续子串。
  - 后续轮:本轮新增或变更字段的 evidence 必须命中本轮原文(连续子串);未变更字段继承原轮 evidence,LLM 重新给出的片段一律忽略。
  - dialogue_id、null、[]、{} 和默认未指定 Dish 不需要证据。

## 轮次结束与完整性

- 硬门槛(满足 → status=ready_for_planning):餐次经 Spec_07 解析为唯一 resolved。
- 餐次解析为 needs_confirmation(多餐次、下午茶、时间窗外)→ status=needs_confirmation,用户澄清后重新判定。
- 缺失要素(不阻塞,写入 missing_requirements):值为固定枚举,按固定顺序返回——人数在前(diner_count 为 null 时缺失)、明确菜品类型在后(所有 Dish 的 dish_type 均为未指定时缺失;count、口味、菜系、功效、人群、必需食材不改变该判定)。
- ready_for_planning 后收到新轮次:自动回到 in_progress 继续合并并重新判定。

## 边界(每条之后会变成一条测试)

- create_session 传入不存在的 profile_id 时返回 409。
- submit_turn 的 session_id 不存在或 user_message 不是非空字符串时返回 400,且不调用 LLM。
- 首轮(状态为空)正常提取并落库:turn 行记录轮次与原文,session 行记录合并约束。
- 数组累加:第一轮 meal_periods=[晚餐],第二轮 [午餐] → 合并为 [晚餐, 午餐],去重保序。
- 标量增改删:第一轮 diner_count=2;"再加一个人" → add,合并为 3;"改成三个人" → replace,合并为 3;"人数不限" → remove,合并为 null。
- 数组增删:"还有土豆" → available_ingredients 追加元素;"不要土豆了" → 移除元素,去重保序。
- 口味增改删:新口味键追加;同名键新值覆盖(改口);移除键视为放开该口味。
- Dish 增改删:"再加一个菜" → 菜 Dish 的 count 累加;"换成一道菜" → count 被新表达覆盖;"汤不要了" → 汤 Dish 从状态删除。
- 重放校验失败(未声明的改动、声明与输出不一致、标量 add 新值不大于旧值、同一字段或 Dish 多条声明)时返回 502,重试一次后仍失败仍返回 502。
- 首轮 change_actions 非空、evidence 路径与所有非空约束不对应时返回 502;标量或 Dish count 的 add 在旧值为空时返回 502(应使用 replace)。
- 变更字段的 evidence 不是本轮原文连续片段时返回 502;继承字段保留原轮 evidence。
- "今晚三个人吃" → 餐次 resolved → ready_for_planning。
- 多餐次或时间窗外 → needs_confirmation;用户下一轮补充餐次后重新判定。
- "今晚吃啥" → ready_for_planning,missing_requirements 含人数与明确菜品类型,不阻塞规划。
- ready_for_planning 后提交新轮次 → 状态回到 in_progress 并重新判定完整性。
- 轮次提取失败(400/5xx)时不落库,会话状态不变。
- 同一会话 turn_number 严格递增;并发提交同一会话时以行锁与唯一约束保证串行。

## 明确不做(划出范围,防 AI 自由发挥)

- 不改 Spec_02 单轮提取逻辑;单轮调用方暂不迁移到统一提取器(后续独立任务)。
- 不生成对话文案、不实现 Agent 追问循环、不处理自由闲聊语义。
- 不调用 Spec_03 及以后的下游服务,不下发规划。
- 不引入新依赖、不使用 Redis;状态只存业务数据库。
- 状态中不保存历史原文,只保存结构化约束。
- 不缓存或持久化 LLM 请求、响应与每轮提取输出。

---
**三条自检**:①每条规则均可直接转成测试。②正文可在 2 分钟内审完。③合并规则、完整性门槛与不支持范围均已划定。

# Spec_11 统一对话约束提取

## 一句话目标

> 使用同一持久化会话接口处理一条或多条用户消息，生成完整、可验证、可继续修改的菜单约束；单条消息就是只有一轮的会话，不再保留独立单轮提取接口。

本规格替代原单轮与多轮约束提取规格。

## 数据模型

### MergedConstraints

| 字段 | 类型 | 约束 |
|---|---|---|
| dialogue_id | integer | 等于数据库生成的会话ID |
| meal_periods | string[] | 值限下午茶、早餐、午餐、晚餐；不重复 |
| diner_count | integer/null | 正整数或null |
| total_dish_count | integer/null | 整桌明确菜品数；正整数或null |
| max_total_time_minutes | integer/null | 正整数分钟或null |
| max_difficulty | string/null | 简单、中等或null |
| available_ingredients | string[] | 数据库标准食材名；不重复 |
| dishes | Dish[] | 至少一项 |
| evidence | object<string,string> | 非空约束到用户连续原文片段的映射 |

### Dish

| 字段 | 类型 | 约束 |
|---|---|---|
| count | integer/null | 正整数或null |
| dish_type | string | 菜、汤、主食、小菜、未指定 |
| taste_preferences | object<string,boolean> | 键限is_sweet、is_light、is_spicy、is_salty、is_sour |
| cuisines | string[] | 值限西餐风味、东北菜、粤菜、川湘菜、江浙菜 |
| effects | string[] | 值限助眠、减脂、养胃健胃消食、贫血、哺乳 |
| special_populations | string[] | 值限上班族、儿童、老人、更年期 |
| required_ingredient_groups | IngredientGroup[] | 组间固定为AND；无要求为[] |

未明确菜品类型时返回一个`count=null、dish_type=未指定`的占位Dish。适用于所有Dish的限制复制到每个Dish。

### IngredientGroup

| 字段 | 类型 | 约束 |
|---|---|---|
| match | string | all或any |
| items | IngredientRequirement[] | all至少1项；any至少2项 |

### IngredientRequirement

| 字段 | 类型 | 约束 |
|---|---|---|
| kind | string | ingredient、category、concept |
| value | string | 分别命中数据库标准食材、非空类别或概念“面” |

确定性食材规则：

- Dish内各IngredientGroup之间为AND。
- all组内全部满足；any组内至少满足一项。
- 单个要求统一输出单项all组。
- 任意两个或更多有效食材条件由“和、并且、都要”连接时生成一个all组；由“或、或者、二选一”连接时生成一个any组。
- 例如，“番茄和鸡蛋”生成一个两项all组。
- 例如，“鱼或者鸡翅”生成一个两项any组。
- 例如，“要番茄，并且鱼或鸡翅选一个”生成一个单项all组和一个两项any组。
- 同组及跨组均不允许重复的`kind+value`。
- “家里有、家里只剩、现有”进入available_ingredients，不进入食材要求组。

### 每轮LLM输出

LLM接收当前消息及上一MergedConstraints；首轮上一状态为null。输出完整新约束，并额外返回：

| 字段 | 类型 | 约束 |
|---|---|---|
| change_actions | ChangeAction[] | 首轮必须为[]；后续轮声明本轮全部变化 |

### ChangeAction

| 字段 | 类型 | 约束 |
|---|---|---|
| field | string/null | 顶层变更字段 |
| dish_index | integer/null | 被修改的旧Dish索引 |
| action | string | add、replace、remove |
| evidence | string | 本轮连续原文片段 |

动作规则：

- 顶层变更只填field；Dish变更只填dish_index。
- 新增Dish时field和dish_index均为null，action必须为add。
- 同一轮中，同一顶层字段或同一旧Dish最多一条声明。
- 同一Dish内多个字段变化必须合并为一条Dish声明。
- replace表示整体替换为输出值。
- 标量add要求旧值非空且新值更大；remove后为null。
- 数组add要求新数组按原顺序包含旧数组；remove要求新数组是旧数组的有序子集。
- 已有Dish的add只允许count从非空值增大；remove删除该Dish。
- max_difficulty不允许add。
- 重放全部动作后必须与LLM输出的新约束完全一致。

### Evidence

- 首轮evidence必须精确覆盖全部非空约束。
- 后续轮只提供新增或变化证据；未变化证据继承。
- 证据必须是对应轮用户原文的连续片段。
- 食材值路径为`dishes[i].required_ingredient_groups[j].items[k].value`。
- 食材关系路径为`dishes[i].required_ingredient_groups[j].match`，证据为包含该关系表达的连续原文；单项all组可使用该食材原文。
- kind、dialogue_id、null、空容器和默认未指定Dish不需要证据。

## 受控映射

用户直接说出允许值时保留该值；额外只允许以下映射：

| 原文 | 输出 |
|---|---|
| 早上、早饭 | 早餐 |
| 中午、午饭 | 午餐 |
| 晚上、今晚、晚饭 | 晚餐 |
| 微辣、香辣、麻辣 | is_spicy=true |
| 不辣、别做辣的 | is_spicy=false |
| 清淡、清爽、别太抢味 | is_light=true |
| 咸鲜 | is_salty=true |
| 别太甜、不太甜 | is_sweet=false |
| 西餐、西式 | 西餐风味 |
| 广东菜 | 粤菜 |
| 川菜、湘菜 | 川湘菜 |
| 暖胃、养胃、健胃消食 | 养胃健胃消食 |
| 公司、上班、下班 | 上班族 |
| 小孩、孩子 | 儿童 |
| 简单、简单点、家常、家常一点 | max_difficulty=简单 |
| 不太复杂、不想太复杂、别太复杂、别太难做、太麻烦不行 | max_difficulty=中等 |
| 面 | kind=concept、value=面 |

明确禁止以下推导：

- 简单不得产生清淡。
- 正式、仪式感不得产生西餐风味。
- 胃口不好、便秘不得产生养胃健胃消食。
- 补气血、没精神不得产生贫血。
- 夜宵不得直接产生晚餐，但同句中的“晚上”等可独立产生晚餐。
- 适合夏天、热乎、牙口不好、复杂、大部分食材共用等未支持描述不产生字段。

## 端点 / 接口

| 动作 | 输入 | 成功返回 | 失败情况 |
|---|---|---|---|
| create_session | profile_id正整数 | session_id | 400输入非法；409档案不存在；500数据库失败 |
| submit_turn | session_id正整数、非空user_message | session_id、turn_number、status、merged_constraints、missing_requirements | 400输入或会话非法；500配置、数据库或餐次服务失败；502模型输出或演化非法；503模型不可用 |
| get_session | session_id正整数 | session_id、profile_id、status、merged_constraints、missing_requirements | 400输入或会话非法；500数据库失败 |

公开服务统一为`DialogueConstraintService`，只创建一套LLM结构化提取器。不再提供`extract(dialogue)`或独立多轮Service。

## 边界

- 单条消息也必须创建会话并提交第一轮。
- 第一轮change_actions非空返回502。
- “简单点的早餐”得到早餐和简单难度，不得得到清淡。
- “有仪式感，但不想做太复杂”只得到中等难度。
- “别做辣的，口味清淡一点”使用一条Dish replace，同时更新两个口味。
- “胃口不好”不产生功效；“暖胃”产生养胃健胃消食。
- “补气血”不产生贫血。
- all/any组数量非法、重复项、未知match或食材不存在返回502。
- total_dish_count非空时，明确count之和加null组数不得超过总数；全部count明确时总和必须等于总数。
- 未声明改动、重复声明、证据缺失或动作重放不一致返回502。
- 首次502后只重试一次；第二次Prompt必须包含首次异常文本。再次失败时不保存轮次或状态。
- 明确且唯一的早餐、午餐或晚餐为ready_for_planning。
- 未明确餐次时，Asia/Shanghai的05:00～10:00、11:00～14:00、17:00～21:00分别解析为早餐、午餐、晚餐，端点包含；其他时间为needs_confirmation。
- 多个餐次或单独下午茶为needs_confirmation。
- missing_requirements固定按人数、明确菜品类型顺序输出，但不阻止规划。
- 成功轮次从1开始严格递增；失败轮次不占用编号。
- 并发提交由数据库行锁和唯一约束串行化。

## 明确不做

- 不保留两套单轮/多轮契约、服务、提示词或LLM适配器。
- 不允许模型扩充字段、枚举、食材概念或语义映射。
- 不保存Prompt、模型原始响应或change_actions。
- 不向模型传递完整历史原文，只传上一结构化状态和当前消息。
- 不提供规则引擎、普通文本JSON、切换模型或放宽校验等fallback。
- 不在本规格实现约束整合、Neo4j筛选、菜单规划、营养计算、推荐理由或候选扩容。

---

**三条自检**

1. 通过：每条字段、映射、动作、状态和错误规则均可转成确定性测试。
2. 通过：正文只保留统一接口、结构、映射及边界，审核重点可在两分钟内定位。
3. 通过：本规格止于对话约束输出；整合、筛选、规划和推荐理由均明确排除。

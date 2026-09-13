# Spec_14 对外服务接口与回答生成

## 一句话目标

> 提供公网可访问的 OpenAI 兼容 HTTP 服务（流式/非流式），把多轮对话经统一推荐链路生成的结构化结果组装成只含真实菜谱菜名的自然语言回答文本；会话由系统创建并返回会话ID，首轮请求带用户档案ID自动建会话。

## 数据模型

### CreateSessionRequest

| 字段 | 类型 | 约束 |
|---|---|---|
| profile_id | integer | 必填，1~50 |

### CreateSessionResponse

| 字段 | 类型 | 约束 |
|---|---|---|
| session_id | integer | 新会话ID |

### ChatRequest（OpenAI 兼容子集 + 自定义字段）

| 字段 | 类型 | 约束 |
|---|---|---|
| model | string | 可选，接受但忽略（不参与逻辑） |
| messages | array | 必填，取最后一条非空 user 消息作为本轮输入 |
| stream | boolean | 可选，缺省 false |
| session_id | integer | 可选；有则继续该会话 |
| profile_id | integer | 可选；无 session_id 时必填，用于自动创建会话 |
| polish | boolean | 可选，缺省 false；true 时用 LLM 润色回答文本，false 时模板组装 |

### 非流式 ChatResponse

```json
{
  "id": "chatcmpl-<session_id>",
  "object": "chat.completion",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "<回答文本>"}, "finish_reason": "stop"}],
  "session_id": 101,
  "status": "recommended"
}
```

### 流式 SSE chunk（OpenAI 格式）

```json
{"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": null}]}
{"choices": [{"index": 0, "delta": {"content": "<片段>"}, "finish_reason": null}]}
...
{"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
```

- 回答文本按句子/固定长度切块依次输出；首块必须尽快发出
- `session_id` 通过响应头 `X-Session-Id` 返回，结束块为 `finish_reason: "stop"`
- 响应头固定携带 `Cache-Control: no-cache`、`X-Accel-Buffering: no`、`Connection: keep-alive`，禁止中间层缓存或缓冲流式响应
- 流式期间不得在中间块中混杂错误；错误以标准错误体整体返回

### 错误体（OpenAI 风格）

```json
{"error": {"message": "<中文说明>", "type": "invalid_request_error", "code": "<status_code>"}}
```

## 回答文本（AnswerComposer 输出约定）

| status | 业务含义 | 回答内容 |
|---|---|---|
| recommended | 推荐成功，唯一给出完整菜单的终态 | 固定按“菜单、筛选依据、规划依据、营养结果”四段输出；菜名逐字取自 `dish_recommendations[].recipe_name`；依据只描述本次实际执行的筛选与规划；低于8分时附质量提示 |
| needs_confirmation | 餐次不明确，需确认交互，不是错误 | 输出确认状态的 message 文本（含"请确认这次要安排早餐、午餐还是晚餐？"） |
| in_progress | 会话尚未提交任何消息 | 简短提示（会话尚无内容） |
| constraint_conflict | 档案约束与对话约束冲突（如过敏档案要求吃海鲜），不强行推荐 | 简短状态说明文本 |
| unmatched_allergen | 过敏表述无法映射到已知过敏原，零违反无法保证，拒绝推荐 | 简短状态说明文本 |
| empty_candidate | 约束过严，无任何候选菜 | 简短状态说明文本 |
| planning_infeasible | 有候选但整桌组合无可行解 | 简短状态说明文本 |

- 任一回答中的菜名都必须与结构化结果中的 `recipe_name` 完全一致，不得增改
- “菜单”段包含餐次、人数和最终菜品清单；不重复解释筛选与规划规则
- “筛选依据”段消费 `filtering_reasons`，包含本次生效的标签、否定条件、类型、时间、难度、普通食材、主食来源、排除主食、过敏规则，以及固定执行的推荐资格门禁和候选排序；空条件不输出
- “规划依据”段消费 `planning_reasons` 和已应用健康约束理由，包含实际数量规则、菜名去重、固定配方、候选阶段结果、选优优先级和最优性；相同规则整桌只输出一次
- “营养结果”段只消费结构化营养摘要和质量警告，不自由推导健康效果
- 未参与本次筛选或规划的健康需求、体检指标和其他档案字段不得出现在回答中
- 回答各段的自然语言陈述必须能在推荐理由的结构化规则、参数和 sources 中溯源
- 默认 `polish=false` 走模板组装且不调用 LLM；`polish=true` 不得作为结构化依据正确性的验收替代

四段结构示意（实际取值全部来自结构化结果，本示意不含可照抄的字面值）：

```text
菜单
已为您安排{餐次}，{人数}人份菜单：
1. {dish_recommendations 中的菜名}

筛选依据
- {filtering_reasons 生成的中文说明}

规划依据
- {planning_reasons 和已应用健康约束理由生成的中文说明}

营养结果
- {营养摘要生成的中文说明}
```

## 接口

| 动作 | 输入 | 成功返回 | 失败情况（状态码） |
|---|---|---|---|
| POST /v1/sessions | CreateSessionRequest | 201 CreateSessionResponse | 401 访问密钥缺失或错误；400 profile_id非法；409 档案不存在；500 依赖失败 |
| POST /v1/chat/completions | ChatRequest（stream 可 true） | 200 非流式 ChatResponse 或 SSE 流 | 401 访问密钥缺失或错误；400 缺 profile_id/session_id、消息为空、会话不存在；409 档案不存在；502 约束提取结构非法；503 LLM 不可用；500 依赖失败 |
| GET /health/live | 无 | 200 {"status":"ok"} | - |
| GET /health | 无 | 200 完整依赖检查；全部正常为 ok，单个LLM可用为 degraded | 401 访问密钥缺失或错误；PostgreSQL、Neo4j或必需数据异常，或两个LLM均不可用：503 unhealthy |

### 鉴权

- 除 `GET /health/live` 外的所有路由要求请求头 `Authorization: Bearer <访问密钥>`
- 访问密钥在应用构造阶段注入，不参与业务逻辑
- 缺失或不匹配：401，响应体沿用统一错误体，且不进入业务链路
- `GET /health/live` 免鉴权，用于零成本存活探测

### 健康检查

- `/health/live` 只确认HTTP进程能够响应，不访问外部依赖
- `/health` 检查 PostgreSQL 连接、Neo4j 连接、必需业务数据、主LLM和备用LLM
- PostgreSQL 必须存在 Recipe、Ingredient、RecipeIngredient、RecipeNutrition、UserProfile、ProfileDriTarget 数据，且RecipeIngredient.is_staple_component全部为非空布尔值
- Neo4j 必须存在 Recipe、Ingredient、Concept 节点以及 `part_of`、`is_a` 关系；全部part_of关系必须带合法布尔is_staple_component属性
- 两个LLM分别进行一次最小真实生成；任意一个可用即可提供服务，并在 `available_models` 返回可用模型名
- 全部检查成功时返回 200 `ok`；只有一个LLM成功时返回 200 `degraded`
- PostgreSQL、Neo4j、必需数据任一失败，或两个LLM均失败时返回 503 `unhealthy`
- 响应包含检查时间、总耗时和各项耗时；失败仅返回预定义错误码，不返回密钥、连接地址或上游原始错误

## 边界

- 同时缺失 session_id 与 profile_id：400
- 两者同时提供：以 session_id 为准继续会话，忽略 profile_id
- 同一档案并发创建多个会话互不影响
- 首轮自动建会话后返回新 session_id（响应体 + X-Session-Id 头）
- 多轮带 session_id 继续：约束累加、餐次解析按当前时间重新判定
- “主食不要米饭，换成玉米或者红薯”的推荐回答不得出现仅把玉米作为配料的培根披萨，筛选依据必须说明主食来源而非普通包含关系
- messages 为空数组或最后一条非 user 消息：400
- stream 时回答为空（in_progress 等短文本）：至少输出一个含文本的块再结束
- 业务异常状态码与 HTTP 状态码一致映射；未知异常统一 500 且不静默吞错

## 明确不做

- 空候选/无解的完整降级回答（只输出状态说明）
- 性能评测链路、并发压测、方案否定重试、模糊追问扩面
- 多人档案加载（按对话 query 规划即可）
- OpenAI 全部字段兼容（只实现评测所需子集）
- 本机 TLS 终止与证书管理、Docker 镜像（复赛才要求）
- 将 LLM 润色回答设为默认行为（待对比实验后定夺）
- 为“增肌、增加体重”等当前未进入规划的健康需求生成推荐理由
- 根据体检指标、BMI或其他未参与本次计算的档案字段自由推导健康效果

---
**三条自检**：

① **每条规则能不能写成测试？** 能。访问密钥校验、请求校验、状态码、流式块顺序与响应头、菜名溯源、四段回答顺序、筛选与规划依据来源、同类规则合并以及未应用字段不输出均可直接断言。

② **结构是否可审查？** 可以。HTTP契约、回答文本约定和边界分别成表，规则不重复陈述。

③ **范围划死了吗？** 划死了。只扩充现有推荐理由和默认模板回答，加上访问密钥校验与流式响应头；不新增健康目标规则、业务HTTP字段、端点或默认LLM润色。

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
| session_id | integer | 新会话ID，同一档案可创建多个会话 |

### ChatRequest（OpenAI 兼容子集 + 自定义字段）

| 字段 | 类型 | 约束 |
|---|---|---|
| model | string | 可选，接受但忽略（不参与逻辑） |
| messages | array | 必填，取最后一条非空 user 消息作为本轮输入 |
| stream | boolean | 可选，缺省 false |
| session_id | integer | 可选；有则继续该会话 |
| profile_id | integer | 可选；无 session_id 时必填，用于自动创建会话 |

- 同时缺失 session_id 与 profile_id：400
- 两者同时提供：以 session_id 为准继续会话，忽略 profile_id
- 会话不存在：404

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
- 流式期间不得在中间块中混杂错误；错误以标准错误体整体返回

### 错误体（OpenAI 风格）

```json
{"error": {"message": "<中文说明>", "type": "invalid_request_error", "code": "<status_code>"}}
```

### 回答文本（AnswerComposer 输出约定）

| 终态 | 内容 |
|---|---|
| recommended | 餐次、人数、菜品清单（菜名逐字取自结构化结果 recipe_name）及份量、逐菜推荐理由、整桌健康约束理由、营养摘要；低于8分时附质量提示 |
| needs_confirmation | 输出确认状态现有 message 文本（含"请确认这次要安排早餐、午餐还是晚餐？"） |
| in_progress | 简短提示（会话尚无内容） |
| constraint_conflict / unmatched_allergen / empty_candidate / planning_infeasible | 简短状态说明文本，不静默空输出 |

- 回答中出现的菜名必须与 `recommendation_reason_result.dish_recommendations[].recipe_name` 完全一致，不得增改
- 模板组装纯函数实现，无 LLM 调用；LLM 润色仅作对比实验，默认不接入请求路径

### 终态业务含义（status 取值）

- `recommended`：推荐成功，唯一给出完整菜单的终态
- `needs_confirmation`：餐次不明确时的确认交互（"请确认这次要安排早餐、午餐还是晚餐？"），不是错误
- `in_progress`：会话尚未提交任何消息，正常流程不会出现，属兜底
- `constraint_conflict`：档案约束与对话约束冲突（如过敏档案要求吃海鲜），不强行推荐
- `unmatched_allergen`：过敏表述无法映射到已知过敏原，零违反无法保证，拒绝推荐
- `empty_candidate`：约束过严，无任何候选菜
- `planning_infeasible`：有候选但整桌组合无可行解（营养/菜数/搭配无法同时满足）

后四个是给不出方案的失败终态，本 spec 只输出状态说明文本；完整降级回答属 P1，明确不做。

## 接口

| 动作 | 输入 | 成功返回 | 失败情况（状态码） |
|---|---|---|---|
| POST /v1/sessions | CreateSessionRequest | 201 CreateSessionResponse | 400 profile_id非法；404 档案不存在；409 档案冲突；500 依赖失败 |
| POST /v1/chat/completions | ChatRequest（stream 可 true） | 200 非流式 ChatResponse 或 SSE 流 | 400 缺 profile_id/session_id、消息为空；404 会话/档案不存在；502 约束提取结构非法；503 LLM 不可用；500 依赖失败 |
| GET /health | 无 | 200 {"status":"ok"} | - |

### 服务链路

1. 无 session_id 且带 profile_id：调用 `confirmation.create_session(profile_id)` 自动建会话
2. `confirmation.submit_turn(session_id, user_message)` 提交本轮
3. `recommendation.generate(session_id)` 走统一推荐链路
4. `answer_composer.compose(result)` 组装回答文本
5. 按 stream 参数返回非流式 JSON 或 SSE 分块

## 边界

- profile_id 非整数/越界/缺失：400；档案不存在：404
- 首轮自动建会话后返回新 session_id（响应体 + X-Session-Id 头）
- 多轮带 session_id 继续：约束累加、餐次解析按当前时间重新判定
- 同一档案并发创建多个会话互不影响
- messages 为空数组或最后一条非 user 消息：400
- stream 时回答为空（in_progress 等短文本）：至少输出一个含文本的块再结束
- 业务异常状态码与 HTTP 状态码一致映射；未知异常统一 500 且不静默吞错
- 菜名真实性：任一回答中的菜名都能在 `dish_recommendations` 中溯源

## 明确不做

- 空候选/无解的完整降级回答（只输出状态说明，见数据模型回答文本约定）
- 性能评测链路、并发压测、方案否定重试、模糊追问扩面
- 多人档案加载（按对话 query 规划即可）
- OpenAI 全部字段兼容（只实现评测所需子集）
- 鉴权、HTTPS、Docker 镜像（复赛才要求）
- LLM 润色回答默认启用（待对比实验后定夺）

---
**三条自检**：

① **每条规则能不能写成测试？** 能。数据模型逐条约束可测：profile_id 越界/缺失、messages 空/非 user、session_id 与 profile_id 同时缺失、两者同时提供取 session_id、会话不存在、菜名溯源（任一回答菜名可回溯到 dish_recommendations）。接口表逐行可测：两个端点各状态码一条；流式 chunk 顺序、X-Session-Id 头、finish_reason 结束块各一条。回答文本六个终态各一条模板断言。

② **人能在 2 分钟内审完吗？** 能。全文约 90 行，三张表（请求/响应/错误体）、一个接口表、六条边界、六条不做，无实现细节，无可推导内容。

③ **范围划死了吗？** 划死了。只做两个端点 + 回答组装 + 自动建会话/多轮延续；P1 降级回答、P2 各项、多人档案、鉴权、LLM 润色接入全部明确排除。

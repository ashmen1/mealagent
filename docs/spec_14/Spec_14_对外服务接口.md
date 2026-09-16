# Spec_14 对外服务接口文档

更新时间：2026-09-16

本服务通过公网 HTTP API 提供个性化膳食规划能力。对话端点采用 OpenAI Chat Completions 的常用请求与响应形式，并增加 `profile_id`、`session_id` 和 `polish` 字段。接口只返回回答文本，不单独返回结构化菜谱列表。

## 1. 正式启动

项目根目录的 `.env` 必须配置非空访问密钥：

```dotenv
MEALAGENT_API_TOKEN=<评测访问密钥>
```

Windows 可直接运行：

```powershell
.\start.bat
```

或使用等价命令：

```powershell
uv --cache-dir .uv-cache run --no-dev --env-file .env python -m backend.scripts.serve
```

正式启动入口会自动执行 `docker compose up -d`，等待 PostgreSQL 与 Neo4j 就绪，并在 `127.0.0.1:8000` 启动服务。`MEALAGENT_API_TOKEN` 未配置时拒绝启动。不要直接运行应用工厂，否则会绕过正式启动入口注入的鉴权配置。

## 2. 鉴权

- `GET /health/live` 免鉴权，用于进程存活探测。
- 其余端点全部要求：`Authorization: Bearer <访问密钥>`。
- 缺失密钥、错误密钥或非 Bearer 格式均返回 HTTP 401。
- 仓库文档和 Postman 文件只保存变量或占位符，不保存真实密钥。

## 3. 端点

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/health/live` | 否 | 仅检查HTTP进程存活 |
| GET | `/health` | Bearer | 检查数据库、业务数据和主备LLM |
| POST | `/v1/sessions` | Bearer | 创建绑定用户档案的会话 |
| POST | `/v1/chat/completions` | Bearer | 非流式或SSE流式多轮对话 |

### 3.1 创建会话

```http
POST /v1/sessions
Authorization: Bearer <访问密钥>
Content-Type: application/json
```

```json
{"profile_id": 25}
```

成功返回 HTTP 201：

```json
{"session_id": 101}
```

`profile_id` 必须是 1～50 的整数；参数非法返回400，档案不存在返回409。

### 3.2 对话请求

```http
POST /v1/chat/completions
Authorization: Bearer <访问密钥>
Content-Type: application/json
```

```json
{
  "model": "mealagent",
  "messages": [
    {"role": "user", "content": "帮我安排晚饭，两个人吃"}
  ],
  "stream": false,
  "profile_id": 25,
  "polish": false
}
```

| 字段 | 约束 |
|---|---|
| `model` | 可选；为兼容OpenAI请求而接受，当前业务逻辑忽略 |
| `messages` | 必填；最后一项必须是内容非空的 `user` 消息 |
| `stream` | 可选，默认 `false` |
| `session_id` | 继续已有会话；提供时忽略 `profile_id` |
| `profile_id` | 没有 `session_id` 时必填，用于自动创建会话 |
| `polish` | 可选，默认 `false`；`true` 时增加一次LLM润色调用 |

非流式成功返回 HTTP 200：

```json
{
  "id": "chatcmpl-101",
  "object": "chat.completion",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "菜单\n……"},
      "finish_reason": "stop"
    }
  ],
  "session_id": 101,
  "status": "recommended"
}
```

`status` 只允许：`in_progress`、`needs_confirmation`、`constraint_conflict`、`unmatched_allergen`、`empty_candidate`、`planning_infeasible`、`recommended`。

推荐成功时回答固定为“菜单、筛选依据、规划依据、营养结果”四段。规划依据会按实际结果说明换菜的保留/换出/换入情况，以及本桌荤素数、冷热数和去重后的主烹饪方式；不会新增HTTP响应字段。

### 3.3 SSE流式响应

请求体设置 `"stream": true`，可增加 `Accept: text/event-stream`。响应头包含 `X-Session-Id`、`Cache-Control: no-cache`、`X-Accel-Buffering: no` 和 `Connection: keep-alive`。

```text
data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{"content":"回答片段"},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
```

结束标志是最后一个JSON块中的 `finish_reason: "stop"`，不额外发送 `[DONE]`。

## 4. 多轮与并发

- 首轮只传 `profile_id` 时，服务自动创建会话并返回 `session_id`。
- 后续请求携带同一 `session_id`，继续使用已有约束、最近成功菜单和排除菜名。
- 同一 `session_id` 的“提交约束—规划—保存菜单—组装回答”按请求到达顺序串行。
- 不同会话互不共享状态，可并发处理。

## 5. 错误体

```json
{
  "error": {
    "message": "缺少profile_id与session_id，请至少提供一个",
    "type": "invalid_request_error",
    "code": 400
  }
}
```

| 情况 | HTTP状态码 |
|---|---|
| Token缺失、错误或格式不是Bearer | 401 |
| 请求字段、消息、会话ID或业务输入非法 | 400 |
| 用户档案不存在 | 409 |
| 约束提取或上游结构非法 | 502 |
| LLM不可用或完整健康检查不通过 | 503 |
| 未知依赖或服务错误 | 500 |

4xx错误的 `type` 为 `invalid_request_error`，5xx错误为 `api_error`，`code` 与HTTP状态码一致。

## 6. 健康检查与公网边界

- `/health/live` 固定返回 `{"status":"ok"}`，不访问数据库或LLM。
- `/health` 会真实检查 PostgreSQL、Neo4j、必需业务数据以及主备LLM，因此必须使用Bearer Token且不应高频调用；三方菜谱一致性在应用启动阶段校验。
- 公网穿透只转发 `127.0.0.1:8000`；不得暴露 PostgreSQL 或 Neo4j 端口。
- 应用本身不终止TLS；如公网地址使用HTTPS，由内网穿透或反向代理负责证书与TLS终止。

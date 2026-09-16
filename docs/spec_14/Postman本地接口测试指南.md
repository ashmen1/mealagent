# Postman本地接口测试指南

更新时间：2026-09-16

## 1. 启动服务

确认根目录 `.env` 已配置非空 `MEALAGENT_API_TOKEN`，然后运行：

```powershell
cd D:\codes\A_mealagent_v3
.\start.bat
```

也可运行：

```powershell
uv --cache-dir .uv-cache run --no-dev --env-file .env python -m backend.scripts.serve
```

正式入口会自动启动并检查 PostgreSQL、Neo4j，再监听 `http://127.0.0.1:8000`。不要直接运行应用工厂。

## 2. 导入与变量

导入同目录中的：

- `A_mealagent_v3_本地.postman_collection.json`
- `A_mealagent_v3_本地.postman_environment.json`

选择环境“A_mealagent_v3 本地环境”，然后设置：

| 变量 | 初始值 | 用途 |
|---|---|---|
| `base_url` | `http://127.0.0.1:8000` | API地址 |
| `api_token` | 空 | 手工填入 `.env` 中的 `MEALAGENT_API_TOKEN` |
| `profile_id` | `25` | 用户档案ID |
| `session_id` | 空 | 创建会话后由脚本保存 |

Collection默认使用 `Bearer {{api_token}}`；“01 存活检查”单独关闭鉴权。不要把真实Token导出或提交到仓库。

## 3. 推荐测试顺序

### 01 存活检查

```http
GET {{base_url}}/health/live
```

无需Token，预期HTTP 200和 `{"status":"ok"}`。

### 02 完整健康检查

```http
GET {{base_url}}/health
Authorization: Bearer {{api_token}}
```

正常时HTTP 200，`status` 为 `ok` 或 `degraded`；关键依赖不可用时HTTP 503。该请求会真实调用主备LLM。

### 03 创建会话

```http
POST {{base_url}}/v1/sessions
Authorization: Bearer {{api_token}}
Content-Type: application/json
```

```json
{"profile_id": {{profile_id}}}
```

成功返回HTTP 201。Collection会把响应中的 `session_id` 自动保存到环境。

### 04 第一轮与多轮

```json
{
  "model": "mealagent",
  "messages": [
    {"role": "user", "content": "帮我安排晚饭，两个人吃"}
  ],
  "stream": false,
  "session_id": {{session_id}},
  "polish": false
}
```

继续使用同一个 `session_id` 可补充约束、换菜或否定当前方案。推荐成功时回答包含菜单、筛选依据、规划依据和营养结果四段。

### 05 自动创建会话

省略 `session_id` 并提供 `profile_id`：

```json
{
  "messages": [
    {"role": "user", "content": "帮我安排一人份早餐"}
  ],
  "stream": false,
  "profile_id": {{profile_id}},
  "polish": false
}
```

### 06 SSE流式响应

设置 `Accept: text/event-stream` 和 `"stream": true`。预期响应头包含 `X-Session-Id`，最后一块为：

```text
data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
```

服务不额外发送 `[DONE]`。Postman可能在请求结束后统一显示文本，这不代表服务没有分块发送。

## 4. 手工新建请求

除 `/health/live` 外，在Authorization页选择 `Bearer Token`，Token填写 `{{api_token}}`；同时设置 `Content-Type: application/json`。SSE请求再增加 `Accept: text/event-stream`。

## 5. 401检查

任选一个受保护端点，分别删除Authorization、填写错误Token、把类型改为Basic，三种情况都应返回HTTP 401：

```json
{
  "error": {
    "message": "访问密钥缺失或错误",
    "type": "invalid_request_error",
    "code": 401
  }
}
```

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| HTTP 401 | 检查是否选择环境、`api_token`是否填写、Authorization是否为Bearer |
| `Could not get any response` | 确认正式启动窗口仍在运行，先访问 `/health/live` |
| HTTP 400 | 检查消息、JSON以及是否提供 `profile_id` 或 `session_id` |
| HTTP 409 | 用户档案不存在 |
| HTTP 502 | LLM返回的结构化约束不合规 |
| HTTP 503 | 关键依赖或主备LLM不可用 |
| `{{session_id}}` 为红色 | 先执行创建会话，或检查是否选择本地环境 |

本地验证完成后，只需修改 `base_url` 即可复用同一Collection测试公网入口，Token仍通过环境变量单独填写。

# Postman本地接口测试指南

更新时间：2026-09-11

## 1. 准备本地服务

在PowerShell中进入项目目录：

```powershell
cd D:\codes\A_mealagent_v3
docker compose up -d
uv --cache-dir .uv-cache run --no-dev --env-file .env python -m uvicorn backend.api.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1
```

保持这个PowerShell窗口运行。先在浏览器或Postman访问：

```text
http://127.0.0.1:8000/health/live
```

看到 `{"status":"ok"}` 后再测试业务接口。

## 2. 导入Postman文件

项目提供两个可直接导入的文件：

- `A_mealagent_v3_本地.postman_collection.json`
- `A_mealagent_v3_本地.postman_environment.json`

在Postman中操作：

1. 点击左上角 **Import**。
2. 选择上面两个JSON文件并完成导入。
3. 在Postman右上角的环境下拉框中选择 **A_mealagent_v3 本地环境**。
4. 打开 Collection **A_mealagent_v3 本地接口测试**。
5. 按请求名称前的编号依次点击 **Send**。

本地环境预设变量：

| 变量 | 初始值 | 用途 |
|---|---|---|
| `base_url` | `http://127.0.0.1:8000` | 本机API地址 |
| `profile_id` | `25` | 已导入的测试用户档案 |
| `session_id` | 空 | 创建会话后由Postman测试脚本自动保存 |

## 3. 推荐测试顺序

### 01 存活检查

```http
GET {{base_url}}/health/live
```

预期HTTP 200：

```json
{"status":"ok"}
```

### 02 完整健康检查

```http
GET {{base_url}}/health
```

正常时HTTP 200，`status` 为 `ok` 或 `degraded`。HTTP 503表示数据库、必需数据或两个LLM均不可用。

该请求会真实调用Qwen和DeepSeek各一次，会消耗少量额度。日常反复测试优先使用 `/health/live`。

### 03 创建会话

```http
POST {{base_url}}/v1/sessions
Content-Type: application/json
```

```json
{
  "profile_id": 25
}
```

成功时HTTP 201：

```json
{
  "session_id": 123
}
```

Collection中的测试脚本会自动执行：

```javascript
pm.environment.set("session_id", pm.response.json().session_id);
```

因此后续请求可以直接使用 `{{session_id}}`。

### 04 第一轮对话

```http
POST {{base_url}}/v1/chat/completions
Content-Type: application/json
```

```json
{
  "model": "mealagent",
  "messages": [
    {"role": "user", "content": "帮我安排晚饭，两个人吃"}
  ],
  "stream": false,
  "session_id": 123
}
```

在Postman Collection中，示例里的 `123` 已写成 `{{session_id}}`，不需要手动填写。

成功时HTTP 200，重点查看：

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "回答文本"
      },
      "finish_reason": "stop"
    }
  ],
  "session_id": 123,
  "status": "recommended"
}
```

`model` 字段用于兼容OpenAI请求格式，当前不会决定实际调用哪个LLM。

### 05 继续多轮对话

沿用相同的 `session_id`：

```json
{
  "model": "mealagent",
  "messages": [
    {"role": "user", "content": "不要辣，也不要香菜"}
  ],
  "stream": false,
  "session_id": 123
}
```

服务会加载该会话之前的约束，并与本轮要求合并。

### 06 由profile_id自动创建会话

也可以不先调用 `/v1/sessions`：

```json
{
  "model": "mealagent",
  "messages": [
    {"role": "user", "content": "帮我安排一人份早餐"}
  ],
  "stream": false,
  "profile_id": 25
}
```

没有 `session_id` 且提供 `profile_id` 时，服务会自动创建新会话，并在响应中返回新的 `session_id`。

### 07 SSE流式响应

请求头增加：

```http
Accept: text/event-stream
```

请求体把 `stream` 改为 `true`：

```json
{
  "model": "mealagent",
  "messages": [
    {"role": "user", "content": "总时间不要超过30分钟"}
  ],
  "stream": true,
  "session_id": 123
}
```

响应类型是 `text/event-stream`，格式类似：

```text
data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{"content":"回答片段"},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
```

响应头 `X-Session-Id` 会返回当前会话号。Postman可能在请求结束后统一展示SSE文本，这不代表服务端没有分块发送。

## 4. 手工新建Postman请求

不导入Collection时，可以按以下步骤创建请求：

1. 点击 **New**，选择 **HTTP Request**。
2. 选择请求方法，例如 `POST`。
3. 地址填写 `http://127.0.0.1:8000/v1/chat/completions`。
4. 打开 **Headers**，添加 `Content-Type: application/json`。
5. 打开 **Body**，选择 **raw**，右侧格式选择 **JSON**。
6. 粘贴上面的请求体并点击 **Send**。

如果使用变量，先在Postman环境中建立 `base_url`、`profile_id` 和 `session_id`。

## 5. `polish`参数

默认不需要传 `polish`，此时为 `false`，回答由确定性模板生成。

需要测试LLM润色时可增加：

```json
"polish": true
```

润色会增加一次LLM调用、延迟和额度消耗。基础接口验证阶段建议保持 `false`。

## 6. 常见问题

| 现象 | 原因与处理方法 |
|---|---|
| `Could not get any response` | 确认Uvicorn窗口仍在运行，先访问 `/health/live` |
| `ECONNREFUSED 127.0.0.1:8000` | API未启动或端口被其他程序占用 |
| HTTP 400 | 检查JSON格式、最后一条消息是否为 `user`，以及是否提供 `profile_id` 或 `session_id` |
| HTTP 404 | `profile_id` 或 `session_id` 不存在 |
| HTTP 502 | LLM返回的约束结构不符合要求 |
| HTTP 503 | LLM认证、限流、超时、Provider异常，或完整健康检查发现关键依赖不可用 |
| 请求等待时间较长 | 对话请求需要真实调用LLM并执行推荐；先用 `/health` 判断依赖状态 |
| `{{session_id}}` 显示为红色 | 尚未执行创建会话请求，或没有选择本地环境 |

## 7. 测试完成后的判断

本地接口可以进入内网穿透阶段的最低条件：

- `/health/live` 返回HTTP 200
- `/health` 返回HTTP 200
- 创建会话返回HTTP 201并得到有效 `session_id`
- 第一轮和多轮请求返回HTTP 200
- 非流式回答的 `choices[0].message.content` 非空
- 流式响应包含 `text/event-stream` 和 `X-Session-Id`

满足这些条件后，只需要把Postman环境中的 `base_url` 从本机地址改成SakuraFrp提供的公网地址，即可复用同一套Collection测试公网入口。

# Tool Response Protocol

远程工具必须按照本协议返回响应，Fabric 会将响应原样透传给 LLM。

统一的远程工具响应协议可覆盖 JSON 单次响应、SSE 流式响应和异步轮询场景。

## 设计原则

1. **类型明确** - 每个响应必须有顶层 `type` 字段标识事件类型
2. **状态清晰** - 终态事件必须表达最终结果
3. **输出统一** - 所有产出物统一放在 `outputs` 中
4. **向后兼容** - Fabric 层可做适配，但远程工具应优先返回标准协议

## 事件类型

所有响应均为 JSON 对象，包含顶层 `type` 字段：

| type | 含义 |
|------|------|
| `tool.created` | 任务已创建，等待执行（SSE 中间事件） |
| `tool.in_progress` | 任务执行中（SSE 中间事件） |
| `tool.completed` | 任务成功完成（终态） |
| `tool.failed` | 任务失败（终态） |
| `tool.cancelled` | 任务已取消（终态） |

## 协议概览

```
┌─────────────────────────────────────────────────────────────────┐
│                    Tool Response Protocol                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Event Types:                                                   │
│  ├── tool.created        任务已创建，等待执行                    │
│  ├── tool.in_progress    任务执行中（可选进度事件）              │
│  ├── tool.completed      任务成功完成（终态）                    │
│  ├── tool.failed         任务失败（终态）                        │
│  └── tool.cancelled      任务已取消（终态）                      │
│                                                                 │
│  Response Modes:                                                │
│  ├── JSON      单次请求，直接返回终态                           │
│  ├── SSE       流式返回多个事件                                 │
│  └── Polling   异步任务，轮询获取状态                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## tool.completed

```json
{
  "type": "tool.completed",
  "tool": {
    "id": "task_abc123",
    "name": "generate_image",
    "status": "completed",
    "outputs": [
      {"type": "image", "url": "https://cdn.example.com/output.png"},
      {"type": "video", "url": "https://cdn.example.com/output.mp4"},
      {"type": "audio", "url": "https://cdn.example.com/output.mp3"},
      {"type": "3d",    "url": "https://cdn.example.com/output.glb"},
      {"type": "file",  "url": "https://cdn.example.com/output.zip"}
    ],
    "metadata": {
      "model": "kling_v3",
      "progress": 100.0
    },
    "usage": {
      "cost": 0.05
    }
  }
}
```

### tool 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 任务唯一 ID |
| `name` | string | 是 | 工具名称 |
| `status` | string | 是 | 固定为 `"completed"` |
| `outputs` | array | 是 | 输出列表，见下方 outputs 说明 |
| `metadata` | object | 否 | 附加信息，可包含任意字段 |
| `usage` | object | 否 | 计费信息，见下方 usage 说明 |

### outputs 元素字段

协议层面要求的最小字段如下：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 输出类型：`image` / `video` / `audio` / `3d` / `file` |
| `url` | string | 是 | 可访问的资源 URL |

如有需要，可在单个 output 上补充扩展字段；Fabric 不依赖这些字段做终态识别：

```json
{
  "type": "video",
  "url": "https://cdn.example.com/output.mp4",
  "content_type": "video/mp4",
  "size_bytes": 1234567,
  "duration_ms": 30000,
  "width": 1920,
  "height": 1080
}
```

### Output Types

| Type | 描述 | 可选扩展字段示例 |
|------|------|------------------|
| `image` | 图片 | `width`, `height`, `content_type`, `size_bytes` |
| `video` | 视频 | `width`, `height`, `duration_ms`, `fps`, `content_type`, `size_bytes` |
| `audio` | 音频 | `duration_ms`, `sample_rate`, `content_type`, `size_bytes` |
| `3d` | 3D 模型 | `format`, `content_type`, `size_bytes` |
| `file` | 通用文件 | `filename`, `content_type`, `size_bytes` |

### usage 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `cost` | number | 否 | 本次调用费用（USD） |
| `input_tokens` | integer | 否 | 输入 token 数（文本类模型） |
| `output_tokens` | integer | 否 | 输出 token 数（文本类模型） |
| `total_tokens` | integer | 否 | 总 token 数 |
| `used` | integer | 否 | 消耗积分数 |

> `usage` 中不需要的字段可以省略，不要求全部填写。

---

## tool.failed

```json
{
  "type": "tool.failed",
  "tool": {
    "id": "task_abc123",
    "name": "generate_image",
    "status": "failed",
    "error": {
      "message": "Quota exceeded",
      "code": "quota_exceeded"
    },
    "metadata": {
      "model": "kling_v3"
    },
    "usage": null
  }
}
```

### tool 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 任务唯一 ID |
| `name` | string | 是 | 工具名称 |
| `status` | string | 是 | 固定为 `"failed"` |
| `error` | object | 是 | 错误信息 |
| `error.message` | string | 是 | 人可读的错误描述 |
| `error.code` | string | 否 | 错误码（便于程序处理） |
| `metadata` | object | 否 | 附加信息 |
| `usage` | object\|null | 否 | 若失败前已产生费用可填写，否则传 `null` 或省略 |

如需补充上下文，可在 `error` 中增加扩展字段，例如：

```json
{
  "code": "invalid_input",
  "message": "Video URL is not accessible",
  "details": {
    "url": "https://...",
    "http_status": 404
  }
}
```

---

## tool.cancelled

```json
{
  "type": "tool.cancelled",
  "tool": {
    "id": "task_abc123",
    "name": "generate_image",
    "status": "cancelled"
  }
}
```

---

## SSE 模式中间事件（仅 SSE 响应模式需要）

工具若支持 SSE 流式响应，需在终态事件前推送以下中间事件：

### tool.created

```json
{
  "type": "tool.created",
  "tool": {
    "id": "task_abc123",
    "name": "generate_image",
    "status": "pending"
  }
}
```

### tool.in_progress

```json
{
  "type": "tool.in_progress",
  "tool": {
    "id": "task_abc123",
    "name": "generate_image",
    "status": "in_progress",
    "progress": 42.0,
    "message": "Processing..."
  }
}
```

### SSE 流结束标志

SSE 流末尾需发送：

```
data: [DONE]
```

### 完整 SSE 流示例

```
data: {"type":"tool.created","tool":{"id":"task_abc123","name":"generate_image","status":"pending"}}

data: {"type":"tool.in_progress","tool":{"id":"task_abc123","name":"generate_image","status":"in_progress","progress":30.0}}

data: {"type":"tool.in_progress","tool":{"id":"task_abc123","name":"generate_image","status":"in_progress","progress":80.0}}

data: {"type":"tool.completed","tool":{"id":"task_abc123","name":"generate_image","status":"completed","outputs":[{"type":"image","url":"https://cdn.example.com/output.png"}],"usage":{"cost":0.05}}}

data: [DONE]
```

### 关键规则

1. **不要省略 `type` 字段** - Fabric 依赖 `type` 识别终态事件
2. **终态事件包含完整数据** - `tool.completed` 必须包含所有 outputs
3. **SSE 模式中 Fabric 只取最后一个终态事件** - 中间事件仅用于日志或进度展示
4. **[DONE] 标记结束** - SSE 流以 `data: [DONE]` 结束

---

## JSON 单次响应

对于非流式接口，直接返回终态格式：

```json
{
  "type": "tool.completed",
  "tool": {
    "id": "task_abc123",
    "name": "generate_image",
    "status": "completed",
    "outputs": [
      {"type": "image", "url": "https://cdn.example.com/output.png"}
    ]
  }
}
```

## Polling 异步任务

### 创建任务

```json
POST /v1/generation
Response:
{
  "type": "tool.created",
  "tool": {
    "id": "task_abc123",
    "name": "generate_image",
    "status": "pending"
  }
}
```

### 查询状态

```json
GET /v1/generation/task/{task_id}
Response:
{
  "type": "tool.in_progress",  // 或 tool.completed / tool.failed / tool.cancelled
  "tool": {
    "id": "task_abc123",
    "name": "generate_image",
    "status": "in_progress",
    "progress": 75
  }
}
```

## 实现要求

### 远程服务端

所有远程工具必须返回符合协议的响应格式，包括：

1. **JSON 模式** - 直接返回终态事件
2. **SSE 模式** - 流式返回事件，最后需出现终态事件
3. **Polling 模式** - 创建接口返回 `tool.created`，查询接口返回当前状态对应事件

### Fabric 端

SSE 处理逻辑应以终态事件为准，并保留最后一个终态事件作为最终结果：

```python
TOOL_TERMINAL_EVENTS = frozenset({
    "tool.completed",
    "tool.failed",
    "tool.cancelled",
})

final_result = None

for event in sse_stream:
    event_type = event.get("type")

    if event_type in TOOL_TERMINAL_EVENTS:
        final_result = event
        continue

    if event_type in {"tool.created", "tool.in_progress"}:
        continue

return final_result
```

### 内置工具适配

将 SDK 或第三方任务对象转换为协议格式时，应优先满足本协议要求的最小字段：

```python
def _task_to_response(task: Task, tool_name: str) -> dict:
    if task.status == "completed":
        return {
            "type": "tool.completed",
            "tool": {
                "id": task.id,
                "name": tool_name,
                "status": "completed",
                "outputs": [
                    {"type": _infer_output_type(url), "url": url}
                    for url in task.urls()
                ],
                "metadata": {"model": task.model},
            },
        }

    return {
        "type": "tool.failed",
        "tool": {
            "id": task.id,
            "name": tool_name,
            "status": "failed",
            "error": {"message": str(task.error)},
        },
    }
```

## 与 Agent 事件协议的关系

| 层级 | 协议 | 用途 |
|------|------|------|
| Agent 层 | OpenAI Responses API | Agent 与 Gateway 之间的通信 |
| Tool 层 | Tool Response Protocol | 远程工具与 Fabric 之间的通信 |

Tool 调用结果会被包装进 Agent 事件：

```json
{
  "type": "response.output_item.done",
  "item": {
    "type": "function_call",
    "name": "compose_video",
    "output": "{\"type\":\"tool.completed\",\"tool\":{...}}"
  }
}
```

## 示例：compose_video

### 请求

```json
POST /tools/compose_video
Content-Type: application/json

{
  "video_urls": ["https://..."],
  "audio_url": "https://...",
  "replace_audio": true
}
```

### SSE 响应

```
data: {"type":"tool.created","tool":{"id":"task_123","name":"compose_video","status":"pending"}}

data: {"type":"tool.in_progress","tool":{"id":"task_123","name":"compose_video","status":"in_progress","progress":50,"message":"Merging clips..."}}

data: {"type":"tool.completed","tool":{"id":"task_123","name":"compose_video","status":"completed","outputs":[{"type":"video","url":"https://cdn.example.com/output.mp4","content_type":"video/mp4"}],"usage":{"cost":0.05}}}

data: [DONE]
```

### JSON 响应

```json
{
  "type": "tool.completed",
  "tool": {
    "id": "task_123",
    "name": "compose_video",
    "status": "completed",
    "outputs": [
      {
        "type": "video",
        "url": "https://cdn.example.com/output.mp4",
        "content_type": "video/mp4"
      }
    ]
  }
}
```

---

## 注意事项

1. **不要省略 `type` 字段**，Fabric 依赖 `type` 识别终态事件。
2. **`outputs` 在 `tool.completed` 中必须存在**，即使为空数组 `[]`。
3. **SSE 模式中 Fabric 只取最后一个终态事件**（`tool.completed` / `tool.failed` / `tool.cancelled`）作为最终结果，中间事件仅用于日志。
4. **`metadata` 可放任意附加字段**，不会被 Fabric 丢弃。
5. **HTTP 400~5xx 错误**由 Fabric 侧自动转换为 `tool.failed`，无需工具服务自行处理网络层错误。

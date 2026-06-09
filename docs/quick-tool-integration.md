# Tool 快速接入说明

这份文档面向想用当前 `toolctl-sdk` 快速暴露一个 HTTP tool 的服务方。

当前 SDK 有 3 种常见接入方式：

1. 代码直注册：你自己写 handler，用 `@app.tool(...)` 或 `app.register_tool(...)`
2. 代理已有 HTTP 服务：用 `app.register_proxy_tool(...)`
3. 从 OpenAPI 导入：用 `app.register_tool_from_openapi(...)`

如果你只是要最快跑起来，优先用第 1 种。

## 1. 安装

```bash
pip install -e .
```

## 2. 最短路径：注册一个本地 tool

```python
from sea_tools_server_sdk import ToolResult, toolctl


app = toolctl.start(
    title="demo-tools",
    version="0.1.0",
    description="Demo tool service",
)


@app.tool(
    name="ping",
    description="Return the submitted message.",
    request_schema={
        "type": "object",
        "properties": {
            "message": {"type": "string"},
        },
        "required": ["message"],
    },
    tags=["demo"],
)
async def ping(payload: dict) -> ToolResult:
    return ToolResult(
        outputs=[],
        metadata={"echo": payload["message"]},
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080)
```

启动后可直接调用：

```bash
curl -X POST "http://127.0.0.1:8080/tools/ping" \
  -H "Content-Type: application/json" \
  -d '{"message":"hello"}'
```

返回值默认会被 SDK 包装成统一协议：

```json
{
  "type": "tool.completed",
  "tool": {
    "id": "task_xxx",
    "name": "ping",
    "status": "completed",
    "outputs": [],
    "metadata": {
      "echo": "hello"
    }
  }
}
```

## 3. handler 应该返回什么

最常见有 3 种返回方式：

### 3.1 返回普通 `dict`

```python
@app.tool(
    name="echo",
    description="Echo payload",
    request_schema={"type": "object", "properties": {}},
)
async def echo(payload: dict) -> dict:
    return {"ok": True, "payload": payload}
```

SDK 会自动包装成 `tool.completed`，并把原始结果放到 `tool.metadata.result`。

### 3.2 返回 `ToolResult`

如果你的工具会产出图片、视频、音频、文件等，优先返回 `ToolResult`：

```python
from sea_tools_server_sdk import ToolResult, file_output


@app.tool(
    name="compose_video",
    description="Compose a video.",
    request_schema={"type": "object", "properties": {}},
)
async def compose_video(_payload: dict) -> ToolResult:
    return ToolResult(
        outputs=[
            file_output(
                "video",
                "https://cdn.example.com/output.mp4",
                content_type="video/mp4",
                duration_ms=30000,
            )
        ],
        usage={"duration_ms": 6358},
        metadata={"provider": "ffmpeg"},
    )
```

### 3.3 直接返回协议事件

如果你已经自己构造好了协议，也可以直接返回：

```python
from sea_tools_server_sdk import completed


@app.tool(
    name="ready_made_result",
    description="Return a protocol event directly.",
    request_schema={"type": "object", "properties": {}},
)
async def ready_made_result(_payload: dict) -> dict:
    return completed(
        tool_name="ready_made_result",
        task_id="task_fixed",
        outputs=[],
        metadata={"result": "ok"},
    )
```

## 4. 什么时候用 SSE

如果你的 tool 需要进度流，使用 `@app.sse_tool(...)` 或 `register_sse_tool(...)`：

```python
from sea_tools_server_sdk import completed, in_progress, toolctl


app = toolctl.start(title="stream-tools", version="0.1.0")


@app.sse_tool(
    name="stream_ping",
    description="Stream progress events.",
    request_schema={
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
)
async def stream_ping(payload: dict):
    async def generator():
        yield in_progress(
            tool_name="stream_ping",
            task_id="task_stream_ping",
            progress=50,
            message=payload["message"],
        )
        yield completed(
            tool_name="stream_ping",
            task_id="task_stream_ping",
            outputs=[],
            metadata={"result": "ok"},
        )

    return generator()
```

SSE 模式下，SDK 会：

1. 自动先发一个 `tool.created`
2. 透传你产出的中间事件
3. 在流结尾补 `data: [DONE]`

## 5. 代理一个已有 HTTP 服务

如果你已经有上游服务，不想重写 handler，可以直接代理：

```python
from sea_tools_server_sdk import AuthConfig, toolctl


app = toolctl.start(title="proxy-tools", version="0.1.0")

app.register_proxy_tool(
    name="get_video_metadata",
    description="Proxy video metadata requests.",
    base_url="https://video.example.com",
    path="/tools/get_video_metadata",
    request_schema={
        "type": "object",
        "properties": {
            "video_url": {"type": "string"},
            "video_path": {"type": "string"},
        },
    },
    auth=AuthConfig(type="bearer", token="YOUR_TOKEN"),
    retry_count=2,
    retry_delay_seconds=0.2,
    verify_tls=True,
)
```

说明：

1. 对外暴露路径就是 `path`
2. 如果上游路径不同，可额外传 `upstream_path`
3. `auth` 同时支持 `bearer`、`api_key`、自定义 header
4. `response_mode="sse"` 时会代理上游 SSE

## 6. 从 OpenAPI 快速导入

如果上游已经有 OpenAPI，可以直接导一个 operation：

```python
app.register_tool_from_openapi(
    name="weather_lookup",
    base_url="https://api.example.com",
    spec_url="https://api.example.com/openapi.json",
    operation_id="weatherLookup",
)
```

也可以改用：

1. `spec=...` 直接传字典
2. `spec_path="openapi.json"` 从本地文件读取
3. `path="/weather", method="POST"` 按路径和方法定位 operation

导入后：

1. SDK 会提取该 operation 的 JSON request schema
2. 对外默认注册成 `/tools/<name>`
3. 实际请求会被转发到上游原始 path

## 7. 注册到 gateway

本地注册完 tool 之后，可以直接导出或提交注册载荷。

### 7.1 只导出 payload

```python
payloads = app.export_gateway_payloads(
    provider="demo",
    base_url="http://tools.example.com",
    version="v1",
    category="general",
)
```

### 7.2 直接提交到 gateway

```python
from sea_tools_server_sdk import AuthConfig


results = app.register_to_gateway(
    gateway_url="https://gateway.example.com/v1/tools/register",
    provider="demo",
    base_url="http://tools.example.com",
    gateway_auth=AuthConfig(type="bearer", token="GATEWAY_TOKEN"),
    verify_tls=True,
    retry_count=1,
)
```

生成的 tool id 规则是：

```text
<provider>:<tool_name>:<version>
```

例如：

```text
demo:ping:v1
```

## 8. 默认暴露的系统接口

每个 `ToolApp` 默认会带这些接口：

1. `GET /health`
2. `GET /tools`
3. `GET /openapi.json`
4. `GET /docs`

其中：

1. `/tools` 用于查看当前已注册工具列表
2. `/openapi.json` 和 `/docs` 方便联调和自查

## 9. 当前 SDK 的几个接入约束

这是按当前实现整理的，不是泛化建议。

1. 运行时入参只支持 JSON object；请求体如果不是对象会返回 400 或协议失败
2. 当前运行时校验主要检查 `required` 字段是否缺失，不会完整执行 JSON Schema 校验
3. 默认 `protocol_mode="strict"`，返回会被包装成统一 tool 协议
4. 如果你还要兼容旧接口，可临时用 `protocol_mode="passthrough"`
5. `response_mode` 只支持 `"json"` 和 `"sse"`
6. `GET` 工具会从 query string 取参数；其他方法从 JSON body 取参数

## 10. 推荐接入顺序

如果你要尽快把一个 tool 接进来，建议按这个顺序：

1. 先用 `@app.tool(...)` 写一个最小可跑通版本
2. 用 `curl` 验证 `/tools/<name>` 的协议响应
3. 确认是否需要 `ToolResult.outputs`
4. 需要进度再切换成 `@app.sse_tool(...)`
5. 工具稳定后再调用 `register_to_gateway(...)`

## 11. 一个完整的最小模板

```python
from sea_tools_server_sdk import ToolResult, toolctl


app = toolctl.start(title="my-tools", version="0.1.0")


@app.tool(
    name="my_tool",
    description="Describe what the tool does.",
    request_schema={
        "type": "object",
        "properties": {
            "input": {"type": "string"},
        },
        "required": ["input"],
    },
)
async def my_tool(payload: dict) -> ToolResult:
    result = payload["input"].upper()
    return ToolResult(
        outputs=[],
        metadata={"result": result},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
```

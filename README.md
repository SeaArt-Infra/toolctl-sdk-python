# toolctl-sdk

`toolctl-sdk` 是面向工具服务提供方的 Python 服务端 SDK。它用于将 Python 函数、已有 HTTP API 或 OpenAPI 服务封装为标准化工具服务，并提供工具注册、协议化响应、工具清单、SSE 流式输出和资源监控等能力。

## 特性

- **快速构建工具服务**：通过 `toolctl.start()` 或 `create_app()` 创建服务应用。
- **代码优先注册工具**：通过 `@app.tool()` 注册普通工具，通过 `@app.sse_tool()` 注册流式工具。
- **协议化响应**：默认输出标准 tool protocol 响应，支持 JSON 和 SSE 两种模式。
- **代理与导入能力**：支持代理已有 HTTP API，并支持从 OpenAPI / Swagger 注册工具。
- **服务发现**：内置 `/tools` 和 `/tool-manifest.json`，用于工具元数据发现。
- **运行状态检查**：内置 `/health`、`/openapi.json`、`/docs` 等基础路由。
- **资源监控**：支持定时采集并上报服务心跳、CPU、内存、进程等运行指标。
- **证书加载**：支持通过本地文件路径读取 GCP service account credentials JSON。

## 安装

开发模式安装：

```bash
pip install -e .
```

如需使用 Google Pub/Sub 资源监控发布器，请安装 `pubsub` 可选依赖：

```bash
pip install -e '.[pubsub]'
```

## 快速开始

以下示例创建一个最小工具服务，并注册一个 `ping` 工具：

```python
from sea_tools_server_sdk import toolctl

app = toolctl.start(title="video-tools", version="0.1.0")


@app.tool(
    name="ping",
    description="返回提交的 payload。",
    request_schema={
        "type": "object",
        "properties": {
            "message": {"type": "string"},
        },
        "required": ["message"],
    },
)
async def ping(payload: dict) -> dict:
    return {"ok": True, "payload": payload}


app.run(host="127.0.0.1", port=8080)
```

调用工具：

```bash
curl -X POST "http://127.0.0.1:8080/tools/ping" \
  -H "Content-Type: application/json" \
  -d '{"message":"hello"}'
```

响应示例：

```json
{
  "type": "tool.completed",
  "tool": {
    "id": "task_xxx",
    "name": "ping",
    "status": "completed",
    "outputs": [],
    "metadata": {
      "result": {
        "ok": true,
        "payload": {
          "message": "hello"
        }
      }
    }
  }
}
```

也可以使用顶层构造函数创建应用：

```python
from sea_tools_server_sdk import create_app

app = create_app(title="video-tools", version="0.1.0")
```

## 工具注册

### 普通工具

普通工具使用 `@app.tool()` 注册。建议使用 JSON Schema 描述请求参数，便于调用方、调度器或工具发现系统理解工具能力。

```python
@app.tool(
    name="add",
    description="计算两个数字之和。",
    request_schema={
        "type": "object",
        "properties": {
            "a": {"type": "number"},
            "b": {"type": "number"},
        },
        "required": ["a", "b"],
    },
)
async def add(payload: dict) -> dict:
    return {"result": payload["a"] + payload["b"]}
```

默认情况下，SDK 会将函数返回值包装为标准 tool protocol 响应。

### 富媒体输出

当工具需要返回图片、视频、文件等结构化结果时，可以返回 `ToolResult`。

```python
from sea_tools_server_sdk import ToolResult, file_output


@app.tool(
    name="compose_video",
    description="合成视频。",
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

如果没有显式提供 `usage.cost`，SDK 会默认设置为 `0`。

### SSE 流式工具

SSE 工具适用于需要持续返回进度、日志或分阶段结果的任务。

```python
from sea_tools_server_sdk import completed, in_progress


@app.sse_tool(
    name="stream_ping",
    description="流式返回执行进度。",
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

SSE 响应会按协议输出事件，并以 `data: [DONE]` 结束。

## 工具清单

SDK 会为已注册工具维护应用内工具清单。工具发现系统应优先读取该清单，而不是依赖 OpenAPI 路由或手写文档推断工具能力。

建议为服务配置稳定的 `server_name`：

```python
app = toolctl.start(
    title="Video Tools",
    server_name="video-tools",
    version="0.1.0",
)
```

进程内读取工具清单：

```python
manifest = app.tool_manifest("ping")
if manifest:
    print(manifest.server_name)
    print(manifest.response_mode)  # "json" 或 "sse"
    print(manifest.is_sse)
    print(manifest.request_schema)
```

通过 HTTP 读取工具清单：

```bash
curl http://127.0.0.1:8080/tool-manifest.json
```

`/tool-manifest.json` 包含 `server_name`、工具描述、`request_schema`、可选的 `response_schema`、`response_mode`、`is_sse`、HTTP method、path、tags、timeout 和协议模式等信息。`/tools` 提供更轻量的工具元数据列表。

## 协议模式

默认协议模式为 `strict`，SDK 会返回标准 tool protocol 响应。

如需临时兼容旧版原始 JSON 返回，可以为单个工具设置 `protocol_mode="passthrough"`：

```python
@app.tool(
    name="legacy_ping",
    description="旧版返回格式。",
    request_schema={"type": "object", "properties": {}},
    protocol_mode="passthrough",
)
async def legacy_ping(_payload: dict) -> dict:
    return {"ok": True}
```

## 资源监控

SDK 内置资源监控能力，可定时采集并发布服务心跳。默认采集间隔为 5 秒。

心跳数据包含 `id`、`ip`、`routes`、`send_time`、`machine_id`、`status`、`category`、`task_url`、`instance_group`、`express`、`task_express_url`、`cloud`、`host_id`、`partition` 等调度相关字段，并附加 CPU、内存、进程 RSS、进程 CPU、uptime、load average 和主机元数据。

### 自定义 publisher

如果业务方已有消息发布实现，可以传入自定义 publisher。publisher 需要实现 `publish(data, attributes=None, ordering_key=None)` 方法。

```python
from sea_tools_server_sdk import start_resource_monitor


class MyPublisher:
    def publish(
        self,
        data: bytes,
        attributes: dict[str, str] | None = None,
        ordering_key: str | None = None,
    ):
        print(data.decode("utf-8"), attributes)
        return {"ok": True}


monitor = start_resource_monitor(
    service_name="web-tool",
    publisher=MyPublisher(),
    enabled=True,
    interval_seconds=5,
    labels={"tool": "web-tool", "port": "8080", "api": "tools"},
)
```

### Google Pub/Sub publisher

SDK 提供 `PubSubMetricsPublisher`，可将资源监控数据发布到 Google Pub/Sub。

```python
from sea_tools_server_sdk import PubSubMetricsPublisher, start_resource_monitor

publisher = PubSubMetricsPublisher(
    topic="projects/PROJECT_ID/topics/TOPIC_NAME",
    credentials_file="./configs/service-account.json",
)

monitor = start_resource_monitor(
    service_name="web-tool",
    publisher=publisher,
    enabled=True,
    interval_seconds=5,
    labels={"tool": "web-tool", "port": "8080", "api": "tools"},
)
```

`topic` 可以传完整 Pub/Sub topic path：

```text
projects/PROJECT_ID/topics/TOPIC_NAME
```

如果只传短 topic 名称，需要同时传入 `project_id`：

```python
publisher = PubSubMetricsPublisher(
    topic="TOPIC_NAME",
    project_id="PROJECT_ID",
    credentials_file="./configs/service-account.json",
)
```

### 绑定 ToolApp 生命周期

对于 `ToolApp`，推荐使用 `enable_resource_monitoring(...)`，SDK 会自动在 FastAPI startup / shutdown 阶段启动和停止监控。

```python
publisher = PubSubMetricsPublisher(
    topic="projects/PROJECT_ID/topics/TOPIC_NAME",
    credentials_file="./configs/service-account.json",
)

app.enable_resource_monitoring(
    publisher=publisher,
    enabled=True,
    interval_seconds=5,
    publish_immediately=True,
    labels={"tool": "web-tool"},
)
```

设置 `enabled=False` 时，监控配置会被保留，但不会启动心跳线程，也不会发布指标。

## 证书文件加载

SDK 提供 `get_credentials_json(...)` 用于从本地路径读取 GCP service account credentials JSON。

示例路径：

```text
/app/gcp/service-account.json
```

基础用法：

```python
from sea_tools_server_sdk.vault import get_credentials_json, project_id_from_credentials_json

credentials_json = get_credentials_json("/app/gcp/service-account.json")
project_id = project_id_from_credentials_json(credentials_json)
```

`get_credentials_json(...)` 行为说明：

- `key_path` 为本地证书文件路径。
- 文件内容支持 raw JSON 或 base64 编码后的 JSON。
- 返回值为 base64 编码后的 JSON 字符串。
- 证书文件不存在或内容格式非法时，会抛出 `VaultError`。

与 `PubSubMetricsPublisher` 结合使用：

```python
from sea_tools_server_sdk import PubSubMetricsPublisher
from sea_tools_server_sdk.vault import get_credentials_json, project_id_from_credentials_json

credentials_json = get_credentials_json("/app/gcp/service-account.json")
project_id = project_id_from_credentials_json(credentials_json)

publisher = PubSubMetricsPublisher(
    topic="TOPIC_NAME",
    project_id=project_id,
    credentials_json=credentials_json,
)
```

## 项目示例

- `examples/basic_app.py`
- `examples/proxy_app.py`
- `docs/quick-tool-integration.md`
- `docs/tool-response-protocol.md`

## 测试

运行全部测试：

```bash
uv run pytest
```

运行资源监控和证书路径相关测试：

```bash
uv run pytest tests/test_monitoring.py tests/test_vault.py
```

<script
  type="text/plain"
  data-doc-skill
  data-doc-skill-id="toolctl-sdk-python"
  data-doc-skill-label="Toolctl Python SDK"
  data-doc-skill-filename="toolctl-sdk-python-SKILL.md"
  data-doc-skill-version="1"
>
---
name: toolctl-sdk-python
description: Build and extend Python HTTP tool services with toolctl-sdk. Use when creating a ToolApp, registering JSON or SSE tools, proxying an existing HTTP API, importing an OpenAPI operation, exposing tool manifests, or configuring tool-service resource monitoring.
---

# Toolctl Python SDK

Use `toolctl-sdk` to expose Python handlers and upstream HTTP APIs as standard tool services. Keep handlers asynchronous and provide a JSON Schema request body for every tool.

## Create A Tool Service

Install the package in the service environment, then create the application with a stable `server_name` when it differs from the display title.

```python
from sea_tools_server_sdk import toolctl

app = toolctl.start(
    title="Video Tools",
    server_name="video-tools",
    version="0.1.0",
)
```

Register ordinary request-response tools with `@app.tool`. Handlers must be `async`; return a `dict` for an automatically wrapped `tool.completed` response, or return `ToolResult` when producing structured outputs.

```python
@app.tool(
    name="ping",
    description="Return the submitted message.",
    request_schema={
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
)
async def ping(payload: dict) -> dict:
    return {"message": payload["message"]}


app.run(host="0.0.0.0", port=8080)
```

Use `protocol_mode="strict"` unless a caller explicitly requires raw legacy JSON. Set `protocol_mode="passthrough"` only for that compatibility case.

## Stream Progress Or Return Files

Use `@app.sse_tool` for handlers that stream progress. Yield protocol events such as `in_progress(...)` and `completed(...)`; the SDK sends the initial creation event and the final `data: [DONE]` marker.

```python
from sea_tools_server_sdk import completed, in_progress


@app.sse_tool(
    name="render",
    description="Render a video with progress.",
    request_schema={"type": "object", "properties": {}},
)
async def render(_payload: dict):
    async def events():
        yield in_progress(tool_name="render", task_id="task_render", progress=50)
        yield completed(tool_name="render", task_id="task_render", outputs=[])

    return events()
```

For image, video, audio, or file results, return `ToolResult(outputs=[file_output(...)])` instead of placing media URLs in an unstructured dictionary.

## Proxy Or Import An Existing API

Use `register_proxy_tool` to retain an existing HTTP implementation. The public tool route is `path`; set `upstream_path` only when the upstream path is different. Pass `response_mode="sse"` to proxy an upstream event stream, and use `AuthConfig` for bearer, API-key, or custom-header authentication.

```python
from sea_tools_server_sdk import AuthConfig

app.register_proxy_tool(
    name="get_video_metadata",
    description="Read video metadata.",
    base_url="https://video.example.com",
    path="/tools/get_video_metadata",
    request_schema={"type": "object", "properties": {"video_url": {"type": "string"}}},
    auth=AuthConfig(type="bearer", token="${VIDEO_API_TOKEN}"),
    retry_count=2,
)
```

Use `register_tool_from_openapi` when the upstream service publishes an OpenAPI document. Select exactly one operation with `operation_id`, or with both `path` and `method`.

```python
app.register_tool_from_openapi(
    name="weather_lookup",
    base_url="https://api.example.com",
    spec_url="https://api.example.com/openapi.json",
    operation_id="weatherLookup",
)
```

Do not put real tokens, credentials, or production-only endpoints in source code or tool descriptions.

## Discover And Verify

Use `/health` to check process health, `/tools` for a lightweight tool list, and `/tool-manifest.json` for complete discovery metadata. `app.tool_manifest(name)` returns a single manifest in process; `app.tools_manifest()` returns all registered tools.

Run `uv run pytest` before delivery. Start the service, POST a valid request to `/tools/<name>`, inspect the response mode, and retrieve `/tool-manifest.json` to verify that the registered schema, route, and protocol metadata are visible.

## Monitor A Tool Service

Use `app.enable_resource_monitoring(...)` to bind resource monitoring to the FastAPI startup and shutdown lifecycle. Supply a publisher that implements `publish(data, attributes=None, ordering_key=None)`. Use `PubSubMetricsPublisher` only when Google Pub/Sub credentials and topic configuration are available.
</script>

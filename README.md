# toolctl-sdk

`toolctl-sdk` is a server-side SDK for plain HTTP tool services.

It is designed for tool providers, not tool callers.

Features:

- `toolctl.start()` to create a tool app quickly
- `create_app()` as a direct top-level constructor
- protocol-first JSON and SSE responses
- `register_tool()` for code-first tools
- `register_sse_tool()` for code-first SSE tools
- `register_proxy_tool()` for existing upstream HTTP APIs
- `register_tool_from_openapi()` for OpenAPI / Swagger-backed tools
- `register_to_gateway()` to submit current tools to agent-gateway
- built-in `/health`, `/tools`, `/openapi.json`, and `/docs`
- gateway registration payload export
- proxy auth, retry, and TLS controls
- optional SSE response mode for local and proxy tools

## Install

```bash
pip install -e .
```

## Quick start

```python
from sea_tools_server_sdk import toolctl

app = toolctl.start(title="video-tools", version="0.1.0")

@app.tool(
    name="ping",
    description="Return the submitted payload.",
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

If you prefer a direct constructor:

```python
from sea_tools_server_sdk import create_app

app = create_app(title="video-tools", version="0.1.0")
```

Then call:

```bash
curl -X POST "http://127.0.0.1:8080/tools/ping" \
  -H "Content-Type: application/json" \
  -d '{"message":"hello"}'
```

Response:

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

For richer output items, return `ToolResult`:

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

## SSE tool

```python
from sea_tools_server_sdk import completed, in_progress


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

SSE streams are emitted with protocol events and end with `data: [DONE]`.

## Compatibility mode

Default behavior is `protocol_mode="strict"`.

If you still need raw legacy JSON temporarily:

```python
@app.tool(
    name="legacy_ping",
    description="Legacy behavior",
    request_schema={"type": "object", "properties": {}},
    protocol_mode="passthrough",
)
async def legacy_ping(_payload: dict) -> dict:
    return {"ok": True}
```

## Examples

- `examples/basic_app.py`
- `examples/proxy_app.py`
- `docs/quick-tool-integration.md`
- `docs/tool-response-protocol.md`

## Register to gateway

```python
results = app.register_to_gateway(
    gateway_url="https://gateway.example.com/v1/tools/register",
    provider="video-tools",
    base_url="https://video.example.com",
    verify_tls=False,
)
```

## Resource monitoring

`toolctl-sdk` includes resource monitoring for tool services. The SDK publishes comfy-agent style heartbeats with the same top-level scheduling fields (`id`, `ip`, `routes`, `send_time`, `machine_id`, `status`, `category`, `task_url`, `instance_group`, `express`, `task_express_url`, `cloud`, `host_id`, `partition`). CPU, memory, process RSS, process CPU, uptime, load average, and host metadata are appended to that payload. The default interval is 5 seconds.

The SDK does not own topics or messaging configuration. Tools can pass their own publisher, or use the SDK Pub/Sub publisher with config values loaded by the tool:

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

Set `enabled=False` to keep resource monitoring configured but inactive. Disabled monitors do not start the heartbeat thread, publish metrics, or require a publisher.

`PubSubMetricsPublisher` accepts full Pub/Sub topic paths. If a tool passes a short topic name instead, it must also pass `project_id`.

For `ToolApp` instances, `enable_resource_monitoring(...)` attaches start/stop to the FastAPI lifecycle.

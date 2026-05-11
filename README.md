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
    "outputs": [
      {
        "type": "text",
        "content": "{\"ok\": true, \"payload\": {\"message\": \"hello\"}}"
      }
    ],
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
from sea_tools_server_sdk import completed, in_progress, text_output


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
            outputs=[text_output("ok")],
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

## Register to gateway

```python
results = app.register_to_gateway(
    gateway_url="https://gateway.example.com/v1/tools/register",
    provider="video-tools",
    base_url="https://video.example.com",
    verify_tls=False,
)
```

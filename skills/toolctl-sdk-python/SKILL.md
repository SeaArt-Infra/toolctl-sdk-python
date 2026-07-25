---
name: toolctl-sdk-python
description: Build and extend Python HTTP tool services with toolctl-sdk. Use when creating a ToolApp, registering JSON or SSE tools, proxying an existing HTTP API, importing an OpenAPI operation, exposing tool manifests, or configuring tool-service resource monitoring.
---

# Toolctl Python SDK

Use `toolctl-sdk` to expose Python handlers and upstream HTTP APIs as standard tool services. Keep handlers asynchronous and provide a JSON Schema request body for every tool.

## Install

Install the current GitHub `main` revision in the service environment:

```bash
pip install "git+https://github.com/SeaArt-Infra/toolctl-sdk-python.git@main"
```

## Create A Tool Service

Create the application with a stable `server_name` when it differs from the display title.

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

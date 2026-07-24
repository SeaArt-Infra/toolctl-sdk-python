# toolctl-sdk

`toolctl-sdk` is a Python server SDK for tool-service providers. It wraps Python functions, existing HTTP APIs, and OpenAPI services as standardized tool services with tool registration, protocol responses, tool manifests, SSE streaming, and resource monitoring.

## Features

- **Quick service setup**: Create an application with `toolctl.start()` or `create_app()`.
- **Code-first tool registration**: Register standard tools with `@app.tool()` and streaming tools with `@app.sse_tool()`.
- **Protocol responses**: Return standard tool protocol responses in JSON or SSE mode by default.
- **Proxy and import support**: Proxy existing HTTP APIs or register tools from OpenAPI and Swagger definitions.
- **Service discovery**: Expose built-in `/tools` and `/tool-manifest.json` metadata endpoints.
- **Runtime checks**: Include `/health`, `/openapi.json`, `/docs`, and other base routes.
- **Resource monitoring**: Periodically collect and publish service heartbeat, CPU, memory, and process metrics.
- **Credential loading**: Read GCP service-account credential JSON from a local file path.

## Installation

Install in development mode:

```bash
pip install -e .
```

Install the `pubsub` extra to use the Google Pub/Sub resource-monitoring publisher:

```bash
pip install -e '.[pubsub]'
```

## Quick Start

The following example creates a minimal tool service and registers a `ping` tool:

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

Call the tool:

```bash
curl -X POST "http://127.0.0.1:8080/tools/ping" \
  -H "Content-Type: application/json" \
  -d '{"message":"hello"}'
```

Example response:

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

Alternatively, create an application with the top-level constructor:

```python
from sea_tools_server_sdk import create_app

app = create_app(title="video-tools", version="0.1.0")
```

## Tool Registration

### Standard Tools

Register standard tools with `@app.tool()`. Use JSON Schema to describe request parameters so callers, schedulers, and discovery systems can understand the tool contract.

```python
@app.tool(
    name="add",
    description="Add two numbers.",
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

By default, the SDK wraps the function return value in a standard tool protocol response.

### Rich Media Output

Return `ToolResult` when a tool produces structured outputs such as images, videos, or files.

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

The SDK sets `usage.cost` to `0` when it is not provided explicitly.

### SSE Streaming Tools

Use SSE tools for tasks that continuously return progress, logs, or intermediate results.

```python
from sea_tools_server_sdk import completed, in_progress


@app.sse_tool(
    name="stream_ping",
    description="Stream execution progress.",
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

SSE responses emit protocol events and terminate with `data: [DONE]`.

## Tool Manifest

The SDK maintains an in-process manifest for registered tools. Tool discovery systems should read this manifest instead of inferring capabilities from OpenAPI routes or handwritten documentation.

Configure a stable `server_name` for the service:

```python
app = toolctl.start(
    title="Video Tools",
    server_name="video-tools",
    version="0.1.0",
)
```

Read a tool manifest in process:

```python
manifest = app.tool_manifest("ping")
if manifest:
    print(manifest.server_name)
    print(manifest.response_mode)  # "json" or "sse"
    print(manifest.is_sse)
    print(manifest.request_schema)
```

Read the tool manifest over HTTP:

```bash
curl http://127.0.0.1:8080/tool-manifest.json
```

`/tool-manifest.json` includes `server_name`, tool descriptions, `request_schema`, optional `response_schema`, `response_mode`, `is_sse`, HTTP method, path, tags, timeout, and protocol mode. `/tools` provides a lighter-weight list of tool metadata.

## Protocol Mode

The default protocol mode is `strict`, which returns standard tool protocol responses.

For temporary compatibility with legacy raw JSON responses, set `protocol_mode="passthrough"` on an individual tool:

```python
@app.tool(
    name="legacy_ping",
    description="Legacy response format.",
    request_schema={"type": "object", "properties": {}},
    protocol_mode="passthrough",
)
async def legacy_ping(_payload: dict) -> dict:
    return {"ok": True}
```

## Resource Monitoring

The SDK includes resource monitoring that periodically collects and publishes a service heartbeat. The default collection interval is five seconds.

Heartbeat data includes scheduler-oriented fields such as `id`, `ip`, `routes`, `send_time`, `machine_id`, `status`, `category`, `task_url`, `instance_group`, `express`, `task_express_url`, `cloud`, `host_id`, and `partition`, as well as CPU, memory, process RSS, process CPU, uptime, load average, and host metadata.

### Custom Publisher

Pass a custom publisher when the service already has a message-publishing implementation. The publisher must implement `publish(data, attributes=None, ordering_key=None)`.

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

### Google Pub/Sub Publisher

Use `PubSubMetricsPublisher` to publish resource-monitoring data to Google Pub/Sub.

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

Pass a complete Pub/Sub topic path as `topic`:

```text
projects/PROJECT_ID/topics/TOPIC_NAME
```

When passing a short topic name, also provide `project_id`:

```python
publisher = PubSubMetricsPublisher(
    topic="TOPIC_NAME",
    project_id="PROJECT_ID",
    credentials_file="./configs/service-account.json",
)
```

### Bind To The ToolApp Lifecycle

For `ToolApp`, use `enable_resource_monitoring(...)` to start and stop monitoring automatically during FastAPI startup and shutdown.

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

With `enabled=False`, the monitoring configuration is retained, but the heartbeat thread does not start and no metrics are published.

## Credential File Loading

Use `get_credentials_json(...)` to read GCP service-account credential JSON from a local path.

Example path:

```text
/app/gcp/service-account.json
```

Basic usage:

```python
from sea_tools_server_sdk.vault import get_credentials_json, project_id_from_credentials_json

credentials_json = get_credentials_json("/app/gcp/service-account.json")
project_id = project_id_from_credentials_json(credentials_json)
```

`get_credentials_json(...)` behavior:

- `key_path` is the path to a local credential file.
- File content may be raw JSON or base64-encoded JSON.
- The return value is a base64-encoded JSON string.
- A missing credential file or invalid content raises `VaultError`.

Use it with `PubSubMetricsPublisher`:

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

## Project Examples

- `examples/basic_app.py`
- `examples/proxy_app.py`
- `docs/quick-tool-integration.md`
- `docs/tool-response-protocol.md`

## Tests

Run the complete test suite:

```bash
uv run pytest
```

Run resource-monitoring and credential-path tests:

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

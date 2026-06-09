# toolctl-sdk-python

Python SDK for registering tool service metadata and handlers.

The SDK keeps tool metadata in one registry so agents and skills can discover a
tool service without guessing:

- stable `server_name`
- function name and description
- whether the function returns `json` or `sse`
- request parameter JSON schema
- optional response schema
- method, path, tags, timeout, and protocol mode
- HTTP endpoints for `/health`, `/tools`, `/tool-manifest.json`, and `/openapi.json`
- JSON and SSE tool responses
- resource heartbeat monitoring for scheduler integration

## Quick start

```python
from toolctl import App, AppConfig

app = App(AppConfig(title="Video Tools", server_name="video-tools"))

app.register_tool(
    name="ping",
    description="Return the submitted payload.",
    request_schema={
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Message to echo"},
        },
        "required": ["message"],
    },
    handler=lambda payload: {"ok": True, "payload": payload},
)

manifest = app.tool_manifest("ping")
print(manifest.server_name)
print(manifest.response_mode)  # "json"
print(manifest.is_sse)         # False
print(manifest.request_schema)

app.run("127.0.0.1", 8080)
```

## SSE tools

```python
app.register_sse_tool(
    name="stream_ping",
    description="Stream progress events.",
    request_schema={"type": "object", "properties": {}},
    handler=lambda payload, writer: None,
)

manifest = app.tool_manifest("stream_ping")
assert manifest.response_mode == "sse"
assert manifest.is_sse is True
```

## Server manifest

```python
payload = app.server_manifest()
```

`payload` contains `server_name`, `title`, `version`, and a sorted `tools` list.
Each tool item includes `description`, `request_schema`, optional
`response_schema`, `response_mode`, `is_sse`, `method`, `path`, `tags`,
`timeout_ms`, and `protocol_mode`.

Tool services should register every exposed function through this SDK. Skills and
agents should consume the manifest rather than deriving parameters, streaming
mode, or server identity from source code or route conventions.

## HTTP endpoints

```bash
curl http://127.0.0.1:8080/tool-manifest.json

curl -X POST http://127.0.0.1:8080/tools/ping \
  -H 'Content-Type: application/json' \
  -d '{"message":"hello"}'

curl -N -X POST http://127.0.0.1:8080/tools/stream_ping \
  -H 'Content-Type: application/json' \
  -d '{}'
```

## Scheduler monitoring

```python
from toolctl import EnableResourceMonitoringOptions

monitor = app.enable_resource_monitoring(
    EnableResourceMonitoringOptions(
        publish=lambda payload: print(payload),
        publish_immediately=True,
    )
)
```

When monitoring is enabled, the SDK reports busy while a tool request is active
and idle otherwise. The scheduler can use this heartbeat for instance selection.

The Python SDK also exposes scheduler helper APIs aligned with the Go SDK:

```python
from toolctl import (
    PubSubMetricsPublisher,
    PubSubMetricsPublisherOptions,
    get_credentials_json,
    project_id_from_credentials_json,
)
```

Use `EnableResourceMonitoringOptions(publish=...)` for the actual scheduler
publisher. `PubSubMetricsPublisher` keeps the same configuration shape as Go;
wire its publishing implementation to your runtime's Google auth stack when
needed.

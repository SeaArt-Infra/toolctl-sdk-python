import base64
import json

from toolctl import (
    App,
    AppConfig,
    CompletedOptions,
    EnableResourceMonitoringOptions,
    MACHINE_STATUS_BUSY,
    MACHINE_STATUS_IDLE,
    PubSubMetricsPublisher,
    PubSubMetricsPublisherOptions,
    completed,
    project_id_from_credentials_json,
)


def test_register_tool_exports_manifest_metadata():
    app = App(AppConfig(title="display-name", server_name="video-tools"))
    app.register_tool(
        name="json_ping",
        description="JSON ping",
        request_schema={
            "type": "object",
            "properties": {"message": {"type": "string", "description": "Message to echo"}},
            "required": ["message"],
        },
        handler=lambda payload: payload,
    )
    app.register_sse_tool(
        name="stream_ping",
        description="Stream ping",
        request_schema={"type": "object", "properties": {}},
        handler=lambda payload, writer: None,
    )

    manifest = app.tool_manifest("stream_ping")

    assert manifest is not None
    assert manifest.server_name == "video-tools"
    assert manifest.response_mode == "sse"
    assert manifest.is_sse is True
    assert manifest.request_schema["type"] == "object"

    server_manifest = app.server_manifest()
    assert server_manifest["server_name"] == "video-tools"
    assert [tool["name"] for tool in server_manifest["tools"]] == ["json_ping", "stream_ping"]


def test_http_json_tool_and_manifest():
    app = App(AppConfig(title="display-name", server_name="video-tools"))
    app.register_tool(
        name="ping",
        description="Ping",
        request_schema={
            "type": "object",
            "properties": {"message": {"type": "string", "description": "Message to echo"}},
            "required": ["message"],
        },
        handler=lambda payload: {"echo": payload["message"]},
    )

    status, headers, body = app.handle_request("GET", "/tool-manifest.json")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    manifest = json.loads(body)
    assert manifest["server_name"] == "video-tools"
    assert manifest["tools"][0]["request_schema"]["properties"]["message"]["description"] == "Message to echo"

    status, headers, body = app.handle_request("POST", "/tools/ping", b'{"message":"hello"}')
    assert status == 200
    payload = json.loads(body)
    assert payload["type"] == "tool.completed"
    assert payload["tool"]["metadata"]["result"]["echo"] == "hello"


def test_http_sse_tool():
    app = App(AppConfig(title="sse-tools"))
    app.register_sse_tool(
        name="stream_ping",
        request_schema={"type": "object", "properties": {}},
        handler=lambda payload, writer: writer.write(
            completed(CompletedOptions(tool_name="stream_ping", task_id="task_1", outputs=[], metadata={"result": "ok"}))
        ),
    )

    status, headers, body = app.handle_request("POST", "/tools/stream_ping", b"{}")
    assert status == 200
    assert headers["Content-Type"] == "text/event-stream"
    assert b"tool.created" in body
    assert b"tool.completed" in body
    assert b"data: [DONE]" in body


def test_resource_monitor_status_tracks_active_request():
    app = App(AppConfig(title="monitor-tools"))
    payloads = []
    monitor = app.enable_resource_monitoring(
        EnableResourceMonitoringOptions(publish=payloads.append, enabled=False)
    )

    assert monitor.collect_once()["status"] == MACHINE_STATUS_IDLE
    app._active_tool_requests = 1
    assert monitor.collect_once()["status"] == MACHINE_STATUS_BUSY


def test_scheduler_helpers():
    raw = '{"project_id":"demo-project"}'
    encoded = base64.b64encode(raw.encode()).decode()
    assert project_id_from_credentials_json(raw) == "demo-project"
    assert project_id_from_credentials_json(encoded) == "demo-project"

    publisher = PubSubMetricsPublisher(PubSubMetricsPublisherOptions(topic="events", project_id="demo-project"))
    assert publisher.topic_path == "projects/demo-project/topics/events"

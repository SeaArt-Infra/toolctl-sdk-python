from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from sea_tools_server_sdk import (
    AuthConfig,
    GatewayRegistrationResult,
    ToolResult,
    UpstreamNetworkError,
    completed,
    file_output,
    in_progress,
    toolctl,
)


class ToolAppTests(unittest.TestCase):
    def test_register_tool_and_call_route(self) -> None:
        app = toolctl.start(title="test-tools", version="0.1.0")

        @app.tool(
            name="ping",
            description="Ping",
            request_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
            tags=["demo"],
        )
        async def ping(payload: dict) -> dict:
            return {"echo": payload["message"]}

        client = TestClient(app.fastapi)
        response = client.post("/tools/ping", json={"message": "hello"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["type"], "tool.completed")
        self.assertEqual(body["tool"]["status"], "completed")
        self.assertEqual(body["tool"]["name"], "ping")
        self.assertEqual(body["tool"]["outputs"], [])
        self.assertEqual(body["tool"]["metadata"]["result"]["echo"], "hello")

    def test_missing_required_field_returns_protocol_failure(self) -> None:
        app = toolctl.start(title="test-tools", version="0.1.0")

        @app.tool(
            name="ping",
            description="Ping",
            request_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        )
        async def ping(payload: dict) -> dict:
            return payload

        client = TestClient(app.fastapi)
        response = client.post("/tools/ping", json={})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["type"], "tool.failed")
        self.assertEqual(body["tool"]["error"]["code"], "INVALID_INPUT")
        self.assertIn("Missing required fields", body["tool"]["error"]["message"])

    @patch("sea_tools_server_sdk.app.call_upstream_tool", new_callable=AsyncMock)
    def test_register_proxy_tool(self, mock_call_upstream: AsyncMock) -> None:
        mock_call_upstream.return_value = {"success": True}
        app = toolctl.start(title="proxy-tools", version="0.1.0")
        app.register_proxy_tool(
            name="video_metadata",
            description="Proxy metadata",
            base_url="http://example.com",
            path="/tools/video_metadata",
            request_schema={
                "type": "object",
                "properties": {"video_url": {"type": "string"}},
            },
            auth=AuthConfig(type="bearer", token="token-1"),
            retry_count=2,
            retry_delay_seconds=0.1,
        )

        client = TestClient(app.fastapi)
        response = client.post("/tools/video_metadata", json={"video_url": "https://example.com/test.mp4"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["type"], "tool.completed")
        self.assertEqual(body["tool"]["metadata"]["result"]["success"], True)
        mock_call_upstream.assert_awaited_once()
        self.assertEqual(mock_call_upstream.await_args.kwargs["auth"].token, "token-1")
        self.assertEqual(mock_call_upstream.await_args.kwargs["retry_count"], 2)

    def test_register_sse_tool(self) -> None:
        app = toolctl.start(title="sse-tools", version="0.1.0")

        @app.sse_tool(
            name="stream_ping",
            description="Stream ping events",
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

        client = TestClient(app.fastapi)
        response = client.post("/tools/stream_ping", json={"message": "hello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"].split(";")[0], "text/event-stream")
        self.assertIn('"type": "tool.created"', response.text)
        self.assertIn('"type": "tool.in_progress"', response.text)
        self.assertIn('"type": "tool.completed"', response.text)
        self.assertIn("data: [DONE]", response.text)

    def test_tool_manifest_endpoint_includes_schema_and_response_mode(self) -> None:
        async def ping(payload: dict) -> dict:
            return {"echo": payload["message"]}

        app = toolctl.start(title="Display Name", server_name="video-tools", version="0.1.0")
        app.register_tool(
            name="ping",
            description="Ping",
            request_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to echo"},
                },
                "required": ["message"],
            },
            handler=ping,
        )

        client = TestClient(app.fastapi)
        response = client.get("/tool-manifest.json")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["server_name"], "video-tools")
        self.assertEqual(body["tools"][0]["name"], "ping")
        self.assertEqual(body["tools"][0]["response_mode"], "json")
        self.assertFalse(body["tools"][0]["is_sse"])
        self.assertEqual(
            body["tools"][0]["request_schema"]["properties"]["message"]["description"],
            "Message to echo",
        )

    @patch("sea_tools_server_sdk.app.stream_upstream_tool")
    def test_register_proxy_sse_tool(self, mock_stream_upstream_tool) -> None:
        async def fake_stream():
            yield 'event: tool.in_progress\ndata: {"type":"tool.in_progress","tool":{"id":"task_1","name":"stream_video_status","status":"in_progress","progress":50}}\n\n'
            yield 'event: tool.completed\ndata: {"type":"tool.completed","tool":{"id":"task_1","name":"stream_video_status","status":"completed","outputs":[],"metadata":{"result":"ok"}}}\n\n'
            yield "data: [DONE]\n\n"

        mock_stream_upstream_tool.return_value = fake_stream()
        app = toolctl.start(title="proxy-sse-tools", version="0.1.0")
        app.register_proxy_tool(
            name="stream_video_status",
            description="Proxy video status stream",
            base_url="http://example.com",
            path="/tools/stream_video_status",
            request_schema={"type": "object", "properties": {}},
            response_mode="sse",
        )

        client = TestClient(app.fastapi)
        response = client.post("/tools/stream_video_status", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"].split(";")[0], "text/event-stream")
        self.assertIn('"type": "tool.created"', response.text)
        self.assertIn("event: tool.in_progress", response.text)
        self.assertIn("event: tool.completed", response.text)

    @patch("sea_tools_server_sdk.app.call_upstream_tool", new_callable=AsyncMock)
    def test_proxy_upstream_error_maps_to_protocol_failure(self, mock_call_upstream: AsyncMock) -> None:
        mock_call_upstream.side_effect = UpstreamNetworkError("upstream down")
        app = toolctl.start(title="proxy-tools", version="0.1.0")
        app.register_proxy_tool(
            name="video_metadata",
            description="Proxy metadata",
            base_url="http://example.com",
            path="/tools/video_metadata",
            request_schema={"type": "object", "properties": {}},
        )

        client = TestClient(app.fastapi)
        response = client.post("/tools/video_metadata", json={})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["type"], "tool.failed")
        self.assertEqual(body["tool"]["error"]["code"], "UPSTREAM_REQUEST_FAILED")
        self.assertIn("upstream down", body["tool"]["error"]["message"])

    def test_register_tool_supports_explicit_protocol_result(self) -> None:
        app = toolctl.start(title="protocol-tools", version="0.1.0")

        @app.tool(
            name="compose_video",
            description="Compose video",
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

        client = TestClient(app.fastapi)
        response = client.post("/tools/compose_video", json={})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["type"], "tool.completed")
        self.assertEqual(body["tool"]["outputs"][0]["type"], "video")
        self.assertEqual(body["tool"]["usage"]["duration_ms"], 6358)
        self.assertEqual(body["tool"]["usage"]["cost"], 0)

    def test_register_tool_defaults_cost_to_zero_when_usage_missing(self) -> None:
        app = toolctl.start(title="default-cost-tools", version="0.1.0")

        @app.tool(
            name="ping",
            description="Ping",
            request_schema={"type": "object", "properties": {}},
        )
        async def ping(_payload: dict) -> dict:
            return {"ok": True}

        client = TestClient(app.fastapi)
        response = client.post("/tools/ping", json={})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["type"], "tool.completed")
        self.assertEqual(body["tool"]["usage"]["cost"], 0)

    def test_register_tool_defaults_cost_to_zero_for_explicit_protocol_event(self) -> None:
        app = toolctl.start(title="default-cost-event-tools", version="0.1.0")

        @app.tool(
            name="compose_video",
            description="Compose video",
            request_schema={"type": "object", "properties": {}},
        )
        async def compose_video(_payload: dict) -> dict:
            return {
                "type": "tool.completed",
                "tool": {
                    "outputs": [
                        file_output(
                            "video",
                            "https://cdn.example.com/output.mp4",
                            content_type="video/mp4",
                        )
                    ]
                },
            }

        client = TestClient(app.fastapi)
        response = client.post("/tools/compose_video", json={})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["type"], "tool.completed")
        self.assertEqual(body["tool"]["usage"]["cost"], 0)

    def test_register_tool_passthrough_mode_keeps_legacy_response(self) -> None:
        app = toolctl.start(title="legacy-tools", version="0.1.0")

        @app.tool(
            name="legacy_ping",
            description="Legacy ping",
            request_schema={"type": "object", "properties": {}},
            protocol_mode="passthrough",
        )
        async def legacy_ping(_payload: dict) -> dict:
            return {"ok": True}

        client = TestClient(app.fastapi)
        response = client.post("/tools/legacy_ping", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_openapi_contains_registered_tool_schema(self) -> None:
        app = toolctl.start(title="openapi-tools", version="0.1.0")

        @app.tool(
            name="ping",
            description="Ping",
            request_schema={
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": ["message"],
            },
        )
        async def ping(payload: dict) -> dict:
            return payload

        client = TestClient(app.fastapi)
        response = client.get("/openapi.json")
        body = response.json()

        self.assertEqual(response.status_code, 200)
        route = body["paths"]["/tools/ping"]["post"]
        self.assertEqual(route["operationId"], "ping")
        self.assertEqual(route["requestBody"]["content"]["application/json"]["schema"]["required"], ["message"])
        self.assertEqual(route["responses"]["200"]["content"]["application/json"]["schema"]["required"], ["type", "tool"])

    def test_export_gateway_payloads(self) -> None:
        app = toolctl.start(title="gateway-tools", version="0.1.0")

        @app.tool(
            name="ping",
            description="Ping",
            request_schema={"type": "object", "properties": {}},
            tags=["demo"],
        )
        async def ping(payload: dict) -> dict:
            return payload

        payloads = app.export_gateway_payloads(
            provider="demo",
            base_url="http://tools.example.com",
            version="v1",
            category="general",
        )

        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["id"], "demo:ping:v1")
        self.assertEqual(payloads[0]["endpoint"], "http://tools.example.com/tools/ping")

    @patch("sea_tools_server_sdk.app.register_tools_to_gateway")
    def test_register_to_gateway(self, mock_register_tools_to_gateway) -> None:
        mock_register_tools_to_gateway.return_value = [
            GatewayRegistrationResult(name="ping", status=201, body={"data": {"name": "ping"}})
        ]
        app = toolctl.start(title="gateway-tools", version="0.1.0")

        @app.tool(
            name="ping",
            description="Ping",
            request_schema={"type": "object", "properties": {}},
        )
        async def ping(payload: dict) -> dict:
            return payload

        results = app.register_to_gateway(
            gateway_url="https://gateway.example.com/v1/tools/register",
            provider="demo",
            base_url="http://tools.example.com",
            gateway_auth=AuthConfig(type="bearer", token="abc"),
            verify_tls=False,
            retry_count=1,
        )

        self.assertEqual(results[0].status, 201)
        mock_register_tools_to_gateway.assert_called_once()
        kwargs = mock_register_tools_to_gateway.call_args.kwargs
        self.assertEqual(kwargs["auth"].token, "abc")
        self.assertEqual(kwargs["verify_tls"], False)


if __name__ == "__main__":
    unittest.main()

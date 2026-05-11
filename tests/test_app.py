from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from sea_tools_server_sdk import AuthConfig, GatewayRegistrationResult, UpstreamNetworkError, toolctl


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
        self.assertEqual(response.json()["echo"], "hello")

    def test_missing_required_field_returns_400(self) -> None:
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

        self.assertEqual(response.status_code, 400)
        self.assertIn("Missing required fields", response.json()["detail"])

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
        self.assertEqual(response.json(), {"success": True})
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
                yield {"event": "message", "data": {"message": payload["message"], "step": 1}}
                yield {"event": "done", "data": "ok"}

            return generator()

        client = TestClient(app.fastapi)
        response = client.post("/tools/stream_ping", json={"message": "hello"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"].split(";")[0], "text/event-stream")
        self.assertIn("event: message", response.text)
        self.assertIn("\"message\": \"hello\"", response.text)

    @patch("sea_tools_server_sdk.app.stream_upstream_tool")
    def test_register_proxy_sse_tool(self, mock_stream_upstream_tool) -> None:
        async def fake_stream():
            yield "event: message\ndata: hello\n\n"
            yield "event: done\ndata: ok\n\n"

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
        self.assertIn("event: message", response.text)

    @patch("sea_tools_server_sdk.app.call_upstream_tool", new_callable=AsyncMock)
    def test_proxy_upstream_error_maps_to_502(self, mock_call_upstream: AsyncMock) -> None:
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

        self.assertEqual(response.status_code, 502)
        self.assertIn("upstream down", response.json()["detail"])

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

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from .models import AppConfig, EnableResourceMonitoringOptions, HandlerFunc, StreamHandlerFunc, ToolManifest, ToolSpec, normalize_schema
from .monitoring import MACHINE_STATUS_BUSY, MACHINE_STATUS_IDLE, ResourceMonitor
from .protocol import FailedOptions, failed, new_task_id, normalize_json_result, sse_event


class ToolRegistrationError(ValueError):
    pass


class App:
    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        title: str = "",
        server_name: str = "",
        version: str = "0.1.0",
        description: str = "",
        base_path: str = "",
    ) -> None:
        if config is None:
            config = AppConfig(
                title=title,
                server_name=server_name,
                version=version or "0.1.0",
                description=description,
                base_path=base_path,
            )
        self.title = config.title
        self.server_name = config.server_name or config.title
        self.version = config.version or "0.1.0"
        self.description = config.description
        self.base_path = config.base_path
        self._tools: dict[str, ToolSpec] = {}
        self._active_tool_requests = 0
        self._resource_monitor: ResourceMonitor | None = None

    def register_tool(
        self,
        *,
        name: str,
        description: str = "",
        request_schema: Mapping[str, Any] | None = None,
        handler: HandlerFunc | None = None,
        method: str = "POST",
        path: str = "",
        tags: list[str] | None = None,
        response_schema: Mapping[str, Any] | None = None,
        timeout_ms: int = 30000,
        response_mode: str = "json",
        protocol_mode: str = "strict",
    ) -> ToolSpec:
        if not name:
            raise ToolRegistrationError("tool name is required")
        if handler is None:
            raise ToolRegistrationError("tool handler must be provided")
        spec = self._build_spec(
            name=name,
            description=description,
            request_schema=request_schema,
            handler=handler,
            method=method,
            path=path,
            tags=tags,
            response_schema=response_schema,
            timeout_ms=timeout_ms,
            response_mode=response_mode,
            protocol_mode=protocol_mode,
        )
        self._register_spec(spec)
        return spec

    def register_sse_tool(
        self,
        *,
        name: str,
        description: str = "",
        request_schema: Mapping[str, Any] | None = None,
        handler: StreamHandlerFunc | None = None,
        method: str = "POST",
        path: str = "",
        tags: list[str] | None = None,
        timeout_ms: int = 30000,
        protocol_mode: str = "strict",
    ) -> ToolSpec:
        if handler is None:
            raise ToolRegistrationError("stream handler must be provided")
        spec = self._build_spec(
            name=name,
            description=description,
            request_schema=request_schema,
            handler=None,
            stream_handler=handler,
            method=method,
            path=path,
            tags=tags,
            timeout_ms=timeout_ms,
            response_mode="sse",
            protocol_mode=protocol_mode,
        )
        self._register_spec(spec)
        return spec

    def tool(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def tool_manifest(self, name: str) -> ToolManifest | None:
        spec = self.tool(name)
        if spec is None:
            return None
        return spec.manifest(self.server_name)

    def tools_manifest(self) -> list[ToolManifest]:
        return [self._tools[name].manifest(self.server_name) for name in sorted(self._tools)]

    def server_manifest(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "server_name": self.server_name,
            "title": self.title,
            "version": self.version,
            "tools": [manifest.to_dict() for manifest in self.tools_manifest()],
        }
        if self.description:
            payload["description"] = self.description
        if self.base_path:
            payload["base_path"] = self.base_path
        return payload

    def enable_resource_monitoring(self, opts: EnableResourceMonitoringOptions | None = None, **kwargs: Any) -> ResourceMonitor:
        if opts is None:
            opts = EnableResourceMonitoringOptions(**kwargs)
        monitor = ResourceMonitor(self.title, opts, self._resource_monitor_status)
        self._resource_monitor = monitor
        monitor.start()
        return monitor

    def stop_resource_monitoring(self, timeout: float = 5.0) -> None:
        if self._resource_monitor is not None:
            self._resource_monitor.stop(timeout)

    def run(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        server = ThreadingHTTPServer((host, port), self._handler_class())
        server.serve_forever()

    def handle_request(self, method: str, path: str, body: bytes = b"", query: str = "") -> tuple[int, dict[str, str], bytes]:
        if method == "GET" and path == "/health":
            return self._json_response({"status": "ok"})
        if method == "GET" and path == "/tools":
            return self._json_response({"server_name": self.server_name, "tools": [item.to_dict() for item in self.tools_manifest()]})
        if method == "GET" and path == "/tool-manifest.json":
            return self._json_response(self.server_manifest())
        if method == "GET" and path == "/openapi.json":
            return self._json_response(self.openapi_schema())

        spec = next((tool for tool in self._tools.values() if tool.path == path and tool.method == method), None)
        if spec is None:
            return HTTPStatus.NOT_FOUND, {"Content-Type": "text/plain; charset=utf-8"}, b"Not found"
        return self._handle_tool(spec, body, query)

    def openapi_schema(self) -> dict[str, Any]:
        paths: dict[str, Any] = {
            "/health": {"get": {"tags": ["system"], "summary": "Health check", "responses": {"200": {"description": "Successful response"}}}},
            "/tools": {"get": {"tags": ["system"], "summary": "List tools", "responses": {"200": {"description": "Successful response"}}}},
            "/tool-manifest.json": {
                "get": {"tags": ["system"], "summary": "Tool manifest", "responses": {"200": {"description": "Successful response"}}}
            },
        }
        for spec in self._tools.values():
            paths.setdefault(spec.path, {})[spec.method.lower()] = {
                "summary": spec.description,
                "description": spec.description,
                "operationId": spec.name,
                "tags": spec.tags,
                "requestBody": {"required": True, "content": {"application/json": {"schema": spec.request_schema}}},
                "responses": {"200": {"description": "Successful response"}},
            }
        return {
            "openapi": "3.0.0",
            "info": {
                "title": self.title,
                "version": self.version,
                "description": self.description,
                "x-server-name": self.server_name,
            },
            "paths": paths,
        }

    def _build_spec(
        self,
        *,
        name: str,
        description: str,
        request_schema: Mapping[str, Any] | None,
        handler: HandlerFunc | None,
        stream_handler: StreamHandlerFunc | None = None,
        method: str,
        path: str,
        tags: list[str] | None,
        response_schema: Mapping[str, Any] | None = None,
        timeout_ms: int,
        response_mode: str,
        protocol_mode: str,
    ) -> ToolSpec:
        if response_mode not in {"json", "sse"}:
            raise ToolRegistrationError("response mode must be either 'json' or 'sse'")
        if protocol_mode not in {"strict", "passthrough"}:
            raise ToolRegistrationError("protocol mode must be either 'strict' or 'passthrough'")
        normalized_path = path or f"/tools/{name}"
        return ToolSpec(
            name=name,
            description=description,
            request_schema=normalize_schema(request_schema),
            handler=handler,
            stream_handler=stream_handler,
            method=(method or "POST").upper(),
            path=normalized_path,
            tags=list(tags or []),
            response_schema=dict(response_schema) if response_schema is not None else None,
            timeout_ms=timeout_ms or 30000,
            response_mode=response_mode,
            protocol_mode=protocol_mode,
        )

    def _register_spec(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ToolRegistrationError(f"tool {spec.name!r} is already registered")
        self._tools[spec.name] = spec

    def _handle_tool(self, spec: ToolSpec, body: bytes, query: str) -> tuple[int, dict[str, str], bytes]:
        task_id = new_task_id()
        try:
            payload = self._parse_payload(spec, body, query)
            self._validate_payload(spec.request_schema, payload)
        except ValueError as exc:
            if spec.protocol_mode == "passthrough":
                return HTTPStatus.BAD_REQUEST, {"Content-Type": "text/plain; charset=utf-8"}, str(exc).encode()
            return self._json_response(failed(FailedOptions(tool_name=spec.name, task_id=task_id, code="INVALID_INPUT", message=str(exc))))

        self._active_tool_requests += 1
        try:
            if spec.is_sse:
                chunks = [sse_event({"type": "tool.created", "tool": {"id": task_id, "name": spec.name, "status": "pending"}}, spec.name, task_id)]
                writer = _MemoryStreamWriter(chunks, spec.name, task_id, spec.protocol_mode == "strict")
                if spec.stream_handler is not None:
                    spec.stream_handler(payload, writer)
                elif spec.handler is not None:
                    writer.write(spec.handler(payload))
                chunks.append("data: [DONE]\n\n")
                return HTTPStatus.OK, {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}, "".join(chunks).encode()
            if spec.handler is None:
                raise RuntimeError("tool handler must be provided")
            result = spec.handler(payload)
            if spec.protocol_mode == "passthrough":
                return self._value_response(result)
            return self._json_response(normalize_json_result(result, spec.name, task_id))
        except Exception as exc:
            if spec.protocol_mode == "passthrough":
                return HTTPStatus.INTERNAL_SERVER_ERROR, {"Content-Type": "text/plain; charset=utf-8"}, str(exc).encode()
            return self._json_response(failed(FailedOptions(tool_name=spec.name, task_id=task_id, code="INTERNAL_ERROR", message=str(exc))))
        finally:
            self._active_tool_requests -= 1

    def _parse_payload(self, spec: ToolSpec, body: bytes, query: str) -> dict[str, Any]:
        if spec.method == "GET":
            parsed = parse_qs(query, keep_blank_values=True)
            return {key: values[0] if len(values) == 1 else values for key, values in parsed.items()}
        if not body.strip():
            return {}
        payload = json.loads(body.decode())
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _validate_payload(self, schema: dict[str, Any], payload: dict[str, Any]) -> None:
        required = schema.get("required") or []
        missing = [field for field in required if field not in payload]
        if missing:
            raise ValueError("Missing required fields: " + ", ".join(missing))

    def _resource_monitor_status(self) -> int:
        return MACHINE_STATUS_BUSY if self._active_tool_requests > 0 else MACHINE_STATUS_IDLE

    def _json_response(self, payload: Any, status: int = HTTPStatus.OK) -> tuple[int, dict[str, str], bytes]:
        return status, {"Content-Type": "application/json"}, json.dumps(payload, separators=(",", ":")).encode()

    def _value_response(self, payload: Any) -> tuple[int, dict[str, str], bytes]:
        if payload is None:
            return HTTPStatus.OK, {}, b""
        if isinstance(payload, str):
            return HTTPStatus.OK, {"Content-Type": "text/plain; charset=utf-8"}, payload.encode()
        return self._json_response(payload)

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        app = self

        class ToolctlHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self._serve()

            def do_POST(self) -> None:
                self._serve()

            def _serve(self) -> None:
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length) if length else b""
                status, headers, response = app.handle_request(self.command, parsed.path, body, parsed.query)
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(response)

        return ToolctlHandler


class _MemoryStreamWriter:
    def __init__(self, chunks: list[str], tool_name: str, task_id: str, strict: bool) -> None:
        self.chunks = chunks
        self.tool_name = tool_name
        self.task_id = task_id
        self.strict = strict

    def write(self, item: Any) -> None:
        self.chunks.append(sse_event(item, self.tool_name, self.task_id, self.strict))


def start(config: AppConfig | None = None, **kwargs: Any) -> App:
    return App(config, **kwargs)


def create_app(config: AppConfig | None = None, **kwargs: Any) -> App:
    return start(config, **kwargs)


Start = start
CreateApp = create_app

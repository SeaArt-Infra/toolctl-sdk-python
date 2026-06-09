"""Main ToolApp implementation."""

from __future__ import annotations

import inspect
import json
import threading
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, StreamingResponse
import uvicorn

from sea_tools_server_sdk.errors import GatewayRegistrationError, ToolRegistrationError, ToolValidationError, UpstreamRequestError
from sea_tools_server_sdk.gateway import build_gateway_registration_payload, register_tools_to_gateway
from sea_tools_server_sdk.models import AuthConfig, GatewayRegistrationResult, ToolHandler, ToolManifest, ToolSpec
from sea_tools_server_sdk.monitoring import (
    MACHINE_STATUS_BUSY,
    MACHINE_STATUS_IDLE,
    MetricsPublisher,
    MonitoringConfig,
    PublishFn,
    ResourceMonitor,
)
from sea_tools_server_sdk.openapi import find_openapi_operation, load_openapi_spec
from sea_tools_server_sdk.protocol import (
    created,
    ensure_tool_event,
    failed,
    is_tool_event,
    normalize_json_result,
    protocol_response_schema,
)
from sea_tools_server_sdk.proxy import call_upstream_tool, stream_upstream_tool


class ToolApp:
    """HTTP app that exposes registered tools plus metadata endpoints."""

    def __init__(
        self,
        *,
        title: str,
        server_name: str | None = None,
        version: str = "0.1.0",
        description: str = "",
        base_path: str = "",
        docs_url: str = "/docs",
        openapi_url: str = "/openapi.json",
    ) -> None:
        self.title = title
        self.server_name = server_name or title
        self.version = version
        self.description = description
        self.base_path = base_path
        self._tools: dict[str, ToolSpec] = {}
        self.fastapi = FastAPI(
            title=title,
            version=version,
            description=description,
            docs_url=docs_url,
            openapi_url=openapi_url,
        )
        self.fastapi.openapi = self._build_openapi
        self._resource_monitor: ResourceMonitor | None = None
        self._active_tool_requests = 0
        self._active_tool_requests_lock = threading.Lock()
        self._install_base_routes()

    def _install_base_routes(self) -> None:
        @self.fastapi.get("/health", tags=["system"])
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @self.fastapi.get("/tools", tags=["system"])
        async def list_tools() -> dict[str, Any]:
            return {
                "server_name": self.server_name,
                "tools": [manifest.to_dict() for manifest in self.tools_manifest()],
            }

        @self.fastapi.get("/tool-manifest.json", tags=["system"])
        async def tool_manifest() -> dict[str, Any]:
            return self.server_manifest()

    def register_tool(
        self,
        *,
        name: str,
        description: str,
        request_schema: dict[str, Any],
        handler: ToolHandler,
        method: str = "POST",
        path: str | None = None,
        tags: list[str] | None = None,
        response_schema: dict[str, Any] | None = None,
        timeout_ms: int = 30000,
        auth: AuthConfig | dict[str, Any] | None = None,
        response_mode: str = "json",
        protocol_mode: str = "strict",
    ) -> ToolSpec:
        """Register a code-first tool."""

        if name in self._tools:
            raise ToolRegistrationError(f"Tool '{name}' is already registered.")
        if not (inspect.iscoroutinefunction(handler) or inspect.isasyncgenfunction(handler)):
            raise ToolRegistrationError("Tool handler must be async or async-generator based.")
        if response_mode not in {"json", "sse"}:
            raise ToolRegistrationError("response_mode must be either 'json' or 'sse'.")
        if protocol_mode not in {"strict", "passthrough"}:
            raise ToolRegistrationError("protocol_mode must be either 'strict' or 'passthrough'.")
        spec = ToolSpec(
            name=name,
            description=description,
            request_schema=request_schema,
            handler=handler,
            path=path or f"/tools/{name}",
            method=method.upper(),
            tags=tags or [],
            response_schema=response_schema,
            timeout_ms=timeout_ms,
            auth=self._coerce_auth(auth),
            response_mode=response_mode,
            protocol_mode=protocol_mode,
        )
        self._tools[name] = spec
        self._mount_tool_route(spec)
        return spec

    def register_proxy_tool(
        self,
        *,
        name: str,
        description: str,
        base_url: str,
        path: str,
        request_schema: dict[str, Any],
        method: str = "POST",
        upstream_path: str | None = None,
        tags: list[str] | None = None,
        headers: dict[str, str] | None = None,
        timeout_ms: int = 30000,
        auth: AuthConfig | dict[str, Any] | None = None,
        retry_count: int = 0,
        retry_delay_seconds: float = 0.0,
        verify_tls: bool = True,
        response_mode: str = "json",
        protocol_mode: str = "strict",
    ) -> ToolSpec:
        """Register a proxy tool backed by an upstream HTTP API."""

        async def handler(payload: dict[str, Any]) -> Any:
            if response_mode == "sse":
                return stream_upstream_tool(
                    base_url=base_url,
                    path=upstream_path or path,
                    method=method,
                    payload=payload,
                    headers=headers,
                    timeout_ms=timeout_ms,
                    auth=self._coerce_auth(auth),
                    retry_count=retry_count,
                    retry_delay_seconds=retry_delay_seconds,
                    verify_tls=verify_tls,
                )
            return await call_upstream_tool(
                base_url=base_url,
                path=upstream_path or path,
                method=method,
                payload=payload,
                headers=headers,
                timeout_ms=timeout_ms,
                auth=self._coerce_auth(auth),
                retry_count=retry_count,
                retry_delay_seconds=retry_delay_seconds,
                verify_tls=verify_tls,
            )

        spec = self.register_tool(
            name=name,
            description=description,
            request_schema=request_schema,
            handler=handler,
            method=method,
            path=path,
            tags=tags,
            timeout_ms=timeout_ms,
            auth=auth,
            response_mode=response_mode,
            protocol_mode=protocol_mode,
        )
        spec.headers.update(headers or {})
        spec.upstream_base_url = base_url
        spec.upstream_path = upstream_path or path
        spec.retry_count = retry_count
        spec.retry_delay_seconds = retry_delay_seconds
        spec.verify_tls = verify_tls
        return spec

    def register_tool_from_openapi(
        self,
        *,
        name: str,
        base_url: str,
        spec: dict[str, Any] | None = None,
        spec_path: str | None = None,
        spec_url: str | None = None,
        operation_id: str | None = None,
        path: str | None = None,
        method: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        headers: dict[str, str] | None = None,
        timeout_ms: int = 30000,
        verify_tls: bool = True,
        auth: AuthConfig | dict[str, Any] | None = None,
        retry_count: int = 0,
        retry_delay_seconds: float = 0.0,
        response_mode: str = "json",
        protocol_mode: str = "strict",
    ) -> ToolSpec:
        """Register a proxy tool from an OpenAPI operation."""

        openapi = load_openapi_spec(
            spec=spec,
            spec_path=spec_path,
            spec_url=spec_url,
            verify_tls=verify_tls,
        )
        matched_path, matched_method, operation = find_openapi_operation(
            spec=openapi,
            operation_id=operation_id,
            path=path,
            method=method,
        )
        request_schema = (
            operation.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {"type": "object", "properties": {}})
        )
        resolved_description = description or operation.get("description") or operation.get("summary") or name
        resolved_tags = tags or operation.get("tags") or []
        return self.register_proxy_tool(
            name=name,
            description=resolved_description,
            base_url=base_url,
            path=f"/tools/{name}",
            upstream_path=matched_path,
            method=matched_method,
            request_schema=request_schema,
            tags=resolved_tags,
            headers=headers,
            timeout_ms=timeout_ms,
            auth=auth,
            retry_count=retry_count,
            retry_delay_seconds=retry_delay_seconds,
            verify_tls=verify_tls,
            response_mode=response_mode,
            protocol_mode=protocol_mode,
        )

    def tool(
        self,
        *,
        name: str,
        description: str,
        request_schema: dict[str, Any],
        method: str = "POST",
        path: str | None = None,
        tags: list[str] | None = None,
        response_schema: dict[str, Any] | None = None,
        timeout_ms: int = 30000,
        auth: AuthConfig | dict[str, Any] | None = None,
        response_mode: str = "json",
        protocol_mode: str = "strict",
    ):
        """Decorator form of register_tool()."""

        def decorator(handler: ToolHandler) -> ToolHandler:
            self.register_tool(
                name=name,
                description=description,
                request_schema=request_schema,
                handler=handler,
                method=method,
                path=path,
                tags=tags,
                response_schema=response_schema,
                timeout_ms=timeout_ms,
                auth=auth,
                response_mode=response_mode,
                protocol_mode=protocol_mode,
            )
            return handler

        return decorator

    def register_sse_tool(
        self,
        *,
        name: str,
        description: str,
        request_schema: dict[str, Any],
        handler: ToolHandler,
        method: str = "POST",
        path: str | None = None,
        tags: list[str] | None = None,
        timeout_ms: int = 30000,
        auth: AuthConfig | dict[str, Any] | None = None,
        protocol_mode: str = "strict",
    ) -> ToolSpec:
        """Register a code-first SSE tool."""

        return self.register_tool(
            name=name,
            description=description,
            request_schema=request_schema,
            handler=handler,
            method=method,
            path=path,
            tags=tags,
            timeout_ms=timeout_ms,
            auth=auth,
            response_mode="sse",
            protocol_mode=protocol_mode,
        )

    def get_tool(self, name: str) -> ToolSpec | None:
        """Return a registered tool by name."""

        return self._tools.get(name)

    def tool_manifest(self, name: str) -> ToolManifest | None:
        """Return discovery metadata for one registered tool."""

        spec = self.get_tool(name)
        if spec is None:
            return None
        return spec.manifest(self.server_name)

    def tools_manifest(self) -> list[ToolManifest]:
        """Return discovery metadata for all tools, sorted by name."""

        return [self._tools[name].manifest(self.server_name) for name in sorted(self._tools)]

    def server_manifest(self) -> dict[str, Any]:
        """Return discovery metadata for this tool service."""

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

    def sse_tool(
        self,
        *,
        name: str,
        description: str,
        request_schema: dict[str, Any],
        method: str = "POST",
        path: str | None = None,
        tags: list[str] | None = None,
        timeout_ms: int = 30000,
        auth: AuthConfig | dict[str, Any] | None = None,
        protocol_mode: str = "strict",
    ):
        """Decorator form of register_sse_tool()."""

        def decorator(handler: ToolHandler) -> ToolHandler:
            self.register_sse_tool(
                name=name,
                description=description,
                request_schema=request_schema,
                handler=handler,
                method=method,
                path=path,
                tags=tags,
                timeout_ms=timeout_ms,
                auth=auth,
                protocol_mode=protocol_mode,
            )
            return handler

        return decorator

    def export_gateway_payloads(
        self,
        *,
        provider: str,
        base_url: str,
        version: str = "v1",
        category: str = "general",
        auth: dict[str, Any] | None = None,
        enabled: bool = True,
        owner_id: str | None = None,
        created_by: str | None = None,
        timeout_ms: int | None = None,
        tool_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Export agent-gateway registration payloads for selected tools."""

        names = set(tool_names) if tool_names else set(self._tools)
        return [
            build_gateway_registration_payload(
                spec=spec,
                provider=provider,
                base_url=base_url,
                version=version,
                category=category,
                auth=auth,
                enabled=enabled,
                owner_id=owner_id,
                created_by=created_by,
                timeout_ms=timeout_ms,
            )
            for spec in self._tools.values()
            if spec.name in names
        ]

    def run(self, *, host: str = "0.0.0.0", port: int = 8080) -> None:
        """Run the underlying HTTP service."""

        uvicorn.run(self.fastapi, host=host, port=port)

    def register_to_gateway(
        self,
        *,
        gateway_url: str,
        provider: str,
        base_url: str,
        version: str = "v1",
        category: str = "general",
        auth: AuthConfig | dict[str, Any] | None = None,
        enabled: bool = True,
        owner_id: str | None = None,
        created_by: str | None = None,
        timeout_ms: int | None = None,
        tool_names: list[str] | None = None,
        gateway_auth: AuthConfig | dict[str, Any] | None = None,
        verify_tls: bool = True,
        timeout_seconds: float = 30.0,
        retry_count: int = 0,
        retry_delay_seconds: float = 0.0,
    ) -> list[GatewayRegistrationResult]:
        """Export and submit registration payloads to agent-gateway."""

        payloads = self.export_gateway_payloads(
            provider=provider,
            base_url=base_url,
            version=version,
            category=category,
            auth=asdict(self._coerce_auth(auth)),
            enabled=enabled,
            owner_id=owner_id,
            created_by=created_by,
            timeout_ms=timeout_ms,
            tool_names=tool_names,
        )
        return register_tools_to_gateway(
            gateway_url=gateway_url,
            payloads=payloads,
            auth=self._coerce_auth(gateway_auth),
            verify_tls=verify_tls,
            timeout_seconds=timeout_seconds,
            retry_count=retry_count,
            retry_delay_seconds=retry_delay_seconds,
        )

    def enable_resource_monitoring(
        self,
        *,
        publisher: MetricsPublisher | None = None,
        publish: PublishFn | None = None,
        enabled: bool = True,
        interval_seconds: float = 5.0,
        instance_id: str | None = None,
        labels: dict[str, str] | None = None,
        publish_immediately: bool = False,
    ) -> ResourceMonitor:
        """Attach resource monitoring to the FastAPI app lifespan."""

        monitor_labels = dict(labels or {})
        monitor = ResourceMonitor(
            config=MonitoringConfig(
                service_name=self.title,
                enabled=enabled,
                interval_seconds=interval_seconds,
                instance_id=instance_id or f"{self.title}-{uuid4().hex}",
                labels=monitor_labels,
                publish_immediately=publish_immediately,
                status_provider=lambda: self._resource_monitor_status(monitor_labels),
            ),
            publisher=publisher,
            publish=publish,
        )
        self._resource_monitor = monitor

        @self.fastapi.on_event("startup")
        async def _start_resource_monitor() -> None:
            monitor.start()

        @self.fastapi.on_event("shutdown")
        async def _stop_resource_monitor() -> None:
            monitor.stop()

        return monitor

    def _mount_tool_route(self, spec: ToolSpec) -> None:
        async def endpoint(request: Request) -> Any:
            task_id = request.headers.get("x-tool-task-id") or request.headers.get("x-request-id") or f"task_{uuid4().hex}"
            payload = {}
            if spec.method == "GET":
                payload = dict(request.query_params)
            else:
                try:
                    payload = await request.json()
                except Exception:  # noqa: BLE001
                    raise HTTPException(status_code=400, detail="Invalid JSON request body.") from None
            if not isinstance(payload, dict):
                raise HTTPException(status_code=400, detail="Request body must be a JSON object.")
            streaming = False
            request_tracked = False
            try:
                self._validate_payload(spec.request_schema, payload)
                request_tracked = self._begin_tool_request()
                result = spec.handler(payload)
                if inspect.isawaitable(result):
                    result = await result
                if spec.response_mode == "sse":
                    streaming = True
                    event_stream = (
                        self._tracked_sse_event_stream(result, spec=spec, task_id=task_id)
                        if request_tracked
                        else self._sse_event_stream(result, spec=spec, task_id=task_id)
                    )
                    return StreamingResponse(
                        event_stream,
                        media_type="text/event-stream",
                    )
                if spec.protocol_mode == "passthrough":
                    return result
                return JSONResponse(normalize_json_result(result, tool_name=spec.name, task_id=task_id))
            except ToolValidationError as exc:
                if spec.protocol_mode == "passthrough":
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                return JSONResponse(
                    failed(
                        tool_name=spec.name,
                        task_id=task_id,
                        code="INVALID_INPUT",
                        message=str(exc),
                    )
                )
            except UpstreamRequestError as exc:
                if spec.protocol_mode == "passthrough":
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
                return JSONResponse(
                    failed(
                        tool_name=spec.name,
                        task_id=task_id,
                        code="UPSTREAM_REQUEST_FAILED",
                        message=str(exc),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                if spec.protocol_mode == "passthrough":
                    raise
                return JSONResponse(
                    failed(
                        tool_name=spec.name,
                        task_id=task_id,
                        code="INTERNAL_ERROR",
                        message=str(exc) or exc.__class__.__name__,
                    )
                )
            finally:
                if request_tracked and not streaming:
                    self._end_tool_request()

        self.fastapi.add_api_route(
            spec.path,
            endpoint,
            methods=[spec.method],
            name=spec.name,
            tags=spec.tags,
            summary=spec.description,
        )

    def _build_openapi(self) -> dict[str, Any]:
        if self.fastapi.openapi_schema:
            return self.fastapi.openapi_schema

        schema = get_openapi(
            title=self.title,
            version=self.version,
            description=self.description,
            routes=self.fastapi.routes,
        )
        schema.setdefault("info", {})["x-server-name"] = self.server_name
        for spec in self._tools.values():
            path_item = schema.setdefault("paths", {}).setdefault(spec.path, {})
            operation = path_item.setdefault(spec.method.lower(), {})
            operation["summary"] = spec.description
            operation["description"] = spec.description
            operation["operationId"] = spec.name
            operation["tags"] = spec.tags
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": spec.request_schema,
                    }
                },
            }
            response_schema = spec.response_schema or protocol_response_schema()
            operation["responses"] = {
                "200": {
                    "description": "Successful response",
                    "content": {
                        "application/json": {
                            "schema": response_schema,
                        }
                    },
                },
                "400": {"description": "Invalid request body"},
            }
        self.fastapi.openapi_schema = schema
        return schema

    @staticmethod
    def _validate_payload(schema: dict[str, Any], payload: dict[str, Any]) -> None:
        if schema.get("type") not in {None, "object"}:
            raise ToolValidationError("Only object payload schemas are supported.")
        required = schema.get("required", [])
        missing = [field for field in required if field not in payload]
        if missing:
            raise ToolValidationError(f"Missing required fields: {', '.join(missing)}")

    async def _sse_event_stream(self, result: Any, *, spec: ToolSpec, task_id: str):
        saw_done = False
        if spec.protocol_mode != "passthrough":
            yield self._serialize_tool_sse_event(created(tool_name=spec.name, task_id=task_id))
        if hasattr(result, "__aiter__"):
            async for item in result:
                formatted = self._format_sse_event(item, spec=spec, task_id=task_id)
                yield formatted
                saw_done = saw_done or self._is_done_marker(formatted)
            if not saw_done:
                yield "data: [DONE]\n\n"
            return
        if isinstance(result, (list, tuple)):
            for item in result:
                formatted = self._format_sse_event(item, spec=spec, task_id=task_id)
                yield formatted
                saw_done = saw_done or self._is_done_marker(formatted)
            if not saw_done:
                yield "data: [DONE]\n\n"
            return
        formatted = self._format_sse_event(result, spec=spec, task_id=task_id)
        yield formatted
        if not self._is_done_marker(formatted):
            yield "data: [DONE]\n\n"

    async def _tracked_sse_event_stream(self, result: Any, *, spec: ToolSpec, task_id: str):
        try:
            async for event in self._sse_event_stream(result, spec=spec, task_id=task_id):
                yield event
        finally:
            self._end_tool_request()

    def _begin_tool_request(self) -> bool:
        if self._resource_monitor is None or not self._resource_monitor.enabled:
            return False
        with self._active_tool_requests_lock:
            self._active_tool_requests += 1
        return True

    def _end_tool_request(self) -> None:
        with self._active_tool_requests_lock:
            self._active_tool_requests = max(0, self._active_tool_requests - 1)

    def _resource_monitor_status(self, labels: dict[str, str]) -> int:
        raw_status = labels.get("status")
        try:
            status = int(raw_status) if raw_status not in {None, ""} else MACHINE_STATUS_IDLE
        except (TypeError, ValueError):
            status = MACHINE_STATUS_IDLE
        if status != MACHINE_STATUS_IDLE:
            return status
        with self._active_tool_requests_lock:
            return MACHINE_STATUS_BUSY if self._active_tool_requests > 0 else MACHINE_STATUS_IDLE

    @staticmethod
    def _format_sse_event(item: Any, *, spec: ToolSpec, task_id: str) -> str:
        if isinstance(item, bytes):
            return item.decode("utf-8")
        if isinstance(item, str):
            if item.endswith("\n\n") or item.startswith("data:") or item.startswith("event:"):
                return item
            if spec.protocol_mode == "passthrough":
                return f"data: {item}\n\n"
            payload = normalize_json_result(item, tool_name=spec.name, task_id=task_id)
            return ToolApp._serialize_tool_sse_event(payload)
        if isinstance(item, dict):
            if spec.protocol_mode != "passthrough" and is_tool_event(item):
                payload = ensure_tool_event(item, tool_name=spec.name, task_id=task_id)
                return ToolApp._serialize_tool_sse_event(payload)
            if any(key in item for key in ("event", "id", "retry", "data")):
                parts: list[str] = []
                if "event" in item:
                    parts.append(f"event: {item['event']}")
                if "id" in item:
                    parts.append(f"id: {item['id']}")
                if "retry" in item:
                    parts.append(f"retry: {item['retry']}")
                data = item.get("data")
                if isinstance(data, (dict, list)):
                    data_value = json.dumps(data, ensure_ascii=False)
                else:
                    data_value = "" if data is None else str(data)
                for line in data_value.splitlines() or [""]:
                    parts.append(f"data: {line}")
                return "\n".join(parts) + "\n\n"
            if spec.protocol_mode == "passthrough":
                return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            payload = normalize_json_result(item, tool_name=spec.name, task_id=task_id)
            return ToolApp._serialize_tool_sse_event(payload)
        if spec.protocol_mode == "passthrough":
            return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        payload = normalize_json_result(item, tool_name=spec.name, task_id=task_id)
        return ToolApp._serialize_tool_sse_event(payload)

    @staticmethod
    def _serialize_tool_sse_event(payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @staticmethod
    def _is_done_marker(payload: str) -> bool:
        return payload.strip() == "data: [DONE]"

    @staticmethod
    def _coerce_auth(auth: AuthConfig | dict[str, Any] | None) -> AuthConfig:
        if isinstance(auth, AuthConfig):
            return auth
        if not auth:
            return AuthConfig()
        return AuthConfig(**auth)


def start(
    *,
    title: str,
    server_name: str | None = None,
    version: str = "0.1.0",
    description: str = "",
    base_path: str = "",
    docs_url: str = "/docs",
    openapi_url: str = "/openapi.json",
) -> ToolApp:
    """Convenient package-level constructor."""

    return ToolApp(
        title=title,
        server_name=server_name,
        version=version,
        description=description,
        base_path=base_path,
        docs_url=docs_url,
        openapi_url=openapi_url,
    )

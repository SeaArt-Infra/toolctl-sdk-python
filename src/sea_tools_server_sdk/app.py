"""Main ToolApp implementation."""

from __future__ import annotations

import inspect
import json
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import StreamingResponse
import uvicorn

from sea_tools_server_sdk.errors import GatewayRegistrationError, ToolRegistrationError, ToolValidationError, UpstreamRequestError
from sea_tools_server_sdk.gateway import build_gateway_registration_payload, register_tools_to_gateway
from sea_tools_server_sdk.models import AuthConfig, GatewayRegistrationResult, ToolHandler, ToolSpec
from sea_tools_server_sdk.openapi import find_openapi_operation, load_openapi_spec
from sea_tools_server_sdk.proxy import call_upstream_tool, stream_upstream_tool


class ToolApp:
    """HTTP app that exposes registered tools plus metadata endpoints."""

    def __init__(
        self,
        *,
        title: str,
        version: str = "0.1.0",
        description: str = "",
        base_path: str = "",
        docs_url: str = "/docs",
        openapi_url: str = "/openapi.json",
    ) -> None:
        self.title = title
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
        self._install_base_routes()

    def _install_base_routes(self) -> None:
        @self.fastapi.get("/health", tags=["system"])
        async def health() -> dict[str, str]:
            return {"status": "ok"}

        @self.fastapi.get("/tools", tags=["system"])
        async def list_tools() -> dict[str, list[dict[str, Any]]]:
            return {
                "tools": [
                    {
                        "name": spec.name,
                        "method": spec.method,
                        "path": spec.path,
                        "description": spec.description,
                        "tags": spec.tags,
                    }
                    for spec in self._tools.values()
                ]
            }

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
    ) -> ToolSpec:
        """Register a code-first tool."""

        if name in self._tools:
            raise ToolRegistrationError(f"Tool '{name}' is already registered.")
        if not (inspect.iscoroutinefunction(handler) or inspect.isasyncgenfunction(handler)):
            raise ToolRegistrationError("Tool handler must be async or async-generator based.")
        if response_mode not in {"json", "sse"}:
            raise ToolRegistrationError("response_mode must be either 'json' or 'sse'.")
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
        )

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

    def _mount_tool_route(self, spec: ToolSpec) -> None:
        async def endpoint(request: Request) -> Any:
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
            try:
                self._validate_payload(spec.request_schema, payload)
                result = spec.handler(payload)
                if inspect.isawaitable(result):
                    result = await result
                if spec.response_mode == "sse":
                    return StreamingResponse(
                        self._sse_event_stream(result),
                        media_type="text/event-stream",
                    )
                return result
            except ToolValidationError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            except UpstreamRequestError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

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
            response_schema = spec.response_schema or {"type": "object"}
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

    async def _sse_event_stream(self, result: Any):
        if hasattr(result, "__aiter__"):
            async for item in result:
                yield self._format_sse_event(item)
            return
        if isinstance(result, (list, tuple)):
            for item in result:
                yield self._format_sse_event(item)
            return
        yield self._format_sse_event(result)

    @staticmethod
    def _format_sse_event(item: Any) -> str:
        if isinstance(item, bytes):
            return item.decode("utf-8")
        if isinstance(item, str):
            if item.endswith("\n\n") or item.startswith("data:") or item.startswith("event:"):
                return item
            return f"data: {item}\n\n"
        if isinstance(item, dict):
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
            return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

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
    version: str = "0.1.0",
    description: str = "",
    base_path: str = "",
    docs_url: str = "/docs",
    openapi_url: str = "/openapi.json",
) -> ToolApp:
    """Convenient package-level constructor."""

    return ToolApp(
        title=title,
        version=version,
        description=description,
        base_path=base_path,
        docs_url=docs_url,
        openapi_url=openapi_url,
    )

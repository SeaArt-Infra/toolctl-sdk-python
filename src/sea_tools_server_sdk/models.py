"""Shared data models for the server SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin


ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(slots=True)
class AuthConfig:
    """Simple auth declaration used by proxy tools and gateway registration."""

    type: str = "none"
    token: str | None = None
    key: str | None = None
    header_name: str | None = None
    prefix: str = "Bearer"
    location: str = "header"
    headers: dict[str, str] = field(default_factory=dict)
    query_param: str | None = None


@dataclass(slots=True)
class GatewayRegistrationResult:
    """One gateway registration attempt result."""

    name: str
    status: int
    body: Any


@dataclass(slots=True)
class ToolSpec:
    """One tool exposed by the service."""

    name: str
    description: str
    request_schema: dict[str, Any]
    handler: ToolHandler
    path: str
    method: str = "POST"
    tags: list[str] = field(default_factory=list)
    response_schema: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout_ms: int = 30000
    upstream_base_url: str | None = None
    upstream_path: str | None = None
    auth: AuthConfig = field(default_factory=AuthConfig)
    retry_count: int = 0
    retry_delay_seconds: float = 0.0
    verify_tls: bool = True
    response_mode: str = "json"
    protocol_mode: str = "strict"

    @property
    def endpoint(self) -> str | None:
        """Resolve the upstream endpoint when this is a proxy tool."""

        if not self.upstream_base_url or not self.upstream_path:
            return None
        return urljoin(self.upstream_base_url.rstrip("/") + "/", self.upstream_path.lstrip("/"))

    @property
    def is_sse(self) -> bool:
        """Return whether this tool uses SSE responses."""

        return self.response_mode == "sse"

    def manifest(self, server_name: str) -> "ToolManifest":
        """Return the stable discovery metadata for this tool."""

        return ToolManifest(
            server_name=server_name,
            name=self.name,
            description=self.description,
            request_schema=dict(self.request_schema),
            response_schema=dict(self.response_schema) if self.response_schema is not None else None,
            method=self.method,
            path=self.path,
            tags=list(self.tags),
            timeout_ms=self.timeout_ms,
            response_mode=self.response_mode,
            is_sse=self.is_sse,
            protocol_mode=self.protocol_mode,
        )


@dataclass(slots=True)
class ToolManifest:
    """Stable metadata consumed by schedulers, skills, and agents."""

    server_name: str
    name: str
    description: str
    request_schema: dict[str, Any]
    method: str
    path: str
    timeout_ms: int
    response_mode: str
    is_sse: bool
    protocol_mode: str
    response_schema: dict[str, Any] | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "server_name": self.server_name,
            "name": self.name,
            "description": self.description,
            "request_schema": dict(self.request_schema),
            "method": self.method,
            "path": self.path,
            "timeout_ms": self.timeout_ms,
            "response_mode": self.response_mode,
            "is_sse": self.is_sse,
            "protocol_mode": self.protocol_mode,
        }
        if self.response_schema is not None:
            payload["response_schema"] = dict(self.response_schema)
        if self.tags:
            payload["tags"] = list(self.tags)
        return payload

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

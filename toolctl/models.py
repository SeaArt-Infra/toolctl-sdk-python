from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable, Mapping


HandlerFunc = Callable[[dict[str, Any]], Any]
StreamHandlerFunc = Callable[[dict[str, Any], Any], Any]


@dataclass(slots=True)
class AppConfig:
    title: str
    server_name: str = ""
    version: str = "0.1.0"
    description: str = ""
    base_path: str = ""


@dataclass(slots=True)
class ToolManifest:
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


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    request_schema: dict[str, Any]
    handler: HandlerFunc | None = None
    stream_handler: StreamHandlerFunc | None = None
    method: str = "POST"
    path: str = ""
    tags: list[str] = field(default_factory=list)
    response_schema: dict[str, Any] | None = None
    timeout_ms: int = 30000
    response_mode: str = "json"
    protocol_mode: str = "strict"

    @property
    def is_sse(self) -> bool:
        return self.response_mode == "sse"

    def manifest(self, server_name: str) -> ToolManifest:
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


def normalize_schema(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    if schema is None:
        return {"type": "object", "properties": {}}
    return dict(schema)


@dataclass(slots=True)
class EnableResourceMonitoringOptions:
    publish: Callable[[dict[str, Any]], Any] | None = None
    enabled: bool = True
    interval: float = 5.0
    instance_id: str = ""
    labels: dict[str, str] = field(default_factory=dict)
    publish_immediately: bool = False


@dataclass(slots=True)
class CreatedOptions:
    tool_name: str
    task_id: str
    created_at: int = 0
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class InProgressOptions:
    tool_name: str
    task_id: str
    progress: int | None = None
    message: str = ""
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class CompletedOptions:
    tool_name: str
    task_id: str
    outputs: list[Any] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class FailedOptions:
    tool_name: str
    task_id: str
    code: str
    message: str
    details: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None


@dataclass(slots=True)
class CancelledOptions:
    tool_name: str
    task_id: str
    reason: str = ""


def now_ms() -> int:
    return int(time.time() * 1000)

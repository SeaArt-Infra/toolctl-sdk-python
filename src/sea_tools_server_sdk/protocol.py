"""Tool response protocol helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any
from urllib.parse import quote
from uuid import uuid4


TOOL_TERMINAL_EVENTS = frozenset({"tool.completed", "tool.failed", "tool.cancelled"})
TOOL_EVENT_TYPES = TOOL_TERMINAL_EVENTS | frozenset({"tool.created", "tool.in_progress"})
_TOOL_EVENT_STATUS = {
    "tool.created": "pending",
    "tool.in_progress": "in_progress",
    "tool.completed": "completed",
    "tool.failed": "failed",
    "tool.cancelled": "cancelled",
}


@dataclass(slots=True)
class ToolOutput:
    """Unified protocol output item."""

    type: str
    url: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    sample_rate: int | None = None
    format: str | None = None
    filename: str | None = None
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResult:
    """Convenience wrapper for a completed tool result."""

    outputs: list[ToolOutput | dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


def new_task_id() -> str:
    """Generate a task id."""

    return f"task_{uuid4().hex}"


def text_output(content: str) -> dict[str, Any]:
    """Create a plain-text file output item."""

    return file_output(
        "file",
        f"data:text/plain;charset=utf-8,{quote(content)}",
        content_type="text/plain",
        filename="output.txt",
    )


def file_output(output_type: str, url: str, **kwargs: Any) -> dict[str, Any]:
    """Create a non-text output item."""

    return _clean_dict({"type": output_type, "url": url, **kwargs})


def created(*, tool_name: str, task_id: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a tool.created event."""

    tool = {
        "id": task_id,
        "name": tool_name,
        "status": "pending",
        "metadata": metadata,
    }
    return {"type": "tool.created", "tool": _clean_dict(tool)}


def in_progress(
    *,
    tool_name: str,
    task_id: str,
    progress: int | float | None = None,
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a tool.in_progress event."""

    tool = {
        "id": task_id,
        "name": tool_name,
        "status": "in_progress",
        "progress": progress,
        "message": message,
        "metadata": metadata,
    }
    return {"type": "tool.in_progress", "tool": _clean_dict(tool)}


def completed(
    *,
    tool_name: str,
    task_id: str,
    outputs: list[ToolOutput | dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a tool.completed event."""

    tool = {
        "id": task_id,
        "name": tool_name,
        "status": "completed",
        "outputs": [_normalize_output(output) for output in outputs or []],
        "usage": usage,
        "metadata": metadata,
    }
    return {"type": "tool.completed", "tool": _clean_dict(tool)}


def failed(
    *,
    tool_name: str,
    task_id: str,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a tool.failed event."""

    return {
        "type": "tool.failed",
        "tool": {
            "id": task_id,
            "name": tool_name,
            "status": "failed",
            "error": _clean_dict({"code": code, "message": message, "details": details}),
            "metadata": metadata,
            "usage": usage,
        },
    }


def cancelled(*, tool_name: str, task_id: str, reason: str | None = None) -> dict[str, Any]:
    """Create a tool.cancelled event."""

    return {
        "type": "tool.cancelled",
        "tool": _clean_dict({
            "id": task_id,
            "name": tool_name,
            "status": "cancelled",
            "reason": reason,
        }),
    }


def is_tool_event(payload: Any) -> bool:
    """Return whether the payload is already a tool protocol event."""

    return isinstance(payload, dict) and payload.get("type") in TOOL_EVENT_TYPES and isinstance(payload.get("tool"), dict)


def ensure_tool_event(payload: dict[str, Any], *, tool_name: str, task_id: str) -> dict[str, Any]:
    """Fill missing protocol fields on an event."""

    event_type = payload["type"]
    tool = dict(payload.get("tool") or {})
    tool.setdefault("id", task_id)
    tool.setdefault("name", tool_name)
    tool["status"] = _TOOL_EVENT_STATUS.get(event_type, tool.get("status"))
    if event_type == "tool.completed":
        tool["outputs"] = [_normalize_output(output) for output in tool.get("outputs") or []]
    return {**payload, "tool": tool}


def normalize_json_result(result: Any, *, tool_name: str, task_id: str) -> dict[str, Any]:
    """Normalize a handler result into a JSON protocol event."""

    if is_tool_event(result):
        return ensure_tool_event(result, tool_name=tool_name, task_id=task_id)

    if isinstance(result, ToolResult):
        return completed(
            tool_name=tool_name,
            task_id=task_id,
            outputs=result.outputs,
            usage=result.usage,
            metadata=result.metadata,
        )

    if isinstance(result, str):
        return completed(tool_name=tool_name, task_id=task_id, outputs=[], metadata={"result": result})

    if isinstance(result, dict):
        if "outputs" in result:
            metadata = dict(result.get("metadata") or {})
            extra = {key: value for key, value in result.items() if key not in {"outputs", "usage", "metadata"}}
            if extra:
                metadata["result"] = extra
            return completed(
                tool_name=tool_name,
                task_id=task_id,
                outputs=result.get("outputs") or [],
                usage=result.get("usage"),
                metadata=metadata or None,
            )
        return completed(
            tool_name=tool_name,
            task_id=task_id,
            outputs=[],
            metadata={"result": result},
        )

    if isinstance(result, (list, tuple)):
        payload = list(result)
        return completed(
            tool_name=tool_name,
            task_id=task_id,
            outputs=[],
            metadata={"result": payload},
        )

    return completed(
        tool_name=tool_name,
        task_id=task_id,
        outputs=[],
        metadata={"result": result},
    )


def protocol_response_schema() -> dict[str, Any]:
    """Return the generic response schema used by the SDK."""

    return {
        "type": "object",
        "required": ["type", "tool"],
        "properties": {
            "type": {"type": "string", "enum": sorted(TOOL_EVENT_TYPES)},
            "tool": {
                "type": "object",
                "required": ["id", "name", "status"],
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "status": {"type": "string"},
                    "progress": {"type": "number"},
                    "message": {"type": "string"},
                    "outputs": {"type": "array", "items": {"type": "object"}},
                    "usage": {"type": "object"},
                    "metadata": {"type": "object"},
                    "error": {"type": "object"},
                },
            },
        },
    }


def _normalize_output(output: ToolOutput | dict[str, Any]) -> dict[str, Any]:
    if isinstance(output, ToolOutput):
        output = asdict(output)
    if is_dataclass(output):
        output = asdict(output)
    if isinstance(output, dict):
        normalized = dict(output)
        if normalized.get("type") == "text":
            content = normalized.pop("content", "")
            normalized.pop("type", None)
            return file_output(
                "file",
                f"data:text/plain;charset=utf-8,{quote(content)}",
                content_type=normalized.pop("content_type", None) or "text/plain",
                filename=normalized.pop("filename", None) or "output.txt",
                **normalized,
            )
        return _clean_dict(normalized)
    raise TypeError(f"Unsupported output type: {type(output)!r}")


def _clean_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}

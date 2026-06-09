from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .models import CancelledOptions, CompletedOptions, CreatedOptions, FailedOptions, InProgressOptions


TOOL_EVENT_TYPES = {
    "tool.created",
    "tool.in_progress",
    "tool.completed",
    "tool.failed",
    "tool.cancelled",
}


def new_task_id() -> str:
    return f"task_{uuid.uuid4().hex}"


def text_output(content: str) -> dict[str, Any]:
    return {
        "type": "file",
        "url": "data:text/plain;charset=utf-8," + content,
        "content_type": "text/plain",
        "filename": "output.txt",
    }


def file_output(output_type: str, resource_url: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"type": output_type, "url": resource_url}
    for key, value in (extra or {}).items():
        if value is not None:
            payload[key] = value
    return payload


def created(opts: CreatedOptions) -> dict[str, Any]:
    return {
        "type": "tool.created",
        "tool": _clean(
            {
                "id": opts.task_id,
                "name": opts.tool_name,
                "status": "pending",
                "created_at": opts.created_at or int(time.time() * 1000),
                "metadata": opts.metadata,
            }
        ),
    }


def in_progress(opts: InProgressOptions) -> dict[str, Any]:
    return {
        "type": "tool.in_progress",
        "tool": _clean(
            {
                "id": opts.task_id,
                "name": opts.tool_name,
                "status": "in_progress",
                "progress": opts.progress,
                "message": opts.message or None,
                "metadata": opts.metadata,
            }
        ),
    }


def completed(opts: CompletedOptions) -> dict[str, Any]:
    return {
        "type": "tool.completed",
        "tool": _clean(
            {
                "id": opts.task_id,
                "name": opts.tool_name,
                "status": "completed",
                "outputs": opts.outputs,
                "usage": opts.usage,
                "metadata": opts.metadata,
            }
        ),
    }


def failed(opts: FailedOptions) -> dict[str, Any]:
    return {
        "type": "tool.failed",
        "tool": _clean(
            {
                "id": opts.task_id,
                "name": opts.tool_name,
                "status": "failed",
                "error": _clean({"code": opts.code, "message": opts.message, "details": opts.details}),
                "metadata": opts.metadata,
                "usage": opts.usage,
            }
        ),
    }


def cancelled(opts: CancelledOptions) -> dict[str, Any]:
    return {
        "type": "tool.cancelled",
        "tool": _clean(
            {
                "id": opts.task_id,
                "name": opts.tool_name,
                "status": "cancelled",
                "reason": opts.reason or None,
            }
        ),
    }


def normalize_json_result(result: Any, tool_name: str, task_id: str) -> dict[str, Any]:
    if isinstance(result, dict) and result.get("type") in TOOL_EVENT_TYPES and isinstance(result.get("tool"), dict):
        payload = dict(result)
        tool = dict(payload["tool"])
        tool.setdefault("id", task_id)
        tool.setdefault("name", tool_name)
        payload["tool"] = tool
        return payload
    if result is None:
        return completed(CompletedOptions(tool_name=tool_name, task_id=task_id, outputs=[], metadata={"result": None}))
    if isinstance(result, dict) and "outputs" in result:
        metadata = dict(result.get("metadata") or {})
        extra = {key: value for key, value in result.items() if key not in {"outputs", "usage", "metadata"}}
        if extra:
            metadata["result"] = extra
        return completed(
            CompletedOptions(
                tool_name=tool_name,
                task_id=task_id,
                outputs=list(result.get("outputs") or []),
                usage=result.get("usage"),
                metadata=metadata or None,
            )
        )
    return completed(CompletedOptions(tool_name=tool_name, task_id=task_id, outputs=[], metadata={"result": result}))


def sse_event(payload: Any, tool_name: str, task_id: str, strict: bool = True) -> str:
    if isinstance(payload, str):
        if payload.endswith("\n\n") or payload.startswith("data:") or payload.startswith("event:"):
            return payload
        if not strict:
            return f"data: {payload}\n\n"
    if strict:
        payload = normalize_json_result(payload, tool_name, task_id)
    return "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}

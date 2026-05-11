"""Public package entrypoints for the server SDK."""

from sea_tools_server_sdk.app import ToolApp, start
from sea_tools_server_sdk.errors import (
    GatewayRegistrationError,
    OpenAPIImportError,
    ToolRegistrationError,
    ToolValidationError,
    UpstreamHTTPError,
    UpstreamNetworkError,
    UpstreamRequestError,
    UpstreamTimeoutError,
)
from sea_tools_server_sdk.models import AuthConfig, GatewayRegistrationResult, ToolSpec
from sea_tools_server_sdk.protocol import (
    TOOL_EVENT_TYPES,
    TOOL_TERMINAL_EVENTS,
    ToolOutput,
    ToolResult,
    cancelled,
    completed,
    created,
    failed,
    file_output,
    in_progress,
    is_tool_event,
    new_task_id,
    protocol_response_schema,
    text_output,
)


def create_app(**kwargs) -> ToolApp:
    """Create a ToolApp instance."""

    return start(**kwargs)


class _ToolctlNamespace:
    """Simple namespace so callers can use toolctl.start(...)."""

    @staticmethod
    def start(**kwargs) -> ToolApp:
        return start(**kwargs)

    @staticmethod
    def create_app(**kwargs) -> ToolApp:
        return create_app(**kwargs)


toolctl = _ToolctlNamespace()

__all__ = [
    "AuthConfig",
    "GatewayRegistrationError",
    "GatewayRegistrationResult",
    "OpenAPIImportError",
    "ToolApp",
    "ToolOutput",
    "ToolResult",
    "ToolRegistrationError",
    "ToolSpec",
    "ToolValidationError",
    "TOOL_EVENT_TYPES",
    "TOOL_TERMINAL_EVENTS",
    "UpstreamHTTPError",
    "UpstreamNetworkError",
    "UpstreamRequestError",
    "UpstreamTimeoutError",
    "cancelled",
    "completed",
    "created",
    "failed",
    "file_output",
    "in_progress",
    "is_tool_event",
    "new_task_id",
    "protocol_response_schema",
    "text_output",
    "create_app",
    "start",
    "toolctl",
]

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
    "ToolRegistrationError",
    "ToolSpec",
    "ToolValidationError",
    "UpstreamHTTPError",
    "UpstreamNetworkError",
    "UpstreamRequestError",
    "UpstreamTimeoutError",
    "create_app",
    "start",
    "toolctl",
]

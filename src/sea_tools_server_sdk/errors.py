"""Server SDK errors."""

from __future__ import annotations


class SeaToolsServerSDKError(Exception):
    """Base error for the server SDK."""


class ToolRegistrationError(SeaToolsServerSDKError):
    """Raised when a tool cannot be registered."""


class ToolValidationError(SeaToolsServerSDKError):
    """Raised when a request body does not satisfy a tool schema."""


class OpenAPIImportError(SeaToolsServerSDKError):
    """Raised when an OpenAPI document cannot be imported."""


class UpstreamRequestError(SeaToolsServerSDKError):
    """Raised when a proxied upstream request fails."""


class UpstreamHTTPError(UpstreamRequestError):
    """Raised when the upstream service returns a non-success status."""


class UpstreamNetworkError(UpstreamRequestError):
    """Raised when the upstream service cannot be reached."""


class UpstreamTimeoutError(UpstreamRequestError):
    """Raised when the upstream service times out."""


class GatewayRegistrationError(SeaToolsServerSDKError):
    """Raised when registering tools into agent-gateway fails."""

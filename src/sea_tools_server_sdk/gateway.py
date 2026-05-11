"""Helpers for exporting agent-gateway registration payloads."""

from __future__ import annotations

from typing import Any
import time
from urllib.parse import urljoin

import httpx

from sea_tools_server_sdk.errors import GatewayRegistrationError
from sea_tools_server_sdk.models import AuthConfig, GatewayRegistrationResult
from sea_tools_server_sdk.models import ToolSpec


def build_gateway_registration_payload(
    *,
    spec: ToolSpec,
    provider: str,
    base_url: str,
    version: str = "v1",
    category: str = "general",
    auth: dict[str, Any] | None = None,
    enabled: bool = True,
    owner_id: str | None = None,
    created_by: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    """Build a `/v1/tools/register` payload for one tool."""

    endpoint = urljoin(base_url.rstrip("/") + "/", spec.path.lstrip("/"))
    resolved_owner = owner_id or provider
    resolved_creator = created_by or provider
    resolved_timeout = timeout_ms if timeout_ms is not None else spec.timeout_ms
    return {
        "id": f"{provider}:{spec.name}:{version}",
        "provider": provider,
        "name": spec.name,
        "version": version,
        "category": category,
        "transport": "http",
        "description": spec.description,
        "endpoint": endpoint,
        "method": spec.method,
        "parameters": spec.request_schema,
        "auth": auth or {"type": "none"},
        "config": {"timeout_ms": resolved_timeout},
        "tags": spec.tags,
        "enabled": enabled,
        "owner_id": resolved_owner,
        "created_by": resolved_creator,
    }


def register_tools_to_gateway(
    *,
    gateway_url: str,
    payloads: list[dict[str, Any]],
    auth: AuthConfig | None = None,
    verify_tls: bool = True,
    timeout_seconds: float = 30.0,
    retry_count: int = 0,
    retry_delay_seconds: float = 0.0,
) -> list[GatewayRegistrationResult]:
    """Submit one or more registration payloads to agent-gateway."""

    resolved_auth = auth or AuthConfig()
    headers = _apply_auth_headers({"Content-Type": "application/json"}, resolved_auth)
    params = _auth_query_params(resolved_auth)
    timeout = httpx.Timeout(timeout_seconds)
    results: list[GatewayRegistrationResult] = []
    with httpx.Client(verify=verify_tls, timeout=timeout, follow_redirects=True) as client:
        for payload in payloads:
            attempts = retry_count + 1
            last_error: Exception | None = None
            for attempt in range(attempts):
                try:
                    response = client.post(gateway_url, json=payload, headers=headers, params=params)
                    response.raise_for_status()
                    try:
                        body = response.json()
                    except ValueError:
                        body = response.text
                    results.append(GatewayRegistrationResult(name=payload["name"], status=response.status_code, body=body))
                    break
                except httpx.HTTPStatusError as exc:
                    try:
                        body = exc.response.json()
                    except ValueError:
                        body = exc.response.text
                    raise GatewayRegistrationError(
                        f"Gateway registration failed for {payload['name']}: HTTP {exc.response.status_code} {body}"
                    ) from exc
                except httpx.TimeoutException as exc:
                    last_error = GatewayRegistrationError(f"Gateway registration timed out for {payload['name']}: {exc}")
                except httpx.HTTPError as exc:
                    last_error = GatewayRegistrationError(f"Gateway registration network failure for {payload['name']}: {exc}")
                if attempt < attempts - 1 and retry_delay_seconds > 0:
                    time.sleep(retry_delay_seconds)
            else:
                assert last_error is not None
                raise last_error
    return results


def _apply_auth_headers(headers: dict[str, str], auth: AuthConfig) -> dict[str, str]:
    merged = dict(headers)
    if auth.type == "bearer" and auth.token:
        merged[auth.header_name or "Authorization"] = f"{auth.prefix} {auth.token}"
    elif auth.type == "api_key" and auth.location == "header" and auth.key:
        merged[auth.header_name or "X-API-Key"] = auth.key
    elif auth.type in {"headers", "custom"}:
        merged.update(auth.headers)
    return merged


def _auth_query_params(auth: AuthConfig) -> dict[str, str]:
    if auth.type == "api_key" and auth.location == "query" and auth.key:
        return {auth.query_param or "api_key": auth.key}
    return {}

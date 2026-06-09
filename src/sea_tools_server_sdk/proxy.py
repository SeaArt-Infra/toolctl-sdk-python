"""Proxy tool helpers."""

from __future__ import annotations

import asyncio
from typing import Any
import time

import httpx

from sea_tools_server_sdk.errors import UpstreamHTTPError, UpstreamNetworkError, UpstreamTimeoutError
from sea_tools_server_sdk.models import AuthConfig


async def call_upstream_tool(
    *,
    base_url: str,
    path: str,
    method: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout_ms: int = 30000,
    auth: AuthConfig | None = None,
    retry_count: int = 0,
    retry_delay_seconds: float = 0.0,
    verify_tls: bool = True,
) -> Any:
    """Forward the request to an upstream HTTP API."""

    resolved_auth = auth or AuthConfig()
    request_headers = _apply_auth_headers(headers or {}, resolved_auth)
    request_params = _auth_query_params(resolved_auth)
    timeout = httpx.Timeout(timeout_ms / 1000.0)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, follow_redirects=True, verify=verify_tls) as client:
        attempts = retry_count + 1
        for attempt in range(attempts):
            try:
                if method.upper() == "GET":
                    merged_params = dict(request_params)
                    merged_params.update(payload)
                    response = await client.request(method.upper(), path, params=merged_params, headers=request_headers)
                else:
                    response = await client.request(method.upper(), path, json=payload, params=request_params, headers=request_headers)
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                raise UpstreamHTTPError(f"Upstream request failed: HTTP {exc.response.status_code} {exc.response.text}") from exc
            except httpx.TimeoutException as exc:
                if attempt == attempts - 1:
                    raise UpstreamTimeoutError(f"Upstream request timed out: {exc}") from exc
            except httpx.HTTPError as exc:
                if attempt == attempts - 1:
                    raise UpstreamNetworkError(f"Upstream request failed: {exc}") from exc
            if attempt < attempts - 1 and retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds)

    content_type = response.headers.get("Content-Type", "")
    if "application/json" in content_type:
        return response.json()
    return response.text


async def stream_upstream_tool(
    *,
    base_url: str,
    path: str,
    method: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout_ms: int = 30000,
    auth: AuthConfig | None = None,
    retry_count: int = 0,
    retry_delay_seconds: float = 0.0,
    verify_tls: bool = True,
):
    """Forward the request to an upstream SSE endpoint and yield raw event chunks."""

    resolved_auth = auth or AuthConfig()
    request_headers = _apply_auth_headers(headers or {}, resolved_auth)
    request_params = _auth_query_params(resolved_auth)
    request_headers.setdefault("Accept", "text/event-stream")
    timeout = httpx.Timeout(timeout_ms / 1000.0)
    attempts = retry_count + 1

    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=timeout, follow_redirects=True, verify=verify_tls) as client:
                if method.upper() == "GET":
                    merged_params = dict(request_params)
                    merged_params.update(payload)
                    async with client.stream(method.upper(), path, params=merged_params, headers=request_headers) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_text():
                            if chunk:
                                yield chunk
                else:
                    async with client.stream(method.upper(), path, json=payload, params=request_params, headers=request_headers) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_text():
                            if chunk:
                                yield chunk
                return
        except httpx.HTTPStatusError as exc:
            raise UpstreamHTTPError(f"Upstream request failed: HTTP {exc.response.status_code} {exc.response.text}") from exc
        except httpx.TimeoutException as exc:
            if attempt == attempts - 1:
                raise UpstreamTimeoutError(f"Upstream request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            if attempt == attempts - 1:
                raise UpstreamNetworkError(f"Upstream request failed: {exc}") from exc
        if attempt < attempts - 1 and retry_delay_seconds > 0:
            await asyncio.sleep(retry_delay_seconds)


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

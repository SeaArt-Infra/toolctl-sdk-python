"""Vault client for fetching dynamic credentials via VAULT_URL / VAULT_TOKEN."""

from __future__ import annotations

import base64
import json
import logging
import os
import ssl
import urllib.request
import urllib.error
from typing import Any

_logger = logging.getLogger(__name__)


class VaultError(Exception):
    """Raised when vault credential retrieval fails."""


def _vault_request(url: str, token: str) -> dict[str, Any]:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers={"X-Vault-Token": token})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise VaultError(f"vault request failed [{exc.code}]: {url}") from exc
    except OSError as exc:
        raise VaultError(f"vault connection error: {exc}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise VaultError(f"vault response is not valid JSON: {exc}") from exc


def _renew_token(vault_url: str, token: str) -> None:
    renew_url = vault_url.rstrip("/") + "/v1/auth/token/renew-self"
    req = urllib.request.Request(
        renew_url,
        data=b"{}",
        headers={"X-Vault-Token": token, "Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30):
            pass
    except urllib.error.HTTPError as exc:
        _logger.warning("vault token renew skipped [%d]: token may not have renew permission", exc.code)
    except OSError as exc:
        _logger.warning("vault token renew skipped: %s", exc)


def _secret_url(vault_url: str, key_path: str) -> str:
    """Build the secret fetch URL, mirroring tool-scheduler's SecretURL logic.

    If VAULT_URL already contains a path beyond scheme://host, use it directly.
    Otherwise join VAULT_URL with key_path from config.
    """
    from urllib.parse import urlparse

    parsed = urlparse(vault_url.rstrip("/"))
    if parsed.path and parsed.path.strip("/"):
        return vault_url.rstrip("/")
    return vault_url.rstrip("/") + "/" + key_path.lstrip("/")


def get_credentials_json(key_path: str, renew_path: str | None = None) -> str:
    """Fetch credentials_json from Vault using VAULT_URL and VAULT_TOKEN env vars.

    Returns the raw JSON string (or base64-encoded JSON) suitable for passing
    to PubSubMetricsPublisher as credentials_json.

    Raises VaultError if env vars are missing or the request fails.
    """
    vault_url = os.environ.get("VAULT_URL", "").strip()
    vault_token = os.environ.get("VAULT_TOKEN", "").strip()
    if not vault_url or not vault_token:
        raise VaultError("VAULT_URL and VAULT_TOKEN environment variables are required")

    if renew_path:
        _renew_token(vault_url, vault_token)

    url = _secret_url(vault_url, key_path)
    data = _vault_request(url, vault_token)

    credentials_json: str | None = None
    try:
        credentials_json = data["data"]["data"]["credentials_json"]
    except (KeyError, TypeError):
        pass

    if credentials_json is None:
        raise VaultError("credentials_json field not found in vault response")

    return _normalize(credentials_json)


def _normalize(value: str) -> str:
    """Return base64-encoded JSON; accept both raw JSON and already-base64 input."""
    trimmed = value.strip()
    if trimmed.startswith("{") and json.dumps(json.loads(trimmed)):
        return base64.b64encode(trimmed.encode()).decode()
    return value


def project_id_from_credentials_json(value: str) -> str:
    """Extract project_id from a credentials_json string (raw JSON or base64)."""
    payload = value.strip()
    if not payload.startswith("{"):
        try:
            payload = base64.b64decode(payload).decode()
        except Exception:
            return ""
    try:
        return json.loads(payload).get("project_id", "")
    except json.JSONDecodeError:
        return ""

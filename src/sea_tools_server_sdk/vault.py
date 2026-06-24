"""Credential file helpers for loading service account JSON by path."""

from __future__ import annotations

import base64
import json
from pathlib import Path


class VaultError(Exception):
    """Raised when credential retrieval fails."""


def get_credentials_json(key_path: str, renew_path: str | None = None) -> str:
    """Read credentials JSON directly from ``key_path``.

    ``renew_path`` is accepted for backward-compatible call signatures but is
    ignored because credentials are no longer fetched from Vault.

    Returns a base64-encoded JSON string suitable for passing to
    PubSubMetricsPublisher as credentials_json.
    """
    del renew_path

    if not key_path or not key_path.strip():
        raise VaultError("credential file path is required")

    path = Path(key_path).expanduser()
    try:
        credentials_json = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise VaultError(f"credential file read failed: {path}") from exc

    return _normalize(credentials_json)


def _normalize(value: str) -> str:
    """Return base64-encoded JSON; accept both raw JSON and already-base64 input."""
    trimmed = value.strip()
    if trimmed.startswith("{"):
        try:
            json.loads(trimmed)
        except json.JSONDecodeError as exc:
            raise VaultError(f"credential file is not valid JSON: {exc}") from exc
        return base64.b64encode(trimmed.encode()).decode()

    try:
        decoded = base64.b64decode(trimmed, validate=True).decode()
        json.loads(decoded)
    except Exception as exc:
        raise VaultError("credential file is not valid JSON or base64-encoded JSON") from exc
    return trimmed


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

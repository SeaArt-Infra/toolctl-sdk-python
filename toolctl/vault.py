from __future__ import annotations

import base64
import json
import os
import urllib.request
from typing import Any


class VaultError(RuntimeError):
    pass


def get_credentials_json(key_path: str, renew_path: str = "") -> str:
    vault_url = os.getenv("VAULT_URL", "").strip()
    vault_token = os.getenv("VAULT_TOKEN", "").strip()
    if not vault_url or not vault_token:
        raise VaultError("VAULT_URL and VAULT_TOKEN environment variables are required")
    if renew_path:
        _renew_vault_token(vault_url, vault_token)
    data = _vault_request(_secret_url(vault_url, key_path), vault_token)
    cred_json = _nested(data, "data", "data", "credentials_json")
    if not isinstance(cred_json, str) or not cred_json:
        raise VaultError("credentials_json field not found in vault response")
    return _normalize_credentials_json(cred_json)


def project_id_from_credentials_json(value: str) -> str:
    payload = value.strip()
    if not payload.startswith("{"):
        try:
            payload = base64.b64decode(payload).decode()
        except Exception:
            return ""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return ""
    project_id = data.get("project_id")
    return project_id if isinstance(project_id, str) else ""


def _vault_request(raw_url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(raw_url, headers={"X-Vault-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except Exception as exc:
        raise VaultError(f"vault request failed: {exc}") from exc


def _renew_vault_token(vault_url: str, token: str) -> None:
    request = urllib.request.Request(
        vault_url.rstrip("/") + "/v1/auth/token/renew-self",
        data=b"{}",
        headers={"X-Vault-Token": token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=30).close()
    except Exception:
        return


def _secret_url(vault_url: str, key_path: str) -> str:
    trimmed = vault_url.rstrip("/")
    path = urllib.request.urlparse(trimmed).path.strip("/")
    if path:
        return trimmed
    return trimmed + "/" + key_path.lstrip("/")


def _normalize_credentials_json(value: str) -> str:
    trimmed = value.strip()
    if trimmed.startswith("{"):
        return base64.b64encode(trimmed.encode()).decode()
    return trimmed


def _nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current

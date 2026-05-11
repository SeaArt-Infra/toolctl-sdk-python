"""OpenAPI loading and extraction helpers."""

from __future__ import annotations

import json
import ssl
from pathlib import Path
from typing import Any
from urllib import request

from sea_tools_server_sdk.errors import OpenAPIImportError


def load_openapi_spec(
    *,
    spec: dict[str, Any] | None = None,
    spec_path: str | Path | None = None,
    spec_url: str | None = None,
    verify_tls: bool = True,
) -> dict[str, Any]:
    """Load an OpenAPI spec from memory, disk, or URL."""

    provided = [spec is not None, spec_path is not None, spec_url is not None]
    if sum(provided) != 1:
        raise OpenAPIImportError("Provide exactly one of spec, spec_path, or spec_url.")

    if spec is not None:
        return spec
    if spec_path is not None:
        path = Path(spec_path).expanduser().resolve()
        return json.loads(path.read_text(encoding="utf-8"))

    context = None if verify_tls else ssl._create_unverified_context()
    with request.urlopen(spec_url, timeout=30, context=context) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def find_openapi_operation(
    *,
    spec: dict[str, Any],
    operation_id: str | None = None,
    path: str | None = None,
    method: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Locate one operation inside an OpenAPI spec."""

    normalized_method = method.lower() if method else None
    for candidate_path, path_item in spec.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for candidate_method, operation in path_item.items():
            if candidate_method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if operation_id and operation.get("operationId") == operation_id:
                return candidate_path, candidate_method.upper(), operation
            if path and normalized_method and candidate_path == path and candidate_method.lower() == normalized_method:
                return candidate_path, candidate_method.upper(), operation
    raise OpenAPIImportError("Could not find a matching operation in the OpenAPI spec.")

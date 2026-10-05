"""
API — HTTP response helpers.

All Lambda handlers return API Gateway proxy response dicts.  This module
provides a small set of helpers that keep every handler consistent and
reduce the risk of accidentally omitting required keys.

Every response:
- sets ``Content-Type: application/json``
- serialises the body with ``json.dumps``
- uses the error envelope ``{"error": {"code": ..., "message": ...}}``
  for all non-2xx responses

No HTTP status codes, AWS details, stack traces, or credentials are
included in error bodies returned to the client.
"""

from __future__ import annotations

import json
from typing import Any

# API Gateway proxy response keys
_HEADERS = {"Content-Type": "application/json"}


def _proxy(status_code: int, body: Any) -> dict[str, Any]:
    """Build a minimal API Gateway Lambda proxy response."""
    return {
        "statusCode": status_code,
        "headers": _HEADERS,
        "body": json.dumps(body),
    }


# ── Success responses ─────────────────────────────────────────────────────────


def created(body: Any) -> dict[str, Any]:
    """Return HTTP 201 Created with a JSON body."""
    return _proxy(201, body)


def ok(body: Any) -> dict[str, Any]:
    """Return HTTP 200 OK with a JSON body."""
    return _proxy(200, body)


# ── Error responses ───────────────────────────────────────────────────────────


def _error_body(code: str, message: str) -> dict[str, Any]:
    """Return the standard error envelope used across all endpoints."""
    return {"error": {"code": code, "message": message}}


def bad_request(code: str, message: str) -> dict[str, Any]:
    """Return HTTP 400 Bad Request."""
    return _proxy(400, _error_body(code, message))


def unauthorized(message: str = "Authentication is required.") -> dict[str, Any]:
    """Return HTTP 401 Unauthorized."""
    return _proxy(401, _error_body("UNAUTHORIZED", message))


def forbidden(
    message: str = "You do not have permission to perform this action.",
) -> dict[str, Any]:
    """Return HTTP 403 Forbidden."""
    return _proxy(403, _error_body("FORBIDDEN", message))


def not_found(code: str, message: str) -> dict[str, Any]:
    """Return HTTP 404 Not Found."""
    return _proxy(404, _error_body(code, message))


def internal_error(message: str = "An internal error occurred.") -> dict[str, Any]:
    """Return HTTP 500 Internal Server Error.

    The message must never include stack traces, AWS details, or credentials.
    """
    return _proxy(500, _error_body("INTERNAL_ERROR", message))

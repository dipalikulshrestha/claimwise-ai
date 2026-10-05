"""
API handler — POST /v1/claims (Create Claim).

This module is the Lambda entry point for claim creation.  It is
intentionally thin: it parses the API Gateway event, extracts the
authenticated identity, delegates all business logic to ``ClaimService``,
and maps the result or any exception to an HTTP response.

No domain logic, DynamoDB calls, or boto3 usage lives here.

API Gateway authorizer context (Cognito User Pool Authorizer, REST API):
    event["requestContext"]["authorizer"]["claims"]["sub"]

The handler is safe to call with a plain dict in tests — no Lambda runtime
or real AWS resources are required.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, cast

from app.api.responses import bad_request, created, internal_error, unauthorized
from app.domain.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.infrastructure.config import load_config
from app.infrastructure.dynamodb import ClaimRepository, EvidenceRepository
from app.services.claim_service import ClaimService

logger = logging.getLogger(__name__)

# ── Module-level service instance ─────────────────────────────────────────────
# Initialised once at Lambda cold-start; reused across warm invocations.
# This avoids re-creating boto3 clients on every request while keeping the
# handler body free of infrastructure concerns.
#
# The ``_service`` name is intentionally underscore-prefixed so tests can
# replace it without accessing a public module-level name.
#
# Tests that want full isolation should patch ``_get_service()`` or inject
# a service instance directly via the ``_service`` module attribute.

_service: ClaimService | None = None


def _get_service() -> ClaimService:
    """Return (and lazily initialise) the module-level ClaimService.

    Separated into a function so unit tests can patch it cleanly without
    triggering real AWS calls at import time.
    """
    global _service  # noqa: PLW0603
    if _service is None:
        cfg = load_config()
        claim_repo = ClaimRepository(
            table_name=cfg.claims_table_name,
            region=cfg.aws_region,
        )
        evidence_repo = EvidenceRepository(
            table_name=cfg.evidence_table_name,
            region=cfg.aws_region,
        )
        _service = ClaimService(claim_repo, evidence_repo)
    return _service


# ── Identity extraction ───────────────────────────────────────────────────────


def _extract_claimant_id(event: dict[str, Any]) -> str | None:
    """Extract the authenticated Cognito ``sub`` from the authorizer context.

    API Gateway Cognito User Pool Authorizer (REST API) injects the JWT
    claims under ``requestContext.authorizer.claims``.

    Returns ``None`` if the identity is absent so the handler can return
    a 401 rather than raising an unhandled exception.
    """
    try:
        return cast(
            "str | None", event["requestContext"]["authorizer"]["claims"].get("sub") or None
        )
    except (KeyError, TypeError):
        return None


# ── Body parsing ──────────────────────────────────────────────────────────────


def _parse_body(event: dict[str, Any]) -> dict[str, Any] | None:
    """Parse the request body from the API Gateway event.

    Handles:
    - Missing / null body → returns ``None`` (caller returns 400)
    - Base64-encoded body (``isBase64Encoded: true``) → decoded first
    - Plain JSON string body → parsed directly

    Returns the parsed dict, or ``None`` if the body is absent or empty.
    Raises ``json.JSONDecodeError`` if the body is present but not valid JSON.
    """
    raw = event.get("body")
    if not raw:
        return None

    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")

    return cast("dict[str, Any]", json.loads(raw))


# ── Lambda handler ────────────────────────────────────────────────────────────


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for POST /v1/claims.

    Parameters
    ----------
    event:
        API Gateway Lambda proxy event dict.
    context:
        Lambda context object (unused; present for Lambda contract).

    Returns
    -------
    dict
        API Gateway Lambda proxy response dict.
    """
    logger.info("CreateClaim request received")

    # ── 1. Extract authenticated identity ─────────────────────────────────────
    claimant_id = _extract_claimant_id(event)
    if not claimant_id:
        logger.warning("CreateClaim rejected: missing Cognito identity in authorizer context")
        return unauthorized("Authentication is required.")

    # ── 2. Parse request body ─────────────────────────────────────────────────
    try:
        body = _parse_body(event)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("CreateClaim rejected: malformed request body (%s)", type(exc).__name__)
        return bad_request("VALIDATION_ERROR", "Request body is not valid JSON.")

    if body is None:
        logger.warning("CreateClaim rejected: missing or empty request body")
        return bad_request("VALIDATION_ERROR", "Request body is required.")

    # ── 3. Delegate to service ────────────────────────────────────────────────
    try:
        result = _get_service().create_claim(claimant_id, body)
    except ValidationError as exc:
        logger.info("CreateClaim validation failure: %s", exc.error_code)
        return bad_request(exc.error_code, exc.message)
    except (NotFoundError, ForbiddenError) as exc:
        # These should not normally arise during claim creation, but guard
        # against any future path that might raise them.
        logger.warning("CreateClaim unexpected auth error: %s", exc.error_code)
        return bad_request(exc.error_code, exc.message)
    except Exception:
        # Catch-all: log for ops visibility but never expose internals.
        logger.exception("CreateClaim unexpected error for claimantId omitted")
        return internal_error()

    # ── 4. Return 201 Created ─────────────────────────────────────────────────
    logger.info("CreateClaim success claimId=%s", result.get("claimId"))
    return created(result)

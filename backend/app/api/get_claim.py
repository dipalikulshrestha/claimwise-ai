"""
API handler — GET /v1/claims/{claimId} (Get Claim).

Thin Lambda adapter for claim retrieval.  Delegates all business logic,
ownership checks, and evidence assembly to ``ClaimService.get_claim_with_evidence()``.

Flow:
  1. Extract Cognito sub from authorizer context  → 401 if absent
  2. Extract claimId from path parameters         → 400 if missing/blank
  3. Call ClaimService.get_claim_with_evidence()
     NotFoundError  → 404
     ForbiddenError → 403
     Any other      → 500
  4. Return 200 with ClaimWithEvidenceResponse

No domain logic, DynamoDB calls, or boto3 usage lives here.
"""

from __future__ import annotations

import logging
from typing import Any

from app.api.create_claim import _extract_claimant_id, _get_service
from app.api.responses import bad_request, forbidden, internal_error, not_found, ok, unauthorized
from app.domain.exceptions import ForbiddenError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)


# ── Path parameter extraction ─────────────────────────────────────────────────


def _extract_claim_id(event: dict[str, Any]) -> str | None:
    """Extract and validate claimId from API Gateway path parameters.

    Returns ``None`` if path parameters are absent, claimId is missing,
    or claimId is blank — the handler returns 400 in each case.
    """
    try:
        claim_id = event["pathParameters"]["claimId"]
    except (KeyError, TypeError):
        return None

    if not claim_id or not str(claim_id).strip():
        return None

    return str(claim_id).strip()


# ── Lambda handler ────────────────────────────────────────────────────────────


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for GET /v1/claims/{claimId}.

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
    logger.info("GetClaim request received")

    # ── 1. Extract authenticated identity ─────────────────────────────────────
    claimant_id = _extract_claimant_id(event)
    if not claimant_id:
        logger.warning("GetClaim rejected: missing Cognito identity in authorizer context")
        return unauthorized("Authentication is required.")

    # ── 2. Extract claimId from path parameters ───────────────────────────────
    claim_id = _extract_claim_id(event)
    if not claim_id:
        logger.warning("GetClaim rejected: missing or blank claimId in path parameters")
        return bad_request("VALIDATION_ERROR", "claimId path parameter is required.")

    # ── 3. Delegate to service ────────────────────────────────────────────────
    try:
        result = _get_service().get_claim_with_evidence(claim_id, claimant_id)
    except NotFoundError as exc:
        logger.info("GetClaim not found claimId=%s code=%s", claim_id, exc.error_code)
        return not_found(exc.error_code, exc.message)
    except ForbiddenError as exc:
        # Do not echo the claimId back to avoid confirming resource existence
        logger.warning("GetClaim forbidden code=%s", exc.error_code)
        return forbidden(exc.message)
    except ValidationError as exc:
        logger.info("GetClaim validation error: %s", exc.error_code)
        return bad_request(exc.error_code, exc.message)
    except Exception:
        logger.exception("GetClaim unexpected error claimId=%s", claim_id)
        return internal_error()

    # ── 4. Return 200 OK ──────────────────────────────────────────────────────
    logger.info("GetClaim success claimId=%s", claim_id)
    return ok(result)

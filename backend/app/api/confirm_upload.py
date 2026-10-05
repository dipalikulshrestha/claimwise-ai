"""
API handler — POST /v1/claims/{claimId}/evidence/{evidenceId}/confirm (Confirm Upload).

Thin Lambda adapter.  Delegates all business logic to:
    EvidenceService.confirm_upload(claim_id, evidence_id, claimant_id)

No request body is expected or read.

Flow:
  1. Extract Cognito sub from authorizer context  → 401 if absent/blank
  2. Extract claimId from path parameters         → 400 if missing/blank
  3. Extract evidenceId from path parameters      → 400 if missing/blank
  4. Call EvidenceService.confirm_upload()
     NotFoundError  → 404
     ForbiddenError → 403
     ValidationError → 400
     Any other      → 500
  5. Return 200 with confirmed evidence record

The handler never:
  - reads a request body
  - calls S3 or DynamoDB directly
  - constructs an S3 key
  - logs tokens, authorization headers, or AWS internals
"""

from __future__ import annotations

import logging
from typing import Any

from app.api.create_claim import _extract_claimant_id
from app.api.get_claim import _extract_claim_id
from app.api.request_upload_url import _get_evidence_service
from app.api.responses import bad_request, forbidden, internal_error, not_found, ok, unauthorized
from app.domain.exceptions import ForbiddenError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)


# ── Path parameter extraction ─────────────────────────────────────────────────


def _extract_evidence_id(event: dict[str, Any]) -> str | None:
    """Extract and validate evidenceId from API Gateway path parameters.

    Returns ``None`` if path parameters are absent, evidenceId is missing,
    or evidenceId is blank — the handler returns 400 in each case.
    """
    try:
        evidence_id = event["pathParameters"]["evidenceId"]
    except (KeyError, TypeError):
        return None

    if not evidence_id or not str(evidence_id).strip():
        return None

    return str(evidence_id).strip()


# ── Lambda handler ────────────────────────────────────────────────────────────


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for POST /v1/claims/{claimId}/evidence/{evidenceId}/confirm.

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
    logger.info("ConfirmUpload request received")

    # ── 1. Extract authenticated identity ─────────────────────────────────────
    claimant_id = _extract_claimant_id(event)
    if not claimant_id:
        logger.warning("ConfirmUpload rejected: missing Cognito identity")
        return unauthorized("Authentication is required.")

    # ── 2. Extract claimId from path parameters ───────────────────────────────
    claim_id = _extract_claim_id(event)
    if not claim_id:
        logger.warning("ConfirmUpload rejected: missing or blank claimId")
        return bad_request("VALIDATION_ERROR", "claimId path parameter is required.")

    # ── 3. Extract evidenceId from path parameters ────────────────────────────
    evidence_id = _extract_evidence_id(event)
    if not evidence_id:
        logger.warning("ConfirmUpload rejected: missing or blank evidenceId")
        return bad_request("VALIDATION_ERROR", "evidenceId path parameter is required.")

    # ── 4. Delegate to service ────────────────────────────────────────────────
    try:
        result = _get_evidence_service().confirm_upload(claim_id, evidence_id, claimant_id)
    except NotFoundError as exc:
        logger.info(
            "ConfirmUpload not found claimId=%s evidenceId=%s code=%s",
            claim_id,
            evidence_id,
            exc.error_code,
        )
        return not_found(exc.error_code, exc.message)
    except ForbiddenError as exc:
        logger.warning("ConfirmUpload forbidden code=%s", exc.error_code)
        return forbidden(exc.message)
    except ValidationError as exc:
        logger.info("ConfirmUpload validation error: %s", exc.error_code)
        return bad_request(exc.error_code, exc.message)
    except Exception:
        logger.exception(
            "ConfirmUpload unexpected error claimId=%s evidenceId=%s", claim_id, evidence_id
        )
        return internal_error()

    # ── 5. Return 200 OK ──────────────────────────────────────────────────────
    logger.info(
        "ConfirmUpload success claimId=%s evidenceId=%s",
        claim_id,
        evidence_id,
    )
    return ok(result)

"""
API handler — POST /v1/claims/{claimId}/evidence/upload-url (Request Evidence Upload URL).

Thin Lambda adapter.  Delegates all business logic, ownership checks, and
presigned URL generation to ``EvidenceService.request_upload_url()``.

Flow:
  1. Extract Cognito sub from authorizer context  → 401 if absent
  2. Extract claimId from path parameters         → 400 if missing/blank
  3. Parse JSON request body                      → 400 if missing/malformed
  4. Call EvidenceService.request_upload_url()
     NotFoundError  → 404
     ForbiddenError → 403
     ValidationError → 400  (UNSUPPORTED_EVIDENCE_TYPE, FILE_SIZE_EXCEEDED, …)
     Any other      → 500
  5. Return 201 with UploadUrlResponse

The handler never:
  - receives file bytes
  - uploads to S3
  - constructs an S3 key from user input
  - logs presigned URLs, auth headers, or tokens
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.api.create_claim import _extract_claimant_id, _parse_body
from app.api.get_claim import _extract_claim_id
from app.api.responses import (
    bad_request,
    created,
    forbidden,
    internal_error,
    not_found,
    unauthorized,
)
from app.domain.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.infrastructure.config import load_config
from app.infrastructure.dynamodb import ClaimRepository, EvidenceRepository
from app.infrastructure.s3 import S3Repository
from app.services.evidence_service import EvidenceService

logger = logging.getLogger(__name__)

# ── Module-level EvidenceService instance ─────────────────────────────────────
# Lazily initialised on first request; reused on warm Lambda invocations.
# Tests patch ``_get_evidence_service()`` to inject a fake service.

_evidence_service: EvidenceService | None = None


def _get_evidence_service() -> EvidenceService:
    """Return (and lazily initialise) the module-level EvidenceService."""
    global _evidence_service  # noqa: PLW0603
    if _evidence_service is None:
        cfg = load_config()
        claim_repo = ClaimRepository(cfg.claims_table_name, cfg.aws_region)
        evidence_repo = EvidenceRepository(cfg.evidence_table_name, cfg.aws_region)
        s3_repo = S3Repository(cfg.evidence_bucket_name, cfg.aws_region)
        _evidence_service = EvidenceService(
            claim_repo=claim_repo,
            evidence_repo=evidence_repo,
            s3_repo=s3_repo,
            size_limits=cfg.file_size_limits,
            presigned_url_expiry_seconds=cfg.presigned_url_expiry_seconds,
        )
    return _evidence_service


# ── Lambda handler ────────────────────────────────────────────────────────────


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for POST /v1/claims/{claimId}/evidence/upload-url.

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
    logger.info("RequestUploadUrl request received")

    # ── 1. Extract authenticated identity ─────────────────────────────────────
    claimant_id = _extract_claimant_id(event)
    if not claimant_id:
        logger.warning("RequestUploadUrl rejected: missing Cognito identity")
        return unauthorized("Authentication is required.")

    # ── 2. Extract claimId from path parameters ───────────────────────────────
    claim_id = _extract_claim_id(event)
    if not claim_id:
        logger.warning("RequestUploadUrl rejected: missing or blank claimId")
        return bad_request("VALIDATION_ERROR", "claimId path parameter is required.")

    # ── 3. Parse request body ─────────────────────────────────────────────────
    try:
        body = _parse_body(event)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("RequestUploadUrl rejected: malformed body (%s)", type(exc).__name__)
        return bad_request("VALIDATION_ERROR", "Request body is not valid JSON.")

    if body is None:
        logger.warning("RequestUploadUrl rejected: missing or empty request body")
        return bad_request("VALIDATION_ERROR", "Request body is required.")

    # ── 4. Delegate to service ────────────────────────────────────────────────
    try:
        result = _get_evidence_service().request_upload_url(claim_id, claimant_id, body)
    except NotFoundError as exc:
        logger.info("RequestUploadUrl not found claimId=%s code=%s", claim_id, exc.error_code)
        return not_found(exc.error_code, exc.message)
    except ForbiddenError as exc:
        logger.warning("RequestUploadUrl forbidden code=%s", exc.error_code)
        return forbidden(exc.message)
    except ValidationError as exc:
        logger.info("RequestUploadUrl validation error: %s", exc.error_code)
        return bad_request(exc.error_code, exc.message)
    except Exception:
        logger.exception("RequestUploadUrl unexpected error claimId=%s", claim_id)
        return internal_error()

    # ── 5. Return 201 Created ─────────────────────────────────────────────────
    logger.info(
        "RequestUploadUrl success claimId=%s evidenceId=%s",
        claim_id,
        result.get("evidenceId"),
    )
    return created(result)

"""
Service — Evidence.

Orchestrates evidence upload URL request and upload confirmation workflows.
Sits between API handlers and the domain/infrastructure layers.

Dependency direction:
  API Handler → EvidenceService → domain functions + repositories → DynamoDB / S3

This module contains no HTTP-specific code and no DynamoDB AttributeValue
structures.  All AWS interaction is delegated to the injected repositories.

Key workflows
-------------
request_upload_url:
  1. Verify claim exists and is owned by the claimant.
  2. Validate evidence type, MIME type, and file size against domain rules.
  3. Build the Evidence domain record (generates evidenceId + storageKey).
  4. Persist evidence as UPLOAD_PENDING.
  5. Generate a pre-signed S3 PUT URL for the storage key.
  6. Return evidence metadata + URL to the caller.

confirm_upload:
  1. Verify claim exists and is owned by the claimant.
  2. Fetch the evidence record; verify it belongs to the claim and claimant.
  3. If evidence is already UPLOAD_COMPLETE, return the existing record
     idempotently (no further action).
  4. Verify the expected S3 object exists.
  5. Conditionally transition evidence to UPLOAD_COMPLETE.
  6. Conditionally transition claim to EVIDENCE_SUBMITTED if eligible.
  7. Return the updated evidence record.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.evidence import build_evidence_record
from app.domain.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.infrastructure.dynamodb import ClaimRepository, EvidenceRepository
from app.infrastructure.s3 import S3Repository
from app.services.claim_service import ClaimService, _evidence_to_response

logger = logging.getLogger(__name__)

_ClockFn = Callable[[], str]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


# ── Response model ────────────────────────────────────────────────────────────

UploadUrlResponse = dict[str, Any]
ConfirmUploadResponse = dict[str, Any]


# ── EvidenceService ───────────────────────────────────────────────────────────


class EvidenceService:
    """Application service for evidence upload URL request and confirmation.

    Parameters
    ----------
    claim_repo:
        Repository for claim read/update operations.
    evidence_repo:
        Repository for evidence read/write operations.
    s3_repo:
        Repository for S3 presigned URL generation and object-existence checks.
    size_limits:
        Dict mapping evidence type strings to maximum file sizes in bytes.
        Sourced from ``AppConfig.file_size_limits`` at Lambda cold-start.
    presigned_url_expiry_seconds:
        TTL for pre-signed URLs.  Sourced from ``AppConfig``.
    clock:
        Injectable time function — defaults to UTC now.
    """

    def __init__(
        self,
        claim_repo: ClaimRepository,
        evidence_repo: EvidenceRepository,
        s3_repo: S3Repository,
        size_limits: dict[str, int],
        presigned_url_expiry_seconds: int = 900,
        clock: _ClockFn = _utc_now,
    ) -> None:
        self._claim_repo = claim_repo
        self._evidence_repo = evidence_repo
        self._s3_repo = s3_repo
        self._size_limits = size_limits
        self._expiry = presigned_url_expiry_seconds
        self._clock = clock
        # Internal ClaimService used for ownership checks and claim transitions.
        # We share the same repos and clock so behaviour is consistent.
        self._claim_service = ClaimService(claim_repo, evidence_repo, clock)

    # ── Request evidence upload URL ───────────────────────────────────────────

    def request_upload_url(
        self,
        claim_id: str,
        claimant_id: str,
        body: dict[str, Any],
    ) -> UploadUrlResponse:
        """Validate a request, create an evidence record, and return a
        pre-signed S3 PUT URL.

        Parameters
        ----------
        claim_id:
            The ``CLM-<UUID>`` identifier from the request path.
        claimant_id:
            Cognito ``sub`` of the authenticated user.
        body:
            Parsed request body containing ``evidenceType``, ``fileName``,
            ``contentType``, and ``fileSizeBytes``.

        Returns
        -------
        UploadUrlResponse
            Dict with ``evidenceId``, ``claimId``, ``presignedUrl``,
            ``urlExpiresAt``, ``storageKey``, ``status``, and evidence metadata.

        Raises
        ------
        NotFoundError (CLAIM_NOT_FOUND)
            If the claim does not exist.
        ForbiddenError
            If the claim belongs to a different claimant.
        ValidationError
            If evidence type, MIME type, or file size is invalid.
        """
        # Step 1: verify claim ownership (raises NotFoundError / ForbiddenError)
        self._claim_service._get_owned_claim(claim_id, claimant_id)

        # Step 2: extract and type-check required body fields
        evidence_type = str(body.get("evidenceType", ""))
        file_name = str(body.get("fileName", ""))
        content_type = str(body.get("contentType", ""))
        file_size_raw = body.get("fileSizeBytes")

        if not file_name.strip():
            raise ValidationError("fileName is required.")
        if not content_type.strip():
            raise ValidationError("contentType is required.")
        if file_size_raw is None:
            raise ValidationError("fileSizeBytes is required.")

        try:
            file_size_bytes = int(file_size_raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"fileSizeBytes must be an integer, got: {file_size_raw!r}."
            ) from exc

        # Step 3: build Evidence domain record — validates type/mime/size
        # build_evidence_record raises ValidationError with specific error_codes
        # (UNSUPPORTED_EVIDENCE_TYPE, UNSUPPORTED_CONTENT_TYPE, FILE_SIZE_EXCEEDED)
        evidence = build_evidence_record(
            claim_id=claim_id,
            claimant_id=claimant_id,
            evidence_type=evidence_type,
            original_file_name=file_name,
            content_type=content_type,
            file_size_bytes=file_size_bytes,
            size_limits=self._size_limits,
            clock=self._clock,
        )

        # Step 4: persist as UPLOAD_PENDING
        self._evidence_repo.save(evidence.to_dict())
        logger.info(
            "Created evidence evidenceId=%s claimId=%s",
            evidence.evidence_id,
            claim_id,
        )

        # Step 5: generate pre-signed PUT URL for the backend-controlled key
        presigned_url = self._s3_repo.generate_presigned_put_url(
            key=evidence.storage_key,
            content_type=content_type,
            expiry_seconds=self._expiry,
        )

        # Step 6: build response
        expiry_dt = datetime.now(UTC) + timedelta(seconds=self._expiry)

        return {
            "evidenceId": evidence.evidence_id,
            "claimId": claim_id,
            "presignedUrl": presigned_url,
            "urlExpiresAt": expiry_dt.isoformat(),
            "storageKey": evidence.storage_key,
            "status": evidence.status.value,
            "evidenceType": evidence.evidence_type.value,
            "originalFileName": evidence.original_file_name,
            "contentType": evidence.content_type,
            "fileSizeBytes": evidence.file_size_bytes,
            "createdAt": evidence.created_at,
        }

    # ── Confirm evidence upload ───────────────────────────────────────────────

    def confirm_upload(
        self,
        claim_id: str,
        evidence_id: str,
        claimant_id: str,
    ) -> ConfirmUploadResponse:
        """Confirm that a direct S3 upload has completed.

        Idempotent: if the evidence is already ``UPLOAD_COMPLETE``, the
        existing completed record is returned without further mutation.

        Concurrent-safe: evidence and claim status transitions use conditional
        DynamoDB updates so only one concurrent request applies each change.

        Parameters
        ----------
        claim_id:
            The ``CLM-<UUID>`` identifier from the request path.
        evidence_id:
            The ``EVD-<UUID>`` identifier from the request path.
        claimant_id:
            Cognito ``sub`` of the authenticated user.

        Returns
        -------
        ConfirmUploadResponse
            Updated evidence record dict.

        Raises
        ------
        NotFoundError (CLAIM_NOT_FOUND)
            If the claim does not exist.
        ForbiddenError
            If the claim belongs to a different claimant.
        NotFoundError (EVIDENCE_NOT_FOUND)
            If the evidence record does not exist.
        ForbiddenError
            If the evidence belongs to a different claimant or a different claim.
        ValidationError
            If the S3 object has not been uploaded yet.
        """
        # Step 1: verify claim ownership
        claim_item = self._claim_service._get_owned_claim(claim_id, claimant_id)

        # Step 2: fetch evidence record
        ev_item = self._evidence_repo.get(evidence_id)
        if ev_item is None:
            raise NotFoundError(
                f"Evidence '{evidence_id}' not found.",
                error_code="EVIDENCE_NOT_FOUND",
            )

        # Step 3: verify evidence belongs to this claim
        if ev_item.get("claimId") != claim_id:
            # Return a generic not-found rather than leaking ownership details
            raise NotFoundError(
                f"Evidence '{evidence_id}' not found.",
                error_code="EVIDENCE_NOT_FOUND",
            )

        # Step 4: verify evidence belongs to the authenticated claimant
        if ev_item.get("claimantId") != claimant_id:
            raise ForbiddenError(
                "You do not have permission to access this evidence.",
            )

        # Step 5: idempotency — already complete, return current state
        if ev_item.get("status") == "UPLOAD_COMPLETE":
            logger.info("Evidence already complete evidenceId=%s (idempotent)", evidence_id)
            return _evidence_to_response(ev_item)

        # Step 6: verify the S3 object actually exists
        storage_key = str(ev_item.get("storageKey", ""))
        if not self._s3_repo.object_exists(storage_key):
            raise ValidationError(
                f"The uploaded file for evidence '{evidence_id}' was not found "
                f"in storage.  Complete the S3 upload before confirming.",
                error_code="VALIDATION_ERROR",
            )

        # Step 7: conditionally transition evidence UPLOAD_PENDING → UPLOAD_COMPLETE
        now = self._clock()
        applied = self._evidence_repo.update_status_conditional(
            evidence_id=evidence_id,
            expected_current_status="UPLOAD_PENDING",
            new_status="UPLOAD_COMPLETE",
            updated_at=now,
            uploaded_at=now,
        )

        if applied:
            logger.info("Confirmed upload evidenceId=%s claimId=%s", evidence_id, claim_id)
            ev_item = {**ev_item, "status": "UPLOAD_COMPLETE", "uploadedAt": now, "updatedAt": now}
        else:
            # Concurrent request beat us — re-read to get the current state
            refreshed = self._evidence_repo.get(evidence_id)
            if refreshed is not None:
                ev_item = refreshed

        # Step 8: conditionally transition claim CREATED → EVIDENCE_SUBMITTED
        self._claim_service._try_transition_claim_to_evidence_submitted(
            claim_id=claim_id,
            current_status=str(claim_item.get("status", "")),
        )

        return _evidence_to_response(ev_item)

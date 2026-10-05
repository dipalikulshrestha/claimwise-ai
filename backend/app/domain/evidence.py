"""
Domain — Evidence.

Contains pure business logic for the Evidence aggregate.  This module has
zero AWS dependencies: no boto3, no DynamoDB AttributeValue types, no S3,
no Lambda, no HTTP status codes.

Public surface
--------------
EvidenceType            — extensible string enum of supported evidence types
EvidenceStatus          — extensible string enum of evidence lifecycle statuses
ALLOWED_CONTENT_TYPES   — per-type mapping of permitted MIME strings
Evidence                — immutable value object representing an evidence record
generate_evidence_id    — factory for unique EVD-<UUID4> identifiers
generate_storage_key    — backend-controlled S3 key (filename excluded)
validate_evidence_request — raise typed ValidationError on bad input
build_evidence_record   — create a new Evidence from validated inputs
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from app.domain.exceptions import InvalidStatusTransitionError, ValidationError

# ── Time helper ───────────────────────────────────────────────────────────────

_ClockFn = Callable[[], str]


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string with timezone info."""
    return datetime.now(UTC).isoformat()


# ── Evidence type ─────────────────────────────────────────────────────────────


class EvidenceType(StrEnum):
    """Supported evidence types for Sprint 1.

    Inherits from ``StrEnum`` so values serialise directly to/from plain
    strings in JSON and DynamoDB without extra conversion.

    Additional types (e.g. DASHCAM_FOOTAGE) can be added in later sprints
    without breaking existing records.
    """

    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    POLICY_DOCUMENT = "POLICY_DOCUMENT"


# ── Evidence status ───────────────────────────────────────────────────────────


class EvidenceStatus(StrEnum):
    """Evidence upload lifecycle statuses.

    Future statuses (PROCESSING, PROCESSED, REJECTED, SUPERSEDED) are
    reserved for later specifications and must not be added here.
    """

    UPLOAD_PENDING = "UPLOAD_PENDING"
    UPLOAD_COMPLETE = "UPLOAD_COMPLETE"


# Valid evidence status transitions.
_ALLOWED_EVIDENCE_TRANSITIONS: dict[EvidenceStatus, set[EvidenceStatus]] = {
    EvidenceStatus.UPLOAD_PENDING: {EvidenceStatus.UPLOAD_COMPLETE},
    EvidenceStatus.UPLOAD_COMPLETE: set(),  # terminal for this specification
}

# ── Allowed MIME types ────────────────────────────────────────────────────────

# Each evidence type maps to its own exhaustive set of permitted MIME types.
# A MIME type valid for one category is NOT automatically valid for another
# (e.g. video/mp4 is not valid for POLICY_DOCUMENT).
ALLOWED_CONTENT_TYPES: dict[EvidenceType, frozenset[str]] = {
    EvidenceType.VIDEO: frozenset(
        {
            "video/mp4",
            "video/quicktime",
            "video/x-msvideo",
        }
    ),
    EvidenceType.AUDIO: frozenset(
        {
            "audio/mpeg",
            "audio/mp4",
            "audio/wav",
            "audio/ogg",
        }
    ),
    EvidenceType.POLICY_DOCUMENT: frozenset(
        {
            "application/pdf",
        }
    ),
}

# ── Storage key ───────────────────────────────────────────────────────────────

# Fixed suffix used for every evidence object in S3.
# The original filename is intentionally excluded — it is stored only as
# metadata in the Evidence record and must never influence the S3 key.
_STORAGE_KEY_SUFFIX = "object"


def generate_storage_key(claim_id: str, evidence_id: str) -> str:
    """Return the backend-controlled S3 object key for an evidence file.

    Pattern: ``claims/{claimId}/evidence/{evidenceId}/object``

    The original filename is intentionally absent from the key.  This means:
    - The client cannot influence the storage location.
    - Path-traversal characters in a filename cannot affect the key.
    - The key is deterministic for a given (claimId, evidenceId) pair.
    - Two calls with the same IDs but different filenames return the same key.

    Parameters
    ----------
    claim_id:
        The ``CLM-<UUID>`` identifier of the parent claim.
    evidence_id:
        The ``EVD-<UUID>`` identifier of this evidence record.

    Returns
    -------
    str
        A deterministic, filename-independent S3 object key.

    Examples
    --------
    >>> generate_storage_key(
    ...     "CLM-550e8400-e29b-41d4-a716-446655440000",
    ...     "EVD-7f3c9a12-4b2e-4a8f-9c1d-2e3f4a5b6c7d",
    ... )
    'claims/CLM-550e8400-e29b-41d4-a716-446655440000/evidence/EVD-7f3c9a12-4b2e-4a8f-9c1d-2e3f4a5b6c7d/object'
    """
    return f"claims/{claim_id}/evidence/{evidence_id}/{_STORAGE_KEY_SUFFIX}"


# ── Evidence ID generation ────────────────────────────────────────────────────


def generate_evidence_id() -> str:
    """Generate a new unique evidence identifier.

    Format: ``EVD-<UUID4>``

    Example: ``EVD-7f3c9a12-4b2e-4a8f-9c1d-2e3f4a5b6c7d``

    IDs are generated server-side and are never accepted from client input.
    """
    return f"EVD-{uuid.uuid4()}"


# ── Evidence value object ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Evidence:
    """Immutable value object representing an evidence record.

    All fields are set at construction time.  The object is frozen to prevent
    accidental mutation; use ``complete_upload`` to obtain a new ``Evidence``
    with UPLOAD_COMPLETE status.

    ``original_file_name`` is preserved for display/audit purposes only and
    must never be used to construct filesystem paths or S3 object keys.

    ``created_at`` is immutable — it records the instant the evidence record
    was first created.  ``uploaded_at`` is ``None`` until the upload is
    confirmed.

    This type is persistence-agnostic: it contains plain Python values with
    no DynamoDB AttributeValue wrappers.
    """

    evidence_id: str
    claim_id: str
    claimant_id: str
    evidence_type: EvidenceType
    original_file_name: str
    content_type: str
    file_size_bytes: int
    storage_key: str
    status: EvidenceStatus
    created_at: str
    uploaded_at: str | None = field(default=None)

    def complete_upload(
        self,
        *,
        clock: _ClockFn = _utc_now,
    ) -> Evidence:
        """Return a new ``Evidence`` with status UPLOAD_COMPLETE.

        Preserves all fields.  Sets ``uploaded_at`` to the current UTC time
        via ``clock``.  ``created_at`` is unchanged.

        This is the only valid status transition for evidence in this
        specification.  Calling it on an already-complete record raises
        ``InvalidStatusTransitionError``.

        Raises
        ------
        InvalidStatusTransitionError
            If the current status is not UPLOAD_PENDING.
        """
        allowed = _ALLOWED_EVIDENCE_TRANSITIONS.get(self.status, set())
        if EvidenceStatus.UPLOAD_COMPLETE not in allowed:
            raise InvalidStatusTransitionError(
                f"Cannot transition evidence from '{self.status.value}' "
                f"to '{EvidenceStatus.UPLOAD_COMPLETE.value}'.",
            )
        return dataclasses.replace(
            self,
            status=EvidenceStatus.UPLOAD_COMPLETE,
            uploaded_at=clock(),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dict suitable for DynamoDB storage or JSON.

        All enum values are converted to their string representations.
        ``None`` values (``uploaded_at`` before confirmation) are omitted
        from the output so they are not stored as null attributes in DynamoDB.
        """
        result: dict[str, object] = {
            "evidenceId": self.evidence_id,
            "claimId": self.claim_id,
            "claimantId": self.claimant_id,
            "evidenceType": self.evidence_type.value,
            "originalFileName": self.original_file_name,
            "contentType": self.content_type,
            "fileSizeBytes": self.file_size_bytes,
            "storageKey": self.storage_key,
            "status": self.status.value,
            "createdAt": self.created_at,
        }
        if self.uploaded_at is not None:
            result["uploadedAt"] = self.uploaded_at
        return result


# ── Input validation ──────────────────────────────────────────────────────────


def validate_evidence_request(
    evidence_type: str,
    content_type: str,
    file_size_bytes: int,
    size_limits: dict[str, int],
) -> None:
    """Validate a client evidence upload request against domain rules.

    Validation order:
    1. ``evidence_type`` must be a supported ``EvidenceType``.
    2. ``content_type`` must be in the allowed MIME set for that type.
    3. ``file_size_bytes`` must be > 0.
    4. ``file_size_bytes`` must not exceed the configured limit for the type.

    All file size limits are injected via ``size_limits`` — they are never
    read from environment variables here.  This keeps the domain layer
    independent of infrastructure concerns and makes tests straightforward.

    Parameters
    ----------
    evidence_type:
        Client-supplied evidence type string (e.g. ``"VIDEO"``).
    content_type:
        Client-supplied MIME type string (e.g. ``"video/mp4"``).
    file_size_bytes:
        Declared file size in bytes.
    size_limits:
        Dict mapping evidence type strings to their maximum allowed bytes
        (e.g. ``{"VIDEO": 524288000, "AUDIO": 52428800, ...}``).
        Comes from ``AppConfig.file_size_limits``.

    Raises
    ------
    ValidationError
        With ``error_code`` set to the most specific applicable code:
        - ``UNSUPPORTED_EVIDENCE_TYPE`` — unknown evidence type
        - ``UNSUPPORTED_CONTENT_TYPE`` — MIME not allowed for this type
        - ``VALIDATION_ERROR`` — file_size_bytes <= 0
        - ``FILE_SIZE_EXCEEDED`` — file exceeds the configured limit
    """
    # 1. Validate evidence type
    try:
        ev_type = EvidenceType(evidence_type)
    except ValueError:
        raise ValidationError(
            f"Unsupported evidence type: '{evidence_type}'. "
            f"Allowed types: {sorted(t.value for t in EvidenceType)}.",
            error_code="UNSUPPORTED_EVIDENCE_TYPE",
        ) from None

    # 2. Validate content type against the per-type allowed list
    allowed_mimes = ALLOWED_CONTENT_TYPES[ev_type]
    if content_type not in allowed_mimes:
        raise ValidationError(
            f"Content type '{content_type}' is not supported for evidence "
            f"type '{ev_type.value}'. "
            f"Allowed types: {sorted(allowed_mimes)}.",
            error_code="UNSUPPORTED_CONTENT_TYPE",
        )

    # 3. Validate file size is positive
    if file_size_bytes <= 0:
        raise ValidationError(
            f"fileSizeBytes must be greater than zero, got {file_size_bytes}.",
            error_code="VALIDATION_ERROR",
        )

    # 4. Validate file size against the configured limit
    max_bytes = size_limits.get(ev_type.value)
    if max_bytes is not None and file_size_bytes > max_bytes:
        raise ValidationError(
            f"File size {file_size_bytes} bytes exceeds the maximum allowed "
            f"{max_bytes} bytes for evidence type '{ev_type.value}'.",
            error_code="FILE_SIZE_EXCEEDED",
        )


# ── Evidence factory ──────────────────────────────────────────────────────────


def build_evidence_record(
    claim_id: str,
    claimant_id: str,
    evidence_type: str,
    original_file_name: str,
    content_type: str,
    file_size_bytes: int,
    size_limits: dict[str, int],
    *,
    clock: _ClockFn = _utc_now,
) -> Evidence:
    """Construct a new ``Evidence`` value object for persistence.

    Validates all inputs via ``validate_evidence_request`` before creating
    the record.  The ``evidenceId`` and ``storageKey`` are generated
    internally — they are never accepted from the client.

    The initial status is always ``UPLOAD_PENDING``.

    Parameters
    ----------
    claim_id:
        The ``CLM-<UUID>`` identifier of the parent claim.
    claimant_id:
        Cognito ``sub`` of the authenticated user.
    evidence_type:
        Client-supplied evidence type string (validated against ``EvidenceType``).
    original_file_name:
        Original filename provided by the client — stored as metadata only,
        never used to construct the storage key.
    content_type:
        MIME type string (validated against the per-type allowed list).
    file_size_bytes:
        Declared file size in bytes (validated against ``size_limits``).
    size_limits:
        Dict mapping evidence type strings to maximum byte limits.
        Use ``AppConfig.file_size_limits`` in production.
    clock:
        Injectable time function for deterministic testing.

    Returns
    -------
    Evidence
        A fully populated, immutable evidence record ready for persistence.

    Raises
    ------
    ValidationError
        If any input validation fails (see ``validate_evidence_request``).
    """
    # Validate all inputs — raises ValidationError with specific error_code
    validate_evidence_request(evidence_type, content_type, file_size_bytes, size_limits)

    evidence_id = generate_evidence_id()
    storage_key = generate_storage_key(claim_id, evidence_id)
    now = clock()

    return Evidence(
        evidence_id=evidence_id,
        claim_id=claim_id,
        claimant_id=claimant_id,
        evidence_type=EvidenceType(evidence_type),
        original_file_name=original_file_name,
        content_type=content_type,
        file_size_bytes=file_size_bytes,
        storage_key=storage_key,
        status=EvidenceStatus.UPLOAD_PENDING,
        created_at=now,
        uploaded_at=None,
    )

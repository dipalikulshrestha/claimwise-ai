"""
Domain — Claim.

Contains pure business logic for the Claim aggregate.  This module has
zero AWS dependencies: no boto3, no DynamoDB AttributeValue types, no S3,
no Lambda, no HTTP status codes.

Public surface
--------------
ClaimStatus         — extensible string enum of valid claim statuses
Claim               — immutable value object representing a claim record
generate_claim_id   — factory for unique CLM-<UUID4> identifiers
build_claim_record  — create a new Claim from validated inputs
validate_claim_input — raise ValidationError on bad client input
should_transition_to_evidence_submitted — pure status-transition predicate
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

# The default clock function returns a timezone-aware UTC datetime string.
# It is accepted as a parameter by factory functions so tests can inject a
# fixed time instead of depending on the real system clock.
_ClockFn = Callable[[], str]


def _utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string with timezone info."""
    return datetime.now(UTC).isoformat()


# ── Claim status ──────────────────────────────────────────────────────────────


class ClaimStatus(StrEnum):
    """Valid claim lifecycle statuses.

    Inherits from ``StrEnum`` so values serialise naturally to/from JSON and
    DynamoDB string attributes without extra conversion.

    Only the two statuses defined in the Sprint 1 specification are present.
    Future statuses (e.g. UNDER_REVIEW, ASSESSMENT_COMPLETE) will be added
    in later specifications.
    """

    CREATED = "CREATED"
    EVIDENCE_SUBMITTED = "EVIDENCE_SUBMITTED"


# Valid transitions: maps each status to the set of statuses it may move to.
_ALLOWED_TRANSITIONS: dict[ClaimStatus, set[ClaimStatus]] = {
    ClaimStatus.CREATED: {ClaimStatus.EVIDENCE_SUBMITTED},
    ClaimStatus.EVIDENCE_SUBMITTED: set(),  # terminal for this specification
}


# ── Claim value object ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Claim:
    """Immutable value object representing a claim record.

    All fields are set at construction time.  The object is frozen to prevent
    accidental mutation; use ``transition_status`` to obtain a new ``Claim``
    with an updated status.

    The ``description`` field is optional and defaults to ``None``.

    ``createdAt`` is immutable — it records the instant the claim was first
    created and must never change.  ``updatedAt`` changes on every state
    transition.

    This type is persistence-agnostic: it contains plain Python values, not
    DynamoDB AttributeValue dicts.  The repository layer handles conversion.
    """

    claim_id: str
    claimant_id: str
    policy_number: str
    incident_date_time: str
    incident_location: str
    status: ClaimStatus
    created_at: str
    updated_at: str
    description: str | None = field(default=None)

    def transition_status(
        self,
        new_status: ClaimStatus,
        *,
        clock: _ClockFn = _utc_now,
    ) -> Claim:
        """Return a new ``Claim`` with the status advanced to ``new_status``.

        ``createdAt`` is preserved from the original instance.
        ``updatedAt`` is set to the current UTC time via ``clock``.

        Raises
        ------
        InvalidStatusTransitionError
            If the transition from the current status to ``new_status`` is not
            permitted by the domain rules.
        """
        allowed = _ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStatusTransitionError(
                f"Cannot transition claim from '{self.status.value}' to '{new_status.value}'.",
            )
        # frozen=True requires object.__setattr__ — use dataclasses.replace
        # instead which creates a new instance cleanly.
        return dataclasses.replace(
            self,
            status=new_status,
            updated_at=clock(),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialise to a plain dict suitable for DynamoDB storage or JSON.

        All enum values are converted to their string representations.
        ``None`` values (optional fields) are omitted from the output so they
        are not stored as null attributes in DynamoDB.
        """
        result: dict[str, object] = {
            "claimId": self.claim_id,
            "claimantId": self.claimant_id,
            "policyNumber": self.policy_number,
            "incidentDateTime": self.incident_date_time,
            "incidentLocation": self.incident_location,
            "status": self.status.value,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }
        if self.description is not None:
            result["description"] = self.description
        return result


# ── Claim ID generation ───────────────────────────────────────────────────────


def generate_claim_id() -> str:
    """Generate a new unique claim identifier.

    Format: ``CLM-<UUID4>``

    Example: ``CLM-550e8400-e29b-41d4-a716-446655440000``

    IDs are generated server-side using ``uuid.uuid4()`` and are never
    accepted from client input.
    """
    return f"CLM-{uuid.uuid4()}"


# ── Input validation ──────────────────────────────────────────────────────────


def validate_claim_input(body: dict[str, object]) -> None:
    """Validate client-supplied claim creation fields.

    Checks that all required fields are present and non-blank.  The
    ``description`` field is optional and is not validated here.

    Parameters
    ----------
    body:
        Raw request body dict from the API handler (keys are camelCase as
        received from the client).

    Raises
    ------
    ValidationError
        With a message naming the first invalid field encountered.
    """
    required_fields = [
        ("policyNumber", "policyNumber is required."),
        ("incidentDateTime", "incidentDateTime is required."),
        ("incidentLocation", "incidentLocation is required."),
    ]

    for field_name, message in required_fields:
        value = body.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValidationError(message)


# ── Claim factory ─────────────────────────────────────────────────────────────


def build_claim_record(
    claimant_id: str,
    policy_number: str,
    incident_date_time: str,
    incident_location: str,
    description: str | None = None,
    *,
    clock: _ClockFn = _utc_now,
) -> Claim:
    """Construct a new ``Claim`` value object for persistence.

    The ``claimId`` is generated internally.  The initial status is always
    ``CREATED``.  Both ``createdAt`` and ``updatedAt`` are set to the current
    UTC time via ``clock``.

    Parameters
    ----------
    claimant_id:
        Cognito ``sub`` of the authenticated user.  Must not be blank.
    policy_number:
        Client-supplied policy reference.  Must not be blank.
    incident_date_time:
        ISO 8601 UTC datetime string of the accident.
    incident_location:
        Free-text description of the accident location.
    description:
        Optional claimant narrative.
    clock:
        Injectable time function — defaults to ``_utc_now``.  Pass a lambda
        returning a fixed string in tests to get deterministic timestamps.

    Raises
    ------
    ValidationError
        If ``claimant_id`` is blank (programming error — the handler should
        always extract this from the Cognito authorizer context).
    """
    if not claimant_id or not claimant_id.strip():
        raise ValidationError("claimantId is required and must not be blank.")

    now = clock()
    return Claim(
        claim_id=generate_claim_id(),
        claimant_id=claimant_id,
        policy_number=policy_number,
        incident_date_time=incident_date_time,
        incident_location=incident_location,
        description=description,
        status=ClaimStatus.CREATED,
        created_at=now,
        updated_at=now,
    )


# ── Status transition predicate ───────────────────────────────────────────────


def should_transition_to_evidence_submitted(
    current_status: ClaimStatus | str,
    confirmed_evidence_count: int,
) -> bool:
    """Return True when the claim should move to EVIDENCE_SUBMITTED.

    This is a pure predicate used by the service layer to decide whether to
    perform a conditional DynamoDB status update after an evidence confirmation.

    The rule (from FR-05): when at least one evidence item reaches
    UPLOAD_COMPLETE and the claim is still CREATED, it transitions.

    Parameters
    ----------
    current_status:
        The claim's current status (``ClaimStatus`` enum or the equivalent
        string value).
    confirmed_evidence_count:
        Number of evidence records for this claim with status UPLOAD_COMPLETE.

    Returns
    -------
    bool
        ``True`` iff the transition should be attempted.
    """
    # Normalise string → enum so callers don't have to convert
    if isinstance(current_status, str):
        try:
            current_status = ClaimStatus(current_status)
        except ValueError:
            return False

    return current_status == ClaimStatus.CREATED and confirmed_evidence_count >= 1

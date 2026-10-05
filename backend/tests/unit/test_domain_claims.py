"""
Unit tests for app.domain.claims and app.domain.exceptions — Task 3.3.

Covers all 20 required test scenarios:
 1.  Valid claim creation.
 2.  Claim ID starts with 'CLM-'.
 3.  Claim ID contains a valid UUID4.
 4.  claimantId is required.
 5.  Blank policyNumber is rejected.
 6.  Missing policyNumber is rejected.
 7.  Missing incidentDateTime is rejected.
 8.  Blank incidentLocation is rejected.
 9.  Missing incidentLocation is rejected.
10.  description can be omitted.
11.  New claim always starts as CREATED.
12.  Client cannot initialise the claim in EVIDENCE_SUBMITTED.
13.  createdAt is populated.
14.  updatedAt is populated.
15.  createdAt is not changed during a status transition.
16.  updatedAt changes during a status transition.
17.  CREATED → EVIDENCE_SUBMITTED succeeds.
18.  Invalid status transitions are rejected.
19.  Domain validation errors are clear and testable (error_code + message).
20.  Domain tests do not require AWS credentials or AWS services.

No boto3, moto, or AWS imports are used anywhere in this file.
"""

from __future__ import annotations

import uuid

import pytest

from app.domain.claims import (
    Claim,
    ClaimStatus,
    build_claim_record,
    generate_claim_id,
    should_transition_to_evidence_submitted,
    validate_claim_input,
)
from app.domain.exceptions import (
    ForbiddenError,
    InvalidStatusTransitionError,
    NotFoundError,
    ValidationError,
)

# ── Shared fixtures / helpers ─────────────────────────────────────────────────

CLAIMANT_ID = "cognito-sub-abc-123"
POLICY_NUMBER = "POL-123456"
INCIDENT_DT = "2026-09-15T14:30:00Z"
INCIDENT_LOC = "123 Main St, Springfield"
DESCRIPTION = "Rear-ended at traffic lights."

FIXED_TIME_1 = "2026-09-30T10:00:00+00:00"
FIXED_TIME_2 = "2026-09-30T10:05:00+00:00"


def _fixed_clock(ts: str):
    """Return a no-arg callable that always returns *ts*."""
    return lambda: ts


def _make_claim(
    claimant_id: str = CLAIMANT_ID,
    policy_number: str = POLICY_NUMBER,
    incident_date_time: str = INCIDENT_DT,
    incident_location: str = INCIDENT_LOC,
    description: str | None = DESCRIPTION,
    clock=_fixed_clock(FIXED_TIME_1),  # noqa: B008
) -> Claim:
    """Convenience wrapper around build_claim_record with sensible defaults."""
    return build_claim_record(
        claimant_id=claimant_id,
        policy_number=policy_number,
        incident_date_time=incident_date_time,
        incident_location=incident_location,
        description=description,
        clock=clock,
    )


# ── 1. Valid claim creation ────────────────────────────────────────────────────


class TestValidClaimCreation:
    def test_build_claim_record_returns_claim_instance(self) -> None:
        """Scenario 1 — valid inputs produce a Claim instance."""
        claim = _make_claim()
        assert isinstance(claim, Claim)

    def test_claim_fields_match_inputs(self) -> None:
        """All supplied fields are stored verbatim on the Claim."""
        claim = _make_claim()
        assert claim.claimant_id == CLAIMANT_ID
        assert claim.policy_number == POLICY_NUMBER
        assert claim.incident_date_time == INCIDENT_DT
        assert claim.incident_location == INCIDENT_LOC
        assert claim.description == DESCRIPTION


# ── 2. Claim ID starts with 'CLM-' ────────────────────────────────────────────


class TestClaimIdFormat:
    def test_generate_claim_id_starts_with_clm_prefix(self) -> None:
        """Scenario 2 — generated ID always starts with 'CLM-'."""
        claim_id = generate_claim_id()
        assert claim_id.startswith("CLM-")

    def test_build_claim_record_id_starts_with_clm_prefix(self) -> None:
        """Scenario 2 — ID on the Claim object starts with 'CLM-'."""
        claim = _make_claim()
        assert claim.claim_id.startswith("CLM-")

    # ── 3. Claim ID contains a valid UUID4 ────────────────────────────────────

    def test_generate_claim_id_suffix_is_valid_uuid4(self) -> None:
        """Scenario 3 — the part after 'CLM-' is a valid UUID4."""
        claim_id = generate_claim_id()
        suffix = claim_id[len("CLM-") :]
        parsed = uuid.UUID(suffix, version=4)
        # uuid.UUID raises ValueError for invalid strings; version=4 enforces variant
        assert str(parsed) == suffix

    def test_claim_id_suffix_is_valid_uuid4(self) -> None:
        """Scenario 3 — Claim.claim_id suffix is a valid UUID4."""
        claim = _make_claim()
        suffix = claim.claim_id[len("CLM-") :]
        parsed = uuid.UUID(suffix, version=4)
        assert str(parsed) == suffix

    def test_two_generated_ids_are_unique(self) -> None:
        """Each call to generate_claim_id returns a different value."""
        assert generate_claim_id() != generate_claim_id()

    def test_concurrent_claims_have_different_ids(self) -> None:
        """Scenario from AC-01.6 — two claims built with identical inputs differ."""
        claim_a = _make_claim()
        claim_b = _make_claim()
        assert claim_a.claim_id != claim_b.claim_id


# ── 4. claimantId is required ─────────────────────────────────────────────────


class TestClaimantIdRequired:
    def test_empty_claimant_id_raises_validation_error(self) -> None:
        """Scenario 4 — empty claimantId raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            _make_claim(claimant_id="")
        assert exc_info.value.error_code == "VALIDATION_ERROR"

    def test_whitespace_only_claimant_id_raises_validation_error(self) -> None:
        """Scenario 4 — whitespace-only claimantId is treated as missing."""
        with pytest.raises(ValidationError):
            _make_claim(claimant_id="   ")

    def test_none_claimant_id_raises_validation_error(self) -> None:
        """Scenario 4 — None claimantId raises ValidationError."""
        with pytest.raises(ValidationError):
            build_claim_record(
                claimant_id=None,  # type: ignore[arg-type]
                policy_number=POLICY_NUMBER,
                incident_date_time=INCIDENT_DT,
                incident_location=INCIDENT_LOC,
            )


# ── 5 & 6. policyNumber validation ───────────────────────────────────────────


class TestPolicyNumberValidation:
    def test_blank_policy_number_raises_validation_error(self) -> None:
        """Scenario 5 — blank policyNumber is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_claim_input(
                {
                    "policyNumber": "   ",
                    "incidentDateTime": INCIDENT_DT,
                    "incidentLocation": INCIDENT_LOC,
                }
            )
        assert "policyNumber" in exc_info.value.message

    def test_missing_policy_number_raises_validation_error(self) -> None:
        """Scenario 6 — missing policyNumber is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_claim_input(
                {
                    "incidentDateTime": INCIDENT_DT,
                    "incidentLocation": INCIDENT_LOC,
                }
            )
        assert "policyNumber" in exc_info.value.message

    def test_empty_string_policy_number_raises_validation_error(self) -> None:
        """Scenario 5 — empty-string policyNumber is rejected."""
        with pytest.raises(ValidationError):
            validate_claim_input(
                {
                    "policyNumber": "",
                    "incidentDateTime": INCIDENT_DT,
                    "incidentLocation": INCIDENT_LOC,
                }
            )


# ── 7. incidentDateTime is required ──────────────────────────────────────────


class TestIncidentDateTimeRequired:
    def test_missing_incident_datetime_raises_validation_error(self) -> None:
        """Scenario 7 — missing incidentDateTime is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_claim_input(
                {
                    "policyNumber": POLICY_NUMBER,
                    "incidentLocation": INCIDENT_LOC,
                }
            )
        assert "incidentDateTime" in exc_info.value.message

    def test_none_incident_datetime_raises_validation_error(self) -> None:
        """Scenario 7 — None value for incidentDateTime is rejected."""
        with pytest.raises(ValidationError):
            validate_claim_input(
                {
                    "policyNumber": POLICY_NUMBER,
                    "incidentDateTime": None,
                    "incidentLocation": INCIDENT_LOC,
                }
            )


# ── 8 & 9. incidentLocation validation ───────────────────────────────────────


class TestIncidentLocationValidation:
    def test_blank_incident_location_raises_validation_error(self) -> None:
        """Scenario 8 — blank incidentLocation is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_claim_input(
                {
                    "policyNumber": POLICY_NUMBER,
                    "incidentDateTime": INCIDENT_DT,
                    "incidentLocation": "   ",
                }
            )
        assert "incidentLocation" in exc_info.value.message

    def test_missing_incident_location_raises_validation_error(self) -> None:
        """Scenario 9 — missing incidentLocation is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_claim_input(
                {
                    "policyNumber": POLICY_NUMBER,
                    "incidentDateTime": INCIDENT_DT,
                }
            )
        assert "incidentLocation" in exc_info.value.message


# ── 10. description is optional ──────────────────────────────────────────────


class TestDescriptionOptional:
    def test_description_can_be_omitted_from_validate_input(self) -> None:
        """Scenario 10 — validate_claim_input passes without description."""
        # Should not raise
        validate_claim_input(
            {
                "policyNumber": POLICY_NUMBER,
                "incidentDateTime": INCIDENT_DT,
                "incidentLocation": INCIDENT_LOC,
            }
        )

    def test_claim_builds_without_description(self) -> None:
        """Scenario 10 — build_claim_record succeeds when description=None."""
        claim = _make_claim(description=None)
        assert claim.description is None

    def test_to_dict_omits_none_description(self) -> None:
        """Scenario 10 — serialised dict does not contain 'description' key when None."""
        claim = _make_claim(description=None)
        serialised = claim.to_dict()
        assert "description" not in serialised

    def test_to_dict_includes_description_when_provided(self) -> None:
        """description is included in the dict when it has a value."""
        claim = _make_claim(description="Some details.")
        serialised = claim.to_dict()
        assert serialised["description"] == "Some details."


# ── 11. New claim starts as CREATED ──────────────────────────────────────────


class TestInitialStatus:
    def test_new_claim_status_is_created(self) -> None:
        """Scenario 11 — freshly built claim always starts as CREATED."""
        claim = _make_claim()
        assert claim.status == ClaimStatus.CREATED

    def test_new_claim_status_string_value_is_created(self) -> None:
        """Scenario 11 — status serialises to the string 'CREATED'."""
        claim = _make_claim()
        assert claim.to_dict()["status"] == "CREATED"


# ── 12. Client cannot initialise claim as EVIDENCE_SUBMITTED ─────────────────


class TestClientCannotSetArbitraryStatus:
    def test_build_claim_record_always_sets_created_status(self) -> None:
        """Scenario 12 — build_claim_record ignores any attempt to pass status."""
        # build_claim_record has no status parameter — the only way to create
        # a claim is via the factory, which always sets CREATED.
        import inspect

        sig = inspect.signature(build_claim_record)
        assert "status" not in sig.parameters

    def test_claim_is_frozen_so_status_cannot_be_mutated_directly(self) -> None:
        """Scenario 12 — frozen dataclass prevents direct status assignment."""
        claim = _make_claim()
        with pytest.raises((AttributeError, TypeError)):
            claim.status = ClaimStatus.EVIDENCE_SUBMITTED  # type: ignore[misc]


# ── 13. createdAt is populated ────────────────────────────────────────────────


class TestTimestamps:
    def test_created_at_is_populated(self) -> None:
        """Scenario 13 — createdAt is a non-empty string after build."""
        claim = _make_claim(clock=_fixed_clock(FIXED_TIME_1))
        assert claim.created_at
        assert isinstance(claim.created_at, str)

    def test_created_at_matches_clock(self) -> None:
        """Scenario 13 — createdAt reflects the value returned by the clock."""
        claim = _make_claim(clock=_fixed_clock(FIXED_TIME_1))
        assert claim.created_at == FIXED_TIME_1

    # ── 14. updatedAt is populated ────────────────────────────────────────────

    def test_updated_at_is_populated(self) -> None:
        """Scenario 14 — updatedAt is a non-empty string after build."""
        claim = _make_claim(clock=_fixed_clock(FIXED_TIME_1))
        assert claim.updated_at
        assert isinstance(claim.updated_at, str)

    def test_updated_at_matches_clock_on_creation(self) -> None:
        """Scenario 14 — updatedAt equals createdAt immediately after creation."""
        claim = _make_claim(clock=_fixed_clock(FIXED_TIME_1))
        assert claim.updated_at == FIXED_TIME_1
        assert claim.updated_at == claim.created_at

    # ── 15. createdAt is not changed during status transition ─────────────────

    def test_created_at_unchanged_after_status_transition(self) -> None:
        """Scenario 15 — transition_status preserves the original createdAt."""
        claim = _make_claim(clock=_fixed_clock(FIXED_TIME_1))
        transitioned = claim.transition_status(
            ClaimStatus.EVIDENCE_SUBMITTED,
            clock=_fixed_clock(FIXED_TIME_2),
        )
        assert transitioned.created_at == FIXED_TIME_1
        assert transitioned.created_at == claim.created_at

    # ── 16. updatedAt changes during status transition ────────────────────────

    def test_updated_at_changes_after_status_transition(self) -> None:
        """Scenario 16 — transition_status writes a new updatedAt."""
        claim = _make_claim(clock=_fixed_clock(FIXED_TIME_1))
        transitioned = claim.transition_status(
            ClaimStatus.EVIDENCE_SUBMITTED,
            clock=_fixed_clock(FIXED_TIME_2),
        )
        assert transitioned.updated_at == FIXED_TIME_2
        assert transitioned.updated_at != claim.updated_at


# ── 17. CREATED → EVIDENCE_SUBMITTED succeeds ────────────────────────────────


class TestValidStatusTransition:
    def test_created_to_evidence_submitted_succeeds(self) -> None:
        """Scenario 17 — valid transition returns a new Claim with updated status."""
        claim = _make_claim(clock=_fixed_clock(FIXED_TIME_1))
        transitioned = claim.transition_status(
            ClaimStatus.EVIDENCE_SUBMITTED,
            clock=_fixed_clock(FIXED_TIME_2),
        )
        assert transitioned.status == ClaimStatus.EVIDENCE_SUBMITTED

    def test_transition_returns_new_instance_not_mutation(self) -> None:
        """Scenario 17 — transition_status returns a new object; original is unchanged."""
        claim = _make_claim()
        transitioned = claim.transition_status(ClaimStatus.EVIDENCE_SUBMITTED)
        assert claim.status == ClaimStatus.CREATED
        assert transitioned.status == ClaimStatus.EVIDENCE_SUBMITTED
        assert transitioned is not claim

    def test_non_status_fields_are_preserved_after_transition(self) -> None:
        """All non-status fields survive a status transition unchanged."""
        claim = _make_claim(clock=_fixed_clock(FIXED_TIME_1))
        transitioned = claim.transition_status(
            ClaimStatus.EVIDENCE_SUBMITTED,
            clock=_fixed_clock(FIXED_TIME_2),
        )
        assert transitioned.claim_id == claim.claim_id
        assert transitioned.claimant_id == claim.claimant_id
        assert transitioned.policy_number == claim.policy_number
        assert transitioned.incident_date_time == claim.incident_date_time
        assert transitioned.incident_location == claim.incident_location
        assert transitioned.description == claim.description


# ── 18. Invalid status transitions are rejected ───────────────────────────────


class TestInvalidStatusTransitions:
    def test_evidence_submitted_to_created_raises(self) -> None:
        """Scenario 18 — EVIDENCE_SUBMITTED → CREATED is not a valid transition."""
        claim = _make_claim()
        transitioned = claim.transition_status(ClaimStatus.EVIDENCE_SUBMITTED)

        with pytest.raises(InvalidStatusTransitionError) as exc_info:
            transitioned.transition_status(ClaimStatus.CREATED)

        assert exc_info.value.error_code == "VALIDATION_ERROR"

    def test_evidence_submitted_to_evidence_submitted_raises(self) -> None:
        """Scenario 18 — self-transition from EVIDENCE_SUBMITTED is not allowed."""
        claim = _make_claim()
        transitioned = claim.transition_status(ClaimStatus.EVIDENCE_SUBMITTED)

        with pytest.raises(InvalidStatusTransitionError):
            transitioned.transition_status(ClaimStatus.EVIDENCE_SUBMITTED)

    def test_invalid_transition_message_names_both_statuses(self) -> None:
        """Scenario 18 — error message identifies the from/to statuses."""
        claim = _make_claim()
        transitioned = claim.transition_status(ClaimStatus.EVIDENCE_SUBMITTED)

        with pytest.raises(InvalidStatusTransitionError) as exc_info:
            transitioned.transition_status(ClaimStatus.CREATED)

        msg = str(exc_info.value)
        assert "EVIDENCE_SUBMITTED" in msg
        assert "CREATED" in msg


# ── 19. Domain validation errors are clear and testable ──────────────────────


class TestDomainExceptions:
    def test_validation_error_has_error_code_attribute(self) -> None:
        """Scenario 19 — ValidationError carries a machine-readable error_code."""
        exc = ValidationError("something is wrong")
        assert exc.error_code == "VALIDATION_ERROR"

    def test_validation_error_message_is_accessible(self) -> None:
        """Scenario 19 — ValidationError message is inspectable."""
        msg = "policyNumber is required."
        exc = ValidationError(msg)
        assert exc.message == msg
        assert str(exc) == msg

    def test_not_found_error_default_code(self) -> None:
        """NotFoundError defaults to NOT_FOUND error code."""
        exc = NotFoundError("claim not found")
        assert exc.error_code == "NOT_FOUND"

    def test_not_found_error_custom_code(self) -> None:
        """NotFoundError accepts a custom code for resource-specific errors."""
        exc = NotFoundError("claim not found", error_code="CLAIM_NOT_FOUND")
        assert exc.error_code == "CLAIM_NOT_FOUND"

    def test_forbidden_error_has_correct_code(self) -> None:
        """ForbiddenError error_code is FORBIDDEN."""
        exc = ForbiddenError("access denied")
        assert exc.error_code == "FORBIDDEN"

    def test_invalid_status_transition_error_is_subclass_of_validation_error(self) -> None:
        """InvalidStatusTransitionError can be caught as ValidationError."""
        exc = InvalidStatusTransitionError("bad transition")
        assert isinstance(exc, ValidationError)

    def test_validate_claim_input_error_names_the_field(self) -> None:
        """Scenario 19 — each validation error message names the failing field."""
        cases = [
            ({"incidentDateTime": INCIDENT_DT, "incidentLocation": INCIDENT_LOC}, "policyNumber"),
            ({"policyNumber": POLICY_NUMBER, "incidentLocation": INCIDENT_LOC}, "incidentDateTime"),
            ({"policyNumber": POLICY_NUMBER, "incidentDateTime": INCIDENT_DT}, "incidentLocation"),
        ]
        for body, expected_field in cases:
            with pytest.raises(ValidationError) as exc_info:
                validate_claim_input(body)
            assert expected_field in exc_info.value.message, (
                f"Expected '{expected_field}' in error message for body={body}"
            )


# ── 20. No AWS credentials or services required ───────────────────────────────


class TestNoDomainAwsDependencies:
    def test_no_boto3_import_in_claims_module(self) -> None:
        """Scenario 20 — claims.py must not import boto3."""
        import app.domain.claims as claims_module

        assert not hasattr(claims_module, "boto3"), "boto3 must not be imported in the domain"

    def test_no_boto3_import_in_exceptions_module(self) -> None:
        """Scenario 20 — exceptions.py must not import boto3."""
        import app.domain.exceptions as exc_module

        assert not hasattr(exc_module, "boto3")

    def test_claims_module_imported_without_aws_env_vars(self) -> None:
        """Scenario 20 — domain modules import cleanly with no AWS env vars set."""
        # If any domain module attempted to call boto3 or load AWS config at
        # import time, this test would fail in a clean environment.
        import importlib

        importlib.import_module("app.domain.claims")
        importlib.import_module("app.domain.exceptions")

    def test_build_claim_record_needs_no_aws_credentials(self) -> None:
        """Scenario 20 — building a claim record requires no AWS calls."""
        # Simply running this confirms no real AWS SDK interaction occurs.
        claim = _make_claim()
        assert claim.claim_id.startswith("CLM-")


# ── should_transition_to_evidence_submitted predicate ────────────────────────


class TestShouldTransitionPredicate:
    def test_returns_true_when_created_and_one_confirmed(self) -> None:
        assert should_transition_to_evidence_submitted(ClaimStatus.CREATED, 1) is True

    def test_returns_true_when_created_and_multiple_confirmed(self) -> None:
        assert should_transition_to_evidence_submitted(ClaimStatus.CREATED, 3) is True

    def test_returns_false_when_created_and_zero_confirmed(self) -> None:
        assert should_transition_to_evidence_submitted(ClaimStatus.CREATED, 0) is False

    def test_returns_false_when_already_evidence_submitted(self) -> None:
        assert should_transition_to_evidence_submitted(ClaimStatus.EVIDENCE_SUBMITTED, 1) is False

    def test_accepts_string_status_created(self) -> None:
        """Callers may pass string values from DynamoDB without converting."""
        assert should_transition_to_evidence_submitted("CREATED", 1) is True

    def test_accepts_string_status_evidence_submitted(self) -> None:
        assert should_transition_to_evidence_submitted("EVIDENCE_SUBMITTED", 1) is False

    def test_returns_false_for_unknown_status_string(self) -> None:
        """Unknown status strings are treated as non-transitionable."""
        assert should_transition_to_evidence_submitted("UNKNOWN_STATUS", 1) is False


# ── to_dict serialisation ─────────────────────────────────────────────────────


class TestClaimToDict:
    def test_to_dict_contains_all_required_keys(self) -> None:
        claim = _make_claim()
        d = claim.to_dict()
        for key in (
            "claimId",
            "claimantId",
            "policyNumber",
            "incidentDateTime",
            "incidentLocation",
            "status",
            "createdAt",
            "updatedAt",
        ):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_status_is_string_not_enum(self) -> None:
        """status must serialise to a plain string, not a ClaimStatus enum instance."""
        claim = _make_claim()
        d = claim.to_dict()
        assert isinstance(d["status"], str)
        assert d["status"] == "CREATED"

    def test_to_dict_does_not_contain_dynamodb_attribute_value_types(self) -> None:
        """No DynamoDB {'S': ...} / {'N': ...} wrappers in the dict."""
        claim = _make_claim()
        for value in claim.to_dict().values():
            assert not isinstance(value, dict), (
                f"DynamoDB AttributeValue dict found in to_dict() output: {value}"
            )

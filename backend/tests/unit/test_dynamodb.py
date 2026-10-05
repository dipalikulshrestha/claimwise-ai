"""
Unit tests for app.infrastructure.dynamodb — Task 2.

Tests (all use moto-mocked DynamoDB — no real AWS calls):
  ClaimRepository:
    - save() persists the item
    - get() returns the item by claimId
    - get() returns None for a missing item
    - update_status_conditional() applies when condition matches
    - update_status_conditional() returns False when condition fails (already transitioned)

  EvidenceRepository:
    - save() persists the item
    - get() returns the item by evidenceId
    - get() returns None for a missing item
    - list_by_claim() returns all evidence for a claimId via GSI
    - list_by_claim() returns empty list when no evidence exists
    - count_completed_by_claim() counts only UPLOAD_COMPLETE items
    - update_status_conditional() applies when condition matches, sets uploadedAt
    - update_status_conditional() returns False when condition fails
"""

from __future__ import annotations

import boto3
from moto import mock_aws

from app.infrastructure.dynamodb import ClaimRepository, EvidenceRepository

# ── Constants ─────────────────────────────────────────────────────────────────

REGION = "us-east-1"
CLAIMS_TABLE = "ClaimwiseClaims"
EVIDENCE_TABLE = "ClaimwiseEvidence"

CLAIM_ID = "CLM-550e8400-e29b-41d4-a716-446655440000"
CLAIMANT_ID = "cognito-sub-aaa-111"
EVIDENCE_ID = "EVD-7f3c9a12-4b2e-4a8f-9c1d-2e3f4a5b6c7d"


# ── Table creation helpers ────────────────────────────────────────────────────


def _create_claims_table(ddb_resource: object) -> object:
    return ddb_resource.create_table(  # type: ignore[attr-defined]
        TableName=CLAIMS_TABLE,
        KeySchema=[{"AttributeName": "claimId", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "claimId", "AttributeType": "S"},
            {"AttributeName": "claimantId", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "claimantId-index",
                "KeySchema": [{"AttributeName": "claimantId", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _create_evidence_table(ddb_resource: object) -> object:
    return ddb_resource.create_table(  # type: ignore[attr-defined]
        TableName=EVIDENCE_TABLE,
        KeySchema=[{"AttributeName": "evidenceId", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "evidenceId", "AttributeType": "S"},
            {"AttributeName": "claimId", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "claimId-index",
                "KeySchema": [{"AttributeName": "claimId", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _make_claim(
    claim_id: str = CLAIM_ID,
    claimant_id: str = CLAIMANT_ID,
    status: str = "CREATED",
) -> dict:
    return {
        "claimId": claim_id,
        "claimantId": claimant_id,
        "policyNumber": "POL-123456",
        "incidentDateTime": "2026-09-15T14:30:00Z",
        "incidentLocation": "123 Main St",
        "status": status,
        "createdAt": "2026-09-30T10:00:00Z",
        "updatedAt": "2026-09-30T10:00:00Z",
    }


def _make_evidence(
    evidence_id: str = EVIDENCE_ID,
    claim_id: str = CLAIM_ID,
    claimant_id: str = CLAIMANT_ID,
    status: str = "UPLOAD_PENDING",
) -> dict:
    return {
        "evidenceId": evidence_id,
        "claimId": claim_id,
        "claimantId": claimant_id,
        "evidenceType": "VIDEO",
        "fileName": "accident.mp4",
        "contentType": "video/mp4",
        "fileSizeBytes": 104857600,
        "storageKey": f"claims/{claim_id}/evidence/{evidence_id}/object",
        "status": status,
        "createdAt": "2026-09-30T10:01:00Z",
        "updatedAt": "2026-09-30T10:01:00Z",
    }


# ── ClaimRepository ───────────────────────────────────────────────────────────


class TestClaimRepository:
    @mock_aws
    def test_save_and_get_claim(self) -> None:
        """save() then get() round-trips the claim record."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_claims_table(resource)
        repo = ClaimRepository(CLAIMS_TABLE, REGION, _resource=resource)

        claim = _make_claim()
        repo.save(claim)

        result = repo.get(CLAIM_ID)
        assert result is not None
        assert result["claimId"] == CLAIM_ID
        assert result["claimantId"] == CLAIMANT_ID
        assert result["status"] == "CREATED"

    @mock_aws
    def test_get_returns_none_for_missing_claim(self) -> None:
        """get() returns None when the claimId does not exist."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_claims_table(resource)
        repo = ClaimRepository(CLAIMS_TABLE, REGION, _resource=resource)

        result = repo.get("CLM-does-not-exist")
        assert result is None

    @mock_aws
    def test_save_persists_all_fields(self) -> None:
        """save() stores every field in the claim record."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_claims_table(resource)
        repo = ClaimRepository(CLAIMS_TABLE, REGION, _resource=resource)

        claim = _make_claim()
        repo.save(claim)
        result = repo.get(CLAIM_ID)

        assert result is not None
        for key, value in claim.items():
            assert result[key] == value

    @mock_aws
    def test_update_status_conditional_applies_when_condition_matches(self) -> None:
        """Conditional update succeeds when current status matches expectation."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_claims_table(resource)
        repo = ClaimRepository(CLAIMS_TABLE, REGION, _resource=resource)

        repo.save(_make_claim(status="CREATED"))

        applied = repo.update_status_conditional(
            claim_id=CLAIM_ID,
            expected_current_status="CREATED",
            new_status="EVIDENCE_SUBMITTED",
            updated_at="2026-09-30T10:05:00Z",
        )

        assert applied is True
        result = repo.get(CLAIM_ID)
        assert result is not None
        assert result["status"] == "EVIDENCE_SUBMITTED"
        assert result["updatedAt"] == "2026-09-30T10:05:00Z"

    @mock_aws
    def test_update_status_conditional_returns_false_when_condition_fails(self) -> None:
        """Conditional update returns False (does not raise) when already transitioned."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_claims_table(resource)
        repo = ClaimRepository(CLAIMS_TABLE, REGION, _resource=resource)

        # Status is already EVIDENCE_SUBMITTED — a second concurrent request
        # expecting CREATED should be a no-op.
        repo.save(_make_claim(status="EVIDENCE_SUBMITTED"))

        applied = repo.update_status_conditional(
            claim_id=CLAIM_ID,
            expected_current_status="CREATED",
            new_status="EVIDENCE_SUBMITTED",
            updated_at="2026-09-30T10:06:00Z",
        )

        assert applied is False
        # Status must remain unchanged
        result = repo.get(CLAIM_ID)
        assert result is not None
        assert result["status"] == "EVIDENCE_SUBMITTED"

    @mock_aws
    def test_save_replaces_existing_claim(self) -> None:
        """A second save() with the same claimId replaces the record."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_claims_table(resource)
        repo = ClaimRepository(CLAIMS_TABLE, REGION, _resource=resource)

        repo.save(_make_claim(status="CREATED"))
        updated = {**_make_claim(), "status": "EVIDENCE_SUBMITTED"}
        repo.save(updated)

        result = repo.get(CLAIM_ID)
        assert result is not None
        assert result["status"] == "EVIDENCE_SUBMITTED"


# ── EvidenceRepository ────────────────────────────────────────────────────────


class TestEvidenceRepository:
    @mock_aws
    def test_save_and_get_evidence(self) -> None:
        """save() then get() round-trips the evidence record."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        evidence = _make_evidence()
        repo.save(evidence)

        result = repo.get(EVIDENCE_ID)
        assert result is not None
        assert result["evidenceId"] == EVIDENCE_ID
        assert result["claimId"] == CLAIM_ID
        assert result["status"] == "UPLOAD_PENDING"

    @mock_aws
    def test_get_returns_none_for_missing_evidence(self) -> None:
        """get() returns None when the evidenceId does not exist."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        result = repo.get("EVD-does-not-exist")
        assert result is None

    @mock_aws
    def test_list_by_claim_returns_all_evidence_for_claim(self) -> None:
        """list_by_claim() returns every evidence record for a claimId."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        evd1 = _make_evidence(evidence_id="EVD-aaa-111")
        evd2 = _make_evidence(evidence_id="EVD-bbb-222")
        # A different claim — must not appear in results
        evd_other = _make_evidence(evidence_id="EVD-ccc-333", claim_id="CLM-other-claim")

        repo.save(evd1)
        repo.save(evd2)
        repo.save(evd_other)

        results = repo.list_by_claim(CLAIM_ID)
        result_ids = {r["evidenceId"] for r in results}
        assert result_ids == {"EVD-aaa-111", "EVD-bbb-222"}

    @mock_aws
    def test_list_by_claim_returns_empty_list_when_no_evidence(self) -> None:
        """list_by_claim() returns [] when no records exist for the claimId."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        results = repo.list_by_claim(CLAIM_ID)
        assert results == []

    @mock_aws
    def test_count_completed_by_claim_counts_only_upload_complete(self) -> None:
        """count_completed_by_claim() counts only UPLOAD_COMPLETE items."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        repo.save(_make_evidence(evidence_id="EVD-111", status="UPLOAD_COMPLETE"))
        repo.save(_make_evidence(evidence_id="EVD-222", status="UPLOAD_COMPLETE"))
        repo.save(_make_evidence(evidence_id="EVD-333", status="UPLOAD_PENDING"))

        count = repo.count_completed_by_claim(CLAIM_ID)
        assert count == 2

    @mock_aws
    def test_count_completed_by_claim_returns_zero_when_none_complete(self) -> None:
        """count_completed_by_claim() returns 0 when all items are UPLOAD_PENDING."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        repo.save(_make_evidence(evidence_id="EVD-111", status="UPLOAD_PENDING"))
        repo.save(_make_evidence(evidence_id="EVD-222", status="UPLOAD_PENDING"))

        count = repo.count_completed_by_claim(CLAIM_ID)
        assert count == 0

    @mock_aws
    def test_update_status_conditional_applies_and_sets_uploaded_at(self) -> None:
        """Conditional update to UPLOAD_COMPLETE sets uploadedAt."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        repo.save(_make_evidence(status="UPLOAD_PENDING"))

        applied = repo.update_status_conditional(
            evidence_id=EVIDENCE_ID,
            expected_current_status="UPLOAD_PENDING",
            new_status="UPLOAD_COMPLETE",
            updated_at="2026-09-30T10:05:00Z",
            uploaded_at="2026-09-30T10:05:00Z",
        )

        assert applied is True
        result = repo.get(EVIDENCE_ID)
        assert result is not None
        assert result["status"] == "UPLOAD_COMPLETE"
        assert result["uploadedAt"] == "2026-09-30T10:05:00Z"
        assert result["updatedAt"] == "2026-09-30T10:05:00Z"

    @mock_aws
    def test_update_status_conditional_returns_false_when_condition_fails(self) -> None:
        """Concurrent confirmation: second call returns False without raising."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        # Already completed by an earlier request
        repo.save(_make_evidence(status="UPLOAD_COMPLETE"))

        applied = repo.update_status_conditional(
            evidence_id=EVIDENCE_ID,
            expected_current_status="UPLOAD_PENDING",
            new_status="UPLOAD_COMPLETE",
            updated_at="2026-09-30T10:06:00Z",
            uploaded_at="2026-09-30T10:06:00Z",
        )

        assert applied is False

    @mock_aws
    def test_update_status_conditional_without_uploaded_at(self) -> None:
        """update_status_conditional works when uploaded_at is None (non-complete transitions)."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        repo.save(_make_evidence(status="UPLOAD_PENDING"))

        applied = repo.update_status_conditional(
            evidence_id=EVIDENCE_ID,
            expected_current_status="UPLOAD_PENDING",
            new_status="UPLOAD_COMPLETE",
            updated_at="2026-09-30T10:05:00Z",
            uploaded_at=None,
        )

        assert applied is True
        result = repo.get(EVIDENCE_ID)
        assert result is not None
        assert result["status"] == "UPLOAD_COMPLETE"
        # uploadedAt should not have been set
        assert "uploadedAt" not in result

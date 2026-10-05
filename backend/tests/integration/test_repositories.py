"""
Integration tests — DynamoDB and S3 repository implementations.

Uses moto 5.2.3 ``mock_aws`` to simulate AWS services locally.
No real AWS credentials or real AWS resources are contacted.

Covers:
  DynamoDB — ClaimRepository
    1.  Table created with correct key schema and GSI.
    2.  Save and retrieve a claim by primary key.
    3.  get() returns None for a missing claimId.
    4.  Conditional status update succeeds when condition matches.
    5.  Conditional status update returns False (no write) when already changed.
    6.  Second save() with same PK replaces the record.

  DynamoDB — EvidenceRepository
    7.  Table created with correct key schema and GSI.
    8.  Save and retrieve evidence by primary key.
    9.  get() returns None for a missing evidenceId.
    10. list_by_claim() returns all evidence for a claimId via claimId-index GSI.
    11. list_by_claim() returns [] when no evidence exists for a claimId.
    12. count_completed_by_claim() counts only UPLOAD_COMPLETE items.
    13. Conditional evidence status update succeeds and sets uploadedAt.
    14. Conditional evidence update returns False when already transitioned.

  S3 — S3Repository
    15. Bucket created; presigned PUT URL is returned as a non-empty string.
    16. object_exists() returns True after a PUT to the bucket.
    17. object_exists() returns False for a key that was never uploaded.
    18. Storage key follows claims/{claimId}/evidence/{evidenceId}/object.
    19. Storage key does not contain the original filename.
"""

from __future__ import annotations

from typing import Any

import boto3
import pytest
from moto import mock_aws

from app.infrastructure.dynamodb import ClaimRepository, EvidenceRepository
from app.infrastructure.s3 import S3Repository, generate_storage_key

# ── Test constants ─────────────────────────────────────────────────────────────

REGION = "us-east-1"
CLAIMS_TABLE = "ClaimwiseClaims"
EVIDENCE_TABLE = "ClaimwiseEvidence"
BUCKET = "claimwise-evidence-test"

CLAIM_ID = "CLM-550e8400-e29b-41d4-a716-446655440000"
CLAIMANT_ID = "cognito-sub-integration-test"
EVIDENCE_ID = "EVD-7f3c9a12-4b2e-4a8f-9c1d-2e3f4a5b6c7d"
OTHER_CLAIM_ID = "CLM-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OTHER_EVIDENCE_ID = "EVD-11111111-2222-3333-4444-555555555555"
FIXED_TIME = "2026-09-30T10:00:00+00:00"
LATER_TIME = "2026-09-30T10:05:00+00:00"


# ── moto credential setup ─────────────────────────────────────────────────────
# moto requires *some* credentials to be set; these dummy values are sufficient.


@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set dummy AWS credentials so moto accepts boto3 calls."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


# ── DynamoDB table helpers ────────────────────────────────────────────────────


def _create_claims_table(resource: Any) -> None:
    """Create the ClaimwiseClaims table with its GSI."""
    resource.create_table(
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


def _create_evidence_table(resource: Any) -> None:
    """Create the ClaimwiseEvidence table with its claimId-index GSI."""
    resource.create_table(
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
) -> dict[str, Any]:
    return {
        "claimId": claim_id,
        "claimantId": claimant_id,
        "policyNumber": "POL-123456",
        "incidentDateTime": "2026-09-15T14:30:00Z",
        "incidentLocation": "123 Main St",
        "status": status,
        "createdAt": FIXED_TIME,
        "updatedAt": FIXED_TIME,
    }


def _make_evidence(
    evidence_id: str = EVIDENCE_ID,
    claim_id: str = CLAIM_ID,
    claimant_id: str = CLAIMANT_ID,
    status: str = "UPLOAD_PENDING",
) -> dict[str, Any]:
    return {
        "evidenceId": evidence_id,
        "claimId": claim_id,
        "claimantId": claimant_id,
        "evidenceType": "VIDEO",
        "originalFileName": "accident.mp4",
        "contentType": "video/mp4",
        "fileSizeBytes": 10_000_000,
        "storageKey": generate_storage_key(claim_id, evidence_id),
        "status": status,
        "createdAt": FIXED_TIME,
    }


# ── DynamoDB — ClaimRepository ────────────────────────────────────────────────


class TestClaimRepository:
    @mock_aws
    def test_table_created_with_correct_schema(self) -> None:
        """Scenario 1 — table is created with claimId PK and claimantId-index GSI."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_claims_table(resource)

        table = resource.Table(CLAIMS_TABLE)
        table.load()  # raises if table does not exist
        assert table.table_name == CLAIMS_TABLE

        gsi_names = [g["IndexName"] for g in (table.global_secondary_indexes or [])]
        assert "claimantId-index" in gsi_names

    @mock_aws
    def test_save_and_retrieve_claim(self) -> None:
        """Scenario 2 — save() then get() round-trips the full claim record."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_claims_table(resource)
        repo = ClaimRepository(CLAIMS_TABLE, REGION, _resource=resource)

        claim = _make_claim()
        repo.save(claim)

        result = repo.get(CLAIM_ID)
        assert result is not None
        assert result["claimId"] == CLAIM_ID
        assert result["claimantId"] == CLAIMANT_ID
        assert result["policyNumber"] == "POL-123456"
        assert result["status"] == "CREATED"

    @mock_aws
    def test_get_returns_none_for_missing_claim(self) -> None:
        """Scenario 3 — get() returns None when claimId does not exist."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_claims_table(resource)
        repo = ClaimRepository(CLAIMS_TABLE, REGION, _resource=resource)

        assert repo.get("CLM-does-not-exist") is None

    @mock_aws
    def test_conditional_update_succeeds_when_condition_matches(self) -> None:
        """Scenario 4 — conditional update transitions CREATED → EVIDENCE_SUBMITTED."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_claims_table(resource)
        repo = ClaimRepository(CLAIMS_TABLE, REGION, _resource=resource)

        repo.save(_make_claim(status="CREATED"))

        applied = repo.update_status_conditional(
            claim_id=CLAIM_ID,
            expected_current_status="CREATED",
            new_status="EVIDENCE_SUBMITTED",
            updated_at=LATER_TIME,
        )

        assert applied is True
        updated = repo.get(CLAIM_ID)
        assert updated is not None
        assert updated["status"] == "EVIDENCE_SUBMITTED"
        assert updated["updatedAt"] == LATER_TIME

    @mock_aws
    def test_conditional_update_returns_false_when_already_changed(self) -> None:
        """Scenario 5 — second conditional update returns False; record unchanged."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_claims_table(resource)
        repo = ClaimRepository(CLAIMS_TABLE, REGION, _resource=resource)

        repo.save(_make_claim(status="EVIDENCE_SUBMITTED"))

        # Condition expects CREATED but record is already EVIDENCE_SUBMITTED
        applied = repo.update_status_conditional(
            claim_id=CLAIM_ID,
            expected_current_status="CREATED",
            new_status="EVIDENCE_SUBMITTED",
            updated_at=LATER_TIME,
        )

        assert applied is False
        # Original record must be unchanged
        item = repo.get(CLAIM_ID)
        assert item is not None
        assert item["updatedAt"] == FIXED_TIME  # not the LATER_TIME

    @mock_aws
    def test_second_save_replaces_existing_record(self) -> None:
        """Scenario 6 — save() with same PK fully replaces the previous record."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_claims_table(resource)
        repo = ClaimRepository(CLAIMS_TABLE, REGION, _resource=resource)

        repo.save(_make_claim(status="CREATED"))
        repo.save({**_make_claim(), "status": "EVIDENCE_SUBMITTED", "updatedAt": LATER_TIME})

        result = repo.get(CLAIM_ID)
        assert result is not None
        assert result["status"] == "EVIDENCE_SUBMITTED"
        assert result["updatedAt"] == LATER_TIME


# ── DynamoDB — EvidenceRepository ────────────────────────────────────────────


class TestEvidenceRepository:
    @mock_aws
    def test_table_created_with_correct_schema(self) -> None:
        """Scenario 7 — table is created with evidenceId PK and claimId-index GSI."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)

        table = resource.Table(EVIDENCE_TABLE)
        table.load()
        assert table.table_name == EVIDENCE_TABLE

        gsi_names = [g["IndexName"] for g in (table.global_secondary_indexes or [])]
        assert "claimId-index" in gsi_names

    @mock_aws
    def test_save_and_retrieve_evidence(self) -> None:
        """Scenario 8 — save() then get() round-trips the full evidence record."""
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
        assert result["evidenceType"] == "VIDEO"

    @mock_aws
    def test_get_returns_none_for_missing_evidence(self) -> None:
        """Scenario 9 — get() returns None when evidenceId does not exist."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        assert repo.get("EVD-does-not-exist") is None

    @mock_aws
    def test_list_by_claim_returns_all_evidence_via_gsi(self) -> None:
        """Scenario 10 — list_by_claim() uses claimId-index to return all matching items."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        repo.save(_make_evidence(evidence_id="EVD-aaa-111"))
        repo.save(_make_evidence(evidence_id="EVD-bbb-222"))
        # Evidence belonging to a different claim — must not appear
        repo.save(_make_evidence(evidence_id="EVD-ccc-333", claim_id=OTHER_CLAIM_ID))

        results = repo.list_by_claim(CLAIM_ID)
        ids = {r["evidenceId"] for r in results}
        assert ids == {"EVD-aaa-111", "EVD-bbb-222"}

    @mock_aws
    def test_list_by_claim_returns_empty_list_when_none_exist(self) -> None:
        """Scenario 11 — list_by_claim() returns [] for a claimId with no evidence."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        assert repo.list_by_claim(CLAIM_ID) == []

    @mock_aws
    def test_count_completed_by_claim_counts_upload_complete_only(self) -> None:
        """Scenario 12 — count_completed_by_claim() ignores UPLOAD_PENDING items."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        repo.save(_make_evidence(evidence_id="EVD-111", status="UPLOAD_COMPLETE"))
        repo.save(_make_evidence(evidence_id="EVD-222", status="UPLOAD_COMPLETE"))
        repo.save(_make_evidence(evidence_id="EVD-333", status="UPLOAD_PENDING"))

        assert repo.count_completed_by_claim(CLAIM_ID) == 2

    @mock_aws
    def test_conditional_evidence_update_succeeds_and_sets_uploaded_at(self) -> None:
        """Scenario 13 — conditional update to UPLOAD_COMPLETE sets uploadedAt."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        repo.save(_make_evidence(status="UPLOAD_PENDING"))

        applied = repo.update_status_conditional(
            evidence_id=EVIDENCE_ID,
            expected_current_status="UPLOAD_PENDING",
            new_status="UPLOAD_COMPLETE",
            updated_at=LATER_TIME,
            uploaded_at=LATER_TIME,
        )

        assert applied is True
        result = repo.get(EVIDENCE_ID)
        assert result is not None
        assert result["status"] == "UPLOAD_COMPLETE"
        assert result["uploadedAt"] == LATER_TIME
        assert result["updatedAt"] == LATER_TIME

    @mock_aws
    def test_conditional_evidence_update_returns_false_when_already_complete(self) -> None:
        """Scenario 14 — second conditional update returns False; record unchanged."""
        resource = boto3.resource("dynamodb", region_name=REGION)
        _create_evidence_table(resource)
        repo = EvidenceRepository(EVIDENCE_TABLE, REGION, _resource=resource)

        # Pre-transition to UPLOAD_COMPLETE
        repo.save({**_make_evidence(), "status": "UPLOAD_COMPLETE", "uploadedAt": FIXED_TIME})

        applied = repo.update_status_conditional(
            evidence_id=EVIDENCE_ID,
            expected_current_status="UPLOAD_PENDING",  # condition will fail
            new_status="UPLOAD_COMPLETE",
            updated_at=LATER_TIME,
            uploaded_at=LATER_TIME,
        )

        assert applied is False
        # uploadedAt must still be the original value
        item = repo.get(EVIDENCE_ID)
        assert item is not None
        assert item["uploadedAt"] == FIXED_TIME


# ── S3 — S3Repository ─────────────────────────────────────────────────────────


class TestS3Repository:
    @mock_aws
    def test_presigned_put_url_is_returned(self) -> None:
        """Scenario 15 — generate_presigned_put_url() returns a non-empty URL string."""
        s3_client = boto3.client("s3", region_name=REGION)
        s3_client.create_bucket(Bucket=BUCKET)

        repo = S3Repository(BUCKET, REGION, _client=s3_client)
        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        url = repo.generate_presigned_put_url(key, "video/mp4", expiry_seconds=900)

        assert isinstance(url, str)
        assert len(url) > 0
        assert BUCKET in url

    @mock_aws
    def test_object_exists_returns_true_after_put(self) -> None:
        """Scenario 16 — object_exists() returns True after an object is uploaded."""
        s3_client = boto3.client("s3", region_name=REGION)
        s3_client.create_bucket(Bucket=BUCKET)

        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        s3_client.put_object(Bucket=BUCKET, Key=key, Body=b"fake-video-bytes")

        repo = S3Repository(BUCKET, REGION, _client=s3_client)
        assert repo.object_exists(key) is True

    @mock_aws
    def test_object_exists_returns_false_for_missing_key(self) -> None:
        """Scenario 17 — object_exists() returns False for a key never uploaded."""
        s3_client = boto3.client("s3", region_name=REGION)
        s3_client.create_bucket(Bucket=BUCKET)

        repo = S3Repository(BUCKET, REGION, _client=s3_client)
        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        assert repo.object_exists(key) is False

    @mock_aws
    def test_storage_key_follows_expected_pattern(self) -> None:
        """Scenario 18 — storage key is claims/{claimId}/evidence/{evidenceId}/object."""
        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        expected = f"claims/{CLAIM_ID}/evidence/{EVIDENCE_ID}/object"
        assert key == expected

        parts = key.split("/")
        assert parts[0] == "claims"
        assert parts[1] == CLAIM_ID
        assert parts[2] == "evidence"
        assert parts[3] == EVIDENCE_ID
        assert parts[4] == "object"
        assert len(parts) == 5

    @mock_aws
    def test_storage_key_does_not_contain_filename(self) -> None:
        """Scenario 19 — original filename is absent from the storage key."""
        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        filenames = ["accident.mp4", "../../etc/passwd", "policy.pdf", "audio description.mp3"]
        for filename in filenames:
            assert filename not in key, f"Filename {filename!r} leaked into key: {key}"

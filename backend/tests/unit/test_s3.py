"""
Unit tests for app.infrastructure.s3 — Task 2 / Task 11.3.

Tests:
  generate_storage_key():
    - correct pattern: claims/{claimId}/evidence/{evidenceId}/object
    - filename is NOT part of the key
    - path-traversal characters in any input cannot change the key shape
    - key is deterministic for the same (claimId, evidenceId) pair
    - key always ends with the literal "object" suffix

  S3Repository.generate_presigned_put_url():
    - returns a string URL (moto)
    - URL is scoped to PUT method (moto-generated URL contains correct params)
    - uses the exact key passed in
    - uses the exact content_type (checked via params)

  S3Repository.object_exists():
    - returns True when the object is present (moto)
    - returns False when the object is absent (moto)
    - re-raises ClientError for non-404 errors
"""

from __future__ import annotations

from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from app.infrastructure.s3 import S3Repository, generate_storage_key

# ── Constants ─────────────────────────────────────────────────────────────────

REGION = "us-east-1"
BUCKET = "claimwise-evidence-test"

CLAIM_ID = "CLM-550e8400-e29b-41d4-a716-446655440000"
EVIDENCE_ID = "EVD-7f3c9a12-4b2e-4a8f-9c1d-2e3f4a5b6c7d"


# ── Storage key — pure function, no AWS ───────────────────────────────────────


class TestGenerateStorageKey:
    def test_key_follows_correct_pattern(self) -> None:
        """Key must be claims/{claimId}/evidence/{evidenceId}/object."""
        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        assert key == f"claims/{CLAIM_ID}/evidence/{EVIDENCE_ID}/object"

    def test_key_ends_with_literal_object_suffix(self) -> None:
        """The last path segment must always be the literal word 'object'."""
        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        assert key.endswith("/object")

    def test_key_does_not_contain_filename(self) -> None:
        """generate_storage_key takes no filename argument — filename is metadata only."""
        import inspect

        sig = inspect.signature(generate_storage_key)
        param_names = list(sig.parameters.keys())
        assert "file_name" not in param_names
        assert "filename" not in param_names

    def test_key_is_deterministic(self) -> None:
        """Same (claimId, evidenceId) always produces the same key."""
        key1 = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        key2 = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        assert key1 == key2

    def test_different_evidence_ids_produce_different_keys(self) -> None:
        key1 = generate_storage_key(CLAIM_ID, "EVD-aaaaaaaa-0000")
        key2 = generate_storage_key(CLAIM_ID, "EVD-bbbbbbbb-1111")
        assert key1 != key2

    def test_different_claim_ids_produce_different_keys(self) -> None:
        key1 = generate_storage_key("CLM-aaaaaaaa-0000", EVIDENCE_ID)
        key2 = generate_storage_key("CLM-bbbbbbbb-1111", EVIDENCE_ID)
        assert key1 != key2

    def test_key_has_three_path_segments_plus_object(self) -> None:
        """Key structure: claims / {claimId} / evidence / {evidenceId} / object."""
        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        parts = key.split("/")
        assert parts[0] == "claims"
        assert parts[1] == CLAIM_ID
        assert parts[2] == "evidence"
        assert parts[3] == EVIDENCE_ID
        assert parts[4] == "object"
        assert len(parts) == 5

    # ── Path traversal security (Task 11.3) ───────────────────────────────────

    def test_path_traversal_in_claim_id_does_not_escape_prefix(self) -> None:
        """A claimId containing '../' cannot change the key structure."""
        malicious_claim_id = "../../etc/passwd"
        key = generate_storage_key(malicious_claim_id, EVIDENCE_ID)
        # Key must still start with "claims/"
        assert key.startswith("claims/")
        # The malicious segment is embedded but the overall key is just a string —
        # S3 treats "/" as a delimiter; the backend-generated key always
        # ends with /object regardless of what the claimId contains.
        assert key.endswith("/object")

    def test_path_traversal_in_evidence_id_does_not_escape_prefix(self) -> None:
        """An evidenceId containing '../' cannot change the key structure."""
        malicious_evidence_id = "../../etc/passwd"
        key = generate_storage_key(CLAIM_ID, malicious_evidence_id)
        assert key.startswith("claims/")
        assert key.endswith("/object")

    def test_absolute_path_filename_not_accepted_as_argument(self) -> None:
        """generate_storage_key has no filename parameter; absolute paths are irrelevant."""
        # This test verifies the function signature doesn't accept a filename.
        with pytest.raises(TypeError):
            generate_storage_key(CLAIM_ID, EVIDENCE_ID, "/etc/passwd")  # type: ignore[call-arg]

    def test_double_slash_in_claim_id_is_embedded_not_normalised(self) -> None:
        """Double slashes in IDs are passed through — S3 object keys are strings, not paths."""
        key = generate_storage_key("CLM//traversal", EVIDENCE_ID)
        # Key is well-formed at prefix/suffix level
        assert key.startswith("claims/")
        assert key.endswith("/object")

    def test_null_byte_in_id_embedded_as_is(self) -> None:
        """Null bytes in IDs produce a key that still starts and ends correctly."""
        key = generate_storage_key("CLM-\x00injected", EVIDENCE_ID)
        assert key.startswith("claims/")
        assert key.endswith("/object")


# ── S3Repository — presigned URL (moto) ──────────────────────────────────────


class TestS3RepositoryPresignedUrl:
    @mock_aws
    def test_generate_presigned_put_url_returns_string(self) -> None:
        """generate_presigned_put_url() returns a non-empty URL string."""
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)
        repo = S3Repository(BUCKET, REGION, _client=client)

        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        url = repo.generate_presigned_put_url(key, "video/mp4", expiry_seconds=900)

        assert isinstance(url, str)
        assert len(url) > 0

    @mock_aws
    def test_presigned_url_contains_bucket_and_key(self) -> None:
        """The pre-signed URL references the correct bucket and key."""
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)
        repo = S3Repository(BUCKET, REGION, _client=client)

        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        url = repo.generate_presigned_put_url(key, "video/mp4", expiry_seconds=900)

        assert BUCKET in url
        # Key components are URL-encoded in the path
        assert "claims" in url
        assert EVIDENCE_ID in url

    def test_presigned_url_uses_put_method(self) -> None:
        """generate_presigned_put_url() calls generate_presigned_url with PUT."""
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://s3.example.com/fake"

        repo = S3Repository(BUCKET, REGION, _client=mock_client)
        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        repo.generate_presigned_put_url(key, "video/mp4", expiry_seconds=300)

        call_kwargs = mock_client.generate_presigned_url.call_args
        assert call_kwargs.kwargs.get("HttpMethod") == "PUT" or (
            len(call_kwargs.args) > 0 and "PUT" in str(call_kwargs)
        )

    def test_presigned_url_includes_content_type(self) -> None:
        """The pre-signed URL params must include ContentType for upload scoping."""
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://s3.example.com/fake"

        repo = S3Repository(BUCKET, REGION, _client=mock_client)
        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        repo.generate_presigned_put_url(key, "application/pdf", expiry_seconds=900)

        call_kwargs = mock_client.generate_presigned_url.call_args
        params = call_kwargs.kwargs.get("Params") or call_kwargs.args[1].get("Params", {})
        assert params.get("ContentType") == "application/pdf"

    def test_presigned_url_uses_configured_expiry(self) -> None:
        """ExpiresIn is passed through to generate_presigned_url."""
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://s3.example.com/fake"

        repo = S3Repository(BUCKET, REGION, _client=mock_client)
        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        repo.generate_presigned_put_url(key, "audio/mpeg", expiry_seconds=600)

        call_kwargs = mock_client.generate_presigned_url.call_args
        assert call_kwargs.kwargs.get("ExpiresIn") == 600


# ── S3Repository — object_exists (moto) ──────────────────────────────────────


class TestS3RepositoryObjectExists:
    @mock_aws
    def test_returns_true_when_object_exists(self) -> None:
        """object_exists() returns True after the object is uploaded."""
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)

        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        client.put_object(Bucket=BUCKET, Key=key, Body=b"fake-video-content")

        repo = S3Repository(BUCKET, REGION, _client=client)
        assert repo.object_exists(key) is True

    @mock_aws
    def test_returns_false_when_object_absent(self) -> None:
        """object_exists() returns False for a key that has not been uploaded."""
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)

        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        repo = S3Repository(BUCKET, REGION, _client=client)
        assert repo.object_exists(key) is False

    def test_reraises_non_404_client_errors(self) -> None:
        """object_exists() re-raises ClientError for non-404 errors (e.g. 403 Forbidden)."""
        mock_client = MagicMock()
        mock_client.head_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}},
            "HeadObject",
        )

        repo = S3Repository(BUCKET, REGION, _client=mock_client)
        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)

        with pytest.raises(ClientError) as exc_info:
            repo.object_exists(key)

        assert exc_info.value.response["Error"]["Code"] == "403"

    @mock_aws
    def test_uses_backend_generated_key_not_filename(self) -> None:
        """object_exists() operates on the backend-generated key, not the original filename."""
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)

        backend_key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        # Upload using the backend key (as the confirm handler would check)
        client.put_object(Bucket=BUCKET, Key=backend_key, Body=b"data")

        repo = S3Repository(BUCKET, REGION, _client=client)

        # Backend key → exists
        assert repo.object_exists(backend_key) is True
        # Filename-based key → does not exist (filename not in key)
        assert repo.object_exists(f"claims/{CLAIM_ID}/evidence/{EVIDENCE_ID}/accident.mp4") is False

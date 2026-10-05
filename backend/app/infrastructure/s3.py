"""
Infrastructure — S3 storage.

Provides:
  - generate_storage_key()      — deterministic backend-controlled S3 object key
  - S3Repository                — presigned URL generation and object-existence check

Storage key design (from design.md and Task 9):
  claims/{claimId}/evidence/{evidenceId}/object

The literal suffix "object" is fixed.  The original filename is stored only
as evidence metadata in DynamoDB and is NEVER part of the S3 key.  This means:
  - The client cannot influence the storage location.
  - Path-traversal characters in a filename cannot affect the key.
  - The key is deterministic for a given (claimId, evidenceId) pair.
"""

from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# The fixed suffix used for every evidence object in S3.
_OBJECT_SUFFIX = "object"


# ── Storage key ───────────────────────────────────────────────────────────────


def generate_storage_key(claim_id: str, evidence_id: str) -> str:
    """Return the backend-controlled S3 object key for an evidence file.

    Pattern: ``claims/{claimId}/evidence/{evidenceId}/object``

    The original filename is intentionally excluded from the key.  It is
    preserved only as metadata in DynamoDB (the ``fileName`` attribute).

    Parameters
    ----------
    claim_id:
        The ``CLM-<UUID>`` identifier of the parent claim.
    evidence_id:
        The ``EVD-<UUID>`` identifier of the evidence record.

    Returns
    -------
    str
        A deterministic, client-independent S3 object key.

    Examples
    --------
    >>> generate_storage_key(
    ...     "CLM-550e8400-e29b-41d4-a716-446655440000",
    ...     "EVD-7f3c9a12-4b2e-4a8f-9c1d-2e3f4a5b6c7d",
    ... )
    'claims/CLM-550e8400-e29b-41d4-a716-446655440000/evidence/EVD-7f3c9a12-4b2e-4a8f-9c1d-2e3f4a5b6c7d/object'
    """
    return f"claims/{claim_id}/evidence/{evidence_id}/{_OBJECT_SUFFIX}"


# ── Client factory ────────────────────────────────────────────────────────────


def _get_s3_client(region: str) -> Any:
    """Return a boto3 S3 client for the given region.

    Extracted as a module-level function so tests can patch it cleanly.
    """
    return boto3.client("s3", region_name=region)


# ── S3 Repository ─────────────────────────────────────────────────────────────


class S3Repository:
    """S3 operations for evidence file storage.

    Parameters
    ----------
    bucket_name:
        Name of the private S3 bucket (from ``AppConfig.evidence_bucket_name``).
    region:
        AWS region string (from ``AppConfig.aws_region``).
    _client:
        Optional pre-built S3 client — used by tests to inject a moto-mocked
        client without monkey-patching boto3 globally.
    """

    def __init__(
        self,
        bucket_name: str,
        region: str,
        _client: Any = None,
    ) -> None:
        self._bucket = bucket_name
        self._region = region
        self._client = _client or _get_s3_client(region)

    def generate_presigned_put_url(
        self,
        key: str,
        content_type: str,
        expiry_seconds: int,
    ) -> str:
        """Generate a time-limited pre-signed S3 PUT URL for a specific key.

        The URL is scoped to:
          - HTTP method: ``PUT``
          - Specific object key (``key``)
          - Specific ``Content-Type`` header — the client must supply this
            header when uploading; any other value will be rejected by S3.

        No AWS credentials are embedded in the URL.  Access is controlled
        entirely by the time-limited signature.

        Parameters
        ----------
        key:
            The backend-generated S3 object key (from ``generate_storage_key``).
        content_type:
            The validated MIME type of the evidence file (e.g. ``video/mp4``).
        expiry_seconds:
            Number of seconds until the URL expires
            (from ``AppConfig.presigned_url_expiry_seconds``).

        Returns
        -------
        str
            A pre-signed HTTPS URL the client can use for a single PUT upload.
        """
        url: str = self._client.generate_presigned_url(
            ClientMethod="put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=expiry_seconds,
            HttpMethod="PUT",
        )
        # Log key and bucket name are intentionally omitted from info logs to
        # avoid leaking storage paths; log only non-sensitive identifiers when
        # calling code passes them in.
        logger.debug("Generated presigned PUT URL for key=%s expiry=%ds", key, expiry_seconds)
        return url

    def object_exists(self, key: str) -> bool:
        """Check whether an S3 object exists without downloading it.

        Uses ``head_object`` — a lightweight metadata-only request.

        Parameters
        ----------
        key:
            The S3 object key to probe.

        Returns
        -------
        bool
            ``True`` if the object exists and is accessible, ``False`` if it
            does not exist (HTTP 404).

        Raises
        ------
        ClientError
            Re-raised for any S3 error other than a 404 (e.g. permission
            errors, network issues).
        """
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code in ("404", "NoSuchKey"):
                return False
            # Permission errors, throttling, etc. bubble up so callers can
            # handle them appropriately rather than silently reporting "missing".
            raise

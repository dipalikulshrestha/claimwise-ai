"""
Infrastructure — DynamoDB persistence.

Provides two thin repository classes:
  - ClaimRepository   — operations on the ClaimwiseClaims table
  - EvidenceRepository — operations on the ClaimwiseEvidence table

No business logic lives here.  All domain decisions (what to store, whether
an item is authorised, status-transition rules) belong in the domain or
service layers.

DynamoDB table designs (from design.md):
  ClaimwiseClaims
    PK: claimId (String)
    GSI: claimantId-index  PK: claimantId

  ClaimwiseEvidence
    PK: evidenceId (String)
    GSI: claimId-index  PK: claimId
"""

from __future__ import annotations

import logging
from typing import Any, cast

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Type aliases for clarity
Item = dict[str, Any]


# ── Client factory ────────────────────────────────────────────────────────────


def _get_dynamodb_resource(region: str) -> Any:
    """Return a DynamoDB service resource for the given region.

    Extracted as a module-level function so tests can patch it cleanly.
    """
    return boto3.resource("dynamodb", region_name=region)


# ── Claim Repository ──────────────────────────────────────────────────────────


class ClaimRepository:
    """Persistence operations for claim records.

    Parameters
    ----------
    table_name:
        Name of the DynamoDB table (from ``AppConfig.claims_table_name``).
    region:
        AWS region string (from ``AppConfig.aws_region``).
    _resource:
        Optional pre-built DynamoDB resource — used by tests to inject a
        moto-mocked resource without monkey-patching boto3 globally.
    """

    def __init__(
        self,
        table_name: str,
        region: str,
        _resource: Any = None,
    ) -> None:
        resource = _resource or _get_dynamodb_resource(region)
        self._table = resource.Table(table_name)

    def save(self, item: Item) -> None:
        """Persist a claim record (create or full replace).

        Parameters
        ----------
        item:
            Complete claim dict ready for DynamoDB storage.
        """
        self._table.put_item(Item=item)
        logger.info("Saved claim claimId=%s", item.get("claimId"))

    def get(self, claim_id: str) -> Item | None:
        """Fetch a single claim by its primary key.

        Returns ``None`` if the item does not exist.
        """
        response = self._table.get_item(Key={"claimId": claim_id})
        return cast("Item | None", response.get("Item"))

    def update_status_conditional(
        self,
        claim_id: str,
        expected_current_status: str,
        new_status: str,
        updated_at: str,
    ) -> bool:
        """Conditionally update a claim's status field.

        The update succeeds only when the current ``status`` in DynamoDB
        matches ``expected_current_status``.  This prevents lost updates when
        multiple concurrent confirmations race to transition the same claim.

        Returns
        -------
        bool
            ``True`` if the update was applied, ``False`` if the condition
            failed (i.e. the status had already been changed by another
            request).

        Raises
        ------
        ClientError
            Re-raised for any DynamoDB error other than a condition failure.
        """
        try:
            self._table.update_item(
                Key={"claimId": claim_id},
                UpdateExpression="SET #s = :new_status, updatedAt = :updated_at",
                ConditionExpression="#s = :expected_status",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":new_status": new_status,
                    ":expected_status": expected_current_status,
                    ":updated_at": updated_at,
                },
            )
            logger.info(
                "Updated claim status claimId=%s %s -> %s",
                claim_id,
                expected_current_status,
                new_status,
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                logger.info(
                    "Claim status conditional update skipped (already transitioned) claimId=%s",
                    claim_id,
                )
                return False
            raise


# ── Evidence Repository ───────────────────────────────────────────────────────


class EvidenceRepository:
    """Persistence operations for evidence records.

    Parameters
    ----------
    table_name:
        Name of the DynamoDB table (from ``AppConfig.evidence_table_name``).
    region:
        AWS region string (from ``AppConfig.aws_region``).
    _resource:
        Optional pre-built DynamoDB resource — used by tests to inject a
        moto-mocked resource without monkey-patching boto3 globally.
    """

    def __init__(
        self,
        table_name: str,
        region: str,
        _resource: Any = None,
    ) -> None:
        resource = _resource or _get_dynamodb_resource(region)
        self._table = resource.Table(table_name)

    def save(self, item: Item) -> None:
        """Persist an evidence record (create or full replace).

        Parameters
        ----------
        item:
            Complete evidence dict ready for DynamoDB storage.
        """
        self._table.put_item(Item=item)
        logger.info("Saved evidence evidenceId=%s", item.get("evidenceId"))

    def get(self, evidence_id: str) -> Item | None:
        """Fetch a single evidence record by its primary key.

        Returns ``None`` if the item does not exist.
        """
        response = self._table.get_item(Key={"evidenceId": evidence_id})
        return cast("Item | None", response.get("Item"))

    def list_by_claim(self, claim_id: str) -> list[Item]:
        """Return all evidence records associated with a given claim.

        Uses the ``claimId-index`` GSI.
        """
        response = self._table.query(
            IndexName="claimId-index",
            KeyConditionExpression=Key("claimId").eq(claim_id),
        )
        return cast("list[Item]", response.get("Items", []))

    def count_completed_by_claim(self, claim_id: str) -> int:
        """Return the number of UPLOAD_COMPLETE evidence records for a claim.

        Used by the service layer to decide whether to transition the parent
        claim status to EVIDENCE_SUBMITTED.
        """
        items = self.list_by_claim(claim_id)
        return sum(1 for item in items if item.get("status") == "UPLOAD_COMPLETE")

    def update_status_conditional(
        self,
        evidence_id: str,
        expected_current_status: str,
        new_status: str,
        updated_at: str,
        uploaded_at: str | None = None,
    ) -> bool:
        """Conditionally update an evidence record's status.

        The update is applied only when the current ``status`` matches
        ``expected_current_status``.  Concurrent confirmation calls for the
        same evidence item are therefore safe — only one will make the
        transition.

        Parameters
        ----------
        evidence_id:
            Primary key of the evidence record.
        expected_current_status:
            The status value that must be present for the update to proceed.
        new_status:
            The status value to write.
        updated_at:
            ISO 8601 UTC timestamp to write into ``updatedAt``.
        uploaded_at:
            ISO 8601 UTC timestamp to write into ``uploadedAt`` (set when
            transitioning to UPLOAD_COMPLETE).

        Returns
        -------
        bool
            ``True`` if the update was applied, ``False`` if the condition
            failed.
        """
        update_expr = "SET #s = :new_status, updatedAt = :updated_at"
        expr_values: dict[str, Any] = {
            ":new_status": new_status,
            ":expected_status": expected_current_status,
            ":updated_at": updated_at,
        }

        if uploaded_at is not None:
            update_expr += ", uploadedAt = :uploaded_at"
            expr_values[":uploaded_at"] = uploaded_at

        try:
            self._table.update_item(
                Key={"evidenceId": evidence_id},
                UpdateExpression=update_expr,
                ConditionExpression="#s = :expected_status",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues=expr_values,
            )
            logger.info(
                "Updated evidence status evidenceId=%s %s -> %s",
                evidence_id,
                expected_current_status,
                new_status,
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                logger.info(
                    "Evidence status conditional update skipped (already transitioned) "
                    "evidenceId=%s",
                    evidence_id,
                )
                return False
            raise

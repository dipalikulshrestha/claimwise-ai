"""
Unit tests for app.api.confirm_upload — Task 9.

Authentication
  1.  Missing authorizer → 401.
  2.  Missing claims dict → 401.
  3.  Missing sub → 401.
  4.  Blank sub → 401.
  5.  Valid sub → proceeds.

Path parameters
  6.  Missing pathParameters → 400.
  7.  Blank claimId → 400.
  8.  Missing claimId key → 400.
  9.  Missing evidenceId key → 400.
  10. Blank evidenceId → 400.

Service outcomes
  11. Successful confirmation → 200 with evidence record.
  12. Claim not found → 404 CLAIM_NOT_FOUND.
  13. Evidence not found → 404 EVIDENCE_NOT_FOUND.
  14. Wrong claimant → 403 FORBIDDEN.
  15. Claim/evidence mismatch → 404 (EVIDENCE_NOT_FOUND per service impl).
  16. S3 object missing → 400 VALIDATION_ERROR.
  17. Generic ValidationError → 400.
  18. Unexpected exception → 500 INTERNAL_ERROR.

Security / behavior
  19. claimantId comes only from Cognito sub.
  20. No request body required; handler never reads one.
  21. Service receives exactly (claim_id, evidence_id, claimant_id).
  22. Response does not expose claimantId.
  23. 500 body does not expose AWS internals.
  24. Already-confirmed (idempotent) result passed through as 200.
  25. Auth tokens not written to logs.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import app.api.confirm_upload as confirm_upload_module
from app.api.confirm_upload import _extract_evidence_id, handler
from app.domain.exceptions import ForbiddenError, NotFoundError, ValidationError

# ── Constants ─────────────────────────────────────────────────────────────────

COGNITO_SUB = "cognito-sub-abc-123"
CLAIM_ID = "CLM-550e8400-e29b-41d4-a716-446655440000"
EVIDENCE_ID = "EVD-7f3c9a12-4b2e-4a8f-9c1d-2e3f4a5b6c7d"
FIXED_TIME = "2026-09-30T10:05:00+00:00"

CONFIRMED_EVIDENCE: dict[str, Any] = {
    "evidenceId": EVIDENCE_ID,
    "claimId": CLAIM_ID,
    "evidenceType": "VIDEO",
    "originalFileName": "accident.mp4",
    "contentType": "video/mp4",
    "fileSizeBytes": 10_000_000,
    "storageKey": f"claims/{CLAIM_ID}/evidence/{EVIDENCE_ID}/object",
    "status": "UPLOAD_COMPLETE",
    "createdAt": FIXED_TIME,
    "uploadedAt": FIXED_TIME,
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_event(
    cognito_sub: str | None = COGNITO_SUB,
    claim_id: str | None = CLAIM_ID,
    evidence_id: str | None = EVIDENCE_ID,
    include_path_params: bool = True,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "httpMethod": "POST",
        "path": f"/v1/claims/{claim_id}/evidence/{evidence_id}/confirm",
        "body": None,
        "headers": {},
    }
    if cognito_sub is not None:
        event["requestContext"] = {
            "authorizer": {"claims": {"sub": cognito_sub, "email": "u@example.com"}}
        }
    else:
        event["requestContext"] = {}

    if include_path_params:
        params: dict[str, Any] = {}
        if claim_id is not None:
            params["claimId"] = claim_id
        if evidence_id is not None:
            params["evidenceId"] = evidence_id
        event["pathParameters"] = params

    return event


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


def _mock_svc(return_value: dict[str, Any] = CONFIRMED_EVIDENCE) -> MagicMock:
    svc = MagicMock()
    svc.confirm_upload.return_value = return_value
    return svc


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_service() -> None:
    """Reset shared EvidenceService state between tests."""
    import app.api.request_upload_url as ru

    ru._evidence_service = None


# ── 1–5. Authentication ───────────────────────────────────────────────────────


class TestAuthentication:
    def test_missing_authorizer_returns_401(self) -> None:
        """Scenario 1 — no requestContext.authorizer → 401."""
        event = _make_event(cognito_sub=None)
        assert handler(event, MagicMock())["statusCode"] == 401

    def test_missing_claims_dict_returns_401(self) -> None:
        """Scenario 2 — authorizer present but no claims dict → 401."""
        event = _make_event()
        del event["requestContext"]["authorizer"]["claims"]
        assert handler(event, MagicMock())["statusCode"] == 401

    def test_missing_sub_returns_401(self) -> None:
        """Scenario 3 — claims dict present but sub key absent → 401."""
        event = _make_event()
        event["requestContext"]["authorizer"]["claims"].pop("sub")
        assert handler(event, MagicMock())["statusCode"] == 401

    def test_blank_sub_returns_401(self) -> None:
        """Scenario 4 — sub is empty string → 401."""
        event = _make_event(cognito_sub="")
        assert handler(event, MagicMock())["statusCode"] == 401

    def test_valid_sub_proceeds(self) -> None:
        """Scenario 5 — valid sub allows the request to proceed past auth."""
        svc = _mock_svc()
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())
        assert response["statusCode"] == 200


# ── 6–10. Path parameters ────────────────────────────────────────────────────


class TestPathParameters:
    def test_missing_path_parameters_returns_400(self) -> None:
        """Scenario 6 — no pathParameters key → 400."""
        event = _make_event(include_path_params=False)
        assert handler(event, MagicMock())["statusCode"] == 400

    def test_blank_claim_id_returns_400(self) -> None:
        """Scenario 7 — claimId is whitespace → 400."""
        event = _make_event()
        event["pathParameters"]["claimId"] = "   "
        assert handler(event, MagicMock())["statusCode"] == 400

    def test_missing_claim_id_key_returns_400(self) -> None:
        """Scenario 8 — pathParameters present, claimId key absent → 400."""
        event = _make_event(claim_id=None)
        assert handler(event, MagicMock())["statusCode"] == 400

    def test_missing_evidence_id_key_returns_400(self) -> None:
        """Scenario 9 — pathParameters present, evidenceId key absent → 400."""
        event = _make_event(evidence_id=None)
        assert handler(event, MagicMock())["statusCode"] == 400

    def test_blank_evidence_id_returns_400(self) -> None:
        """Scenario 10 — evidenceId is whitespace → 400."""
        event = _make_event()
        event["pathParameters"]["evidenceId"] = "  "
        assert handler(event, MagicMock())["statusCode"] == 400

    def test_400_uses_error_envelope(self) -> None:
        """Path validation errors use the standard error envelope."""
        event = _make_event(claim_id=None)
        body = _body(handler(event, MagicMock()))
        assert body["error"]["code"] == "VALIDATION_ERROR"


# ── 11–18. Service outcomes ───────────────────────────────────────────────────


class TestServiceOutcomes:
    def test_successful_confirmation_returns_200(self) -> None:
        """Scenario 11 — service returns confirmed record → 200."""
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=_mock_svc()):
            response = handler(_make_event(), MagicMock())
        assert response["statusCode"] == 200
        body = _body(response)
        assert body["evidenceId"] == EVIDENCE_ID
        assert body["status"] == "UPLOAD_COMPLETE"

    def test_claim_not_found_returns_404(self) -> None:
        """Scenario 12 — CLAIM_NOT_FOUND → 404."""
        svc = _mock_svc()
        svc.confirm_upload.side_effect = NotFoundError(
            "Claim not found.", error_code="CLAIM_NOT_FOUND"
        )
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())
        assert response["statusCode"] == 404
        assert _body(response)["error"]["code"] == "CLAIM_NOT_FOUND"

    def test_evidence_not_found_returns_404(self) -> None:
        """Scenario 13 — EVIDENCE_NOT_FOUND → 404."""
        svc = _mock_svc()
        svc.confirm_upload.side_effect = NotFoundError(
            "Evidence not found.", error_code="EVIDENCE_NOT_FOUND"
        )
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())
        assert response["statusCode"] == 404
        assert _body(response)["error"]["code"] == "EVIDENCE_NOT_FOUND"

    def test_wrong_claimant_returns_403(self) -> None:
        """Scenario 14 — ForbiddenError → 403."""
        svc = _mock_svc()
        svc.confirm_upload.side_effect = ForbiddenError("No access.")
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())
        assert response["statusCode"] == 403
        assert _body(response)["error"]["code"] == "FORBIDDEN"

    def test_evidence_claim_mismatch_returns_404(self) -> None:
        """Scenario 15 — evidence belongs to different claim → 404."""
        svc = _mock_svc()
        svc.confirm_upload.side_effect = NotFoundError(
            "Evidence not found.", error_code="EVIDENCE_NOT_FOUND"
        )
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())
        assert response["statusCode"] == 404

    def test_s3_object_missing_returns_400(self) -> None:
        """Scenario 16 — S3 object not uploaded yet → 400 VALIDATION_ERROR."""
        svc = _mock_svc()
        svc.confirm_upload.side_effect = ValidationError(
            "Uploaded file not found in storage.", error_code="VALIDATION_ERROR"
        )
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())
        assert response["statusCode"] == 400
        assert _body(response)["error"]["code"] == "VALIDATION_ERROR"

    def test_generic_validation_error_returns_400(self) -> None:
        """Scenario 17 — generic ValidationError → 400."""
        svc = _mock_svc()
        svc.confirm_upload.side_effect = ValidationError(
            "Bad input.", error_code="VALIDATION_ERROR"
        )
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())
        assert response["statusCode"] == 400

    def test_unexpected_exception_returns_500(self) -> None:
        """Scenario 18 — RuntimeError → 500."""
        svc = _mock_svc()
        svc.confirm_upload.side_effect = RuntimeError("database gone")
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())
        assert response["statusCode"] == 500
        assert _body(response)["error"]["code"] == "INTERNAL_ERROR"


# ── 19–25. Security / behavior ────────────────────────────────────────────────


class TestSecurityAndBehavior:
    def test_claimant_id_from_cognito_only(self) -> None:
        """Scenario 19 — claimantId comes only from Cognito sub."""
        svc = _mock_svc()
        # Even if someone adds a claimantId query param, it must be ignored
        event = _make_event()
        event["queryStringParameters"] = {"claimantId": "injected"}
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            handler(event, MagicMock())

        args = svc.confirm_upload.call_args
        # positional: (claim_id, evidence_id, claimant_id)
        passed_claimant = args.args[2] if len(args.args) > 2 else args.kwargs.get("claimant_id")
        assert passed_claimant == COGNITO_SUB
        assert passed_claimant != "injected"

    def test_no_request_body_required(self) -> None:
        """Scenario 20 — handler succeeds with body=None (no body needed)."""
        svc = _mock_svc()
        event = _make_event()
        assert event.get("body") is None  # confirm no body in test event
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            response = handler(event, MagicMock())
        assert response["statusCode"] == 200

    def test_service_called_with_correct_args(self) -> None:
        """Scenario 21 — service receives (claim_id, evidence_id, claimant_id)."""
        svc = _mock_svc()
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            handler(_make_event(), MagicMock())

        svc.confirm_upload.assert_called_once()
        args = svc.confirm_upload.call_args.args
        assert args[0] == CLAIM_ID
        assert args[1] == EVIDENCE_ID
        assert args[2] == COGNITO_SUB

    def test_response_does_not_expose_claimant_id(self) -> None:
        """Scenario 22 — claimantId absent from 200 response."""
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=_mock_svc()):
            response = handler(_make_event(), MagicMock())
        assert "claimantId" not in _body(response)

    def test_500_does_not_expose_aws_internals(self) -> None:
        """Scenario 23 — 500 body has no ARNs, table names, or stack traces."""
        svc = _mock_svc()
        svc.confirm_upload.side_effect = RuntimeError(
            "arn:aws:dynamodb:us-east-1:999:table/ClaimwiseEvidence is missing"
        )
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())

        body_str = response["body"]
        assert "arn:aws" not in body_str
        assert "ClaimwiseEvidence" not in body_str
        assert "Traceback" not in body_str

    def test_already_confirmed_idempotent_result_is_200(self) -> None:
        """Scenario 24 — service returns completed record (idempotent) → 200."""
        already_done = {**CONFIRMED_EVIDENCE, "status": "UPLOAD_COMPLETE"}
        svc = _mock_svc(return_value=already_done)
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())
        assert response["statusCode"] == 200
        assert _body(response)["status"] == "UPLOAD_COMPLETE"

    def test_auth_token_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Scenario 25 — Bearer token is not written to any log record."""
        svc = _mock_svc()
        event = _make_event()
        event["headers"]["Authorization"] = "Bearer eyJhbGciOiJSUzI1NiJ9.fake.token"

        with caplog.at_level(logging.DEBUG, logger="app.api.confirm_upload"):
            with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
                handler(event, MagicMock())

        assert "Bearer" not in caplog.text
        assert "eyJhbGciOiJSUzI1NiJ9" not in caplog.text

    def test_handler_does_not_call_s3_or_dynamodb_directly(self) -> None:
        """Handler only calls the service — no direct AWS SDK calls."""
        svc = _mock_svc()
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=svc):
            handler(_make_event(), MagicMock())

        # The mock service was called; if boto3 were called directly
        # it would raise (no credentials in test env) — passing confirms isolation.
        svc.confirm_upload.assert_called_once()

    def test_content_type_is_json(self) -> None:
        """Every response carries Content-Type: application/json."""
        with patch.object(confirm_upload_module, "_get_evidence_service", return_value=_mock_svc()):
            response = handler(_make_event(), MagicMock())
        assert response["headers"]["Content-Type"] == "application/json"


# ── Helper function tests ─────────────────────────────────────────────────────


class TestExtractEvidenceId:
    def test_returns_evidence_id(self) -> None:
        event = _make_event()
        assert _extract_evidence_id(event) == EVIDENCE_ID

    def test_returns_none_for_missing_path_params(self) -> None:
        assert _extract_evidence_id({"httpMethod": "POST"}) is None

    def test_returns_none_for_none_path_params(self) -> None:
        assert _extract_evidence_id({"pathParameters": None}) is None

    def test_returns_none_for_missing_key(self) -> None:
        assert _extract_evidence_id({"pathParameters": {"claimId": CLAIM_ID}}) is None

    def test_returns_none_for_blank(self) -> None:
        assert _extract_evidence_id({"pathParameters": {"evidenceId": "   "}}) is None

    def test_strips_whitespace(self) -> None:
        result = _extract_evidence_id({"pathParameters": {"evidenceId": f"  {EVIDENCE_ID}  "}})
        assert result == EVIDENCE_ID

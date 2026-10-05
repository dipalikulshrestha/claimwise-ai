"""
Unit tests for app.api.get_claim — Task 7.

Covers all 17 required scenarios:

Success
  1.  Authenticated request with valid claimId → HTTP 200.
  2.  Correct Cognito sub passed to service.
  3.  Correct claimId passed to service.
  4.  Service response returned correctly.
  5.  claimantId not exposed in response.

Authentication
  6.  Missing authorizer → HTTP 401.
  7.  Missing Cognito sub → HTTP 401.

Path validation
  8.  Missing pathParameters → HTTP 400.
  9.  Missing claimId key → HTTP 400.
  10. Blank claimId → HTTP 400.

Service errors
  11. NotFoundError → HTTP 404.
  12. ForbiddenError → HTTP 403.
  13. ValidationError → HTTP 400.
  14. Unexpected exception → HTTP 500.
  15. Internal details not exposed in 500 body.

Security
  16. claimantId always sourced from Cognito sub, never path/query/body.
  17. Tokens and full event are not written to logs.

Additional
  18. Evidence list included in 200 response.
  19. 404 body uses standard error envelope.
  20. 403 body uses standard error envelope.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import app.api.get_claim as get_claim_module
from app.api.get_claim import _extract_claim_id, handler
from app.domain.exceptions import ForbiddenError, NotFoundError, ValidationError

# ── Constants ─────────────────────────────────────────────────────────────────

COGNITO_SUB = "cognito-sub-abc-123"
CLAIM_ID = "CLM-550e8400-e29b-41d4-a716-446655440000"
FIXED_TIME = "2026-09-30T10:00:00+00:00"

VALID_CLAIM_RESPONSE: dict[str, Any] = {
    "claimId": CLAIM_ID,
    "policyNumber": "POL-123456",
    "incidentDateTime": "2026-09-15T14:30:00Z",
    "incidentLocation": "123 Main St, Springfield",
    "status": "CREATED",
    "createdAt": FIXED_TIME,
    "updatedAt": FIXED_TIME,
    "evidence": [
        {
            "evidenceId": "EVD-aaa-111",
            "claimId": CLAIM_ID,
            "evidenceType": "VIDEO",
            "originalFileName": "accident.mp4",
            "contentType": "video/mp4",
            "fileSizeBytes": 1024,
            "storageKey": f"claims/{CLAIM_ID}/evidence/EVD-aaa-111/object",
            "status": "UPLOAD_PENDING",
            "createdAt": FIXED_TIME,
        }
    ],
}


# ── Event builder ─────────────────────────────────────────────────────────────


def _make_event(
    claim_id: str | None = CLAIM_ID,
    cognito_sub: str | None = COGNITO_SUB,
    include_path_params: bool = True,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "httpMethod": "GET",
        "path": f"/v1/claims/{claim_id}",
        "body": None,
        "headers": {},
    }

    if cognito_sub is not None:
        event["requestContext"] = {
            "authorizer": {"claims": {"sub": cognito_sub, "email": "user@example.com"}}
        }
    else:
        event["requestContext"] = {}

    if include_path_params:
        event["pathParameters"] = {"claimId": claim_id} if claim_id is not None else {}
    # no pathParameters key at all when include_path_params=False

    return event


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


# ── Fixture: reset module-level _service ─────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_module_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent cross-test _service leakage via create_claim module."""
    import app.api.create_claim as cc

    monkeypatch.setattr(cc, "_service", None)


def _mock_service(return_value: dict[str, Any] = VALID_CLAIM_RESPONSE) -> MagicMock:
    svc = MagicMock()
    svc.get_claim_with_evidence.return_value = return_value
    return svc


# ── 1–5. Success ──────────────────────────────────────────────────────────────


class TestSuccess:
    def test_returns_200(self) -> None:
        """Scenario 1 — valid authenticated request returns HTTP 200."""
        with patch.object(get_claim_module, "_get_service", return_value=_mock_service()):
            response = handler(_make_event(), MagicMock())
        assert response["statusCode"] == 200

    def test_cognito_sub_passed_to_service(self) -> None:
        """Scenario 2 — Cognito sub is passed as claimant_id to the service."""
        mock_svc = _mock_service()
        with patch.object(get_claim_module, "_get_service", return_value=mock_svc):
            handler(_make_event(cognito_sub=COGNITO_SUB), MagicMock())

        args = mock_svc.get_claim_with_evidence.call_args
        passed_claimant = args.args[1] if len(args.args) > 1 else args.kwargs.get("claimant_id")
        assert passed_claimant == COGNITO_SUB

    def test_claim_id_passed_to_service(self) -> None:
        """Scenario 3 — path claimId is forwarded to the service."""
        mock_svc = _mock_service()
        with patch.object(get_claim_module, "_get_service", return_value=mock_svc):
            handler(_make_event(), MagicMock())

        args = mock_svc.get_claim_with_evidence.call_args
        passed_claim_id = args.args[0] if args.args else args.kwargs.get("claim_id")
        assert passed_claim_id == CLAIM_ID

    def test_service_response_returned(self) -> None:
        """Scenario 4 — service response body is forwarded to the client."""
        with patch.object(get_claim_module, "_get_service", return_value=_mock_service()):
            response = handler(_make_event(), MagicMock())

        body = _body(response)
        assert body["claimId"] == CLAIM_ID
        assert body["status"] == "CREATED"
        assert body["policyNumber"] == "POL-123456"

    def test_claimant_id_not_in_response(self) -> None:
        """Scenario 5 — claimantId is not present in the 200 response."""
        with patch.object(get_claim_module, "_get_service", return_value=_mock_service()):
            response = handler(_make_event(), MagicMock())
        assert "claimantId" not in _body(response)

    def test_evidence_list_included(self) -> None:
        """Scenario 18 — evidence list from service is included in the response."""
        with patch.object(get_claim_module, "_get_service", return_value=_mock_service()):
            response = handler(_make_event(), MagicMock())

        body = _body(response)
        assert "evidence" in body
        assert len(body["evidence"]) == 1
        assert body["evidence"][0]["evidenceId"] == "EVD-aaa-111"

    def test_content_type_is_json(self) -> None:
        """Response always carries Content-Type: application/json."""
        with patch.object(get_claim_module, "_get_service", return_value=_mock_service()):
            response = handler(_make_event(), MagicMock())
        assert response["headers"]["Content-Type"] == "application/json"


# ── 6–7. Authentication ───────────────────────────────────────────────────────


class TestAuthentication:
    def test_missing_authorizer_returns_401(self) -> None:
        """Scenario 6 — no requestContext.authorizer → 401."""
        event = _make_event(cognito_sub=None)
        response = handler(event, MagicMock())
        assert response["statusCode"] == 401
        assert _body(response)["error"]["code"] == "UNAUTHORIZED"

    def test_missing_sub_returns_401(self) -> None:
        """Scenario 7 — authorizer present but sub absent → 401."""
        event = _make_event()
        event["requestContext"]["authorizer"]["claims"].pop("sub")
        response = handler(event, MagicMock())
        assert response["statusCode"] == 401

    def test_empty_sub_returns_401(self) -> None:
        """Empty-string sub treated as missing → 401."""
        event = _make_event(cognito_sub="")
        response = handler(event, MagicMock())
        assert response["statusCode"] == 401


# ── 8–10. Path parameter validation ──────────────────────────────────────────


class TestPathValidation:
    def test_missing_path_parameters_returns_400(self) -> None:
        """Scenario 8 — no pathParameters key at all → 400."""
        event = _make_event(include_path_params=False)
        response = handler(event, MagicMock())
        assert response["statusCode"] == 400
        assert _body(response)["error"]["code"] == "VALIDATION_ERROR"

    def test_missing_claim_id_key_returns_400(self) -> None:
        """Scenario 9 — pathParameters present but no claimId key → 400."""
        event = _make_event()
        event["pathParameters"] = {}  # key present, no claimId
        response = handler(event, MagicMock())
        assert response["statusCode"] == 400

    def test_blank_claim_id_returns_400(self) -> None:
        """Scenario 10 — claimId is whitespace-only → 400."""
        event = _make_event()
        event["pathParameters"] = {"claimId": "   "}
        response = handler(event, MagicMock())
        assert response["statusCode"] == 400

    def test_none_path_parameters_returns_400(self) -> None:
        """pathParameters set to None → 400."""
        event = _make_event()
        event["pathParameters"] = None
        response = handler(event, MagicMock())
        assert response["statusCode"] == 400


# ── 11–15. Service errors ─────────────────────────────────────────────────────


class TestServiceErrors:
    def test_not_found_returns_404(self) -> None:
        """Scenario 11 — NotFoundError maps to 404 with CLAIM_NOT_FOUND."""
        mock_svc = _mock_service()
        mock_svc.get_claim_with_evidence.side_effect = NotFoundError(
            "Claim not found.", error_code="CLAIM_NOT_FOUND"
        )
        with patch.object(get_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), MagicMock())

        assert response["statusCode"] == 404
        body = _body(response)
        assert body["error"]["code"] == "CLAIM_NOT_FOUND"

    def test_forbidden_returns_403(self) -> None:
        """Scenario 12 — ForbiddenError maps to 403 with FORBIDDEN."""
        mock_svc = _mock_service()
        mock_svc.get_claim_with_evidence.side_effect = ForbiddenError("You do not have permission.")
        with patch.object(get_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), MagicMock())

        assert response["statusCode"] == 403
        body = _body(response)
        assert body["error"]["code"] == "FORBIDDEN"

    def test_validation_error_returns_400(self) -> None:
        """Scenario 13 — ValidationError maps to 400."""
        mock_svc = _mock_service()
        mock_svc.get_claim_with_evidence.side_effect = ValidationError(
            "Invalid input.", error_code="VALIDATION_ERROR"
        )
        with patch.object(get_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), MagicMock())

        assert response["statusCode"] == 400

    def test_unexpected_exception_returns_500(self) -> None:
        """Scenario 14 — RuntimeError maps to 500."""
        mock_svc = _mock_service()
        mock_svc.get_claim_with_evidence.side_effect = RuntimeError("database exploded")
        with patch.object(get_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), MagicMock())

        assert response["statusCode"] == 500

    def test_internal_details_not_in_500_body(self) -> None:
        """Scenario 15 — 500 body contains no stack traces or AWS details."""
        mock_svc = _mock_service()
        mock_svc.get_claim_with_evidence.side_effect = RuntimeError(
            "arn:aws:dynamodb:us-east-1:999999999:table/ClaimwiseClaims"
        )
        with patch.object(get_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), MagicMock())

        body_str = response["body"]
        assert "arn:aws" not in body_str
        assert "ClaimwiseClaims" not in body_str
        assert "Traceback" not in body_str
        assert _body(response)["error"]["code"] == "INTERNAL_ERROR"

    def test_404_uses_error_envelope(self) -> None:
        """Scenario 19 — 404 body follows {error: {code, message}} structure."""
        mock_svc = _mock_service()
        mock_svc.get_claim_with_evidence.side_effect = NotFoundError(
            "Not found.", error_code="CLAIM_NOT_FOUND"
        )
        with patch.object(get_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), MagicMock())

        body = _body(response)
        assert "error" in body
        assert "code" in body["error"]
        assert "message" in body["error"]

    def test_403_uses_error_envelope(self) -> None:
        """Scenario 20 — 403 body follows {error: {code, message}} structure."""
        mock_svc = _mock_service()
        mock_svc.get_claim_with_evidence.side_effect = ForbiddenError("No access.")
        with patch.object(get_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), MagicMock())

        body = _body(response)
        assert "error" in body
        assert body["error"]["code"] == "FORBIDDEN"


# ── 16–17. Security ───────────────────────────────────────────────────────────


class TestSecurity:
    def test_claimant_id_always_from_cognito_sub(self) -> None:
        """Scenario 16 — claimantId cannot come from path, query, or body."""
        mock_svc = _mock_service()
        # The claimId path param looks like a Cognito sub — must not be used as identity
        event = _make_event(claim_id=CLAIM_ID, cognito_sub=COGNITO_SUB)
        # Add a query string param that tries to supply an identity
        event["queryStringParameters"] = {"claimantId": "injected-identity"}

        with patch.object(get_claim_module, "_get_service", return_value=mock_svc):
            handler(event, MagicMock())

        args = mock_svc.get_claim_with_evidence.call_args
        passed_claimant = args.args[1] if len(args.args) > 1 else args.kwargs.get("claimant_id")
        assert passed_claimant == COGNITO_SUB
        assert passed_claimant != "injected-identity"

    def test_token_not_written_to_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        """Scenario 17 — Bearer token value is not written to any log record."""
        mock_svc = _mock_service()
        event = _make_event()
        event["headers"]["Authorization"] = "Bearer eyJhbGciOiJSUzI1NiJ9.fake.jwt"

        with caplog.at_level(logging.DEBUG, logger="app.api.get_claim"):
            with patch.object(get_claim_module, "_get_service", return_value=mock_svc):
                handler(event, MagicMock())

        assert "Bearer" not in caplog.text
        assert "eyJhbGciOiJSUzI1NiJ9" not in caplog.text

    def test_full_event_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """The full API Gateway event (which may contain auth info) is not logged."""
        mock_svc = _mock_service()
        event = _make_event()
        # A unique marker that should not appear in logs if we're not logging the event
        event["requestContext"]["requestId"] = "UNIQUE-MARKER-DO-NOT-LOG-12345"

        with caplog.at_level(logging.DEBUG, logger="app.api.get_claim"):
            with patch.object(get_claim_module, "_get_service", return_value=mock_svc):
                handler(event, MagicMock())

        assert "UNIQUE-MARKER-DO-NOT-LOG-12345" not in caplog.text


# ── Helper function unit tests ────────────────────────────────────────────────


class TestExtractClaimId:
    def test_returns_claim_id_from_path_params(self) -> None:
        event = _make_event()
        assert _extract_claim_id(event) == CLAIM_ID

    def test_returns_none_for_missing_path_parameters(self) -> None:
        assert _extract_claim_id({"httpMethod": "GET"}) is None

    def test_returns_none_for_none_path_parameters(self) -> None:
        assert _extract_claim_id({"pathParameters": None}) is None

    def test_returns_none_for_missing_claim_id_key(self) -> None:
        assert _extract_claim_id({"pathParameters": {}}) is None

    def test_returns_none_for_blank_claim_id(self) -> None:
        assert _extract_claim_id({"pathParameters": {"claimId": "   "}}) is None

    def test_strips_whitespace_from_claim_id(self) -> None:
        result = _extract_claim_id({"pathParameters": {"claimId": f"  {CLAIM_ID}  "}})
        assert result == CLAIM_ID

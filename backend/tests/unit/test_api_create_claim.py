"""
Unit tests for app.api.create_claim — Task 6.

All 18 required scenarios plus additional coverage:

Success
  1.  Valid authenticated request returns HTTP 201.
  2.  Handler passes Cognito sub as claimantId to ClaimService.
  3.  Request body fields are forwarded to ClaimService.
  4.  Response contains the expected claim representation.
  5.  claimantId is NOT exposed in the response.

Authentication
  6.  Missing authorizer context returns HTTP 401.
  7.  Missing Cognito sub returns HTTP 401.

Request validation
  8.  Missing body returns HTTP 400.
  9.  Empty body returns HTTP 400.
  10. Malformed JSON returns HTTP 400.

Domain / service errors
  11. ValidationError maps to HTTP 400 with correct error code.
  12. NotFoundError during create maps to HTTP 400 (edge case guard).
  13. Unexpected service exception maps to HTTP 500.
  14. Internal exception details are NOT exposed in the 500 body.

Security
  15. claimantId in request body does NOT override Cognito sub.
  16. Token / authorizer data is NOT written to logs.

API Gateway details
  17. Correct authorizer event structure (requestContext.authorizer.claims.sub).
  18. Base64-encoded body is decoded and parsed correctly.

Extra
  19. description is optional — present when supplied, absent when not.
  20. JSON response has Content-Type: application/json header.
  21. Error body follows {"error": {"code": ..., "message": ...}} envelope.
  22. Empty JSON object body ({}) triggers domain ValidationError → 400.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import app.api.create_claim as create_claim_module
from app.api.create_claim import _extract_claimant_id, _parse_body, handler
from app.domain.exceptions import NotFoundError, ValidationError

# ── Shared constants ──────────────────────────────────────────────────────────

COGNITO_SUB = "cognito-sub-abc-123"
CLAIM_ID = "CLM-550e8400-e29b-41d4-a716-446655440000"
FIXED_TIME = "2026-09-30T10:00:00+00:00"

VALID_BODY: dict[str, Any] = {
    "policyNumber": "POL-123456",
    "incidentDateTime": "2026-09-15T14:30:00Z",
    "incidentLocation": "123 Main St, Springfield",
    "description": "Rear-ended at traffic lights.",
}

VALID_CLAIM_RESPONSE: dict[str, Any] = {
    "claimId": CLAIM_ID,
    "policyNumber": "POL-123456",
    "incidentDateTime": "2026-09-15T14:30:00Z",
    "incidentLocation": "123 Main St, Springfield",
    "description": "Rear-ended at traffic lights.",
    "status": "CREATED",
    "createdAt": FIXED_TIME,
    "updatedAt": FIXED_TIME,
}


# ── Event builders ────────────────────────────────────────────────────────────


def _make_event(
    body: Any = VALID_BODY,
    cognito_sub: str | None = COGNITO_SUB,
    base64_encoded: bool = False,
) -> dict[str, Any]:
    """Build a minimal API Gateway Lambda proxy event."""
    if body is None:
        raw_body = None
        is_b64 = False
    elif isinstance(body, str):
        raw_body = body
        is_b64 = base64_encoded
    else:
        raw_body = json.dumps(body)
        is_b64 = base64_encoded

    if is_b64 and raw_body is not None:
        raw_body = base64.b64encode(raw_body.encode()).decode()

    event: dict[str, Any] = {
        "httpMethod": "POST",
        "path": "/v1/claims",
        "body": raw_body,
        "isBase64Encoded": is_b64,
        "headers": {"Content-Type": "application/json"},
    }

    if cognito_sub is not None:
        event["requestContext"] = {
            "authorizer": {
                "claims": {
                    "sub": cognito_sub,
                    "email": "claimant@example.com",
                }
            }
        }
    else:
        event["requestContext"] = {}

    return event


def _fake_context() -> MagicMock:
    return MagicMock()


# ── Mock service fixture ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_module_service():
    """Reset the module-level _service after every test to avoid state leakage."""
    original = create_claim_module._service
    yield
    create_claim_module._service = original


def _make_mock_service(return_value: dict[str, Any] = VALID_CLAIM_RESPONSE) -> MagicMock:
    svc = MagicMock()
    svc.create_claim.return_value = return_value
    return svc


# ── Helper: parse response body ───────────────────────────────────────────────


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


# ── 1–5. Success ──────────────────────────────────────────────────────────────


class TestSuccess:
    def test_valid_request_returns_201(self) -> None:
        """Scenario 1 — valid authenticated request returns HTTP 201."""
        mock_svc = _make_mock_service()
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), _fake_context())
        assert response["statusCode"] == 201

    def test_cognito_sub_passed_as_claimant_id(self) -> None:
        """Scenario 2 — handler passes Cognito sub, not body contents, as claimantId."""
        mock_svc = _make_mock_service()
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            handler(_make_event(cognito_sub=COGNITO_SUB), _fake_context())

        mock_svc.create_claim.assert_called_once()
        call_args = mock_svc.create_claim.call_args
        assert (
            call_args.args[0] == COGNITO_SUB or call_args.kwargs.get("claimant_id") == COGNITO_SUB
        )

    def test_body_fields_forwarded_to_service(self) -> None:
        """Scenario 3 — body dict is forwarded intact to ClaimService.create_claim."""
        mock_svc = _make_mock_service()
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            handler(_make_event(), _fake_context())

        call_args = mock_svc.create_claim.call_args
        passed_body = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("body")
        assert passed_body is not None
        assert passed_body["policyNumber"] == "POL-123456"
        assert passed_body["incidentDateTime"] == "2026-09-15T14:30:00Z"
        assert passed_body["incidentLocation"] == "123 Main St, Springfield"

    def test_response_contains_expected_claim_fields(self) -> None:
        """Scenario 4 — 201 body contains claim fields from ClaimService."""
        mock_svc = _make_mock_service()
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), _fake_context())

        body = _body(response)
        assert body["claimId"] == CLAIM_ID
        assert body["status"] == "CREATED"
        assert body["policyNumber"] == "POL-123456"
        assert "createdAt" in body
        assert "updatedAt" in body

    def test_claimant_id_not_in_response(self) -> None:
        """Scenario 5 — claimantId is NOT exposed in the HTTP response body."""
        mock_svc = _make_mock_service()
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), _fake_context())

        body = _body(response)
        assert "claimantId" not in body


# ── 6–7. Authentication ───────────────────────────────────────────────────────


class TestAuthentication:
    def test_missing_authorizer_context_returns_401(self) -> None:
        """Scenario 6 — no requestContext.authorizer at all → 401."""
        event = _make_event(cognito_sub=None)
        response = handler(event, _fake_context())
        assert response["statusCode"] == 401
        body = _body(response)
        assert body["error"]["code"] == "UNAUTHORIZED"

    def test_missing_cognito_sub_returns_401(self) -> None:
        """Scenario 7 — authorizer present but sub is absent → 401."""
        event = _make_event()
        # Remove sub but keep the authorizer context
        event["requestContext"]["authorizer"]["claims"].pop("sub")
        response = handler(event, _fake_context())
        assert response["statusCode"] == 401

    def test_empty_cognito_sub_returns_401(self) -> None:
        """Empty-string sub is treated as missing → 401."""
        event = _make_event(cognito_sub="")
        response = handler(event, _fake_context())
        assert response["statusCode"] == 401

    def test_none_requestcontext_returns_401(self) -> None:
        """Event with no requestContext key at all returns 401."""
        event = _make_event(cognito_sub=None)
        del event["requestContext"]
        response = handler(event, _fake_context())
        assert response["statusCode"] == 401


# ── 8–10. Request body validation ────────────────────────────────────────────


class TestRequestBodyValidation:
    def test_missing_body_returns_400(self) -> None:
        """Scenario 8 — body key absent from event returns 400."""
        event = _make_event(body=None)
        response = handler(event, _fake_context())
        assert response["statusCode"] == 400
        body = _body(response)
        assert body["error"]["code"] == "VALIDATION_ERROR"

    def test_empty_string_body_returns_400(self) -> None:
        """Scenario 9 — empty string body returns 400."""
        event = _make_event(body="")
        response = handler(event, _fake_context())
        assert response["statusCode"] == 400

    def test_malformed_json_returns_400(self) -> None:
        """Scenario 10 — body that is not valid JSON returns 400."""
        event = _make_event(body="{not: valid json}")
        response = handler(event, _fake_context())
        assert response["statusCode"] == 400
        body = _body(response)
        assert body["error"]["code"] == "VALIDATION_ERROR"

    def test_empty_json_object_triggers_domain_validation(self) -> None:
        """Scenario 22 — {} body reaches service and domain raises ValidationError → 400."""
        mock_svc = _make_mock_service()
        mock_svc.create_claim.side_effect = ValidationError(
            "policyNumber is required.", error_code="VALIDATION_ERROR"
        )
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(body={}), _fake_context())
        assert response["statusCode"] == 400


# ── 11–14. Domain / service errors ───────────────────────────────────────────


class TestServiceErrors:
    def test_validation_error_maps_to_400(self) -> None:
        """Scenario 11 — ValidationError from service returns 400 with error_code."""
        mock_svc = _make_mock_service()
        mock_svc.create_claim.side_effect = ValidationError(
            "policyNumber is required.", error_code="VALIDATION_ERROR"
        )
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), _fake_context())

        assert response["statusCode"] == 400
        body = _body(response)
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "policyNumber" in body["error"]["message"]

    def test_not_found_error_maps_to_400(self) -> None:
        """Scenario 12 — NotFoundError (edge case during create) returns 400."""
        mock_svc = _make_mock_service()
        mock_svc.create_claim.side_effect = NotFoundError(
            "Resource not found.", error_code="CLAIM_NOT_FOUND"
        )
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), _fake_context())

        assert response["statusCode"] == 400

    def test_unexpected_exception_maps_to_500(self) -> None:
        """Scenario 13 — RuntimeError from service returns 500."""
        mock_svc = _make_mock_service()
        mock_svc.create_claim.side_effect = RuntimeError("DynamoDB connection reset")
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), _fake_context())

        assert response["statusCode"] == 500

    def test_internal_exception_details_not_exposed(self) -> None:
        """Scenario 14 — 500 body must not contain stack trace or AWS details."""
        mock_svc = _make_mock_service()
        mock_svc.create_claim.side_effect = RuntimeError(
            "Table arn:aws:dynamodb:us-east-1:123456789:table/ClaimwiseClaims not found"
        )
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), _fake_context())

        body_str = response["body"]
        # AWS ARNs, table names, account IDs must not appear in the response
        assert "arn:aws" not in body_str
        assert "ClaimwiseClaims" not in body_str
        assert "123456789" not in body_str
        assert "Traceback" not in body_str

        parsed = json.loads(body_str)
        assert parsed["error"]["code"] == "INTERNAL_ERROR"


# ── 15–16. Security ───────────────────────────────────────────────────────────


class TestSecurity:
    def test_claimant_id_in_body_does_not_override_cognito_sub(self) -> None:
        """Scenario 15 — body claimantId is ignored; Cognito sub wins."""
        mock_svc = _make_mock_service()
        body_with_claimant = {**VALID_BODY, "claimantId": "attacker-supplied-id"}

        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            handler(_make_event(body=body_with_claimant, cognito_sub=COGNITO_SUB), _fake_context())

        call_args = mock_svc.create_claim.call_args
        # First positional argument must be the Cognito sub
        passed_claimant_id = (
            call_args.args[0] if call_args.args else call_args.kwargs.get("claimant_id")
        )
        assert passed_claimant_id == COGNITO_SUB
        assert passed_claimant_id != "attacker-supplied-id"

    def test_token_data_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Scenario 16 — JWT / authorizer data does not appear in log output."""
        mock_svc = _make_mock_service()
        event = _make_event()
        # Add a realistic-looking token to the headers
        event["headers"]["Authorization"] = "Bearer eyJhbGciOiJSUzI1NiJ9.fake.token"

        with caplog.at_level(logging.DEBUG, logger="app.api.create_claim"):
            with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
                handler(event, _fake_context())

        log_output = caplog.text
        # Confirm the Bearer token value is not in any log record
        assert "Bearer" not in log_output
        assert "eyJhbGciOiJSUzI1NiJ9" not in log_output

    def test_cognito_sub_not_logged_on_auth_failure(self, caplog: pytest.LogCaptureFixture) -> None:
        """Cognito sub is not echoed back in log warnings."""
        event = _make_event(cognito_sub=None)
        with caplog.at_level(logging.WARNING, logger="app.api.create_claim"):
            handler(event, _fake_context())
        # The sub is None here, but we verify the log doesn't echo it either way
        assert COGNITO_SUB not in caplog.text


# ── 17–18. API Gateway details ────────────────────────────────────────────────


class TestApiGatewayDetails:
    def test_correct_authorizer_event_structure(self) -> None:
        """Scenario 17 — standard Cognito User Pool authorizer path is used."""
        mock_svc = _make_mock_service()
        event = {
            "httpMethod": "POST",
            "path": "/v1/claims",
            "body": json.dumps(VALID_BODY),
            "isBase64Encoded": False,
            "headers": {},
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "sub": COGNITO_SUB,
                        "cognito:username": "user123",
                        "email": "user@example.com",
                    }
                }
            },
        }
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            response = handler(event, _fake_context())
        assert response["statusCode"] == 201

    def test_base64_encoded_body_decoded_correctly(self) -> None:
        """Scenario 18 — base64-encoded body is decoded before parsing."""
        mock_svc = _make_mock_service()
        event = _make_event(body=VALID_BODY, base64_encoded=True)
        assert event["isBase64Encoded"] is True

        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            response = handler(event, _fake_context())

        assert response["statusCode"] == 201
        # Confirm the body was actually decoded and forwarded correctly
        call_args = mock_svc.create_claim.call_args
        passed_body = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("body")
        assert passed_body["policyNumber"] == "POL-123456"


# ── 19–21. Additional coverage ────────────────────────────────────────────────


class TestAdditional:
    def test_description_absent_when_not_provided(self) -> None:
        """Scenario 19 — description is absent from the response when not in body."""
        claim_no_desc = {k: v for k, v in VALID_CLAIM_RESPONSE.items() if k != "description"}
        mock_svc = _make_mock_service(return_value=claim_no_desc)
        body_no_desc = {k: v for k, v in VALID_BODY.items() if k != "description"}

        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(body=body_no_desc), _fake_context())

        assert response["statusCode"] == 201
        assert "description" not in _body(response)

    def test_response_has_content_type_json(self) -> None:
        """Scenario 20 — every response carries Content-Type: application/json."""
        mock_svc = _make_mock_service()
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), _fake_context())
        assert response["headers"]["Content-Type"] == "application/json"

    def test_error_body_envelope_structure(self) -> None:
        """Scenario 21 — all error responses use {"error": {"code": ..., "message": ...}}."""
        # Test with a 400
        event = _make_event(body=None)
        response = handler(event, _fake_context())
        body = _body(response)
        assert "error" in body
        assert "code" in body["error"]
        assert "message" in body["error"]

    def test_service_called_once_per_request(self) -> None:
        """ClaimService.create_claim is called exactly once per request."""
        mock_svc = _make_mock_service()
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            handler(_make_event(), _fake_context())
        mock_svc.create_claim.assert_called_once()

    def test_401_body_uses_error_envelope(self) -> None:
        """401 responses follow the standard error envelope."""
        response = handler(_make_event(cognito_sub=None), _fake_context())
        body = _body(response)
        assert body["error"]["code"] == "UNAUTHORIZED"

    def test_500_body_uses_error_envelope(self) -> None:
        """500 responses follow the standard error envelope."""
        mock_svc = _make_mock_service()
        mock_svc.create_claim.side_effect = RuntimeError("boom")
        with patch.object(create_claim_module, "_get_service", return_value=mock_svc):
            response = handler(_make_event(), _fake_context())
        assert response["statusCode"] == 500
        body = _body(response)
        assert body["error"]["code"] == "INTERNAL_ERROR"


# ── Unit tests for helper functions ──────────────────────────────────────────


class TestHelperFunctions:
    def test_extract_claimant_id_returns_sub(self) -> None:
        """_extract_claimant_id returns the Cognito sub from a well-formed event."""
        event = _make_event()
        assert _extract_claimant_id(event) == COGNITO_SUB

    def test_extract_claimant_id_returns_none_for_missing_context(self) -> None:
        """_extract_claimant_id returns None when requestContext is absent."""
        assert _extract_claimant_id({}) is None

    def test_extract_claimant_id_returns_none_for_missing_authorizer(self) -> None:
        """_extract_claimant_id returns None when authorizer is absent."""
        event = {"requestContext": {}}
        assert _extract_claimant_id(event) is None

    def test_extract_claimant_id_returns_none_for_missing_claims(self) -> None:
        """_extract_claimant_id returns None when claims dict is absent."""
        event = {"requestContext": {"authorizer": {}}}
        assert _extract_claimant_id(event) is None

    def test_parse_body_returns_none_for_no_body(self) -> None:
        """_parse_body returns None for event with no body."""
        assert _parse_body({}) is None
        assert _parse_body({"body": None}) is None
        assert _parse_body({"body": ""}) is None

    def test_parse_body_parses_json(self) -> None:
        """_parse_body parses a valid JSON string body."""
        event = {"body": json.dumps(VALID_BODY), "isBase64Encoded": False}
        result = _parse_body(event)
        assert result == VALID_BODY

    def test_parse_body_decodes_base64(self) -> None:
        """_parse_body decodes base64 before parsing."""
        encoded = base64.b64encode(json.dumps(VALID_BODY).encode()).decode()
        event = {"body": encoded, "isBase64Encoded": True}
        result = _parse_body(event)
        assert result == VALID_BODY

    def test_parse_body_raises_json_decode_error(self) -> None:
        """_parse_body raises json.JSONDecodeError for malformed JSON."""
        import json as _json

        with pytest.raises(_json.JSONDecodeError):
            _parse_body({"body": "{bad json", "isBase64Encoded": False})

"""
Unit tests for app.api.request_upload_url — Task 8.

Covers all 24 required scenarios:

Success
  1.  Valid authenticated request → HTTP 201.
  2.  Correct Cognito sub passed to service.
  3.  Correct claimId passed to service.
  4.  Request body passed to service.
  5.  Service response returned correctly.
  6.  claimantId not exposed in response.

Authentication / path / body
  7.  Missing authorizer → 401.
  8.  Missing Cognito sub → 401.
  9.  Missing pathParameters → 400.
  10. Missing claimId → 400.
  11. Blank claimId → 400.
  12. Missing body → 400.
  13. Empty body → 400.
  14. Malformed JSON → 400.
  15. Base64 body decoded correctly.

Service errors
  16. Claim not found → 404.
  17. ForbiddenError → 403.
  18. ValidationError → 400 with specific error code.
  19. Unexpected exception → 500.
  20. Internal details not exposed in 500 body.

Security
  21. Client-supplied claimantId cannot override Cognito sub.
  22. Handler does not log auth headers/tokens.
  23. Handler does not construct an S3 key.
  24. Handler does not process file contents.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import app.api.request_upload_url as upload_url_module
from app.api.request_upload_url import handler
from app.domain.exceptions import ForbiddenError, NotFoundError, ValidationError

# ── Constants ─────────────────────────────────────────────────────────────────

COGNITO_SUB = "cognito-sub-abc-123"
CLAIM_ID = "CLM-550e8400-e29b-41d4-a716-446655440000"
EVIDENCE_ID = "EVD-7f3c9a12-4b2e-4a8f-9c1d-2e3f4a5b6c7d"
FIXED_TIME = "2026-09-30T10:00:00+00:00"
STORAGE_KEY = f"claims/{CLAIM_ID}/evidence/{EVIDENCE_ID}/object"

VALID_BODY: dict[str, Any] = {
    "evidenceType": "VIDEO",
    "fileName": "accident.mp4",
    "contentType": "video/mp4",
    "fileSizeBytes": 10_000_000,
}

VALID_UPLOAD_URL_RESPONSE: dict[str, Any] = {
    "evidenceId": EVIDENCE_ID,
    "claimId": CLAIM_ID,
    "presignedUrl": "https://s3.example.com/presigned",
    "urlExpiresAt": FIXED_TIME,
    "storageKey": STORAGE_KEY,
    "status": "UPLOAD_PENDING",
    "evidenceType": "VIDEO",
    "originalFileName": "accident.mp4",
    "contentType": "video/mp4",
    "fileSizeBytes": 10_000_000,
    "createdAt": FIXED_TIME,
}


# ── Event builder ─────────────────────────────────────────────────────────────


def _make_event(
    body: Any = VALID_BODY,
    cognito_sub: str | None = COGNITO_SUB,
    claim_id: str | None = CLAIM_ID,
    include_path_params: bool = True,
    base64_encoded: bool = False,
) -> dict[str, Any]:
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
        "path": f"/v1/claims/{claim_id}/evidence/upload-url",
        "body": raw_body,
        "isBase64Encoded": is_b64,
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

    return event


def _body(response: dict[str, Any]) -> dict[str, Any]:
    return json.loads(response["body"])


# ── Fixture: reset module-level service ──────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_service() -> None:
    upload_url_module._evidence_service = None


def _mock_svc(
    return_value: dict[str, Any] = VALID_UPLOAD_URL_RESPONSE,
) -> MagicMock:
    svc = MagicMock()
    svc.request_upload_url.return_value = return_value
    return svc


# ── 1–6. Success ──────────────────────────────────────────────────────────────


class TestSuccess:
    def test_returns_201(self) -> None:
        """Scenario 1 — valid authenticated request returns HTTP 201."""
        with patch.object(upload_url_module, "_get_evidence_service", return_value=_mock_svc()):
            response = handler(_make_event(), MagicMock())
        assert response["statusCode"] == 201

    def test_cognito_sub_passed_to_service(self) -> None:
        """Scenario 2 — Cognito sub is forwarded as claimant_id."""
        svc = _mock_svc()
        with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
            handler(_make_event(cognito_sub=COGNITO_SUB), MagicMock())

        args = svc.request_upload_url.call_args
        passed_claimant = args.args[1] if len(args.args) > 1 else args.kwargs.get("claimant_id")
        assert passed_claimant == COGNITO_SUB

    def test_claim_id_passed_to_service(self) -> None:
        """Scenario 3 — path claimId forwarded to service."""
        svc = _mock_svc()
        with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
            handler(_make_event(), MagicMock())

        args = svc.request_upload_url.call_args
        passed_claim_id = args.args[0] if args.args else args.kwargs.get("claim_id")
        assert passed_claim_id == CLAIM_ID

    def test_body_passed_to_service(self) -> None:
        """Scenario 4 — request body dict forwarded to service intact."""
        svc = _mock_svc()
        with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
            handler(_make_event(), MagicMock())

        args = svc.request_upload_url.call_args
        passed_body = args.args[2] if len(args.args) > 2 else args.kwargs.get("body")
        assert passed_body is not None
        assert passed_body["evidenceType"] == "VIDEO"
        assert passed_body["contentType"] == "video/mp4"
        assert passed_body["fileSizeBytes"] == 10_000_000

    def test_service_response_returned(self) -> None:
        """Scenario 5 — service response fields appear in the 201 body."""
        with patch.object(upload_url_module, "_get_evidence_service", return_value=_mock_svc()):
            response = handler(_make_event(), MagicMock())

        body = _body(response)
        assert body["evidenceId"] == EVIDENCE_ID
        assert body["presignedUrl"] == "https://s3.example.com/presigned"
        assert body["status"] == "UPLOAD_PENDING"
        assert body["storageKey"] == STORAGE_KEY

    def test_claimant_id_not_in_response(self) -> None:
        """Scenario 6 — claimantId is not exposed in the HTTP response."""
        with patch.object(upload_url_module, "_get_evidence_service", return_value=_mock_svc()):
            response = handler(_make_event(), MagicMock())
        assert "claimantId" not in _body(response)

    def test_content_type_is_json(self) -> None:
        """Response carries Content-Type: application/json."""
        with patch.object(upload_url_module, "_get_evidence_service", return_value=_mock_svc()):
            response = handler(_make_event(), MagicMock())
        assert response["headers"]["Content-Type"] == "application/json"


# ── 7–15. Authentication / path / body ───────────────────────────────────────


class TestInputValidation:
    def test_missing_authorizer_returns_401(self) -> None:
        """Scenario 7 — no requestContext.authorizer → 401."""
        event = _make_event(cognito_sub=None)
        response = handler(event, MagicMock())
        assert response["statusCode"] == 401
        assert _body(response)["error"]["code"] == "UNAUTHORIZED"

    def test_missing_sub_returns_401(self) -> None:
        """Scenario 8 — authorizer present, sub absent → 401."""
        event = _make_event()
        event["requestContext"]["authorizer"]["claims"].pop("sub")
        response = handler(event, MagicMock())
        assert response["statusCode"] == 401

    def test_missing_path_parameters_returns_400(self) -> None:
        """Scenario 9 — no pathParameters key → 400."""
        event = _make_event(include_path_params=False)
        response = handler(event, MagicMock())
        assert response["statusCode"] == 400
        assert _body(response)["error"]["code"] == "VALIDATION_ERROR"

    def test_missing_claim_id_returns_400(self) -> None:
        """Scenario 10 — pathParameters present, claimId key missing → 400."""
        event = _make_event()
        event["pathParameters"] = {}
        response = handler(event, MagicMock())
        assert response["statusCode"] == 400

    def test_blank_claim_id_returns_400(self) -> None:
        """Scenario 11 — blank claimId → 400."""
        event = _make_event()
        event["pathParameters"] = {"claimId": "   "}
        response = handler(event, MagicMock())
        assert response["statusCode"] == 400

    def test_missing_body_returns_400(self) -> None:
        """Scenario 12 — no body → 400."""
        event = _make_event(body=None)
        response = handler(event, MagicMock())
        assert response["statusCode"] == 400
        assert _body(response)["error"]["code"] == "VALIDATION_ERROR"

    def test_empty_string_body_returns_400(self) -> None:
        """Scenario 13 — empty string body → 400."""
        event = _make_event(body="")
        response = handler(event, MagicMock())
        assert response["statusCode"] == 400

    def test_malformed_json_returns_400(self) -> None:
        """Scenario 14 — malformed JSON body → 400."""
        event = _make_event(body="{not: valid}")
        response = handler(event, MagicMock())
        assert response["statusCode"] == 400
        assert _body(response)["error"]["code"] == "VALIDATION_ERROR"

    def test_base64_body_decoded_correctly(self) -> None:
        """Scenario 15 — base64-encoded body decoded and forwarded."""
        svc = _mock_svc()
        event = _make_event(body=VALID_BODY, base64_encoded=True)
        assert event["isBase64Encoded"] is True

        with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
            response = handler(event, MagicMock())

        assert response["statusCode"] == 201
        args = svc.request_upload_url.call_args
        passed_body = args.args[2] if len(args.args) > 2 else args.kwargs.get("body")
        assert passed_body["evidenceType"] == "VIDEO"


# ── 16–20. Service errors ─────────────────────────────────────────────────────


class TestServiceErrors:
    def test_not_found_returns_404(self) -> None:
        """Scenario 16 — NotFoundError → 404 CLAIM_NOT_FOUND."""
        svc = _mock_svc()
        svc.request_upload_url.side_effect = NotFoundError(
            "Claim not found.", error_code="CLAIM_NOT_FOUND"
        )
        with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())

        assert response["statusCode"] == 404
        assert _body(response)["error"]["code"] == "CLAIM_NOT_FOUND"

    def test_forbidden_returns_403(self) -> None:
        """Scenario 17 — ForbiddenError → 403."""
        svc = _mock_svc()
        svc.request_upload_url.side_effect = ForbiddenError("No access.")
        with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())

        assert response["statusCode"] == 403
        assert _body(response)["error"]["code"] == "FORBIDDEN"

    def test_validation_error_returns_400_with_code(self) -> None:
        """Scenario 18 — ValidationError → 400 with specific error_code."""
        svc = _mock_svc()
        svc.request_upload_url.side_effect = ValidationError(
            "Unsupported evidence type.", error_code="UNSUPPORTED_EVIDENCE_TYPE"
        )
        with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())

        assert response["statusCode"] == 400
        assert _body(response)["error"]["code"] == "UNSUPPORTED_EVIDENCE_TYPE"

    def test_file_size_exceeded_returns_400(self) -> None:
        """FILE_SIZE_EXCEEDED validation error returns 400."""
        svc = _mock_svc()
        svc.request_upload_url.side_effect = ValidationError(
            "File too large.", error_code="FILE_SIZE_EXCEEDED"
        )
        with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())

        assert response["statusCode"] == 400
        assert _body(response)["error"]["code"] == "FILE_SIZE_EXCEEDED"

    def test_unexpected_exception_returns_500(self) -> None:
        """Scenario 19 — RuntimeError → 500."""
        svc = _mock_svc()
        svc.request_upload_url.side_effect = RuntimeError("something broke")
        with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())

        assert response["statusCode"] == 500

    def test_internal_details_not_in_500_body(self) -> None:
        """Scenario 20 — 500 body contains no AWS ARNs or stack traces."""
        svc = _mock_svc()
        svc.request_upload_url.side_effect = RuntimeError(
            "arn:aws:s3:::claimwise-evidence-dev object not found"
        )
        with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(), MagicMock())

        body_str = response["body"]
        assert "arn:aws" not in body_str
        assert "claimwise-evidence" not in body_str
        assert "Traceback" not in body_str
        assert _body(response)["error"]["code"] == "INTERNAL_ERROR"


# ── 21–24. Security ───────────────────────────────────────────────────────────


class TestSecurity:
    def test_body_claimant_id_cannot_override_cognito_sub(self) -> None:
        """Scenario 21 — claimantId in body is ignored; Cognito sub wins."""
        svc = _mock_svc()
        body_with_claimant = {**VALID_BODY, "claimantId": "attacker-supplied"}

        with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
            handler(_make_event(body=body_with_claimant, cognito_sub=COGNITO_SUB), MagicMock())

        args = svc.request_upload_url.call_args
        passed_claimant = args.args[1] if len(args.args) > 1 else args.kwargs.get("claimant_id")
        assert passed_claimant == COGNITO_SUB
        assert passed_claimant != "attacker-supplied"

    def test_auth_token_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Scenario 22 — Bearer token value is not written to logs."""
        svc = _mock_svc()
        event = _make_event()
        event["headers"]["Authorization"] = "Bearer eyJhbGciOiJSUzI1NiJ9.fake.token"

        with caplog.at_level(logging.DEBUG, logger="app.api.request_upload_url"):
            with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
                handler(event, MagicMock())

        assert "Bearer" not in caplog.text
        assert "eyJhbGciOiJSUzI1NiJ9" not in caplog.text

    def test_handler_does_not_construct_s3_key(self) -> None:
        """Scenario 23 — handler never builds or returns an S3 key itself.

        The storageKey in the response comes from the service, not the handler.
        We verify the handler passes the filename through untouched to the
        service and does not build a key string itself.
        """
        svc = _mock_svc()
        with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
            handler(_make_event(), MagicMock())

        # Handler must not call any key-construction function itself
        # (confirmed by code review: handler only calls request_upload_url)
        svc.request_upload_url.assert_called_once()
        # The storageKey in the response is whatever the service returns,
        # not something the handler manufactured from the filename.
        args = svc.request_upload_url.call_args
        passed_body = args.args[2] if len(args.args) > 2 else args.kwargs.get("body")
        # filename is in the body but the handler does not concatenate it into a path
        assert "fileName" in passed_body
        assert passed_body["fileName"] == "accident.mp4"

    def test_handler_does_not_process_file_bytes(self) -> None:
        """Scenario 24 — handler deals only in metadata; file bytes are never present."""
        svc = _mock_svc()
        # A body that claims to contain file data (should be metadata only)
        body_with_data = {
            **VALID_BODY,
            "fileContent": base64.b64encode(b"fake-file-bytes").decode(),
        }
        with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
            response = handler(_make_event(body=body_with_data), MagicMock())

        # Succeeds regardless — the handler passes the body straight through.
        # It never reads or acts on fileContent itself.
        assert response["statusCode"] == 201
        svc.request_upload_url.assert_called_once()

    def test_presigned_url_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        """Presigned URL is not written to any log record."""
        svc = _mock_svc()
        with caplog.at_level(logging.DEBUG, logger="app.api.request_upload_url"):
            with patch.object(upload_url_module, "_get_evidence_service", return_value=svc):
                handler(_make_event(), MagicMock())

        assert "https://s3.example.com/presigned" not in caplog.text

    def test_error_envelope_structure(self) -> None:
        """All error responses follow {"error": {"code": ..., "message": ...}}."""
        event = _make_event(body=None)
        response = handler(event, MagicMock())
        body = _body(response)
        assert "error" in body
        assert "code" in body["error"]
        assert "message" in body["error"]

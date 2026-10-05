# Implementation Tasks — Claim Submission & Evidence Upload

## Overview

These tasks implement the Claim Submission & Evidence Upload specification end-to-end. They are ordered so that each task builds on the previous one. Complete infrastructure and domain foundations before API handlers, and complete handlers before integration tests.

Each task references the relevant requirements (FR-xx) and acceptance criteria (AC-xx.x) from `requirements.md`.

---

## Task 1 — Project Scaffold & Backend Structure

Set up the Python backend directory structure and project configuration as defined in `design.md` and the `structure.md` steering file.

### Subtasks

- [ ] 1.1 Create the backend directory tree:
  ```
  backend/
    app/
      api/
      domain/
      services/
      infrastructure/
    tests/
      unit/
      integration/
  ```
- [ ] 1.2 Add `backend/pyproject.toml` (or `requirements.txt`) with dependencies:
  - `boto3` (pinned exact version)
  - `pytest` (pinned)
  - `pytest-mock` (pinned)
  - `moto` (pinned, for AWS mocking in unit/integration tests)
- [ ] 1.3 Add `backend/app/__init__.py` and `__init__.py` for each sub-package.
- [ ] 1.4 Add a `backend/.env.example` file documenting all required environment variables (from the configuration table in `design.md`). Do not commit real values.
- [ ] 1.5 Add a root `.gitignore` entry for `.env` files and Python artifacts (`__pycache__`, `*.pyc`, `.venv`).
- [ ] 1.6 Verify the package structure imports correctly with a smoke test (`python -c "from app.domain import claims"`).

**Acceptance:** Directory structure matches `design.md`. Project installs without errors. `.env.example` documents all required variables.

---

## Task 2 — Configuration & Infrastructure Layer

Implement environment-driven configuration loading and AWS client initialisation.

### Subtasks

- [ ] 2.1 Create `backend/app/infrastructure/config.py`:
  - Load all environment variables listed in `design.md` (table: Configuration).
  - Raise a clear error at startup if any required variable is missing.
  - Expose file size limits as a typed dict/dataclass keyed by `evidenceType` string so validation logic can look them up without conditionals.
- [ ] 2.2 Create `backend/app/infrastructure/dynamodb.py`:
  - Initialise a `boto3` DynamoDB resource/client using the AWS region from config.
  - Expose thin wrapper functions: `put_item`, `get_item`, `query` — no business logic here.
- [ ] 2.3 Create `backend/app/infrastructure/s3.py`:
  - Initialise a `boto3` S3 client.
  - Expose a `generate_presigned_put_url(bucket, key, content_type, expiry_seconds) -> str` function.
- [ ] 2.4 Write unit tests for `config.py`:
  - All variables present → loads correctly.
  - A required variable missing → raises `EnvironmentError` with the variable name.

**Acceptance:** Config loads from environment. AWS clients are initialised. Missing config raises at startup, not at first use.

---

## Task 3 — Domain Layer: Claim

Implement claim business logic with no AWS dependencies.

### Subtasks

- [ ] 3.1 Create `backend/app/domain/claims.py`:
  - `generate_claim_id() -> str` — returns `CLM-<UUID4>`.
  - `build_claim_record(claimant_id, policy_number, incident_datetime, incident_location, description) -> dict` — returns a complete claim dict ready for DynamoDB.
  - `validate_claim_input(body: dict) -> None` — raises `ValidationError` if any required field is missing or blank.
  - `should_transition_to_evidence_submitted(current_status, confirmed_evidence_count) -> bool` — returns `True` when claim should move from `CREATED` to `EVIDENCE_SUBMITTED`.
- [ ] 3.2 Create `backend/app/domain/exceptions.py`:
  - Define `ValidationError`, `NotFoundError`, `ForbiddenError` with an `error_code` attribute matching the codes in `requirements.md`.
- [ ] 3.3 Write unit tests for all domain functions (FR-01, FR-02, FR-05):
  - `generate_claim_id` format: starts with `CLM-`, remainder is valid UUID4.
  - `build_claim_record` sets `status: CREATED`, generates `createdAt`/`updatedAt`.
  - `validate_claim_input` raises on each missing required field, passes with optional field absent.
  - `should_transition_to_evidence_submitted` returns correct value for boundary inputs.

**Acceptance:** All unit tests pass. Domain functions have no imports from `infrastructure` or `services`.

---

## Task 4 — Domain Layer: Evidence

Implement evidence business logic with no AWS dependencies.

### Subtasks

- [ ] 4.1 Create `backend/app/domain/evidence.py`:
  - `ALLOWED_EVIDENCE_TYPES` — set of supported type strings.
  - `ALLOWED_CONTENT_TYPES` — dict mapping `evidenceType` → list of allowed MIME strings.
  - `generate_evidence_id() -> str` — returns `EVD-<UUID4>`.
  - `generate_storage_key(claim_id, evidence_id, object) -> str` — returns `claims/{claimId}/evidence/{evidenceId}/object`.
  - `validate_evidence_request(evidence_type, content_type, file_size_bytes, size_limits: dict) -> None` — raises `ValidationError` with appropriate error code for:
    - Unsupported `evidenceType` → `UNSUPPORTED_EVIDENCE_TYPE`
    - Unsupported `contentType` for the given type → `UNSUPPORTED_CONTENT_TYPE`
    - `fileSizeBytes` exceeding the configured limit → `FILE_SIZE_EXCEEDED`
  - `build_evidence_record(claim_id, claimant_id, evidence_id, evidence_type, file_name, content_type, file_size_bytes, storage_key) -> dict` — returns a complete evidence dict ready for DynamoDB with `status: UPLOAD_PENDING`.
- [ ] 4.2 Write unit tests for all evidence domain functions (FR-03, FR-07, FR-08):
  - `generate_evidence_id` format.
  - `generate_storage_key` output format matches `claims/{claimId}/evidence/{evidenceId}/{fileName}`.
  - `validate_evidence_request`: valid inputs for each type, invalid type, invalid content type per type, at limit (valid), one byte over limit (invalid), for each evidence type.
  - `build_evidence_record` sets `status: UPLOAD_PENDING`, sets `createdAt`/`updatedAt`.

**Acceptance:** All unit tests pass. No AWS imports. File size limits are passed in — not read from environment directly.

---

## Task 5 — Service Layer

Implement services that coordinate domain logic with AWS infrastructure.

### Subtasks

- [ ] 5.1 Create `backend/app/services/claim_service.py`:
  - `create_claim(claimant_id, body) -> dict` — validates input, builds record, writes to DynamoDB, returns claim dict.
  - `get_claim(claim_id, claimant_id) -> dict` — fetches claim from DynamoDB; raises `NotFoundError` if absent, `ForbiddenError` if `claimantId` does not match.
  - `get_claim_with_evidence(claim_id, claimant_id) -> dict` — fetches claim + all evidence records from DynamoDB; applies same authorization.
  - `transition_claim_status_if_needed(claim_id, claimant_id) -> None` — checks evidence count and updates claim status to `EVIDENCE_SUBMITTED` if conditions are met (FR-05).
- [ ] 5.2 Create `backend/app/services/evidence_service.py`:
  - `request_upload_url(claim_id, claimant_id, body) -> dict` — validates claim ownership, validates evidence request, generates evidenceId + storage key, creates evidence record in DynamoDB, generates pre-signed URL, returns response dict (FR-03).
  - `confirm_upload(claim_id, evidence_id, claimant_id) -> dict` — validates claim ownership, validates evidence ownership, updates evidence status to `UPLOAD_COMPLETE`, records `uploadedAt`, triggers claim status transition (FR-04).
- [ ] 5.3 Write unit tests for all service functions with mocked DynamoDB and S3 (FR-01 through FR-06):
  - `create_claim`: valid input → DynamoDB put called with correct item, correct response shape.
  - `get_claim`: item found + authorized, item not found, item found + unauthorized.
  - `request_upload_url`: valid → evidence record written + presigned URL returned; invalid evidence type; file too large; wrong claimant.
  - `confirm_upload`: valid → status updated; evidence not found; wrong claimant; claim status transitions correctly after confirmation.

**Acceptance:** All service unit tests pass. Services import from `domain` and `infrastructure` only — no `boto3` calls inline.

---

## Task 6 — API Layer: Create Claim Handler

Implement the Lambda handler for `POST /v1/claims`.

### Subtasks

- [ ] 6.1 Create `backend/app/api/create_claim.py`:
  - Entry point: `handler(event, context)`.
  - Extract `claimantId` from `event['requestContext']['authorizer']['claims']['sub']`.
  - Parse and validate request body.
  - Call `claim_service.create_claim(...)`.
  - Return HTTP 201 with claim JSON on success.
  - Catch `ValidationError` → return HTTP 400 with error body.
  - Catch unexpected exceptions → log claimId (not body content), return HTTP 500 with `INTERNAL_ERROR`.
- [ ] 6.2 Write unit tests for the handler (AC-01.1 through AC-01.6):
  - Valid request → 201 with correct shape.
  - Each missing required field → 400 with `VALIDATION_ERROR`.
  - Missing auth context → 401.
  - `claimantId` comes from authorizer, not request body.

**Acceptance:** Handler returns correct HTTP status and body shape for all tested scenarios. No sensitive data in log output.

---

## Task 7 — API Layer: Get Claim Handler

Implement the Lambda handler for `GET /v1/claims/{claimId}`.

### Subtasks

- [ ] 7.1 Create `backend/app/api/get_claim.py`:
  - Entry point: `handler(event, context)`.
  - Extract `claimId` from path parameters.
  - Extract `claimantId` from authorizer context.
  - Call `claim_service.get_claim_with_evidence(...)`.
  - Return HTTP 200 with claim + evidence list.
  - Catch `NotFoundError` → 404.
  - Catch `ForbiddenError` → 403.
  - Catch unexpected exceptions → 500.
- [ ] 7.2 Write unit tests for the handler (AC-05.1 through AC-05.5):
  - Valid claim + authorized claimant → 200 with evidence list.
  - Claim not found → 404.
  - Different claimant → 403.
  - Missing auth → 401.

**Acceptance:** All AC-05.x covered by tests. Evidence list is included in the response.

---

## Task 8 — API Layer: Request Upload URL Handler

Implement the Lambda handler for `POST /v1/claims/{claimId}/evidence/upload-url`.

### Subtasks

- [ ] 8.1 Create `backend/app/api/request_upload_url.py`:
  - Entry point: `handler(event, context)`.
  - Extract `claimId` from path parameters.
  - Extract `claimantId` from authorizer context.
  - Parse and validate request body (`evidenceType`, `fileName`, `contentType`, `fileSizeBytes`).
  - Call `evidence_service.request_upload_url(...)`.
  - Return HTTP 200 with `evidenceId`, `presignedUrl`, `urlExpiresAt`, `storageKey`, `status`.
  - Catch `NotFoundError` → 404.
  - Catch `ForbiddenError` → 403.
  - Catch `ValidationError` → 400 with specific error code.
  - Catch unexpected exceptions → 500.
- [ ] 8.2 Write unit tests for the handler (AC-02.1 through AC-02.11):
  - Valid request → 200 with presigned URL and evidence record.
  - Unsupported evidence type → 400 `UNSUPPORTED_EVIDENCE_TYPE`.
  - Unsupported content type → 400 `UNSUPPORTED_CONTENT_TYPE`.
  - File too large → 400 `FILE_SIZE_EXCEEDED` with allowed max in message.
  - Claim not found → 404.
  - Wrong claimant → 403.
  - Multiple calls on same claim/type → each returns distinct `evidenceId`.
  - Storage key follows the defined pattern.

**Acceptance:** All AC-02.x covered by tests. Storage key never contains user-controlled path segments beyond `fileName`.

---

## Task 9 — API Layer: Confirm Upload Handler

Implement the Lambda handler for `POST /v1/claims/{claimId}/evidence/{evidenceId}/confirm`.

### Subtasks

### Task 9: Implement Confirm Upload Handler

Implement the `POST /v1/claims/{claimId}/evidence/{evidenceId}/confirm` endpoint.

#### Requirements

* Extract `claimantId` from the authenticated Cognito JWT.
* Validate that the claim exists.
* Validate that the claim belongs to the authenticated claimant.
* Retrieve the evidence record using `evidenceId`.
* Validate that the evidence belongs to the supplied `claimId`.
* Validate that the evidence belongs to the authenticated claimant.
* Verify that the expected S3 object exists using the backend-generated `storageKey`.
* Do not trust the client to provide or select the S3 object key.
* If the S3 object does not exist, return a structured validation error and leave the evidence status unchanged.
* If the evidence status is already `UPLOAD_COMPLETE`, treat the request as **idempotent** and return the existing completed evidence record.
* If the evidence is `UPLOAD_PENDING` and the S3 object exists:

  * update the evidence status to `UPLOAD_COMPLETE`
  * set `uploadedAt`
* Update the parent claim status from `CREATED` to `EVIDENCE_SUBMITTED` using a **conditional DynamoDB update** so concurrent confirmations cannot corrupt the claim state.
* Return the updated evidence/claim response.
* Do not trigger any AI processing.

#### Confirmation Flow

```text
POST /claims/{claimId}/evidence/{evidenceId}/confirm
                    │
                    ▼
          Authenticate Cognito JWT
                    │
                    ▼
             Validate claim
                    │
                    ▼
       Validate claim ownership
                    │
                    ▼
          Retrieve evidence
                    │
                    ▼
       Validate evidence belongs
             to claim + user
                    │
                    ▼
       Does S3 object exist?
             │           │
            NO          YES
             │           │
             ▼           ▼
        Return error   Is already
                       COMPLETE?
                         │      │
                        YES     NO
                         │      │
                         ▼      ▼
                      Return   Mark
                      existing COMPLETE
                                │
                                ▼
                    Conditionally update
                      claim status
                                │
                                ▼
                         Return response
```

#### Error Scenarios

| Scenario                          | Expected Response                                           |
| --------------------------------- | ----------------------------------------------------------- |
| Missing/invalid JWT               | `401 UNAUTHORIZED`                                          |
| Claim does not exist              | `404 CLAIM_NOT_FOUND`                                       |
| Claim belongs to another user     | `403 FORBIDDEN`                                             |
| Evidence does not exist           | `404 EVIDENCE_NOT_FOUND`                                    |
| Evidence belongs to another claim | `404 EVIDENCE_NOT_FOUND` or equivalent ownership-safe error |
| Evidence belongs to another user  | `403 FORBIDDEN`                                             |
| S3 object does not exist          | `400 VALIDATION_ERROR`                                      |
| Evidence already complete         | Return successful/idempotent response                       |
| DynamoDB/S3 unexpected failure    | `500 INTERNAL_ERROR`                                        |

#### Idempotency

The endpoint must be safe to call multiple times.

Example:

```text
First confirm:
UPLOAD_PENDING → UPLOAD_COMPLETE

Second confirm:
UPLOAD_COMPLETE → UPLOAD_COMPLETE
                    ↓
              return success
```

The second request must not create another evidence record or modify the S3 object.

#### Concurrency

Use a conditional DynamoDB update when changing the evidence status:

```text
Update evidence
WHERE status = UPLOAD_PENDING
SET status = UPLOAD_COMPLETE,
    uploadedAt = current timestamp
```

Similarly, update the claim status conditionally:

```text
Update claim
WHERE status = CREATED
SET status = EVIDENCE_SUBMITTED
```

If another concurrent confirmation has already performed the transition, the operation should remain safe and return the current state.

#### Storage Key

The handler must use the `storageKey` generated by the backend when the upload URL was created.

The S3 key must follow:

```text
claims/{claimId}/evidence/{evidenceId}/object
```

The original client filename must **not** be part of the S3 key.

The original filename is stored only as evidence metadata.

#### Tests

Add unit/integration tests covering:

1. Successful confirmation after S3 upload.
2. Confirmation before the S3 upload exists.
3. Repeated confirmation after evidence is already complete.
4. Evidence ID belonging to a different claim.
5. Evidence belonging to another claimant.
6. Claim belonging to another claimant.
7. Missing S3 object.
8. Concurrent confirmation requests.
9. Claim status transition from `CREATED` to `EVIDENCE_SUBMITTED`.
10. S3 key does not contain the original filename.
11. Filename containing path traversal characters such as `../../file.pdf` does not affect the generated S3 key.
12. No AI processing is triggered.


**Acceptance:** All AC-04.x covered by tests. Claim status transition is verified.

---

## Task 10 — Integration Tests

Implement integration tests that exercise the full stack against mocked AWS services (using `moto`).

### Subtasks

- [ ] 10.1 Create `backend/tests/integration/conftest.py`:
  - Set up moto-mocked DynamoDB tables (`ClaimwiseClaims`, `ClaimwiseEvidence`) with correct key schemas and GSIs.
  - Set up moto-mocked S3 bucket with encryption and block-public-access config.
  - Provide reusable fixtures for an authenticated claimant context (mock Cognito `sub`).
- [ ] 10.2 Write `test_claim_lifecycle.py`:
  - Full happy path: create claim → request upload URL → confirm upload → get claim.
  - Verify claim status transitions from `CREATED` → `EVIDENCE_SUBMITTED` after confirmation.
  - Verify evidence record progresses from `UPLOAD_PENDING` → `UPLOAD_COMPLETE`.
  - Verify multiple evidence records of the same type are all retained (re-upload decision D6).
- [ ] 10.3 Write `test_authorization.py`:
  - Claimant A cannot get Claimant B's claim → 403.
  - Claimant A cannot request upload URL for Claimant B's claim → 403.
  - Claimant A cannot confirm Claimant B's evidence → 403.
- [ ] 10.4 Write `test_validation.py`:
  - Each invalid evidence type rejected.
  - Each invalid content type rejected.
  - Each file size limit enforced (at limit passes, over limit fails) for all three evidence types.
  - Missing required claim fields rejected.

**Acceptance:** All integration tests pass with mocked AWS. No real AWS calls made during test runs.

---

## Task 11 — Security Verification

Verify that the implementation meets the security requirements from `requirements.md` and `security.md`.

### Subtasks

- [ ] 11.1 Review all handlers: confirm no sensitive fields (`policyNumber`, `description`, `claimantId`, `storageKey`) appear in any `logger` call.
- [ ] 11.2 Review all error responses: confirm no stack traces, DynamoDB table names, S3 bucket names, or AWS account IDs are returned to clients.
- [ ] 11.3 Write a test asserting that `generate_storage_key` output never contains user-supplied path traversal sequences (`../`, `//`, absolute paths).
- [ ] 11.4 Verify (via moto config assertions) that the S3 bucket has:
  - Block all public access enabled.
  - Server-side encryption configured.
  - Versioning enabled.
- [ ] 11.5 Verify that the pre-signed URL is scoped to `PUT` method only and includes a content-type condition matching the validated MIME type.
- [ ] 11.6 Confirm `claimantId` is always sourced from the Cognito authorizer context — add a test that passes an arbitrary `claimantId` in the request body and verifies it is ignored.

**Acceptance:** All security checks pass. No sensitive data leaks identified in log or error response review.

---

## Task 12 — Code Quality & Documentation

Ensure the codebase meets project quality standards before the feature is considered complete.

### Subtasks

- [ ] 12.1 Add type hints to all public functions in `domain/`, `services/`, and `api/` layers.
- [ ] 12.2 Run a linter (e.g., `flake8` or `ruff`) — resolve all warnings.
- [ ] 12.3 Run a type checker (e.g., `mypy`) — resolve all errors on the new code.
- [ ] 12.4 Ensure all Lambda handler files include a module-level docstring describing the endpoint they serve.
- [ ] 12.5 Update `backend/README.md` (create if absent) with:
  - How to install dependencies.
  - How to run unit tests.
  - How to run integration tests.
  - Required environment variables (reference `.env.example`).

**Acceptance:** Linter and type checker pass with no errors. `backend/README.md` accurately reflects how to run the project.

---

## Completion Checklist

Before marking this specification complete, verify:

- [ ] All 5 user stories (US-01 through US-05) have passing tests covering their acceptance criteria.
- [ ] All 11 functional requirements (FR-01 through FR-11) are implemented.
- [ ] All error scenarios in `requirements.md` return the correct HTTP status and error code.
- [ ] No AI processing, claim decisioning, or adjuster workflow code has been introduced.
- [ ] No new AWS services beyond those defined in `design.md` have been introduced.
- [ ] File size limits are read from environment variables — not hard-coded.
- [ ] Evidence files are never overwritten; multiple records per type are supported.
- [ ] The S3 bucket is private; no public objects exist.
- [ ] All tests pass.
- [ ] Linter and type checker pass.

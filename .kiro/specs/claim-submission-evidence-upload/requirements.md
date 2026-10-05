# Requirements — Claim Submission & Evidence Upload

## Overview

This specification covers the first ClaimWise AI feature: allowing an authenticated insurance claimant to create a new claim and upload supporting evidence for a motor vehicle accident.

This is a foundational capability. All subsequent AI processing features depend on the claim and evidence records created here.

---

## Design Assumptions & Explicit Decisions

The following decisions were made prior to writing this specification and must be respected throughout implementation.

| # | Decision | Detail |
|---|---|---|
| D1 | Authentication | Amazon Cognito. All APIs require authentication. Unauthenticated submission is not permitted. |
| D2 | Upload mechanism | Pre-signed S3 URLs. Files are not proxied through Lambda or API Gateway. |
| D3 | File size limits | VIDEO: 500 MB, AUDIO: 50 MB, POLICY_DOCUMENT: 10 MB. Configurable, not hard-coded. |
| D4 | Claim metadata | Required fields: policyNumber, incidentDateTime, incidentLocation. Optional: description. Generated: claimId, claimantId, status, createdAt, updatedAt. |
| D5 | Multi-file upload | Each evidence file is uploaded independently via a separate pre-signed URL request. |
| D6 | Re-upload / versioning | Multiple evidence records of the same type are allowed. Files are never overwritten. Each has a unique evidenceId and storage key. Evidence status supports future lifecycle management. |
| D7 | Storage key generation | The backend generates all S3 storage keys. Clients cannot choose storage locations. |
| D8 | AI processing | Explicitly out of scope. No AI analysis, transcription, or document extraction in this specification. |

---

## Functional Requirements

### FR-01 — Claim Creation

- The system shall provide an API endpoint to create a new claim.
- The endpoint shall require a valid Cognito authentication token.
- The system shall extract the claimant identity (`claimantId`) from the authenticated Cognito identity — the client shall not supply it.
- The client shall supply: `policyNumber`, `incidentDateTime`, `incidentLocation`, and optionally `description`.
- The system shall generate a unique `claimId`.
- The system shall set the initial claim `status` to `CREATED`.
- The system shall record `createdAt` and `updatedAt` timestamps.
- The system shall persist the claim metadata.
- The system shall return the created claim record in the response.

### FR-02 — Claim ID Generation

- Claim IDs shall be unique across all claims.
- Claim IDs shall follow the format `CLM-<UUID>` (e.g., `CLM-550e8400-e29b-41d4-a716-446655440000`).
- Claim IDs shall be generated server-side and shall not be supplied by the client.

### FR-03 — Evidence Upload URL Request

- The system shall provide an API endpoint to request a pre-signed S3 upload URL for a specific evidence file.
- The endpoint shall require a valid Cognito authentication token.
- The endpoint shall require a valid `claimId` that belongs to the authenticated claimant.
- The client shall supply: `evidenceType`, `fileName`, and `contentType`.
- The system shall validate that `evidenceType` is one of the supported types: `VIDEO`, `AUDIO`, `POLICY_DOCUMENT`.
- The system shall validate that `contentType` is an acceptable MIME type for the given `evidenceType`.
- The system shall validate that the declared file size does not exceed the configured limit for the evidence type (supplied by the client as `fileSizeBytes`).
- The system shall generate a unique `evidenceId`.
- The system shall generate a unique, backend-controlled S3 storage key for the file.
- The system shall generate a time-limited pre-signed S3 PUT URL.
- The system shall create an evidence metadata record with status `UPLOAD_PENDING`.
- The system shall return the `evidenceId`, pre-signed URL, and expiry time to the client.

### FR-04 — Evidence Upload Confirmation

- The system shall provide an API endpoint for the client to confirm that a direct S3 upload has completed.
- The endpoint shall require a valid Cognito authentication token.
- The endpoint shall require a valid `claimId` and `evidenceId` belonging to the authenticated claimant.
- On confirmation, the system shall update the evidence record status to `UPLOAD_COMPLETE`.
- The system shall record the `uploadedAt` timestamp.
- The system shall return the updated evidence record.
- The system shall verify that the expected S3 object exists before
  transitioning evidence from UPLOAD_PENDING to UPLOAD_COMPLETE.

- If the evidence is already UPLOAD_COMPLETE, repeated confirmation
  requests shall return the existing completed evidence record
  idempotently.

### FR-05 — Claim Status Transitions

- A claim shall begin in `CREATED` status.
- When at least one evidence item reaches `UPLOAD_COMPLETE`, the claim status shall transition to `EVIDENCE_SUBMITTED`.
- Status transitions shall be recorded with a timestamp.
- No other status transitions are in scope for this specification.
- Claim status transition shall be performed using a conditional
  DynamoDB update so that concurrent confirmations are handled safely.

### FR-06 — Claim Retrieval

- The system shall provide an API endpoint to retrieve a claim and its associated evidence records.
- The endpoint shall require a valid Cognito authentication token.
- A claimant shall only be permitted to retrieve claims associated with their own `claimantId`.
- The response shall include the claim metadata and the list of associated evidence records.

### FR-07 — Supported Evidence Types

The system must support the following evidence types in Sprint 1:

| evidenceType | Accepted MIME Types | Max Size |
|---|---|---|
| `VIDEO` | `video/mp4`, `video/quicktime`, `video/x-msvideo` | 500 MB |
| `AUDIO` | `audio/mpeg`, `audio/mp4`, `audio/wav`, `audio/ogg` | 50 MB |
| `POLICY_DOCUMENT` | `application/pdf` | 10 MB |

The evidence type system shall be designed to allow new types to be added without major redesign.

### FR-08 — File Validation

- The system shall validate the `evidenceType` against the allowed list.
- The system shall validate the `contentType` against the allowed MIME types for the given `evidenceType`.
- The system shall validate that `fileSizeBytes` does not exceed the configured maximum for the evidence type.
- File size limits shall be read from configuration, not hard-coded.
- Validation shall occur before a pre-signed URL is issued.
- Invalid requests shall be rejected with an informative error response.

### FR-09 — Storage Strategy

- All evidence files shall be stored in a private S3 bucket.
- No S3 objects shall have public access.
- The backend shall generate all S3 object keys using the pattern: `claims/{claimId}/evidence/{evidenceId}/{fileName}`
- The bucket shall have server-side encryption enabled (SSE-KMS or SSE-S3).
- Pre-signed upload URLs shall have a short expiry (default: 15 minutes, configurable).
- S3 object keys shall not contain the client-supplied original filename.
- The original filename shall be stored only as evidence metadata.
- The backend shall generate an opaque object key using claimId and evidenceId.

### FR-10 — Error Handling

- The API shall return structured JSON error responses for all failure cases.
- Error responses shall include an error code and a human-readable message.
- Error responses shall not expose internal stack traces, storage keys, or AWS resource names.
- The system shall handle the following error scenarios gracefully (see Error Scenarios section).

### FR-11 — Security

- All endpoints shall require a valid Cognito JWT.
- API Gateway or the Lambda authorizer shall validate the token before the handler executes.
- A claimant shall not be able to access, modify, or upload evidence for another claimant's claim.
- S3 object keys shall not be guessable or user-controlled.
- Pre-signed URLs shall be scoped to the specific object key and HTTP method (PUT only).
- No sensitive claim or claimant information shall appear in application logs.
- All data shall be encrypted at rest and in transit.

---

## User Stories

### US-01 — Create a Claim

> As an authenticated insurance claimant,
> I want to create a new claim by providing my policy number, the date and time of the incident, the location, and an optional description,
> so that ClaimWise AI can track my claim and associate evidence with it.

**Acceptance Criteria:**

- AC-01.1: Given a valid Cognito token and valid claim fields, the API returns HTTP 201 with the created claim including a generated `claimId`, `claimantId`, `status: CREATED`, `createdAt`, and `updatedAt`.
- AC-01.2: Given a missing required field (`policyNumber`, `incidentDateTime`, or `incidentLocation`), the API returns HTTP 400 with a descriptive error.
- AC-01.3: Given no authentication token, the API returns HTTP 401.
- AC-01.4: The `claimantId` in the created claim matches the authenticated user's Cognito subject (`sub`).
- AC-01.5: The `claimId` follows the `CLM-<UUID>` format.
- AC-01.6: Two concurrent claim creation requests with identical data produce two distinct claims with different `claimId` values.

---

### US-02 — Request an Evidence Upload URL

> As an authenticated claimant with an existing claim,
> I want to request a pre-signed upload URL for an evidence file,
> so that I can upload the file directly to secure storage without routing it through the API.

**Acceptance Criteria:**

- AC-02.1: Given a valid token, a valid `claimId` belonging to the claimant, and a valid `evidenceType`, `contentType`, `fileName`, and `fileSizeBytes`, the API returns HTTP 200 with a `presignedUrl`, `evidenceId`, and `urlExpiresAt`.
- AC-02.2: The returned URL is a valid S3 pre-signed PUT URL scoped to the correct object key.
- AC-02.3: An evidence record is created with status `UPLOAD_PENDING` before the URL is returned.
- AC-02.4: Given an unsupported `evidenceType`, the API returns HTTP 400.
- AC-02.5: Given a `contentType` not permitted for the declared `evidenceType`, the API returns HTTP 400.
- AC-02.6: Given a `fileSizeBytes` exceeding the configured limit for the evidence type, the API returns HTTP 400 including the allowed maximum.
- AC-02.7: Given a `claimId` belonging to a different claimant, the API returns HTTP 403.
- AC-02.8: Given a non-existent `claimId`, the API returns HTTP 404.
- AC-02.9: Given no authentication token, the API returns HTTP 401.
- AC-02.10: Multiple upload URL requests for the same `evidenceType` on the same claim are all accepted; each produces a distinct `evidenceId` and storage key.
- AC-02.11: The S3 object key is generated by the backend and follows the defined pattern.

---

### US-03 — Upload Evidence Directly to S3

> As an authenticated claimant,
> I want to upload my evidence file directly to S3 using the pre-signed URL,
> so that large files are transferred efficiently without passing through the API.

**Acceptance Criteria:**

- AC-03.1: A client using the pre-signed URL can successfully PUT a file to S3 within the expiry window.
- AC-03.2: A client attempting to use an expired pre-signed URL receives an S3 error (HTTP 403).
- AC-03.3: The uploaded file is stored at the key generated by the backend.
- AC-03.4: The uploaded file is not publicly accessible.

---

### US-04 — Confirm Evidence Upload

> As an authenticated claimant,
> I want to confirm that my file has been successfully uploaded,
> so that the system records the completed upload and can track evidence status.

**Acceptance Criteria:**

- AC-04.1: Given a valid token, valid `claimId` and `evidenceId` belonging to the claimant, the API returns HTTP 200 with the updated evidence record showing `status: UPLOAD_COMPLETE` and `uploadedAt`.
- AC-04.2: Given a `claimId` or `evidenceId` belonging to another claimant, the API returns HTTP 403.
- AC-04.3: Given a non-existent `evidenceId`, the API returns HTTP 404.
- AC-04.4: Given no authentication token, the API returns HTTP 401.
- AC-04.5: After at least one evidence record is confirmed, the parent claim status transitions to `EVIDENCE_SUBMITTED`.

---

### US-05 — Retrieve a Claim

> As an authenticated claimant,
> I want to retrieve my claim and see all associated evidence,
> so that I can verify what has been submitted.

**Acceptance Criteria:**

- AC-05.1: Given a valid token and a `claimId` belonging to the claimant, the API returns HTTP 200 with the claim and its evidence list.
- AC-05.2: Given a `claimId` belonging to another claimant, the API returns HTTP 403.
- AC-05.3: Given a non-existent `claimId`, the API returns HTTP 404.
- AC-05.4: Given no authentication token, the API returns HTTP 401.
- AC-05.5: The evidence list includes all evidence records for the claim regardless of status.

---

## Error Scenarios

| Scenario | Expected HTTP Status | Error Code |
|---|---|---|
| Missing authentication token | 401 | `UNAUTHORIZED` |
| Invalid / expired Cognito token | 401 | `UNAUTHORIZED` |
| Accessing another claimant's claim | 403 | `FORBIDDEN` |
| Claim not found | 404 | `CLAIM_NOT_FOUND` |
| Evidence record not found | 404 | `EVIDENCE_NOT_FOUND` |
| Missing required claim field | 400 | `VALIDATION_ERROR` |
| Unsupported evidence type | 400 | `UNSUPPORTED_EVIDENCE_TYPE` |
| Unsupported content type for evidence type | 400 | `UNSUPPORTED_CONTENT_TYPE` |
| File size exceeds limit | 400 | `FILE_SIZE_EXCEEDED` |
| Internal server error | 500 | `INTERNAL_ERROR` |

All error responses shall use the structure:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "incidentDateTime is required."
  }
}
```

---

## Out of Scope

The following are explicitly excluded from this specification:

- AI-based analysis of any evidence (video, audio, document)
- Audio transcription
- Policy document extraction
- Fraud detection
- Claim decision or recommendation
- Human adjuster / assessor workflow
- Claim listing / search (list all claims for a claimant)
- Evidence deletion
- Evidence lifecycle beyond `UPLOAD_PENDING` → `UPLOAD_COMPLETE`
- Payment processing
- Claimant registration or profile management
- Email / notification on upload

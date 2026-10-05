# Technical Design — Claim Submission & Evidence Upload

## Architecture Overview

This feature follows the existing ClaimWise AI architecture: a serverless AWS backend exposed via API Gateway, with Lambda handlers, DynamoDB for metadata persistence, and S3 for evidence file storage. Authentication is handled by Amazon Cognito.

```
Claimant (Browser / Mobile)
        |
        | HTTPS + Cognito JWT
        v
API Gateway (REST, v1)
        |
        | Lambda Authorizer (Cognito token validation)
        v
Lambda Handlers
        |
        +---> DynamoDB (Claim & Evidence metadata)
        |
        +---> S3 (Pre-signed URL generation)
                |
                | Client uploads directly
                v
        Private S3 Bucket (Evidence files)
```

No AI processing occurs in this flow. Evidence is stored and its metadata recorded. Downstream async processing will be introduced in a later specification.

---

## API Contract

Base path: `/v1`

All endpoints require the `Authorization: Bearer <cognito-id-token>` header.

---

### POST /v1/claims

Create a new claim.

**Request body:**
```json
{
  "policyNumber": "POL-123456",
  "incidentDateTime": "2026-09-15T14:30:00Z",
  "incidentLocation": "123 Main St, Springfield",
  "description": "Rear-ended at traffic lights."
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `policyNumber` | string | Yes | Client-supplied policy reference |
| `incidentDateTime` | string (ISO 8601) | Yes | UTC datetime of accident |
| `incidentLocation` | string | Yes | Free-text location description |
| `description` | string | No | Optional claimant narrative |

**Response — 201 Created:**
```json
{
  "claimId": "CLM-550e8400-e29b-41d4-a716-446655440000",
  "claimantId": "us-east-1:cognito-sub-uuid",
  "policyNumber": "POL-123456",
  "incidentDateTime": "2026-09-15T14:30:00Z",
  "incidentLocation": "123 Main St, Springfield",
  "description": "Rear-ended at traffic lights.",
  "status": "CREATED",
  "evidence": [],
  "createdAt": "2026-09-30T10:00:00Z",
  "updatedAt": "2026-09-30T10:00:00Z"
}
```

---

### GET /v1/claims/{claimId}

Retrieve a claim and its evidence list.

**Path parameter:** `claimId`

**Response — 200 OK:**
```json
{
  "claimId": "CLM-550e8400-e29b-41d4-a716-446655440000",
  "claimantId": "us-east-1:cognito-sub-uuid",
  "policyNumber": "POL-123456",
  "incidentDateTime": "2026-09-15T14:30:00Z",
  "incidentLocation": "123 Main St, Springfield",
  "description": "Rear-ended at traffic lights.",
  "status": "EVIDENCE_SUBMITTED",
  "evidence": [
    {
      "evidenceId": "EVD-7f3c9a12-...",
      "evidenceType": "VIDEO",
      "fileName": "accident-clip.mp4",
      "contentType": "video/mp4",
      "fileSizeBytes": 104857600,
      "status": "UPLOAD_COMPLETE",
      "storageKey": "claims/CLM-.../evidence/EVD-.../accident-clip.mp4",
      "uploadedAt": "2026-09-30T10:05:00Z",
      "createdAt": "2026-09-30T10:01:00Z",
      "updatedAt": "2026-09-30T10:05:00Z"
    }
  ],
  "createdAt": "2026-09-30T10:00:00Z",
  "updatedAt": "2026-09-30T10:05:00Z"
}
```

---

### POST /v1/claims/{claimId}/evidence/upload-url

Request a pre-signed S3 upload URL for an evidence file.

**Path parameter:** `claimId`

**Request body:**
```json
{
  "evidenceType": "VIDEO",
  "fileName": "accident-clip.mp4",
  "contentType": "video/mp4",
  "fileSizeBytes": 104857600
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `evidenceType` | enum | Yes | `VIDEO`, `AUDIO`, `POLICY_DOCUMENT` |
| `fileName` | string | Yes | Original filename, used in storage key |
| `contentType` | string | Yes | MIME type — validated against allowed list |
| `fileSizeBytes` | integer | Yes | Declared size in bytes — validated against configured limit |

**Response — 200 OK:**
```json
{
  "evidenceId": "EVD-7f3c9a12-4b2e-4a8f-9c1d-2e3f4a5b6c7d",
  "claimId": "CLM-550e8400-e29b-41d4-a716-446655440000",
  "presignedUrl": "https://s3.amazonaws.com/claimwise-evidence/claims/CLM-.../evidence/EVD-...?X-Amz-...",
  "urlExpiresAt": "2026-09-30T10:16:00Z",
  "storageKey": "claims/CLM-.../evidence/EVD-.../accident-clip.mp4",
  "status": "UPLOAD_PENDING"
}
```

---

### POST /v1/claims/{claimId}/evidence/{evidenceId}/confirm

Confirm a completed direct S3 upload.

**Path parameters:** `claimId`, `evidenceId`

**Request body:** _(empty)_

**Response — 200 OK:**
```json
{
  "evidenceId": "EVD-7f3c9a12-...",
  "claimId": "CLM-550e8400-...",
  "evidenceType": "VIDEO",
  "fileName": "accident-clip.mp4",
  "contentType": "video/mp4",
  "fileSizeBytes": 104857600,
  "status": "UPLOAD_COMPLETE",
  "storageKey": "claims/CLM-.../evidence/EVD-.../accident-clip.mp4",
  "uploadedAt": "2026-09-30T10:05:00Z",
  "createdAt": "2026-09-30T10:01:00Z",
  "updatedAt": "2026-09-30T10:05:00Z"
}
```

---

## Data Models

### Claim Record (DynamoDB)

| Attribute | Type | Notes |
|---|---|---|
| `claimId` | String (PK) | `CLM-<UUID>` |
| `claimantId` | String (GSI PK) | Cognito `sub` |
| `policyNumber` | String | Client-supplied |
| `incidentDateTime` | String | ISO 8601 UTC |
| `incidentLocation` | String | Free text |
| `description` | String | Optional |
| `status` | String | `CREATED` \| `EVIDENCE_SUBMITTED` |
| `createdAt` | String | ISO 8601 UTC |
| `updatedAt` | String | ISO 8601 UTC |

**DynamoDB table:** `ClaimwiseClaims`
**Primary key:** `claimId` (partition key)
**GSI:** `claimantId-index` — partition key `claimantId`, for authorization lookups

---

### Evidence Record (DynamoDB)

| Attribute | Type | Notes |
|---|---|---|
| `evidenceId` | String (PK) | `EVD-<UUID>` |
| `claimId` | String (GSI PK) | Parent claim reference |
| `claimantId` | String | Denormalized for authorization |
| `evidenceType` | String | `VIDEO` \| `AUDIO` \| `POLICY_DOCUMENT` |
| `fileName` | String | Original filename |
| `contentType` | String | Validated MIME type |
| `fileSizeBytes` | Number | Declared size in bytes |
| `storageKey` | String | Backend-generated S3 key |
| `status` | String | `UPLOAD_PENDING` \| `UPLOAD_COMPLETE` |
| `uploadedAt` | String | ISO 8601 UTC — set on confirmation |
| `createdAt` | String | ISO 8601 UTC |
| `updatedAt` | String | ISO 8601 UTC |

**DynamoDB table:** `ClaimwiseEvidence`
**Primary key:** `evidenceId` (partition key)
**GSI:** `claimId-index` — partition key `claimId`, for claim-scoped evidence queries

> `claimantId` is denormalized into the evidence record to allow efficient authorization checks without a cross-table lookup on every request.

---

## Claim Status Transitions

```
CREATED
   |
   | (first UPLOAD_COMPLETE evidence confirmed)
   v
EVIDENCE_SUBMITTED
```

- `CREATED` — claim record exists; no confirmed uploads yet.
- `EVIDENCE_SUBMITTED` — at least one evidence record has status `UPLOAD_COMPLETE`.

Further status transitions (e.g., `UNDER_REVIEW`, `ASSESSMENT_COMPLETE`) are out of scope and will be defined in later specifications.

---

## Evidence Status Transitions

```
UPLOAD_PENDING
      |
      | (client calls confirm endpoint)
      v
UPLOAD_COMPLETE
```

Future statuses (`PROCESSING`, `PROCESSED`, `REJECTED`, `SUPERSEDED`) are reserved for later specifications and should not be implemented here. The status field must be designed as an extensible enum/string.

---

## S3 Storage Structure

**Bucket name:** `claimwise-evidence-{environment}` (e.g., `claimwise-evidence-dev`)

**Object key pattern:**
```
claims/{claimId}/evidence/{evidenceId}/object
```

**Example:**
```
claims/CLM-550e8400-e29b-41d4-a716-446655440000/evidence/EVD-7f3c9a12-4b2e-4a8f-9c1d-2e3f4a5b6c7d/accident-clip.mp4
```

**Bucket configuration:**
- Block all public access: enabled
- Server-side encryption: SSE-KMS (preferred) or SSE-S3
- Versioning: enabled (protects against accidental deletion)
- Lifecycle policy: define retention rules (deferred to infrastructure spec)

**Pre-signed URL configuration:**
- HTTP method: `PUT` only
- Expiry: 15 minutes (configurable via environment variable `PRESIGNED_URL_EXPIRY_SECONDS`)
- Scoped to the exact generated object key
- S3 CORS configuration will be defined as part of frontend/infrastructure implementation if browser-based direct uploads require it.
---

## File Validation Rules

Validation occurs in the Lambda handler **before** a pre-signed URL is issued.

### Allowed MIME types per evidence type

| evidenceType | Allowed contentType values |
|---|---|
| `VIDEO` | `video/mp4`, `video/quicktime`, `video/x-msvideo` |
| `AUDIO` | `audio/mpeg`, `audio/mp4`, `audio/wav`, `audio/ogg` |
| `POLICY_DOCUMENT` | `application/pdf` |

### File size limits (configurable via environment variables)

| evidenceType | Environment variable | Default |
|---|---|---|
| `VIDEO` | `MAX_FILE_SIZE_VIDEO_BYTES` | 524288000 (500 MB) |
| `AUDIO` | `MAX_FILE_SIZE_AUDIO_BYTES` | 52428800 (50 MB) |
| `POLICY_DOCUMENT` | `MAX_FILE_SIZE_POLICY_DOCUMENT_BYTES` | 10485760 (10 MB) |

> These are application-level validations on the declared `fileSizeBytes`. They do not enforce a byte-level check on the actual S3 upload. A future enhancement could use S3 Object Lambda or an S3 event trigger to verify actual uploaded size post-upload.

---

## Backend Component Design

### Lambda Functions

| Function | Trigger | Responsibility |
|---|---|---|
| `createClaim` | POST /v1/claims | Validate input, generate claimId, write to DynamoDB, return claim |
| `getClaim` | GET /v1/claims/{claimId} | Authorize, fetch claim + evidence from DynamoDB, return |
| `requestUploadUrl` | POST /v1/claims/{claimId}/evidence/upload-url | Authorize, validate, generate evidenceId + S3 key, create evidence record, return pre-signed URL |
| `confirmUpload` | POST /v1/claims/{claimId}/evidence/{evidenceId}/confirm | Authorize, update evidence status, update claim status if needed |

### Python Backend Layer Structure

Following the layered architecture defined in `tech.md`:

```
backend/
  app/
    api/              # Lambda handlers — request parsing, response formatting
    domain/           # Business logic — claim creation, status transitions, validation rules
    services/         # DynamoDB service, S3 service (pre-signed URL generation)
    infrastructure/   # AWS SDK clients, configuration loading
  tests/
    unit/
    integration/
```

### Configuration

All environment-specific values shall be read from environment variables injected at Lambda deployment time:

| Variable | Purpose |
|---|---|
| `CLAIMS_TABLE_NAME` | DynamoDB claims table name |
| `EVIDENCE_TABLE_NAME` | DynamoDB evidence table name |
| `EVIDENCE_BUCKET_NAME` | S3 evidence bucket name |
| `PRESIGNED_URL_EXPIRY_SECONDS` | Pre-signed URL TTL (default: 900) |
| `MAX_FILE_SIZE_VIDEO_BYTES` | Max video upload size |
| `MAX_FILE_SIZE_AUDIO_BYTES` | Max audio upload size |
| `MAX_FILE_SIZE_POLICY_DOCUMENT_BYTES` | Max PDF upload size |
| `COGNITO_USER_POOL_ID` | For token validation |
| `AWS_REGION` | AWS region |

---

## Authentication & Authorization Design

### Authentication

- API Gateway is configured with a Cognito User Pool Authorizer.
- Every request must include `Authorization: Bearer <id-token>`.
- API Gateway validates the token and rejects invalid/expired tokens with HTTP 401 before the Lambda is invoked.
- The Lambda receives the validated Cognito claims in the `requestContext.authorizer` event context.
- The `claimantId` is extracted from the Cognito `sub` claim — it is never accepted from the request body or path.

### Authorization

- On every claim or evidence operation, the handler fetches the claim from DynamoDB and compares the stored `claimantId` with the authenticated user's `sub`.
- If they do not match, the handler returns HTTP 403.
- This check occurs before any mutation or data return.

---

## Security Design

| Concern | Control |
|---|---|
| Unauthenticated access | Cognito authorizer on API Gateway; all endpoints require valid JWT |
| Cross-claimant access | `claimantId` ownership check in every handler |
| Arbitrary S3 key injection | Backend generates all storage keys; client supplies only `fileName` for naming reference |
| Public S3 access | Bucket blocks all public access; no bucket policy grants public read |
| Pre-signed URL scope | URLs are scoped to a single PUT on a specific object key with a 15-minute TTL |
| Data at rest | S3 SSE-KMS; DynamoDB encryption enabled |
| Data in transit | API Gateway enforces HTTPS; S3 pre-signed URLs are HTTPS only |
| Sensitive data in logs | Handlers log claimId and evidenceId only; no policy numbers, descriptions, or personal data in logs |
| Stack traces in errors | Lambda handlers catch all exceptions; only a generic error code and message are returned |
| Large file bypass | File size is declared by client and validated at URL issuance; actual upload capped by S3 pre-signed URL conditions where possible |

---

## Testing Requirements

### Unit Tests

Cover the following in isolation (all AWS dependencies mocked):

- Claim creation: valid input, each missing required field, duplicate claimId (retry logic)
- Claim ID generation format
- Evidence type validation: all valid types, invalid type
- Content type validation: valid type per evidence type, invalid type per evidence type
- File size validation: at limit, over limit, under limit, for each evidence type
- Storage key generation format
- Authorization check: matching claimantId, mismatching claimantId
- Claim status transition: CREATED → EVIDENCE_SUBMITTED trigger
- Evidence status transition: UPLOAD_PENDING → UPLOAD_COMPLETE
- Error response format

### Integration Tests

Cover the following against real (or localstack) AWS services:

- Full claim creation and retrieval round-trip
- Upload URL request creates evidence record in DynamoDB
- Upload confirmation updates evidence and claim status in DynamoDB
- Authorization: claimant A cannot access claimant B's claim
- Invalid token rejected at API Gateway

### API / Contract Tests

- All endpoints return the documented response shapes
- All error scenarios return the correct HTTP status and error code
- Pre-signed URL is well-formed and usable for a PUT request

### Security Tests

- Unauthenticated requests return 401 on all endpoints
- Cross-claimant access returns 403 on all endpoints
- S3 bucket is not publicly accessible
- Pre-signed URL cannot be used after expiry

---

## Infrastructure Notes

> Infrastructure-as-code implementation is not part of this spec but the following resources are required:

- DynamoDB table: `ClaimwiseClaims` with GSI on `claimantId`
- DynamoDB table: `ClaimwiseEvidence` with GSI on `claimId`
- S3 bucket: `claimwise-evidence-{env}` with encryption, versioning, and public access block
- API Gateway REST API with Cognito authorizer
- Lambda functions (one per endpoint) with appropriate IAM roles
- Cognito User Pool and App Client
- IAM roles scoped to minimum required permissions per Lambda

All resources shall be defined as infrastructure-as-code (tooling TBD in architecture phase).

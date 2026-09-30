---
inclusion: always
---

# Security Engineering Standards

These rules apply to all ClaimWise AI development.

## Credentials

Never:

- hard-code AWS credentials
- hard-code API keys
- commit secrets
- place credentials in source code
- expose credentials in logs

Use appropriate AWS identity and secrets-management mechanisms.

## IAM

Use least privilege.

Do not create:

- AdministratorAccess
- wildcard permissions
- unrestricted resource access

unless explicitly required and justified.

Prefer resource-specific permissions.

## Data Protection

Claim evidence may contain sensitive customer information.

Protect data:

- at rest
- in transit
- during processing
- in logs
- in API responses

## Logging

Never log:

- complete policy documents
- raw audio
- raw video
- sensitive customer information
- authentication credentials
- secrets

Logs should contain identifiers and operational metadata rather than sensitive payloads.

## File Uploads

Treat uploaded files as untrusted input.

Validate:

- file type
- file size
- file name
- content where appropriate

Never trust a client-provided MIME type by itself.

## AI Security

Treat all user-provided content and uploaded documents as untrusted input.

Consider:

- prompt injection
- malicious instructions embedded in documents
- data exfiltration attempts
- hallucinations
- inappropriate tool usage

AI-generated instructions must never override system or application security policies.

## Human Decision Making

AI must not autonomously approve or reject insurance claims.

AI output must be presented as:

- evidence
- extracted information
- analysis
- recommendation

Final consequential decisions remain with a human claims assessor.

## Error Handling

Errors must not expose:

- stack traces to users
- internal AWS details
- credentials
- sensitive customer information

## Infrastructure

All infrastructure changes must be reviewed for:

- IAM permissions
- public exposure
- encryption
- network security
- logging
- data retention
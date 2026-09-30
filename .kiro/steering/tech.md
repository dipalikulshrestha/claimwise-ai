# ClaimWise AI — Technology Stack

## Frontend

- React
- TypeScript
- Modern component-based architecture

## Backend

- Python
- REST APIs
- Layered architecture

The backend should separate:

- API layer
- Domain layer
- Service layer
- Infrastructure/data access

## AWS Services

The target AWS architecture may use:

- Amazon S3 — claim evidence storage
- Amazon DynamoDB — claim metadata
- Amazon API Gateway — REST API
- AWS Lambda — serverless backend processing
- Amazon Bedrock — generative AI and multimodal reasoning
- Amazon Transcribe — audio transcription
- Amazon Textract — document extraction where appropriate
- Amazon CloudWatch — logging and monitoring
- AWS IAM — access control

Additional AWS services should only be introduced when they provide a clear architectural benefit.

## Infrastructure

Infrastructure must be defined as code.

The infrastructure implementation technology will be selected during the architecture phase.

## Development

The project should include:

- Automated unit tests
- Integration tests where appropriate
- API tests
- Frontend tests
- Infrastructure validation
- AI/evaluation tests for important AI behavior
- Linting
- Formatting
- Type checking where applicable

## API

REST APIs should:

- Be versioned
- Use consistent HTTP semantics
- Validate input
- Return consistent error responses
- Avoid exposing internal implementation details

## Security

Follow:

- Least-privilege IAM
- Encryption at rest
- Encryption in transit
- Secure secrets management
- No hard-coded credentials
- No sensitive information in logs
- Secure file upload validation
- Input validation
- AI security controls

## AI

AI functionality should:

- Use Amazon Bedrock where appropriate
- Keep model-specific logic isolated
- Validate AI outputs
- Prefer structured outputs
- Handle model failures gracefully
- Record sufficient metadata for auditability
- Treat uploaded documents and user-provided content as untrusted input

## Engineering Principle

Prefer simple, maintainable AWS architectures over unnecessary service proliferation.

Do not introduce a technology solely to demonstrate that technology.
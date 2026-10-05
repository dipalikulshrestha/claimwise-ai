# ClaimWise AI — Backend

Python backend for the ClaimWise AI claim submission and evidence upload APIs.

## Structure

```
backend/
  app/
    api/              # Lambda handlers — request parsing, response formatting
    domain/           # Business logic — no AWS dependencies
    services/         # Coordinates domain + AWS infrastructure
    infrastructure/   # AWS SDK clients, configuration loading
  tests/
    unit/             # Unit tests — all AWS mocked
    integration/      # Integration tests — full stack with moto
  conftest.py         # pytest path setup
  pyproject.toml      # project config, dependencies, tool config
  .env.example        # required environment variables (copy to .env for local dev)
```

## Prerequisites

- Python 3.11+

## Setup

```bash
# From the backend/ directory:

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install runtime + dev dependencies
pip install -e ".[dev]"
```

## Environment Variables

Copy `.env.example` to `.env` and fill in values for local development:

```bash
cp .env.example .env
```

See `.env.example` for the full list of required variables. **Never commit `.env`.**

## Running Tests

```bash
# Unit tests (no AWS required)
pytest tests/unit/ -v

# Integration tests (uses moto — no real AWS required)
pytest tests/integration/ -v

# All tests
pytest -v
```

## Linting

```bash
# Check
ruff check .

# Auto-fix
ruff check --fix .

# Format
ruff format .
```

## Type Checking

```bash
mypy app/
```

## Notes

- No real AWS credentials are required to run unit or integration tests — `moto` mocks all AWS services.
- All environment-specific values are injected via environment variables at Lambda deploy time.
- See `.kiro/specs/claim-submission-evidence-upload/` for the full feature specification.

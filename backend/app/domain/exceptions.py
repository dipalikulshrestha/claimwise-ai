"""
Domain exceptions.

All domain exceptions carry a machine-readable ``error_code`` that maps
directly to the error codes defined in requirements.md.  HTTP status codes
are intentionally absent — the API layer is responsible for translating
domain exceptions into HTTP responses.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain exceptions."""

    error_code: str = "DOMAIN_ERROR"

    def __init__(self, message: str, error_code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if error_code is not None:
            self.error_code = error_code


class ValidationError(DomainError):
    """Raised when client-supplied input fails domain validation rules.

    Maps to HTTP 400 / error code ``VALIDATION_ERROR`` in the API layer.

    Examples
    --------
    - Required field is missing or blank
    - Field value does not meet format requirements
    """

    error_code = "VALIDATION_ERROR"


class NotFoundError(DomainError):
    """Raised when a requested resource does not exist.

    Maps to HTTP 404 in the API layer.

    The ``error_code`` is set per-resource at raise time so callers can
    distinguish ``CLAIM_NOT_FOUND`` from ``EVIDENCE_NOT_FOUND`` without
    inspecting the message string.
    """

    error_code = "NOT_FOUND"

    def __init__(self, message: str, error_code: str = "NOT_FOUND") -> None:
        super().__init__(message, error_code=error_code)


class ForbiddenError(DomainError):
    """Raised when a claimant tries to access a resource they do not own.

    Maps to HTTP 403 / error code ``FORBIDDEN`` in the API layer.
    """

    error_code = "FORBIDDEN"


class InvalidStatusTransitionError(ValidationError):
    """Raised when a requested claim or evidence status transition is not allowed.

    Inherits from ``ValidationError`` so callers can catch it either
    specifically (``InvalidStatusTransitionError``) or broadly
    (``ValidationError``).  Maps to HTTP 400 / error code
    ``VALIDATION_ERROR`` in the API layer.
    """

    error_code = "VALIDATION_ERROR"

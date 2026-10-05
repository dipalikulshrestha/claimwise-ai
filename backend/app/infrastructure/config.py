"""
Infrastructure — configuration.

Loads all application configuration from environment variables at import time.
Any missing required variable raises EnvironmentError immediately rather than
at first use, so misconfigured deployments fail fast.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _require(name: str) -> str:
    """Return the value of a required environment variable, or raise."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise OSError(f"Required environment variable '{name}' is not set or is empty.")
    return value


def _optional_int(name: str, default: int) -> int:
    """Return an integer environment variable, falling back to *default*."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise OSError(f"Environment variable '{name}' must be an integer, got: {raw!r}") from exc


def _require_int(name: str) -> int:
    """Return a required integer environment variable, or raise."""
    raw = _require(name)
    try:
        return int(raw)
    except ValueError as exc:
        raise OSError(f"Environment variable '{name}' must be an integer, got: {raw!r}") from exc


@dataclass(frozen=True)
class AppConfig:
    """Strongly-typed application configuration.

    All values are loaded from environment variables.  The instance is
    immutable (frozen dataclass) to prevent accidental mutation at runtime.
    """

    # ── AWS ───────────────────────────────────────────────────────────────────
    aws_region: str

    # ── DynamoDB ──────────────────────────────────────────────────────────────
    claims_table_name: str
    evidence_table_name: str

    # ── S3 ────────────────────────────────────────────────────────────────────
    evidence_bucket_name: str

    # ── Pre-signed URL ────────────────────────────────────────────────────────
    presigned_url_expiry_seconds: int

    # ── File size limits (bytes) ──────────────────────────────────────────────
    max_file_size_video_bytes: int
    max_file_size_audio_bytes: int
    max_file_size_policy_document_bytes: int

    # ── Cognito ───────────────────────────────────────────────────────────────
    cognito_user_pool_id: str

    # ── Derived: file-size lookup dict keyed by evidenceType ─────────────────
    # Populated automatically in __post_init__; allows validation logic to do
    # size_limits["VIDEO"] instead of a chain of if/elif statements.
    file_size_limits: dict[str, int] = field(init=False)

    def __post_init__(self) -> None:
        # frozen=True prevents direct attribute assignment, so we must use
        # object.__setattr__ to initialise the derived field.
        object.__setattr__(
            self,
            "file_size_limits",
            {
                "VIDEO": self.max_file_size_video_bytes,
                "AUDIO": self.max_file_size_audio_bytes,
                "POLICY_DOCUMENT": self.max_file_size_policy_document_bytes,
            },
        )


def load_config() -> AppConfig:
    """Load and validate configuration from the current environment.

    Raises ``EnvironmentError`` if any required variable is absent or blank.
    Call this once at Lambda cold-start (module level) rather than inside
    individual handler functions.
    """
    return AppConfig(
        aws_region=_require("AWS_REGION"),
        claims_table_name=_require("CLAIMS_TABLE_NAME"),
        evidence_table_name=_require("EVIDENCE_TABLE_NAME"),
        evidence_bucket_name=_require("EVIDENCE_BUCKET_NAME"),
        presigned_url_expiry_seconds=_optional_int("PRESIGNED_URL_EXPIRY_SECONDS", default=900),
        max_file_size_video_bytes=_require_int("MAX_FILE_SIZE_VIDEO_BYTES"),
        max_file_size_audio_bytes=_require_int("MAX_FILE_SIZE_AUDIO_BYTES"),
        max_file_size_policy_document_bytes=_require_int("MAX_FILE_SIZE_POLICY_DOCUMENT_BYTES"),
        cognito_user_pool_id=_require("COGNITO_USER_POOL_ID"),
    )

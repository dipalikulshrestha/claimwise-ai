"""
Unit tests for app.infrastructure.config — Task 2.4.

Tests:
  - All required variables present → AppConfig loads correctly
  - Each required variable missing → EnvironmentError naming the variable
  - Integer variables with non-integer value → EnvironmentError
  - PRESIGNED_URL_EXPIRY_SECONDS is optional (has default 900)
  - file_size_limits dict is keyed by evidenceType strings
  - file_size_limits values match the loaded integer env vars
  - AppConfig is immutable (frozen dataclass)
"""

from __future__ import annotations

import pytest

from app.infrastructure.config import AppConfig, load_config

# ── Fixtures ──────────────────────────────────────────────────────────────────

VALID_ENV: dict[str, str] = {
    "AWS_REGION": "us-east-1",
    "CLAIMS_TABLE_NAME": "ClaimwiseClaims",
    "EVIDENCE_TABLE_NAME": "ClaimwiseEvidence",
    "EVIDENCE_BUCKET_NAME": "claimwise-evidence-dev",
    "PRESIGNED_URL_EXPIRY_SECONDS": "900",
    "MAX_FILE_SIZE_VIDEO_BYTES": "524288000",
    "MAX_FILE_SIZE_AUDIO_BYTES": "52428800",
    "MAX_FILE_SIZE_POLICY_DOCUMENT_BYTES": "10485760",
    "COGNITO_USER_POOL_ID": "us-east-1_TestPool",
}

# All required variables — PRESIGNED_URL_EXPIRY_SECONDS is optional so excluded
REQUIRED_VARS = [
    "AWS_REGION",
    "CLAIMS_TABLE_NAME",
    "EVIDENCE_TABLE_NAME",
    "EVIDENCE_BUCKET_NAME",
    "MAX_FILE_SIZE_VIDEO_BYTES",
    "MAX_FILE_SIZE_AUDIO_BYTES",
    "MAX_FILE_SIZE_POLICY_DOCUMENT_BYTES",
    "COGNITO_USER_POOL_ID",
]

INTEGER_VARS = [
    "MAX_FILE_SIZE_VIDEO_BYTES",
    "MAX_FILE_SIZE_AUDIO_BYTES",
    "MAX_FILE_SIZE_POLICY_DOCUMENT_BYTES",
    "PRESIGNED_URL_EXPIRY_SECONDS",
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_with_env(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> AppConfig:
    """Set the given env vars (clearing all others from VALID_ENV) then load."""
    for key in VALID_ENV:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return load_config()


# ── Happy path ────────────────────────────────────────────────────────────────


class TestLoadConfigSuccess:
    def test_loads_all_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """All env vars present → AppConfig fields match."""
        cfg = _load_with_env(VALID_ENV, monkeypatch)

        assert cfg.aws_region == "us-east-1"
        assert cfg.claims_table_name == "ClaimwiseClaims"
        assert cfg.evidence_table_name == "ClaimwiseEvidence"
        assert cfg.evidence_bucket_name == "claimwise-evidence-dev"
        assert cfg.presigned_url_expiry_seconds == 900
        assert cfg.max_file_size_video_bytes == 524288000
        assert cfg.max_file_size_audio_bytes == 52428800
        assert cfg.max_file_size_policy_document_bytes == 10485760
        assert cfg.cognito_user_pool_id == "us-east-1_TestPool"

    def test_returns_app_config_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _load_with_env(VALID_ENV, monkeypatch)
        assert isinstance(cfg, AppConfig)

    def test_presigned_url_expiry_uses_default_when_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PRESIGNED_URL_EXPIRY_SECONDS is optional; default is 900."""
        env = {k: v for k, v in VALID_ENV.items() if k != "PRESIGNED_URL_EXPIRY_SECONDS"}
        cfg = _load_with_env(env, monkeypatch)
        assert cfg.presigned_url_expiry_seconds == 900

    def test_presigned_url_expiry_custom_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A custom expiry value is loaded correctly."""
        env = {**VALID_ENV, "PRESIGNED_URL_EXPIRY_SECONDS": "300"}
        cfg = _load_with_env(env, monkeypatch)
        assert cfg.presigned_url_expiry_seconds == 300


# ── Missing required variable errors ─────────────────────────────────────────


class TestMissingRequiredVariables:
    @pytest.mark.parametrize("missing_var", REQUIRED_VARS)
    def test_raises_environment_error_for_missing_var(
        self, missing_var: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each required variable, when absent, raises EnvironmentError."""
        env = {k: v for k, v in VALID_ENV.items() if k != missing_var}
        for key in VALID_ENV:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        with pytest.raises(EnvironmentError) as exc_info:
            load_config()

        # The error message must name the missing variable so operators know
        # exactly what to set.
        assert missing_var in str(exc_info.value)

    @pytest.mark.parametrize("blank_var", REQUIRED_VARS)
    def test_raises_environment_error_for_blank_var(
        self, blank_var: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A variable set to an empty string is treated as missing."""
        env = {**VALID_ENV, blank_var: ""}
        for key in VALID_ENV:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        with pytest.raises(EnvironmentError) as exc_info:
            load_config()

        assert blank_var in str(exc_info.value)

    @pytest.mark.parametrize("blank_var", REQUIRED_VARS)
    def test_raises_environment_error_for_whitespace_only_var(
        self, blank_var: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A variable set to only whitespace is treated as missing."""
        env = {**VALID_ENV, blank_var: "   "}
        for key in VALID_ENV:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        with pytest.raises(EnvironmentError) as exc_info:
            load_config()

        assert blank_var in str(exc_info.value)


# ── Integer variable validation ───────────────────────────────────────────────


class TestIntegerVariableValidation:
    @pytest.mark.parametrize(
        "int_var",
        [
            "MAX_FILE_SIZE_VIDEO_BYTES",
            "MAX_FILE_SIZE_AUDIO_BYTES",
            "MAX_FILE_SIZE_POLICY_DOCUMENT_BYTES",
            "PRESIGNED_URL_EXPIRY_SECONDS",
        ],
    )
    def test_raises_environment_error_for_non_integer_value(
        self, int_var: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Integer variables reject non-numeric values."""
        env = {**VALID_ENV, int_var: "not-a-number"}
        for key in VALID_ENV:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        with pytest.raises(EnvironmentError) as exc_info:
            load_config()

        assert int_var in str(exc_info.value)


# ── file_size_limits dict ─────────────────────────────────────────────────────


class TestFileSizeLimitsDict:
    def test_file_size_limits_has_all_three_evidence_types(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _load_with_env(VALID_ENV, monkeypatch)
        assert set(cfg.file_size_limits.keys()) == {"VIDEO", "AUDIO", "POLICY_DOCUMENT"}

    def test_file_size_limits_video_matches_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _load_with_env(VALID_ENV, monkeypatch)
        assert cfg.file_size_limits["VIDEO"] == cfg.max_file_size_video_bytes

    def test_file_size_limits_audio_matches_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = _load_with_env(VALID_ENV, monkeypatch)
        assert cfg.file_size_limits["AUDIO"] == cfg.max_file_size_audio_bytes

    def test_file_size_limits_policy_document_matches_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = _load_with_env(VALID_ENV, monkeypatch)
        assert cfg.file_size_limits["POLICY_DOCUMENT"] == cfg.max_file_size_policy_document_bytes

    def test_file_size_limits_values_are_correct_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spot-check actual default byte values from the spec."""
        cfg = _load_with_env(VALID_ENV, monkeypatch)
        assert cfg.file_size_limits["VIDEO"] == 524288000  # 500 MB
        assert cfg.file_size_limits["AUDIO"] == 52428800  # 50 MB
        assert cfg.file_size_limits["POLICY_DOCUMENT"] == 10485760  # 10 MB

    def test_file_size_limits_lookup_by_evidence_type_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Validation logic can look up limits without if/elif chains."""
        cfg = _load_with_env(VALID_ENV, monkeypatch)
        for evidence_type in ("VIDEO", "AUDIO", "POLICY_DOCUMENT"):
            limit = cfg.file_size_limits[evidence_type]
            assert isinstance(limit, int)
            assert limit > 0


# ── Immutability ──────────────────────────────────────────────────────────────


class TestImmutability:
    def test_app_config_is_frozen(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AppConfig must be immutable — mutation must raise."""
        cfg = _load_with_env(VALID_ENV, monkeypatch)
        with pytest.raises((AttributeError, TypeError)):
            cfg.aws_region = "eu-west-1"  # type: ignore[misc]

    def test_file_size_limits_dict_is_present_after_load(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The derived file_size_limits field is populated (not None/empty)."""
        cfg = _load_with_env(VALID_ENV, monkeypatch)
        assert cfg.file_size_limits
        assert len(cfg.file_size_limits) == 3

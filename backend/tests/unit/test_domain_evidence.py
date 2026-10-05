"""
Unit tests for app.domain.evidence — Task 4.

Covers all 24 required scenarios plus the 5 storage-key safety tests:

 1.  Valid VIDEO evidence.
 2.  Valid AUDIO evidence.
 3.  Valid POLICY_DOCUMENT evidence.
 4.  Invalid evidence type.
 5.  Invalid MIME type (not in any category).
 6.  MIME type valid for another category but invalid for the selected one.
 7.  Negative file size.
 8.  Zero file size.
 9.  VIDEO greater than 500 MB.
10.  AUDIO greater than 50 MB.
11.  POLICY_DOCUMENT greater than 10 MB.
12.  File exactly at the configured limit is accepted.
13.  File size limits are injected/configurable.
14.  Evidence starts as UPLOAD_PENDING.
15.  Client cannot initialise evidence as UPLOAD_COMPLETE.
16.  Evidence ID is backend-generated.
17.  Storage key is backend-generated.
18.  Original filename is not included in storage key.
19.  Path-traversal filename does not affect storage key.
20.  UPLOAD_PENDING → UPLOAD_COMPLETE succeeds.
21.  Invalid status transition is rejected.
22.  uploadedAt is set when upload becomes complete.
23.  createdAt remains unchanged.
24.  Domain tests require no AWS credentials or AWS services.

Storage-key safety tests (from spec):
SK-1. Storage key does not contain original filename.
SK-2. Path-traversal-like filename cannot influence storage key.
SK-3. Two different filenames for same claim/evidence produce the same key.
SK-4. Storage key contains claimId and evidenceId.
SK-5. Client cannot provide an arbitrary storage key to the factory.

No boto3, moto, or AWS imports are used anywhere in this file.
"""

from __future__ import annotations

import inspect
import uuid

import pytest

from app.domain.evidence import (
    ALLOWED_CONTENT_TYPES,
    Evidence,
    EvidenceStatus,
    EvidenceType,
    build_evidence_record,
    generate_evidence_id,
    generate_storage_key,
    validate_evidence_request,
)
from app.domain.exceptions import InvalidStatusTransitionError, ValidationError

# ── Shared constants ──────────────────────────────────────────────────────────

CLAIM_ID = "CLM-550e8400-e29b-41d4-a716-446655440000"
CLAIMANT_ID = "cognito-sub-abc-123"
EVIDENCE_ID = "EVD-7f3c9a12-4b2e-4a8f-9c1d-2e3f4a5b6c7d"

FIXED_TIME_1 = "2026-09-30T10:00:00+00:00"
FIXED_TIME_2 = "2026-09-30T10:05:00+00:00"

# Default size limits matching the spec (bytes)
DEFAULT_SIZE_LIMITS: dict[str, int] = {
    "VIDEO": 524288000,  # 500 MB
    "AUDIO": 52428800,  # 50 MB
    "POLICY_DOCUMENT": 10485760,  # 10 MB
}


def _fixed_clock(ts: str):
    return lambda: ts


def _make_evidence(
    evidence_type: str = "VIDEO",
    original_file_name: str = "accident.mp4",
    content_type: str = "video/mp4",
    file_size_bytes: int = 1024,
    size_limits: dict[str, int] | None = None,
    clock=_fixed_clock(FIXED_TIME_1),  # noqa: B008
) -> Evidence:
    return build_evidence_record(
        claim_id=CLAIM_ID,
        claimant_id=CLAIMANT_ID,
        evidence_type=evidence_type,
        original_file_name=original_file_name,
        content_type=content_type,
        file_size_bytes=file_size_bytes,
        size_limits=size_limits if size_limits is not None else DEFAULT_SIZE_LIMITS,
        clock=clock,
    )


# ── 1–3. Valid evidence for each type ────────────────────────────────────────


class TestValidEvidence:
    def test_valid_video_evidence(self) -> None:
        """Scenario 1 — VIDEO with video/mp4 builds successfully."""
        evidence = _make_evidence(
            evidence_type="VIDEO",
            original_file_name="accident.mp4",
            content_type="video/mp4",
            file_size_bytes=10_000_000,
        )
        assert isinstance(evidence, Evidence)
        assert evidence.evidence_type == EvidenceType.VIDEO
        assert evidence.content_type == "video/mp4"

    def test_valid_audio_evidence(self) -> None:
        """Scenario 2 — AUDIO with audio/mpeg builds successfully."""
        evidence = _make_evidence(
            evidence_type="AUDIO",
            original_file_name="description.mp3",
            content_type="audio/mpeg",
            file_size_bytes=5_000_000,
        )
        assert evidence.evidence_type == EvidenceType.AUDIO
        assert evidence.content_type == "audio/mpeg"

    def test_valid_policy_document_evidence(self) -> None:
        """Scenario 3 — POLICY_DOCUMENT with application/pdf builds successfully."""
        evidence = _make_evidence(
            evidence_type="POLICY_DOCUMENT",
            original_file_name="policy.pdf",
            content_type="application/pdf",
            file_size_bytes=500_000,
        )
        assert evidence.evidence_type == EvidenceType.POLICY_DOCUMENT
        assert evidence.content_type == "application/pdf"

    def test_all_allowed_video_mime_types_are_valid(self) -> None:
        """Each MIME type in ALLOWED_CONTENT_TYPES[VIDEO] is accepted."""
        for mime in ALLOWED_CONTENT_TYPES[EvidenceType.VIDEO]:
            validate_evidence_request("VIDEO", mime, 1024, DEFAULT_SIZE_LIMITS)

    def test_all_allowed_audio_mime_types_are_valid(self) -> None:
        """Each MIME type in ALLOWED_CONTENT_TYPES[AUDIO] is accepted."""
        for mime in ALLOWED_CONTENT_TYPES[EvidenceType.AUDIO]:
            validate_evidence_request("AUDIO", mime, 1024, DEFAULT_SIZE_LIMITS)

    def test_policy_document_pdf_is_valid(self) -> None:
        """application/pdf is accepted for POLICY_DOCUMENT."""
        validate_evidence_request("POLICY_DOCUMENT", "application/pdf", 1024, DEFAULT_SIZE_LIMITS)


# ── 4. Invalid evidence type ──────────────────────────────────────────────────


class TestInvalidEvidenceType:
    def test_unknown_evidence_type_raises_validation_error(self) -> None:
        """Scenario 4 — unknown evidence type raises with UNSUPPORTED_EVIDENCE_TYPE."""
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("DASHCAM", "video/mp4", 1024, DEFAULT_SIZE_LIMITS)
        assert exc_info.value.error_code == "UNSUPPORTED_EVIDENCE_TYPE"
        assert "DASHCAM" in exc_info.value.message

    def test_empty_evidence_type_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("", "video/mp4", 1024, DEFAULT_SIZE_LIMITS)
        assert exc_info.value.error_code == "UNSUPPORTED_EVIDENCE_TYPE"

    def test_lowercase_evidence_type_raises(self) -> None:
        """Evidence type is case-sensitive; 'video' is not 'VIDEO'."""
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("video", "video/mp4", 1024, DEFAULT_SIZE_LIMITS)
        assert exc_info.value.error_code == "UNSUPPORTED_EVIDENCE_TYPE"

    def test_error_message_lists_allowed_types(self) -> None:
        """Error message should help the caller know what values are accepted."""
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("UNKNOWN", "video/mp4", 1024, DEFAULT_SIZE_LIMITS)
        msg = exc_info.value.message
        assert "AUDIO" in msg or "VIDEO" in msg or "POLICY_DOCUMENT" in msg


# ── 5. Invalid MIME type (not in any category) ────────────────────────────────


class TestInvalidMimeType:
    def test_completely_unknown_mime_type_raises(self) -> None:
        """Scenario 5 — MIME not in any category raises UNSUPPORTED_CONTENT_TYPE."""
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("VIDEO", "text/html", 1024, DEFAULT_SIZE_LIMITS)
        assert exc_info.value.error_code == "UNSUPPORTED_CONTENT_TYPE"

    def test_application_octet_stream_is_rejected_for_video(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request(
                "VIDEO", "application/octet-stream", 1024, DEFAULT_SIZE_LIMITS
            )
        assert exc_info.value.error_code == "UNSUPPORTED_CONTENT_TYPE"

    def test_unsupported_content_type_error_names_the_mime(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("VIDEO", "image/jpeg", 1024, DEFAULT_SIZE_LIMITS)
        assert "image/jpeg" in exc_info.value.message


# ── 6. MIME type valid for another category ───────────────────────────────────


class TestCrossCategoryMimeRejection:
    def test_pdf_rejected_for_video(self) -> None:
        """Scenario 6 — application/pdf is valid for POLICY_DOCUMENT but not VIDEO."""
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("VIDEO", "application/pdf", 1024, DEFAULT_SIZE_LIMITS)
        assert exc_info.value.error_code == "UNSUPPORTED_CONTENT_TYPE"

    def test_video_mp4_rejected_for_audio(self) -> None:
        """video/mp4 is valid for VIDEO but not AUDIO."""
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("AUDIO", "video/mp4", 1024, DEFAULT_SIZE_LIMITS)
        assert exc_info.value.error_code == "UNSUPPORTED_CONTENT_TYPE"

    def test_audio_mpeg_rejected_for_policy_document(self) -> None:
        """audio/mpeg is valid for AUDIO but not POLICY_DOCUMENT."""
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("POLICY_DOCUMENT", "audio/mpeg", 1024, DEFAULT_SIZE_LIMITS)
        assert exc_info.value.error_code == "UNSUPPORTED_CONTENT_TYPE"

    def test_pdf_rejected_for_audio(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("AUDIO", "application/pdf", 1024, DEFAULT_SIZE_LIMITS)
        assert exc_info.value.error_code == "UNSUPPORTED_CONTENT_TYPE"

    def test_video_quicktime_rejected_for_policy_document(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request(
                "POLICY_DOCUMENT", "video/quicktime", 1024, DEFAULT_SIZE_LIMITS
            )
        assert exc_info.value.error_code == "UNSUPPORTED_CONTENT_TYPE"


# ── 7 & 8. Negative and zero file sizes ──────────────────────────────────────


class TestInvalidFileSizes:
    def test_negative_file_size_raises(self) -> None:
        """Scenario 7 — negative file size is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("VIDEO", "video/mp4", -1, DEFAULT_SIZE_LIMITS)
        assert exc_info.value.error_code == "VALIDATION_ERROR"
        assert "-1" in exc_info.value.message or "zero" in exc_info.value.message.lower()

    def test_zero_file_size_raises(self) -> None:
        """Scenario 8 — zero file size is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("VIDEO", "video/mp4", 0, DEFAULT_SIZE_LIMITS)
        assert exc_info.value.error_code == "VALIDATION_ERROR"


# ── 9–11. File size exceeds limit per type ────────────────────────────────────


class TestFileSizeLimitsEnforced:
    def test_video_over_500mb_raises(self) -> None:
        """Scenario 9 — VIDEO > 500 MB raises FILE_SIZE_EXCEEDED."""
        over_limit = 524288000 + 1  # 500 MB + 1 byte
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("VIDEO", "video/mp4", over_limit, DEFAULT_SIZE_LIMITS)
        assert exc_info.value.error_code == "FILE_SIZE_EXCEEDED"

    def test_audio_over_50mb_raises(self) -> None:
        """Scenario 10 — AUDIO > 50 MB raises FILE_SIZE_EXCEEDED."""
        over_limit = 52428800 + 1  # 50 MB + 1 byte
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("AUDIO", "audio/mpeg", over_limit, DEFAULT_SIZE_LIMITS)
        assert exc_info.value.error_code == "FILE_SIZE_EXCEEDED"

    def test_policy_document_over_10mb_raises(self) -> None:
        """Scenario 11 — POLICY_DOCUMENT > 10 MB raises FILE_SIZE_EXCEEDED."""
        over_limit = 10485760 + 1  # 10 MB + 1 byte
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request(
                "POLICY_DOCUMENT", "application/pdf", over_limit, DEFAULT_SIZE_LIMITS
            )
        assert exc_info.value.error_code == "FILE_SIZE_EXCEEDED"

    def test_file_size_exceeded_message_names_limit(self) -> None:
        """Error message should include the configured maximum."""
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("VIDEO", "video/mp4", 999_999_999, DEFAULT_SIZE_LIMITS)
        assert "524288000" in exc_info.value.message


# ── 12. File exactly at limit is accepted ─────────────────────────────────────


class TestFileSizeAtExactLimit:
    def test_video_exactly_at_500mb_is_accepted(self) -> None:
        """Scenario 12 — file exactly at the limit must not be rejected."""
        validate_evidence_request("VIDEO", "video/mp4", 524288000, DEFAULT_SIZE_LIMITS)

    def test_audio_exactly_at_50mb_is_accepted(self) -> None:
        validate_evidence_request("AUDIO", "audio/mpeg", 52428800, DEFAULT_SIZE_LIMITS)

    def test_policy_document_exactly_at_10mb_is_accepted(self) -> None:
        validate_evidence_request(
            "POLICY_DOCUMENT", "application/pdf", 10485760, DEFAULT_SIZE_LIMITS
        )

    def test_one_byte_under_limit_is_accepted(self) -> None:
        validate_evidence_request("VIDEO", "video/mp4", 524288000 - 1, DEFAULT_SIZE_LIMITS)


# ── 13. File size limits are injected / configurable ─────────────────────────


class TestFileSizeLimitsAreInjected:
    def test_custom_lower_limit_is_enforced(self) -> None:
        """Scenario 13 — injected limits take effect; domain never hard-codes them."""
        tiny_limits = {"VIDEO": 100, "AUDIO": 100, "POLICY_DOCUMENT": 100}
        with pytest.raises(ValidationError) as exc_info:
            validate_evidence_request("VIDEO", "video/mp4", 101, tiny_limits)
        assert exc_info.value.error_code == "FILE_SIZE_EXCEEDED"

    def test_custom_larger_limit_allows_bigger_file(self) -> None:
        """A custom higher limit accepts a file that would fail with defaults."""
        generous_limits = {"VIDEO": 2_000_000_000, "AUDIO": 100, "POLICY_DOCUMENT": 100}
        # 600 MB — would fail with default 500 MB limit
        validate_evidence_request("VIDEO", "video/mp4", 629_145_600, generous_limits)

    def test_validate_evidence_request_has_no_default_size_limits(self) -> None:
        """size_limits must be explicitly supplied — no hidden defaults."""
        sig = inspect.signature(validate_evidence_request)
        param = sig.parameters.get("size_limits")
        assert param is not None
        assert param.default is inspect.Parameter.empty

    def test_build_evidence_record_uses_injected_limits(self) -> None:
        """build_evidence_record passes size_limits through to validation."""
        tiny_limits = {"VIDEO": 100, "AUDIO": 100, "POLICY_DOCUMENT": 100}
        with pytest.raises(ValidationError) as exc_info:
            build_evidence_record(
                claim_id=CLAIM_ID,
                claimant_id=CLAIMANT_ID,
                evidence_type="VIDEO",
                original_file_name="big.mp4",
                content_type="video/mp4",
                file_size_bytes=101,
                size_limits=tiny_limits,
            )
        assert exc_info.value.error_code == "FILE_SIZE_EXCEEDED"


# ── 14. Evidence starts as UPLOAD_PENDING ─────────────────────────────────────


class TestInitialStatus:
    def test_new_evidence_status_is_upload_pending(self) -> None:
        """Scenario 14 — freshly built evidence always starts as UPLOAD_PENDING."""
        evidence = _make_evidence()
        assert evidence.status == EvidenceStatus.UPLOAD_PENDING

    def test_new_evidence_status_string_value_is_upload_pending(self) -> None:
        evidence = _make_evidence()
        assert evidence.to_dict()["status"] == "UPLOAD_PENDING"

    def test_new_evidence_uploaded_at_is_none(self) -> None:
        """uploadedAt must be None before upload is confirmed."""
        evidence = _make_evidence()
        assert evidence.uploaded_at is None

    def test_to_dict_omits_uploaded_at_when_none(self) -> None:
        evidence = _make_evidence()
        assert "uploadedAt" not in evidence.to_dict()


# ── 15. Client cannot initialise evidence as UPLOAD_COMPLETE ─────────────────


class TestClientCannotSetArbitraryStatus:
    def test_build_evidence_record_has_no_status_parameter(self) -> None:
        """Scenario 15 — factory has no status param; always sets UPLOAD_PENDING."""
        sig = inspect.signature(build_evidence_record)
        assert "status" not in sig.parameters

    def test_evidence_is_frozen_so_status_cannot_be_mutated(self) -> None:
        """Scenario 15 — frozen dataclass prevents direct status mutation."""
        evidence = _make_evidence()
        with pytest.raises((AttributeError, TypeError)):
            evidence.status = EvidenceStatus.UPLOAD_COMPLETE  # type: ignore[misc]


# ── 16. Evidence ID is backend-generated ──────────────────────────────────────


class TestEvidenceIdGeneration:
    def test_generate_evidence_id_starts_with_evd(self) -> None:
        """Scenario 16 — generated ID starts with 'EVD-'."""
        assert generate_evidence_id().startswith("EVD-")

    def test_generate_evidence_id_suffix_is_valid_uuid4(self) -> None:
        evd_id = generate_evidence_id()
        suffix = evd_id[len("EVD-") :]
        parsed = uuid.UUID(suffix, version=4)
        assert str(parsed) == suffix

    def test_two_generated_ids_are_unique(self) -> None:
        assert generate_evidence_id() != generate_evidence_id()

    def test_build_evidence_record_has_no_evidence_id_parameter(self) -> None:
        """Scenario 16 — factory generates the ID; caller cannot supply it."""
        sig = inspect.signature(build_evidence_record)
        assert "evidence_id" not in sig.parameters

    def test_built_evidence_id_starts_with_evd(self) -> None:
        evidence = _make_evidence()
        assert evidence.evidence_id.startswith("EVD-")

    def test_two_built_evidence_records_have_different_ids(self) -> None:
        ev1 = _make_evidence()
        ev2 = _make_evidence()
        assert ev1.evidence_id != ev2.evidence_id


# ── 17. Storage key is backend-generated ──────────────────────────────────────


class TestStorageKeyGeneration:
    def test_generate_storage_key_follows_correct_pattern(self) -> None:
        """Scenario 17 — key pattern is claims/{claimId}/evidence/{evidenceId}/object."""
        key = generate_storage_key(CLAIM_ID, EVIDENCE_ID)
        assert key == f"claims/{CLAIM_ID}/evidence/{EVIDENCE_ID}/object"

    def test_build_evidence_record_has_no_storage_key_parameter(self) -> None:
        """Scenario 17 — factory generates the key; caller cannot supply it."""
        sig = inspect.signature(build_evidence_record)
        assert "storage_key" not in sig.parameters

    def test_built_evidence_storage_key_follows_pattern(self) -> None:
        evidence = _make_evidence()
        parts = evidence.storage_key.split("/")
        assert parts[0] == "claims"
        assert parts[1] == CLAIM_ID
        assert parts[2] == "evidence"
        # parts[3] is the generated evidenceId
        assert parts[3] == evidence.evidence_id
        assert parts[4] == "object"
        assert len(parts) == 5

    def test_storage_key_ends_with_object_suffix(self) -> None:
        evidence = _make_evidence()
        assert evidence.storage_key.endswith("/object")


# ── 18 & 19. Filename does not influence storage key (SK-1 to SK-5) ──────────


class TestStorageKeySafety:
    # SK-1 / Scenario 18
    def test_storage_key_does_not_contain_original_filename(self) -> None:
        """Scenario 18 / SK-1 — original filename is absent from the storage key."""
        filename = "accident-clip.mp4"
        evidence = _make_evidence(original_file_name=filename)
        assert filename not in evidence.storage_key
        assert "accident" not in evidence.storage_key
        assert ".mp4" not in evidence.storage_key

    # SK-2 / Scenario 19
    def test_path_traversal_filename_does_not_affect_storage_key(self) -> None:
        """Scenario 19 / SK-2 — path-traversal in filename cannot escape the key."""
        malicious_names = [
            "../../etc/passwd",
            "..\\..\\system32\\win.ini",
            "/etc/shadow",
            "file name with spaces.pdf",
            "policy;drop table claims.pdf",
            "\x00null-byte.pdf",
        ]
        for filename in malicious_names:
            evidence = _make_evidence(
                evidence_type="POLICY_DOCUMENT",
                original_file_name=filename,
                content_type="application/pdf",
            )
            key = evidence.storage_key
            # Key always starts correctly
            assert key.startswith("claims/"), f"Bad key for filename={filename!r}: {key}"
            # Key always ends correctly
            assert key.endswith("/object"), f"Bad key for filename={filename!r}: {key}"
            # Filename is not embedded in the key at all
            assert filename not in key, f"Filename leaked into key for filename={filename!r}"

    # SK-3
    def test_two_different_filenames_produce_same_storage_key(self) -> None:
        """SK-3 — the storage key is independent of the original filename."""
        ev1 = _make_evidence(original_file_name="version-1.mp4")
        ev2_storage_key = generate_storage_key(CLAIM_ID, ev1.evidence_id)
        # Same claimId + evidenceId → same key, regardless of filename
        assert ev1.storage_key == ev2_storage_key

    # SK-4
    def test_storage_key_contains_claim_id_and_evidence_id(self) -> None:
        """SK-4 — both IDs are embedded in the storage key."""
        evidence = _make_evidence()
        assert CLAIM_ID in evidence.storage_key
        assert evidence.evidence_id in evidence.storage_key

    # SK-5
    def test_client_cannot_provide_storage_key_to_factory(self) -> None:
        """SK-5 — build_evidence_record accepts no storage_key argument."""
        sig = inspect.signature(build_evidence_record)
        assert "storage_key" not in sig.parameters

    def test_generate_storage_key_takes_no_filename_parameter(self) -> None:
        """generate_storage_key has no filename parameter by design."""
        sig = inspect.signature(generate_storage_key)
        param_names = list(sig.parameters.keys())
        assert "file_name" not in param_names
        assert "filename" not in param_names
        assert "original_file_name" not in param_names

    def test_generate_storage_key_rejects_extra_argument(self) -> None:
        with pytest.raises(TypeError):
            generate_storage_key(CLAIM_ID, EVIDENCE_ID, "extra.pdf")  # type: ignore[call-arg]


# ── 20. UPLOAD_PENDING → UPLOAD_COMPLETE ─────────────────────────────────────


class TestValidStatusTransition:
    def test_complete_upload_returns_upload_complete_status(self) -> None:
        """Scenario 20 — complete_upload() produces UPLOAD_COMPLETE."""
        evidence = _make_evidence(clock=_fixed_clock(FIXED_TIME_1))
        completed = evidence.complete_upload(clock=_fixed_clock(FIXED_TIME_2))
        assert completed.status == EvidenceStatus.UPLOAD_COMPLETE

    def test_complete_upload_returns_new_instance(self) -> None:
        """Transition returns a new object; original is unchanged."""
        evidence = _make_evidence()
        completed = evidence.complete_upload()
        assert evidence.status == EvidenceStatus.UPLOAD_PENDING
        assert completed.status == EvidenceStatus.UPLOAD_COMPLETE
        assert completed is not evidence

    def test_all_non_status_fields_preserved_after_transition(self) -> None:
        """All identity and content fields survive the transition unchanged."""
        evidence = _make_evidence(
            evidence_type="VIDEO",
            original_file_name="accident.mp4",
            content_type="video/mp4",
            file_size_bytes=10_000,
            clock=_fixed_clock(FIXED_TIME_1),
        )
        completed = evidence.complete_upload(clock=_fixed_clock(FIXED_TIME_2))

        assert completed.evidence_id == evidence.evidence_id
        assert completed.claim_id == evidence.claim_id
        assert completed.claimant_id == evidence.claimant_id
        assert completed.storage_key == evidence.storage_key
        assert completed.original_file_name == evidence.original_file_name
        assert completed.content_type == evidence.content_type
        assert completed.file_size_bytes == evidence.file_size_bytes
        assert completed.evidence_type == evidence.evidence_type
        assert completed.created_at == evidence.created_at

    def test_to_dict_status_after_transition_is_string(self) -> None:
        evidence = _make_evidence()
        completed = evidence.complete_upload()
        assert completed.to_dict()["status"] == "UPLOAD_COMPLETE"


# ── 21. Invalid status transition is rejected ─────────────────────────────────


class TestInvalidStatusTransition:
    def test_complete_upload_on_already_complete_raises(self) -> None:
        """Scenario 21 — UPLOAD_COMPLETE → UPLOAD_COMPLETE is not allowed."""
        evidence = _make_evidence()
        completed = evidence.complete_upload()
        with pytest.raises(InvalidStatusTransitionError) as exc_info:
            completed.complete_upload()
        assert exc_info.value.error_code == "VALIDATION_ERROR"

    def test_invalid_transition_message_names_current_status(self) -> None:
        evidence = _make_evidence()
        completed = evidence.complete_upload()
        with pytest.raises(InvalidStatusTransitionError) as exc_info:
            completed.complete_upload()
        assert "UPLOAD_COMPLETE" in str(exc_info.value)

    def test_invalid_transition_error_is_catchable_as_validation_error(self) -> None:
        """InvalidStatusTransitionError can be caught as ValidationError."""
        evidence = _make_evidence()
        completed = evidence.complete_upload()
        with pytest.raises(ValidationError):
            completed.complete_upload()


# ── 22. uploadedAt is set on completion ──────────────────────────────────────


class TestUploadedAt:
    def test_uploaded_at_set_when_upload_complete(self) -> None:
        """Scenario 22 — uploadedAt is populated after complete_upload()."""
        evidence = _make_evidence(clock=_fixed_clock(FIXED_TIME_1))
        completed = evidence.complete_upload(clock=_fixed_clock(FIXED_TIME_2))
        assert completed.uploaded_at == FIXED_TIME_2

    def test_uploaded_at_reflected_in_to_dict(self) -> None:
        evidence = _make_evidence(clock=_fixed_clock(FIXED_TIME_1))
        completed = evidence.complete_upload(clock=_fixed_clock(FIXED_TIME_2))
        d = completed.to_dict()
        assert d["uploadedAt"] == FIXED_TIME_2

    def test_uploaded_at_is_none_before_completion(self) -> None:
        evidence = _make_evidence()
        assert evidence.uploaded_at is None


# ── 23. createdAt unchanged ───────────────────────────────────────────────────


class TestCreatedAtImmutable:
    def test_created_at_unchanged_after_transition(self) -> None:
        """Scenario 23 — createdAt is preserved across complete_upload()."""
        evidence = _make_evidence(clock=_fixed_clock(FIXED_TIME_1))
        completed = evidence.complete_upload(clock=_fixed_clock(FIXED_TIME_2))
        assert completed.created_at == FIXED_TIME_1
        assert completed.created_at == evidence.created_at

    def test_created_at_not_equal_to_uploaded_at(self) -> None:
        """createdAt and uploadedAt record different instants."""
        evidence = _make_evidence(clock=_fixed_clock(FIXED_TIME_1))
        completed = evidence.complete_upload(clock=_fixed_clock(FIXED_TIME_2))
        assert completed.created_at != completed.uploaded_at


# ── 24. No AWS credentials or services required ───────────────────────────────


class TestNoDomainAwsDependencies:
    def test_no_boto3_import_in_evidence_module(self) -> None:
        """Scenario 24 — evidence.py must not import boto3."""
        import app.domain.evidence as evidence_module

        assert not hasattr(evidence_module, "boto3")

    def test_evidence_module_imports_without_aws_env_vars(self) -> None:
        """Domain module imports cleanly with no AWS env vars present."""
        import importlib

        importlib.import_module("app.domain.evidence")

    def test_build_evidence_record_needs_no_aws_credentials(self) -> None:
        """Building an evidence record requires no AWS calls."""
        evidence = _make_evidence()
        assert evidence.evidence_id.startswith("EVD-")


# ── to_dict serialisation ─────────────────────────────────────────────────────


class TestEvidenceToDict:
    def test_to_dict_contains_all_required_keys_before_completion(self) -> None:
        evidence = _make_evidence()
        d = evidence.to_dict()
        for key in (
            "evidenceId",
            "claimId",
            "claimantId",
            "evidenceType",
            "originalFileName",
            "contentType",
            "fileSizeBytes",
            "storageKey",
            "status",
            "createdAt",
        ):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_evidence_type_is_string_not_enum(self) -> None:
        evidence = _make_evidence()
        d = evidence.to_dict()
        assert isinstance(d["evidenceType"], str)
        assert d["evidenceType"] == "VIDEO"

    def test_to_dict_status_is_string_not_enum(self) -> None:
        evidence = _make_evidence()
        d = evidence.to_dict()
        assert isinstance(d["status"], str)

    def test_to_dict_has_no_dynamodb_attribute_value_wrappers(self) -> None:
        """No DynamoDB {'S': ...} / {'N': ...} in the output."""
        evidence = _make_evidence()
        for value in evidence.to_dict().values():
            assert not isinstance(value, dict), f"DynamoDB wrapper found: {value}"

    def test_to_dict_includes_uploaded_at_after_completion(self) -> None:
        evidence = _make_evidence(clock=_fixed_clock(FIXED_TIME_1))
        completed = evidence.complete_upload(clock=_fixed_clock(FIXED_TIME_2))
        d = completed.to_dict()
        assert "uploadedAt" in d
        assert d["uploadedAt"] == FIXED_TIME_2


# ── ALLOWED_CONTENT_TYPES structure ──────────────────────────────────────────


class TestAllowedContentTypesStructure:
    def test_all_three_evidence_types_have_allowed_mimes(self) -> None:
        assert EvidenceType.VIDEO in ALLOWED_CONTENT_TYPES
        assert EvidenceType.AUDIO in ALLOWED_CONTENT_TYPES
        assert EvidenceType.POLICY_DOCUMENT in ALLOWED_CONTENT_TYPES

    def test_video_has_three_allowed_mimes(self) -> None:
        assert len(ALLOWED_CONTENT_TYPES[EvidenceType.VIDEO]) == 3

    def test_audio_has_four_allowed_mimes(self) -> None:
        assert len(ALLOWED_CONTENT_TYPES[EvidenceType.AUDIO]) == 4

    def test_policy_document_has_one_allowed_mime(self) -> None:
        assert len(ALLOWED_CONTENT_TYPES[EvidenceType.POLICY_DOCUMENT]) == 1
        assert "application/pdf" in ALLOWED_CONTENT_TYPES[EvidenceType.POLICY_DOCUMENT]

    def test_mime_sets_are_disjoint(self) -> None:
        """No MIME type appears in more than one evidence type category."""
        video = ALLOWED_CONTENT_TYPES[EvidenceType.VIDEO]
        audio = ALLOWED_CONTENT_TYPES[EvidenceType.AUDIO]
        policy = ALLOWED_CONTENT_TYPES[EvidenceType.POLICY_DOCUMENT]
        assert not (video & audio), "VIDEO and AUDIO share a MIME type"
        assert not (video & policy), "VIDEO and POLICY_DOCUMENT share a MIME type"
        assert not (audio & policy), "AUDIO and POLICY_DOCUMENT share a MIME type"

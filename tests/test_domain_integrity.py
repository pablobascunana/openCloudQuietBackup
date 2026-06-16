from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from opencloud_backup.domain.archive import ARCHIVE_FORMAT_VERSION
from opencloud_backup.domain.errors import IntegrityError
from opencloud_backup.domain.integrity import (
    SIDECAR_FORMAT_VERSION,
    IntegrityRecord,
    format_sidecar,
    parse_sidecar,
    sidecar_path_for_archive,
)


def test_sidecar_path_appends_suffix() -> None:
    archive_path = Path("/backups/opencloud-2026-06-14_101530.tar.zst")
    assert sidecar_path_for_archive(archive_path) == Path(
        "/backups/opencloud-2026-06-14_101530.tar.zst.sha256"
    )


def test_format_sidecar_gnu_line_two_spaces() -> None:
    recorded_at = datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc)
    record = IntegrityRecord(
        sha256_hex="a" * 64,
        archive_basename="opencloud-2026-06-14_101530.tar.zst",
        size_bytes=1048576,
        recorded_at=recorded_at,
        format_version=SIDECAR_FORMAT_VERSION,
        archive_format_version=ARCHIVE_FORMAT_VERSION,
    )
    content = format_sidecar(record)
    first_line = content.split("\n", maxsplit=1)[0]
    assert first_line == f"{'a' * 64}  opencloud-2026-06-14_101530.tar.zst"
    assert "   " not in first_line


def test_format_sidecar_includes_all_comment_keys_in_order() -> None:
    recorded_at = datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc)
    record = IntegrityRecord(
        sha256_hex="b" * 64,
        archive_basename="opencloud-2026-06-14_101530.tar.zst",
        size_bytes=512,
        recorded_at=recorded_at,
        format_version=SIDECAR_FORMAT_VERSION,
        archive_format_version=ARCHIVE_FORMAT_VERSION,
    )
    lines = format_sidecar(record).strip().split("\n")
    assert lines[1] == "# format_version=1"
    assert lines[2] == "# size_bytes=512"
    assert lines[3] == "# recorded_at=2026-06-14T10:15:30+00:00"
    assert lines[4] == "# archive_format_version=1"


def test_parse_sidecar_roundtrip() -> None:
    recorded_at = datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc)
    original = IntegrityRecord(
        sha256_hex="c" * 64,
        archive_basename="opencloud-2026-06-14_101530.tar.gz",
        size_bytes=2048,
        recorded_at=recorded_at,
        format_version=SIDECAR_FORMAT_VERSION,
        archive_format_version=ARCHIVE_FORMAT_VERSION,
    )
    parsed = parse_sidecar(format_sidecar(original))
    assert parsed == original


def test_parse_sidecar_gnu_only_line_minimal() -> None:
    digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    content = f"{digest}  opencloud-2026-06-14_101530.tar\n"
    parsed = parse_sidecar(content)
    assert parsed.sha256_hex == digest
    assert parsed.archive_basename == "opencloud-2026-06-14_101530.tar"
    assert parsed.format_version == SIDECAR_FORMAT_VERSION
    assert parsed.archive_format_version == ARCHIVE_FORMAT_VERSION
    assert parsed.size_bytes == 0
    assert parsed.recorded_at == datetime(1970, 1, 1, tzinfo=timezone.utc)


def test_parse_sidecar_rejects_invalid_digest_line() -> None:
    with pytest.raises(IntegrityError, match="Invalid sidecar digest line"):
        parse_sidecar("not-a-valid-line\n")


def test_parse_sidecar_ignores_unknown_comments() -> None:
    digest = "d" * 64
    content = (
        f"{digest}  opencloud-2026-06-14_101530.tar.zst\n"
        "# format_version=1\n"
        "# unknown_key=ignored\n"
        "# size_bytes=100\n"
        "# recorded_at=2026-06-14T10:15:30+00:00\n"
        "# archive_format_version=1\n"
    )
    parsed = parse_sidecar(content)
    assert parsed.sha256_hex == digest
    assert parsed.size_bytes == 100

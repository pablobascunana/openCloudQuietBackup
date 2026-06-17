from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from opencloud_backup.domain.archive import (
    ARCHIVE_ENV_MEMBER,
    ARCHIVE_FORMAT_VERSION,
    ARCHIVE_INTERNAL_PREFIX,
    CompressionFormat,
    RESTORE_STAGING_DIR_PREFIX,
    archive_filename,
    archive_output_path,
    default_backup_output_dir,
    detect_compression_format,
    listing_contains_env_member,
    member_indicates_directory,
    normalize_tar_member_line,
    resolve_staging_dir,
    resolve_tar_members,
    tar_member_paths,
    validate_archive_listing,
)
from opencloud_backup.config import ValidationError


def test_archive_format_version_is_one() -> None:
    assert ARCHIVE_FORMAT_VERSION == 1


def test_default_backup_output_dir() -> None:
    root = Path("/data/opencloud")
    assert default_backup_output_dir(root) == root / "backups"


def test_archive_filename_zstd() -> None:
    timestamp = datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc)
    assert archive_filename(timestamp=timestamp, compression=CompressionFormat.ZSTD) == (
        "opencloud-2026-06-14_101530.tar.zst"
    )


def test_archive_filename_gzip() -> None:
    timestamp = datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc)
    assert archive_filename(timestamp=timestamp, compression=CompressionFormat.GZIP) == (
        "opencloud-2026-06-14_101530.tar.gz"
    )


def test_archive_filename_none() -> None:
    timestamp = datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc)
    assert archive_filename(timestamp=timestamp, compression=CompressionFormat.NONE) == (
        "opencloud-2026-06-14_101530.tar"
    )


def test_archive_output_path() -> None:
    timestamp = datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc)
    output_dir = Path("/backups")
    assert archive_output_path(output_dir, timestamp=timestamp, compression=CompressionFormat.ZSTD) == (
        output_dir / "opencloud-2026-06-14_101530.tar.zst"
    )


def test_tar_member_paths_without_env() -> None:
    assert tar_member_paths(include_env=False) == ("config", "data")


def test_tar_member_paths_with_env_flag() -> None:
    assert tar_member_paths(include_env=True) == ("config", "data", ".env")


def test_resolve_tar_members_skips_missing_env(tmp_path: Path) -> None:
    assert resolve_tar_members(tmp_path, include_env=True) == ("config", "data")


def test_resolve_tar_members_includes_existing_env(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
    assert resolve_tar_members(tmp_path, include_env=True) == ("config", "data", ".env")


def test_internal_prefix_constant() -> None:
    assert ARCHIVE_INTERNAL_PREFIX == "opencloud/"


def test_detect_compression_format_tar_zst() -> None:
    assert detect_compression_format(Path("/backups/opencloud.tar.zst")) == CompressionFormat.ZSTD


def test_detect_compression_format_tar_gz() -> None:
    assert detect_compression_format(Path("/backups/opencloud.tar.gz")) == CompressionFormat.GZIP


def test_detect_compression_format_tar() -> None:
    assert detect_compression_format(Path("/backups/opencloud.tar")) == CompressionFormat.NONE


def test_detect_compression_format_invalid_tgz() -> None:
    with pytest.raises(ValidationError, match="Formato de archivo no soportado"):
        detect_compression_format(Path("/backups/opencloud.tgz"))


def test_normalize_tar_member_line_strips_prefix() -> None:
    assert normalize_tar_member_line("./opencloud/config/\n") == "opencloud/config/"


def test_member_indicates_directory_explicit() -> None:
    members = ("opencloud/config/", "opencloud/data/file")
    assert member_indicates_directory(members, "opencloud/config") is True


def test_member_indicates_directory_via_child() -> None:
    members = ("opencloud/config/app.yml",)
    assert member_indicates_directory(members, "opencloud/config") is True


def test_validate_archive_listing_ok() -> None:
    members = ("opencloud/config/", "opencloud/data/file")
    validate_archive_listing(members)


def test_validate_archive_listing_missing_data() -> None:
    members = ("opencloud/config/",)
    with pytest.raises(ValidationError, match="opencloud/data"):
        validate_archive_listing(members)


def test_listing_contains_env_member_true() -> None:
    members = ("opencloud/config/", "opencloud/data/", ARCHIVE_ENV_MEMBER)
    assert listing_contains_env_member(members) is True


def test_listing_contains_env_member_false() -> None:
    members = ("opencloud/config/", "opencloud/data/")
    assert listing_contains_env_member(members) is False


def test_resolve_staging_dir() -> None:
    timestamp = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
    root = Path("/data/opencloud")
    staging = resolve_staging_dir(root, timestamp)
    assert staging == root / f"{RESTORE_STAGING_DIR_PREFIX}2026-06-16_120000"

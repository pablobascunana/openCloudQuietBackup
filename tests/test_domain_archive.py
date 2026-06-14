from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from opencloud_backup.domain.archive import (
    ARCHIVE_FORMAT_VERSION,
    ARCHIVE_INTERNAL_PREFIX,
    CompressionFormat,
    archive_filename,
    archive_output_path,
    default_backup_output_dir,
    resolve_tar_members,
    tar_member_paths,
)


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

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import make_backup_archive_name
from opencloud_backup.adapters.retention import FilesystemRetentionDeleter, list_backup_archives
from opencloud_backup.domain.errors import RetentionError


def test_list_backup_archives_ignores_non_matching_files(tmp_path: Path) -> None:
    matching_name = make_backup_archive_name(datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc))
    (tmp_path / matching_name).write_bytes(b"archive")
    (tmp_path / "manual.tar").write_bytes(b"manual")
    (tmp_path / f"{matching_name}.sha256").write_text("digest\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("notes\n", encoding="utf-8")

    listed = list_backup_archives(tmp_path)

    assert listed == [tmp_path / matching_name]


def test_list_backup_archives_returns_only_regular_files(tmp_path: Path) -> None:
    archive_name = make_backup_archive_name(datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc))
    archive_path = tmp_path / archive_name
    archive_path.write_bytes(b"archive")
    (tmp_path / "opencloud-dir").mkdir()

    listed = list_backup_archives(tmp_path)

    assert listed == [archive_path]
    assert all(path.is_file() for path in listed)


def test_filesystem_retention_deleter_removes_file(tmp_path: Path) -> None:
    target_path = tmp_path / "delete-me.txt"
    target_path.write_text("x", encoding="utf-8")
    deleter = FilesystemRetentionDeleter()

    deleter.delete_file(target_path)

    assert not target_path.exists()


def test_filesystem_retention_deleter_oserror_raises_retention_error(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing.txt"
    deleter = FilesystemRetentionDeleter()

    with pytest.raises(RetentionError) as error_info:
        deleter.delete_file(missing_path)

    assert error_info.value.path == missing_path


def test_list_backup_archives_is_non_recursive(tmp_path: Path) -> None:
    nested_dir = tmp_path / "nested"
    nested_dir.mkdir()
    nested_archive_name = make_backup_archive_name(datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc))
    (nested_dir / nested_archive_name).write_bytes(b"nested")

    listed = list_backup_archives(tmp_path)

    assert listed == []

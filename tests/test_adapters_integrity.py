from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from opencloud_backup.adapters.integrity import FilesystemSidecarStore, HashlibFileHasher
from opencloud_backup.domain.archive import ARCHIVE_FORMAT_VERSION
from opencloud_backup.domain.errors import IntegrityError
from opencloud_backup.domain.integrity import SIDECAR_FORMAT_VERSION, IntegrityRecord, sidecar_path_for_archive

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_hashlib_hasher_empty_file_known_vector(tmp_path: Path) -> None:
    archive_path = tmp_path / "empty.tar.zst"
    archive_path.write_bytes(b"")
    hasher = HashlibFileHasher()
    assert hasher.compute_sha256(archive_path) == EMPTY_SHA256


def test_hashlib_hasher_small_file_chunked(tmp_path: Path) -> None:
    archive_path = tmp_path / "small.tar.zst"
    payload = b"abc" * 100
    archive_path.write_bytes(payload)
    hasher = HashlibFileHasher(chunk_size_bytes=10)
    digest = hasher.compute_sha256(archive_path)
    assert len(digest) == 64
    assert digest != EMPTY_SHA256


def test_filesystem_sidecar_store_write_read_roundtrip(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud-2026-06-14_101530.tar.zst"
    archive_path.write_bytes(b"archive-bytes")
    recorded_at = datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc)
    record = IntegrityRecord(
        sha256_hex="f" * 64,
        archive_basename=archive_path.name,
        size_bytes=13,
        recorded_at=recorded_at,
        format_version=SIDECAR_FORMAT_VERSION,
        archive_format_version=ARCHIVE_FORMAT_VERSION,
    )
    store = FilesystemSidecarStore()
    sidecar_path = store.write(archive_path, record)
    assert sidecar_path == sidecar_path_for_archive(archive_path)
    assert sidecar_path.is_file()
    assert store.read(sidecar_path) == record


def test_filesystem_sidecar_store_read_missing_raises(tmp_path: Path) -> None:
    store = FilesystemSidecarStore()
    missing_sidecar = tmp_path / "missing.tar.zst.sha256"
    with pytest.raises(IntegrityError, match="Sidecar file not found"):
        store.read(missing_sidecar)

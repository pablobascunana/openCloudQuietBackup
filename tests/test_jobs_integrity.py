from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from opencloud_backup.domain.archive import ARCHIVE_FORMAT_VERSION
from opencloud_backup.domain.errors import HashMismatchError, IntegrityError, SidecarNotFoundError
from opencloud_backup.domain.integrity import SIDECAR_FORMAT_VERSION, IntegrityRecord, sidecar_path_for_archive
from opencloud_backup.jobs.integrity import verify_archive_integrity, write_archive_integrity


class FakeFileHasher:
    def __init__(self, *, digest: str = "a" * 64, should_fail: bool = False) -> None:
        self.digest = digest
        self.should_fail = should_fail
        self.calls: list[Path] = []

    def compute_sha256(self, path: Path) -> str:
        self.calls.append(path)
        if self.should_fail:
            raise IntegrityError("hash failed")
        return self.digest


class FakeSidecarStore:
    def __init__(self) -> None:
        self.writes: list[tuple[Path, IntegrityRecord]] = []
        self.records: dict[Path, IntegrityRecord] = {}
        self.should_fail_write = False

    def write(self, archive_path: Path, record: IntegrityRecord) -> Path:
        if self.should_fail_write:
            raise IntegrityError("sidecar write failed")
        sidecar_path = sidecar_path_for_archive(archive_path)
        self.writes.append((archive_path, record))
        self.records[sidecar_path] = record
        return sidecar_path

    def read(self, sidecar_path: Path) -> IntegrityRecord:
        if sidecar_path not in self.records:
            raise IntegrityError("sidecar missing in fake store")
        return self.records[sidecar_path]


def test_write_archive_integrity_returns_sidecar_path(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud-2026-06-14_101530.tar.zst"
    archive_path.write_bytes(b"data")
    store = FakeSidecarStore()
    sidecar_path = write_archive_integrity(
        archive_path,
        file_hasher=FakeFileHasher(digest="b" * 64),
        sidecar_store=store,
        recorded_at=datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc),
    )
    assert sidecar_path == sidecar_path_for_archive(archive_path)


def test_write_archive_integrity_uses_injected_hasher_store(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud-2026-06-14_101530.tar.zst"
    archive_path.write_bytes(b"payload")
    hasher = FakeFileHasher(digest="c" * 64)
    store = FakeSidecarStore()
    write_archive_integrity(
        archive_path,
        file_hasher=hasher,
        sidecar_store=store,
        recorded_at=datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc),
    )
    assert len(hasher.calls) == 1
    assert len(store.writes) == 1
    written_record = store.writes[0][1]
    assert written_record.sha256_hex == "c" * 64
    assert written_record.archive_basename == archive_path.name
    assert written_record.size_bytes == archive_path.stat().st_size
    assert written_record.format_version == SIDECAR_FORMAT_VERSION
    assert written_record.archive_format_version == ARCHIVE_FORMAT_VERSION


def test_verify_archive_integrity_success(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud-2026-06-14_101530.tar.zst"
    archive_path.write_bytes(b"data")
    digest = "d" * 64
    store = FakeSidecarStore()
    sidecar_path = sidecar_path_for_archive(archive_path)
    store.records[sidecar_path] = IntegrityRecord(
        sha256_hex=digest,
        archive_basename=archive_path.name,
        size_bytes=4,
        recorded_at=datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc),
        format_version=SIDECAR_FORMAT_VERSION,
        archive_format_version=ARCHIVE_FORMAT_VERSION,
    )
    sidecar_path.touch()
    verify_archive_integrity(
        archive_path,
        file_hasher=FakeFileHasher(digest=digest),
        sidecar_store=store,
    )


def test_verify_archive_integrity_hash_mismatch_raises(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud-2026-06-14_101530.tar.zst"
    archive_path.write_bytes(b"data")
    store = FakeSidecarStore()
    sidecar_path = sidecar_path_for_archive(archive_path)
    store.records[sidecar_path] = IntegrityRecord(
        sha256_hex="e" * 64,
        archive_basename=archive_path.name,
        size_bytes=4,
        recorded_at=datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc),
        format_version=SIDECAR_FORMAT_VERSION,
        archive_format_version=ARCHIVE_FORMAT_VERSION,
    )
    sidecar_path.touch()
    with pytest.raises(HashMismatchError) as error_info:
        verify_archive_integrity(
            archive_path,
            file_hasher=FakeFileHasher(digest="f" * 64),
            sidecar_store=store,
        )
    assert error_info.value.expected_hex == "e" * 64
    assert error_info.value.actual_hex == "f" * 64


def test_verify_archive_integrity_missing_sidecar_raises(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud-2026-06-14_101530.tar.zst"
    archive_path.write_bytes(b"data")
    with pytest.raises(SidecarNotFoundError):
        verify_archive_integrity(
            archive_path,
            file_hasher=FakeFileHasher(),
            sidecar_store=FakeSidecarStore(),
        )


def test_verify_archive_integrity_custom_sidecar_path(tmp_path: Path) -> None:
    archive_path = tmp_path / "opencloud-2026-06-14_101530.tar.zst"
    archive_path.write_bytes(b"data")
    custom_sidecar = tmp_path / "custom.sha256"
    custom_sidecar.touch()
    digest = "1" * 64
    store = FakeSidecarStore()
    store.records[custom_sidecar] = IntegrityRecord(
        sha256_hex=digest,
        archive_basename=archive_path.name,
        size_bytes=4,
        recorded_at=datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc),
        format_version=SIDECAR_FORMAT_VERSION,
        archive_format_version=ARCHIVE_FORMAT_VERSION,
    )
    verify_archive_integrity(
        archive_path,
        sidecar_path=custom_sidecar,
        file_hasher=FakeFileHasher(digest=digest),
        sidecar_store=store,
    )

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from opencloud_backup.adapters.integrity import (
    FileHasher,
    FilesystemSidecarStore,
    HashlibFileHasher,
    SidecarStore,
)
from opencloud_backup.domain.archive import ARCHIVE_FORMAT_VERSION
from opencloud_backup.domain.errors import HashMismatchError, IntegrityError, SidecarNotFoundError
from opencloud_backup.domain.integrity import SIDECAR_FORMAT_VERSION, IntegrityRecord, sidecar_path_for_archive


def write_archive_integrity(
    archive_path: Path,
    *,
    file_hasher: FileHasher | None = None,
    sidecar_store: SidecarStore | None = None,
    recorded_at: datetime | None = None,
) -> Path:
    hasher = file_hasher if file_hasher is not None else HashlibFileHasher()
    store = sidecar_store if sidecar_store is not None else FilesystemSidecarStore()
    resolved_archive = archive_path.resolve()

    digest = hasher.compute_sha256(resolved_archive)
    archive_stat = resolved_archive.stat()
    record = IntegrityRecord(
        sha256_hex=digest,
        archive_basename=resolved_archive.name,
        size_bytes=archive_stat.st_size,
        recorded_at=recorded_at or datetime.now(timezone.utc),
        format_version=SIDECAR_FORMAT_VERSION,
        archive_format_version=ARCHIVE_FORMAT_VERSION,
    )
    return store.write(resolved_archive, record)


def verify_archive_integrity(
    archive_path: Path,
    *,
    sidecar_path: Path | None = None,
    file_hasher: FileHasher | None = None,
    sidecar_store: SidecarStore | None = None,
) -> None:
    hasher = file_hasher if file_hasher is not None else HashlibFileHasher()
    store = sidecar_store if sidecar_store is not None else FilesystemSidecarStore()
    resolved_archive = archive_path.resolve()
    if not resolved_archive.is_file():
        raise IntegrityError("Archive file not found or not a regular file")

    resolved_sidecar = (sidecar_path or sidecar_path_for_archive(archive_path)).resolve()
    if not resolved_sidecar.is_file():
        raise SidecarNotFoundError(resolved_sidecar)

    expected_record = store.read(resolved_sidecar)
    actual_hex = hasher.compute_sha256(resolved_archive)
    if actual_hex != expected_record.sha256_hex:
        raise HashMismatchError(
            archive_path=resolved_archive,
            expected_hex=expected_record.sha256_hex,
            actual_hex=actual_hex,
        )

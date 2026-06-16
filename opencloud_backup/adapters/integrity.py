from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from opencloud_backup.domain.errors import IntegrityError
from opencloud_backup.domain.integrity import IntegrityRecord, format_sidecar, parse_sidecar, sidecar_path_for_archive

DEFAULT_HASH_CHUNK_SIZE_BYTES: int = 1024 * 1024


class FileHasher(Protocol):
    def compute_sha256(self, path: Path) -> str: ...


class SidecarStore(Protocol):
    def write(self, archive_path: Path, record: IntegrityRecord) -> Path: ...

    def read(self, sidecar_path: Path) -> IntegrityRecord: ...


class HashlibFileHasher:
    def __init__(self, *, chunk_size_bytes: int = DEFAULT_HASH_CHUNK_SIZE_BYTES) -> None:
        self._chunk_size_bytes = chunk_size_bytes

    def compute_sha256(self, path: Path) -> str:
        resolved_path = path.resolve()
        if not resolved_path.is_file():
            raise IntegrityError("Archive file not found or not a regular file")

        digest = hashlib.sha256()
        with resolved_path.open("rb") as archive_file:
            while True:
                chunk = archive_file.read(self._chunk_size_bytes)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()


class FilesystemSidecarStore:
    def write(self, archive_path: Path, record: IntegrityRecord) -> Path:
        sidecar_path = sidecar_path_for_archive(archive_path)
        content = format_sidecar(record)
        temp_path = Path(f"{sidecar_path}.tmp")
        try:
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(sidecar_path)
        except OSError as write_error:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise IntegrityError(f"Failed to write sidecar file: {sidecar_path}") from write_error
        return sidecar_path

    def read(self, sidecar_path: Path) -> IntegrityRecord:
        resolved_sidecar = sidecar_path.resolve()
        if not resolved_sidecar.is_file():
            raise IntegrityError(f"Sidecar file not found or not a regular file: {resolved_sidecar}")
        try:
            content = resolved_sidecar.read_text(encoding="utf-8")
        except OSError as read_error:
            raise IntegrityError(f"Failed to read sidecar file: {resolved_sidecar}") from read_error
        return parse_sidecar(content)

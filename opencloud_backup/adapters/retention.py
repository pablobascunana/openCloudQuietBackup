from __future__ import annotations

from pathlib import Path
from typing import Protocol

from opencloud_backup.domain.errors import RetentionError
from opencloud_backup.domain.retention import is_backup_archive_name


class RetentionDeleter(Protocol):
    def delete_file(self, path: Path) -> None: ...


class FilesystemRetentionDeleter:
    def delete_file(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError as operating_system_error:
            raise RetentionError(path, cause=operating_system_error) from operating_system_error


def list_backup_archives(output_dir: Path) -> list[Path]:
    resolved_output_dir = output_dir.resolve()
    backup_archives: list[Path] = []
    for entry in sorted(resolved_output_dir.iterdir(), key=lambda path: path.name):
        if not entry.is_file():
            continue
        if not is_backup_archive_name(entry.name):
            continue
        backup_archives.append(entry)
    return backup_archives

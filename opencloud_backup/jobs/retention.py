from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from opencloud_backup.adapters.retention import (
    FilesystemRetentionDeleter,
    RetentionDeleter,
    list_backup_archives,
)
from opencloud_backup.config import validate_backup_output_dir
from opencloud_backup.domain.integrity import sidecar_path_for_archive
from opencloud_backup.domain.retention import RetentionPolicy, select_archives_for_deletion


@dataclass(frozen=True, slots=True)
class RetentionResult:
    deleted_archives: tuple[Path, ...]
    deleted_sidecars: tuple[Path, ...]


def _format_retention_log_line(message: str, *, now: datetime | None = None) -> str:
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    return f"[{timestamp}] {message}"


def run_retention_job(
    *,
    output_dir: Path,
    policy: RetentionPolicy,
    protect_archive: Path | None = None,
    deleter: RetentionDeleter | None = None,
    stderr_log: Callable[[str], None] | None = None,
    now: datetime | None = None,
) -> RetentionResult:
    if not policy.is_active:
        return RetentionResult((), ())

    effective_now = now or datetime.now(timezone.utc)
    resolved_output_dir = validate_backup_output_dir(output_dir)

    def log_line(message: str) -> None:
        if stderr_log is not None:
            stderr_log(message)
        else:
            sys.stderr.write(message + "\n")

    log_line(_format_retention_log_line("retention: phase started", now=effective_now))

    candidates = list_backup_archives(resolved_output_dir)
    archives_to_delete = select_archives_for_deletion(
        candidates,
        policy,
        now=effective_now,
        protect_archive=protect_archive,
    )

    filesystem_deleter = deleter if deleter is not None else FilesystemRetentionDeleter()
    deleted_archives: list[Path] = []
    deleted_sidecars: list[Path] = []

    for archive_path in archives_to_delete:
        log_line(
            _format_retention_log_line(
                f"retention: deleting {archive_path.name}",
                now=effective_now,
            )
        )
        filesystem_deleter.delete_file(archive_path)
        deleted_archives.append(archive_path)

        sidecar_path = sidecar_path_for_archive(archive_path)
        if sidecar_path.is_file():
            log_line(
                _format_retention_log_line(
                    f"retention: deleting sidecar {sidecar_path.name}",
                    now=effective_now,
                )
            )
            filesystem_deleter.delete_file(sidecar_path)
            deleted_sidecars.append(sidecar_path)

    log_line(_format_retention_log_line("retention: phase finished", now=effective_now))
    return RetentionResult(tuple(deleted_archives), tuple(deleted_sidecars))

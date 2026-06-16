from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from opencloud_backup.adapters.archive import ArchiveBuilder, SubprocessArchiveBuilder
from opencloud_backup.adapters.docker_compose import ComposeRunner, SubprocessComposeRunner
from opencloud_backup.adapters.integrity import FileHasher, SidecarStore
from opencloud_backup.adapters.prerequisites import HostProbe, run_prerequisite_checks
from opencloud_backup.config import StackPaths
from opencloud_backup.domain.archive import CompressionFormat
from opencloud_backup.domain.errors import (
    ArchiveCommandError,
    ComposeCommandError,
    IntegrityError,
    PrerequisiteCheckError,
)
from opencloud_backup.domain.prereqs import DiskThreshold, JobMode
from opencloud_backup.jobs.integrity import write_archive_integrity


def _format_phase_log_line(message: str, *, now: datetime | None = None) -> str:
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    return f"[{timestamp}] {message}"


def run_backup_job(
    *,
    stack_paths: StackPaths,
    output_dir: Path,
    compression: CompressionFormat,
    include_env: bool,
    disk_check_path: Path,
    disk_threshold: DiskThreshold | None = None,
    stop_timeout_seconds: int,
    start_timeout_seconds: int,
    pack_timeout_seconds: int | None = None,
    write_integrity: bool = False,
    compose_runner: ComposeRunner | None = None,
    archive_builder: ArchiveBuilder | None = None,
    file_hasher: FileHasher | None = None,
    sidecar_store: SidecarStore | None = None,
    stderr_log: Callable[[str], None] | None = None,
    probe: HostProbe | None = None,
    timestamp: datetime | None = None,
) -> Path:
    def log_line(message: str) -> None:
        if stderr_log is not None:
            stderr_log(message)
        else:
            sys.stderr.write(message + "\n")

    prerequisite_report = run_prerequisite_checks(
        mode=JobMode.BACKUP,
        stack_paths=stack_paths,
        disk_check_path=disk_check_path,
        disk_threshold=disk_threshold,
        compression=compression,
        probe=probe,
    )
    if not prerequisite_report.ok:
        raise PrerequisiteCheckError(prerequisite_report)

    runner = compose_runner if compose_runner is not None else SubprocessComposeRunner()
    pack_error: ArchiveCommandError | None = None
    up_error: ComposeCommandError | None = None
    integrity_error: IntegrityError | None = None
    archive_path: Path | None = None
    stack_stopped = False

    try:
        log_line(_format_phase_log_line("backup: stop phase started"))
        try:
            runner.down(stack_paths, stop_timeout_seconds)
        except ComposeCommandError:
            log_line(_format_phase_log_line("backup: stop phase failed"))
            raise
        stack_stopped = True
        log_line(_format_phase_log_line("backup: stop phase finished"))

        packer = archive_builder if archive_builder is not None else SubprocessArchiveBuilder()
        log_line(_format_phase_log_line("backup: pack phase started"))
        try:
            archive_path = packer.create_backup_archive(
                stack_paths,
                output_dir=output_dir,
                compression=compression,
                include_env=include_env,
                pack_timeout_seconds=pack_timeout_seconds,
                archive_timestamp=timestamp,
            )
        except ArchiveCommandError as pack_error_candidate:
            pack_error = pack_error_candidate
            log_line(_format_phase_log_line("backup: pack phase failed"))
        else:
            log_line(_format_phase_log_line("backup: pack phase finished"))
            if write_integrity and archive_path is not None:
                log_line(_format_phase_log_line("backup: hash phase started"))
                try:
                    write_archive_integrity(
                        archive_path,
                        file_hasher=file_hasher,
                        sidecar_store=sidecar_store,
                    )
                except IntegrityError as integrity_error_candidate:
                    integrity_error = integrity_error_candidate
                    log_line(_format_phase_log_line("backup: hash phase failed"))
                else:
                    log_line(_format_phase_log_line("backup: hash phase finished"))
    finally:
        if stack_stopped:
            log_line(_format_phase_log_line("backup: up phase started"))
            try:
                runner.up(stack_paths, start_timeout_seconds)
            except ComposeCommandError as up_error_candidate:
                up_error = up_error_candidate
                log_line(_format_phase_log_line("backup: up phase failed"))
            else:
                log_line(_format_phase_log_line("backup: up phase finished"))

                log_line(_format_phase_log_line("backup: ps phase started"))
                try:
                    ps_output = runner.ps(stack_paths)
                except ComposeCommandError:
                    log_line(_format_phase_log_line("backup: ps phase failed"))
                else:
                    log_line(_format_phase_log_line("backup: ps phase finished"))
                    if stderr_log is not None:
                        stderr_log(ps_output)
                    else:
                        sys.stderr.write(ps_output)

    if up_error is not None:
        raise up_error
    if pack_error is not None:
        # If packing failed but `up` succeeded, we end with an archive error with the stack online.
        raise pack_error
    if integrity_error is not None:
        raise integrity_error
    assert archive_path is not None
    return archive_path

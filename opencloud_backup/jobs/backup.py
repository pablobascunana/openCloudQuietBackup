from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from opencloud_backup.adapters.archive import ArchiveBuilder, SubprocessArchiveBuilder
from opencloud_backup.adapters.docker_compose import ComposeRunner, SubprocessComposeRunner
from opencloud_backup.adapters.prerequisites import HostProbe, run_prerequisite_checks
from opencloud_backup.config import StackPaths
from opencloud_backup.domain.archive import CompressionFormat
from opencloud_backup.domain.errors import ArchiveCommandError, ComposeCommandError, PrerequisiteCheckError
from opencloud_backup.domain.prereqs import DiskThreshold, JobMode


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
    pack_timeout_seconds: int | None = None,
    compose_runner: ComposeRunner | None = None,
    archive_builder: ArchiveBuilder | None = None,
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
    log_line(_format_phase_log_line("backup: stop phase started"))
    try:
        runner.down(stack_paths, stop_timeout_seconds)
    except ComposeCommandError:
        log_line(_format_phase_log_line("backup: stop phase failed"))
        raise
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
    except ArchiveCommandError:
        log_line(_format_phase_log_line("backup: pack phase failed"))
        raise
    log_line(_format_phase_log_line("backup: pack phase finished"))
    return archive_path

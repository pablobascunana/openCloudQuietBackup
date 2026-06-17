from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from opencloud_backup.adapters.archive import ArchiveExtractor, SubprocessArchiveExtractor
from opencloud_backup.adapters.docker_compose import ComposeRunner, SubprocessComposeRunner
from opencloud_backup.adapters.prerequisites import HostProbe, run_prerequisite_checks
from opencloud_backup.adapters.rsync import SubprocessTreeSyncer, TreeSyncer
from opencloud_backup.config import StackPaths, ValidationError, ensure_snapshot_base_dir
from opencloud_backup.domain.archive import (
    CompressionFormat,
    detect_compression_format,
    listing_contains_env_member,
    resolve_staging_dir,
    validate_archive_listing,
)
from opencloud_backup.domain.errors import (
    ArchiveCommandError,
    ComposeCommandError,
    PrerequisiteCheckError,
    RsyncCommandError,
)
from opencloud_backup.domain.prereqs import DiskThreshold, JobMode
from opencloud_backup.domain.restore import RestoreJobResult
from opencloud_backup.domain.snapshot import apply_replace_policy, resolve_snapshot_paths
from opencloud_backup.jobs.integrity import verify_archive_integrity


def _format_phase_log_line(message: str, *, now: datetime | None = None) -> str:
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    return f"[{timestamp}] {message}"


def _cleanup_staging_dir(staging_dir: Path, log_line: Callable[[str], None]) -> Path | None:
    try:
        shutil.rmtree(staging_dir)
    except OSError as operating_system_error:
        log_line(
            _format_phase_log_line(
                f"restore: staging cleanup failed ({operating_system_error})"
            )
        )
        return staging_dir
    return None


def _run_verify_phase(archive_path: Path, log_line: Callable[[str], None]) -> None:
    log_line(_format_phase_log_line("restore: verify phase started"))
    try:
        verify_archive_integrity(archive_path)
    except Exception:
        log_line(_format_phase_log_line("restore: verify phase failed"))
        raise
    log_line(_format_phase_log_line("restore: verify phase finished"))


def _run_snapshot_phase(
    *,
    stack_paths: StackPaths,
    snapshot_base_dir: Path,
    keep_previous_snapshot: bool,
    include_env: bool,
    snapshot_timeout_seconds: int | None,
    tree_syncer: TreeSyncer,
    snapshot_timestamp: datetime,
    log_line: Callable[[str], None],
) -> Path:
    ensure_snapshot_base_dir(snapshot_base_dir)
    apply_replace_policy(snapshot_base_dir, keep_previous=keep_previous_snapshot)
    paths = resolve_snapshot_paths(
        snapshot_base_dir,
        snapshot_timestamp,
        stack_paths,
        include_env=include_env,
    )
    if paths.target_dir.exists():
        raise ValidationError(f"Snapshot directory already exists: {paths.target_dir}")
    paths.target_dir.mkdir(parents=False)
    tree_syncer.sync_tree(
        paths.config_src,
        paths.target_dir / "config",
        timeout_seconds=snapshot_timeout_seconds,
        command_label="rsync snapshot config",
    )
    tree_syncer.sync_tree(
        paths.data_src,
        paths.target_dir / "data",
        timeout_seconds=snapshot_timeout_seconds,
        command_label="rsync snapshot data",
    )
    if paths.env_src is not None:
        tree_syncer.sync_tree(
            paths.env_src,
            paths.target_dir / ".env",
            timeout_seconds=snapshot_timeout_seconds,
            command_label="rsync snapshot env",
        )
    return paths.target_dir.resolve()


def _run_extract_phase(
    *,
    archive_path: Path,
    compression: CompressionFormat,
    archive_extractor: ArchiveExtractor,
    staging_dir: Path,
    extract_timeout_seconds: int | None,
    log_line: Callable[[str], None],
) -> None:
    log_line(_format_phase_log_line("restore: extract phase started"))
    try:
        archive_extractor.extract_archive(
            archive_path,
            staging_dir,
            compression=compression,
            timeout_seconds=extract_timeout_seconds,
        )
    except ArchiveCommandError:
        log_line(_format_phase_log_line("restore: extract phase failed"))
        raise
    log_line(_format_phase_log_line("restore: extract phase finished"))


def _run_apply_phase(
    *,
    stack_paths: StackPaths,
    staging_dir: Path,
    has_env_in_archive: bool,
    tree_syncer: TreeSyncer,
    apply_timeout_seconds: int | None,
    log_line: Callable[[str], None],
) -> None:
    log_line(_format_phase_log_line("restore: apply phase started"))
    opencloud_staging = staging_dir / "opencloud"
    try:
        tree_syncer.sync_tree(
            opencloud_staging / "config",
            stack_paths.config_dir,
            timeout_seconds=apply_timeout_seconds,
            command_label="rsync apply config",
            delete=True,
        )
        tree_syncer.sync_tree(
            opencloud_staging / "data",
            stack_paths.data_dir,
            timeout_seconds=apply_timeout_seconds,
            command_label="rsync apply data",
            delete=True,
        )
        if has_env_in_archive:
            tree_syncer.sync_tree(
                opencloud_staging / ".env",
                stack_paths.opencloud_root / ".env",
                timeout_seconds=apply_timeout_seconds,
                command_label="rsync apply env",
                delete=False,
            )
    except RsyncCommandError:
        log_line(_format_phase_log_line("restore: apply phase failed"))
        raise
    log_line(_format_phase_log_line("restore: apply phase finished"))


def run_restore_job(
    *,
    stack_paths: StackPaths,
    archive_path: Path,
    snapshot_base_dir: Path,
    keep_previous_snapshot: bool = False,
    include_env: bool = True,
    verify_hash: bool = False,
    disk_check_path: Path,
    disk_threshold: DiskThreshold | None = None,
    stop_timeout_seconds: int,
    snapshot_timeout_seconds: int | None = None,
    extract_timeout_seconds: int | None = None,
    apply_timeout_seconds: int | None = None,
    compose_runner: ComposeRunner | None = None,
    tree_syncer: TreeSyncer | None = None,
    archive_extractor: ArchiveExtractor | None = None,
    stderr_log: Callable[[str], None] | None = None,
    probe: HostProbe | None = None,
    snapshot_timestamp: datetime | None = None,
) -> RestoreJobResult:
    def log_line(message: str) -> None:
        if stderr_log is not None:
            stderr_log(message)
        else:
            sys.stderr.write(message + "\n")

    resolved_archive = archive_path.expanduser().resolve()
    compression = detect_compression_format(resolved_archive)

    prerequisite_report = run_prerequisite_checks(
        mode=JobMode.RESTORE,
        stack_paths=stack_paths,
        disk_check_path=disk_check_path,
        disk_threshold=disk_threshold,
        compression=compression,
        probe=probe,
    )
    if not prerequisite_report.ok:
        raise PrerequisiteCheckError(prerequisite_report)

    runner = compose_runner if compose_runner is not None else SubprocessComposeRunner()
    syncer = tree_syncer if tree_syncer is not None else SubprocessTreeSyncer()
    extractor = archive_extractor if archive_extractor is not None else SubprocessArchiveExtractor()

    log_line(_format_phase_log_line("restore: stop phase started"))
    try:
        runner.down(stack_paths, stop_timeout_seconds)
    except ComposeCommandError:
        log_line(_format_phase_log_line("restore: stop phase failed"))
        raise
    log_line(_format_phase_log_line("restore: stop phase finished"))

    log_line(_format_phase_log_line("restore: snapshot phase started"))
    timestamp = snapshot_timestamp or datetime.now(timezone.utc)
    try:
        snapshot_path = _run_snapshot_phase(
            stack_paths=stack_paths,
            snapshot_base_dir=snapshot_base_dir,
            keep_previous_snapshot=keep_previous_snapshot,
            include_env=include_env,
            snapshot_timeout_seconds=snapshot_timeout_seconds,
            tree_syncer=syncer,
            snapshot_timestamp=timestamp,
            log_line=log_line,
        )
    except (ValidationError, RsyncCommandError):
        log_line(_format_phase_log_line("restore: snapshot phase failed"))
        raise
    log_line(_format_phase_log_line("restore: snapshot phase finished"))

    if verify_hash:
        _run_verify_phase(resolved_archive, log_line)

    try:
        members = extractor.list_members(
            resolved_archive,
            compression=compression,
            timeout_seconds=extract_timeout_seconds,
        )
    except ArchiveCommandError:
        raise

    validate_archive_listing(members)
    has_env_in_archive = listing_contains_env_member(members)

    staging_dir = resolve_staging_dir(stack_paths.opencloud_root, timestamp)
    if staging_dir.exists():
        raise ValidationError(f"Staging directory already exists: {staging_dir}")

    staging_dir.mkdir(parents=False)
    try:
        _run_extract_phase(
            archive_path=resolved_archive,
            compression=compression,
            archive_extractor=extractor,
            staging_dir=staging_dir,
            extract_timeout_seconds=extract_timeout_seconds,
            log_line=log_line,
        )
        _run_apply_phase(
            stack_paths=stack_paths,
            staging_dir=staging_dir,
            has_env_in_archive=has_env_in_archive,
            tree_syncer=syncer,
            apply_timeout_seconds=apply_timeout_seconds,
            log_line=log_line,
        )
    except (ArchiveCommandError, RsyncCommandError, ValidationError):
        _cleanup_staging_dir(staging_dir, log_line)
        raise

    residual_staging = _cleanup_staging_dir(staging_dir, log_line)
    return RestoreJobResult(
        snapshot_path=snapshot_path,
        archive_path=resolved_archive,
        staging_path=residual_staging,
    )

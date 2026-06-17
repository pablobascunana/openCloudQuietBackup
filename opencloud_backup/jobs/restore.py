from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from opencloud_backup.adapters.docker_compose import ComposeRunner, SubprocessComposeRunner
from opencloud_backup.adapters.prerequisites import HostProbe, run_prerequisite_checks
from opencloud_backup.adapters.rsync import SubprocessTreeSyncer, TreeSyncer
from opencloud_backup.config import StackPaths, ValidationError, ensure_snapshot_base_dir
from opencloud_backup.domain.errors import ComposeCommandError, PrerequisiteCheckError, RsyncCommandError
from opencloud_backup.domain.prereqs import DiskThreshold, JobMode
from opencloud_backup.domain.snapshot import apply_replace_policy, resolve_snapshot_paths


def _format_phase_log_line(message: str, *, now: datetime | None = None) -> str:
    timestamp = (now or datetime.now(timezone.utc)).isoformat()
    return f"[{timestamp}] {message}"


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


def run_restore_job(
    *,
    stack_paths: StackPaths,
    snapshot_base_dir: Path,
    keep_previous_snapshot: bool = False,
    include_env: bool = True,
    disk_check_path: Path,
    disk_threshold: DiskThreshold | None = None,
    stop_timeout_seconds: int,
    snapshot_timeout_seconds: int | None = None,
    compose_runner: ComposeRunner | None = None,
    tree_syncer: TreeSyncer | None = None,
    stderr_log: Callable[[str], None] | None = None,
    probe: HostProbe | None = None,
    snapshot_timestamp: datetime | None = None,
) -> Path:
    def log_line(message: str) -> None:
        if stderr_log is not None:
            stderr_log(message)
        else:
            sys.stderr.write(message + "\n")

    prerequisite_report = run_prerequisite_checks(
        mode=JobMode.RESTORE,
        stack_paths=stack_paths,
        disk_check_path=disk_check_path,
        disk_threshold=disk_threshold,
        compression=None,
        probe=probe,
    )
    if not prerequisite_report.ok:
        raise PrerequisiteCheckError(prerequisite_report)

    runner = compose_runner if compose_runner is not None else SubprocessComposeRunner()
    syncer = tree_syncer if tree_syncer is not None else SubprocessTreeSyncer()

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
    return snapshot_path

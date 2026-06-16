from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from opencloud_backup.config import StackPaths
from opencloud_backup.domain.archive import CompressionFormat
from opencloud_backup.domain.errors import ArchiveCommandError, ComposeCommandError, PrerequisiteCheckError
from opencloud_backup.domain.prereqs import DiskCheckResult, DiskThreshold, JobMode, PrerequisiteReport
from opencloud_backup.jobs.backup import _format_phase_log_line, run_backup_job


class FakeComposeRunner:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.down_calls: list[tuple[StackPaths, int]] = []

    def down(self, stack_paths: StackPaths, timeout_seconds: int) -> None:
        self.down_calls.append((stack_paths, timeout_seconds))
        if self.should_fail:
            raise ComposeCommandError("docker compose down", 1, "compose failed")


class FakeArchiveBuilder:
    def __init__(self, *, should_fail: bool = False, archive_path: Path | None = None) -> None:
        self.should_fail = should_fail
        self.archive_path = archive_path or Path("/backups/opencloud-2026-06-14_101530.tar.zst")
        self.create_calls: list[dict[str, object]] = []

    def create_backup_archive(
        self,
        stack_paths: StackPaths,
        *,
        output_dir: Path,
        compression: CompressionFormat,
        include_env: bool,
        pack_timeout_seconds: int | None = None,
        archive_timestamp: datetime | None = None,
    ) -> Path:
        self.create_calls.append(
            {
                "stack_paths": stack_paths,
                "output_dir": output_dir,
                "compression": compression,
                "include_env": include_env,
                "archive_timestamp": archive_timestamp,
            }
        )
        if self.should_fail:
            raise ArchiveCommandError("tar create", 1, "pack failed")
        return self.archive_path


def _stack_paths(root: Path) -> StackPaths:
    return StackPaths(
        opencloud_root=root,
        config_dir=root / "config",
        data_dir=root / "data",
        compose_dir=root,
        compose_file=root / "docker-compose.yml",
    )


def _job_kwargs(
    stack_paths: StackPaths,
    *,
    output_dir: Path | None = None,
    **overrides: object,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "stack_paths": stack_paths,
        "output_dir": output_dir or Path("/data/backups"),
        "compression": CompressionFormat.ZSTD,
        "include_env": True,
        "disk_check_path": output_dir or Path("/data/backups"),
        "stop_timeout_seconds": 180,
    }
    kwargs.update(overrides)
    return kwargs


def _ok_prereq_report() -> PrerequisiteReport:
    return PrerequisiteReport(
        ok=True,
        mode=JobMode.BACKUP,
        missing_binaries=(),
        failed_commands=(),
        disk=DiskCheckResult(
            path=Path("/data"),
            total_bytes=100,
            free_bytes=50,
            threshold=None,
            ok=True,
        ),
    )


def _failed_prereq_report() -> PrerequisiteReport:
    return PrerequisiteReport(
        ok=False,
        mode=JobMode.BACKUP,
        missing_binaries=("tar",),
        failed_commands=(),
        disk=None,
    )


def test_format_phase_log_line_uses_utc_iso() -> None:
    fixed_now = datetime(2026, 6, 14, 10, 15, 30, 123456, tzinfo=timezone.utc)
    line = _format_phase_log_line("backup: stop phase started", now=fixed_now)
    assert line == "[2026-06-14T10:15:30.123456+00:00] backup: stop phase started"


def test_run_backup_job_happy_path_returns_archive_path() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    fake_packer = FakeArchiveBuilder()
    log_lines: list[str] = []

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ):
        archive_path = run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=fake_runner,
            archive_builder=fake_packer,
            stderr_log=log_lines.append,
        )

    assert archive_path == fake_packer.archive_path
    assert len(fake_runner.down_calls) == 1
    assert fake_runner.down_calls[0] == (stack_paths, 180)
    assert len(fake_packer.create_calls) == 1
    assert log_lines[0].endswith("backup: stop phase started")
    assert log_lines[1].endswith("backup: stop phase finished")
    assert log_lines[2].endswith("backup: pack phase started")
    assert log_lines[3].endswith("backup: pack phase finished")


def test_run_backup_job_prereqs_fail_no_down_no_logs() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    fake_packer = FakeArchiveBuilder()
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.backup.run_prerequisite_checks",
            return_value=_failed_prereq_report(),
        ),
        pytest.raises(PrerequisiteCheckError),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=fake_runner,
            archive_builder=fake_packer,
            stderr_log=log_lines.append,
        )

    assert fake_runner.down_calls == []
    assert fake_packer.create_calls == []
    assert log_lines == []


def test_run_backup_job_compose_fail_logs_failed_no_pack() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner(should_fail=True)
    fake_packer = FakeArchiveBuilder()
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.backup.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ),
        pytest.raises(ComposeCommandError),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths, stop_timeout_seconds=90),
            compose_runner=fake_runner,
            archive_builder=fake_packer,
            stderr_log=log_lines.append,
        )

    assert log_lines[0].endswith("backup: stop phase started")
    assert log_lines[1].endswith("backup: stop phase failed")
    assert fake_packer.create_calls == []


def test_run_backup_job_pack_fail_logs_failed() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    fake_packer = FakeArchiveBuilder(should_fail=True)
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.backup.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ),
        pytest.raises(ArchiveCommandError),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=fake_runner,
            archive_builder=fake_packer,
            stderr_log=log_lines.append,
        )

    assert log_lines[2].endswith("backup: pack phase started")
    assert log_lines[3].endswith("backup: pack phase failed")


def test_run_backup_job_passes_disk_threshold_and_compression() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    fake_packer = FakeArchiveBuilder()
    threshold = DiskThreshold(kind="bytes", value=1024)
    log_lines: list[str] = []

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ) as mock_checks:
        run_backup_job(
            **_job_kwargs(stack_paths, compression=CompressionFormat.GZIP, disk_threshold=threshold),
            compose_runner=fake_runner,
            archive_builder=fake_packer,
            stderr_log=log_lines.append,
        )

    assert mock_checks.call_args.kwargs["disk_threshold"] == threshold
    assert mock_checks.call_args.kwargs["mode"] == JobMode.BACKUP
    assert mock_checks.call_args.kwargs["compression"] == CompressionFormat.GZIP


def test_run_backup_job_passes_include_env_to_packer() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_packer = FakeArchiveBuilder()

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths, include_env=False),
            compose_runner=FakeComposeRunner(),
            archive_builder=fake_packer,
        )

    assert fake_packer.create_calls[0]["include_env"] is False


def test_run_backup_job_passes_timestamp_to_packer() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_packer = FakeArchiveBuilder()
    fixed_timestamp = datetime(2026, 6, 14, 10, 15, 30, tzinfo=timezone.utc)

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ):
        run_backup_job(
            **_job_kwargs(stack_paths),
            compose_runner=FakeComposeRunner(),
            archive_builder=fake_packer,
            timestamp=fixed_timestamp,
        )

    assert fake_packer.create_calls[0]["archive_timestamp"] == fixed_timestamp


def test_prerequisite_check_error_carries_report() -> None:
    failed_report = _failed_prereq_report()
    with (
        patch(
            "opencloud_backup.jobs.backup.run_prerequisite_checks",
            return_value=failed_report,
        ),
        pytest.raises(PrerequisiteCheckError) as error_info,
    ):
        run_backup_job(
            **_job_kwargs(_stack_paths(Path("/data"))),
            compose_runner=FakeComposeRunner(),
            archive_builder=FakeArchiveBuilder(),
            stderr_log=list.append,
        )
    assert error_info.value.report is failed_report

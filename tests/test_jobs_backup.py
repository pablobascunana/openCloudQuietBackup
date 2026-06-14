from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from opencloud_backup.config import StackPaths
from opencloud_backup.domain.errors import ComposeCommandError, PrerequisiteCheckError
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


def _stack_paths(root: Path) -> StackPaths:
    return StackPaths(
        opencloud_root=root,
        config_dir=root / "config",
        data_dir=root / "data",
        compose_dir=root,
        compose_file=root / "docker-compose.yml",
    )


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


def test_run_backup_job_happy_path_logs_and_calls_down() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    log_lines: list[str] = []

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ):
        run_backup_job(
            stack_paths=stack_paths,
            disk_check_path=Path("/data"),
            stop_timeout_seconds=180,
            compose_runner=fake_runner,
            stderr_log=log_lines.append,
        )

    assert len(fake_runner.down_calls) == 1
    assert fake_runner.down_calls[0] == (stack_paths, 180)
    assert log_lines[0].endswith("backup: stop phase started")
    assert log_lines[1].endswith("backup: stop phase finished")


def test_run_backup_job_prereqs_fail_no_down_no_stop_logs() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.backup.run_prerequisite_checks",
            return_value=_failed_prereq_report(),
        ),
        pytest.raises(PrerequisiteCheckError),
    ):
        run_backup_job(
            stack_paths=stack_paths,
            disk_check_path=Path("/data"),
            stop_timeout_seconds=180,
            compose_runner=fake_runner,
            stderr_log=log_lines.append,
        )

    assert fake_runner.down_calls == []
    assert log_lines == []


def test_run_backup_job_compose_fail_logs_failed() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner(should_fail=True)
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.backup.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ),
        pytest.raises(ComposeCommandError),
    ):
        run_backup_job(
            stack_paths=stack_paths,
            disk_check_path=Path("/data"),
            stop_timeout_seconds=90,
            compose_runner=fake_runner,
            stderr_log=log_lines.append,
        )

    assert log_lines[0].endswith("backup: stop phase started")
    assert log_lines[1].endswith("backup: stop phase failed")


def test_run_backup_job_passes_disk_threshold() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    threshold = DiskThreshold(kind="bytes", value=1024)
    log_lines: list[str] = []

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ) as mock_checks:
        run_backup_job(
            stack_paths=stack_paths,
            disk_check_path=Path("/data"),
            disk_threshold=threshold,
            stop_timeout_seconds=180,
            compose_runner=fake_runner,
            stderr_log=log_lines.append,
        )

    assert mock_checks.call_args.kwargs["disk_threshold"] == threshold
    assert mock_checks.call_args.kwargs["mode"] == JobMode.BACKUP


def test_run_backup_job_uses_backup_mode_for_prereqs() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    log_lines: list[str] = []

    with patch(
        "opencloud_backup.jobs.backup.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ) as mock_checks:
        run_backup_job(
            stack_paths=stack_paths,
            disk_check_path=Path("/data"),
            stop_timeout_seconds=180,
            compose_runner=fake_runner,
            stderr_log=log_lines.append,
        )

    assert mock_checks.call_args.kwargs["mode"] == JobMode.BACKUP


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
            stack_paths=_stack_paths(Path("/data")),
            disk_check_path=Path("/data"),
            stop_timeout_seconds=180,
            compose_runner=FakeComposeRunner(),
            stderr_log=list.append,
        )
    assert error_info.value.report is failed_report

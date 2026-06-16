from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from opencloud_backup.config import StackPaths
from opencloud_backup.domain.errors import ComposeCommandError, PrerequisiteCheckError
from opencloud_backup.domain.prereqs import DiskCheckResult, DiskThreshold, JobMode, PrerequisiteReport
from opencloud_backup.jobs.restore import _format_phase_log_line, run_restore_job


class FakeComposeRunner:
    def __init__(self, *, should_fail_down: bool = False) -> None:
        self.should_fail_down = should_fail_down
        self.down_calls: list[tuple[StackPaths, int]] = []
        self.up_calls: list[tuple[StackPaths, int]] = []

    def down(self, stack_paths: StackPaths, timeout_seconds: int) -> None:
        self.down_calls.append((stack_paths, timeout_seconds))
        if self.should_fail_down:
            raise ComposeCommandError("docker compose down", 1, "compose failed")

    def up(self, stack_paths: StackPaths, timeout_seconds: int) -> None:
        self.up_calls.append((stack_paths, timeout_seconds))

    def ps(self, stack_paths: StackPaths) -> str:
        return "ps-ok"


def _stack_paths(root: Path) -> StackPaths:
    return StackPaths(
        opencloud_root=root,
        config_dir=root / "config",
        data_dir=root / "data",
        compose_dir=root,
        compose_file=root / "docker-compose.yml",
    )


def _job_kwargs(stack_paths: StackPaths, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "stack_paths": stack_paths,
        "disk_check_path": stack_paths.opencloud_root,
        "stop_timeout_seconds": 180,
    }
    kwargs.update(overrides)
    return kwargs


def _ok_prereq_report() -> PrerequisiteReport:
    return PrerequisiteReport(
        ok=True,
        mode=JobMode.RESTORE,
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
        mode=JobMode.RESTORE,
        missing_binaries=("rsync",),
        failed_commands=(),
        disk=None,
    )


def test_format_phase_log_line_uses_utc_iso() -> None:
    fixed_now = datetime(2026, 6, 14, 10, 15, 30, 123456, tzinfo=timezone.utc)
    line = _format_phase_log_line("restore: stop phase started", now=fixed_now)
    assert line == "[2026-06-14T10:15:30.123456+00:00] restore: stop phase started"


def test_run_restore_job_happy_path_stops_stack_no_up() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    log_lines: list[str] = []

    with patch(
        "opencloud_backup.jobs.restore.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ):
        run_restore_job(
            **_job_kwargs(stack_paths),
            compose_runner=fake_runner,
            stderr_log=log_lines.append,
        )

    assert len(fake_runner.down_calls) == 1
    assert fake_runner.down_calls[0] == (stack_paths, 180)
    assert fake_runner.up_calls == []
    assert log_lines[0].endswith("restore: stop phase started")
    assert log_lines[1].endswith("restore: stop phase finished")


def test_run_restore_job_prereqs_fail_no_down_no_logs() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_failed_prereq_report(),
        ),
        pytest.raises(PrerequisiteCheckError),
    ):
        run_restore_job(
            **_job_kwargs(stack_paths),
            compose_runner=fake_runner,
            stderr_log=log_lines.append,
        )

    assert fake_runner.down_calls == []
    assert log_lines == []


def test_run_restore_job_compose_fail_logs_failed_no_up() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner(should_fail_down=True)
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ),
        pytest.raises(ComposeCommandError),
    ):
        run_restore_job(
            **_job_kwargs(stack_paths, stop_timeout_seconds=90),
            compose_runner=fake_runner,
            stderr_log=log_lines.append,
        )

    assert log_lines[0].endswith("restore: stop phase started")
    assert log_lines[1].endswith("restore: stop phase failed")
    assert fake_runner.up_calls == []


def test_run_restore_job_passes_disk_check_path_and_threshold() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    disk_path = Path("/mnt/volume")
    threshold = DiskThreshold(kind="bytes", value=1024)

    with patch(
        "opencloud_backup.jobs.restore.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ) as mock_checks:
        run_restore_job(
            **_job_kwargs(stack_paths, disk_check_path=disk_path, disk_threshold=threshold),
            compose_runner=fake_runner,
        )

    assert mock_checks.call_args.kwargs["disk_check_path"] == disk_path
    assert mock_checks.call_args.kwargs["disk_threshold"] == threshold
    assert mock_checks.call_args.kwargs["mode"] == JobMode.RESTORE
    assert mock_checks.call_args.kwargs["compression"] is None


def test_run_restore_job_prereqs_called_with_restore_mode() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()

    with patch(
        "opencloud_backup.jobs.restore.run_prerequisite_checks",
        return_value=_ok_prereq_report(),
    ) as mock_checks:
        run_restore_job(
            **_job_kwargs(stack_paths),
            compose_runner=fake_runner,
        )

    assert mock_checks.call_args.kwargs["mode"] == JobMode.RESTORE
    assert mock_checks.call_args.kwargs["compression"] is None

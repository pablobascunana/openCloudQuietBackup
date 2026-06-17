from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from conftest import make_valid_stack_tree

from opencloud_backup.config import StackPaths, ValidationError
from opencloud_backup.domain.errors import ComposeCommandError, PrerequisiteCheckError, RsyncCommandError
from opencloud_backup.domain.prereqs import DiskCheckResult, DiskThreshold, JobMode, PrerequisiteReport
from opencloud_backup.domain.snapshot import SNAPSHOT_SUBDIR_PREFIX
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


class FakeTreeSyncer:
    def __init__(self, *, fail_on_label: str | None = None) -> None:
        self.fail_on_label = fail_on_label
        self.sync_calls: list[tuple[Path, Path, int | None, str]] = []

    def sync_tree(
        self,
        source: Path,
        destination: Path,
        *,
        timeout_seconds: int | None = None,
        command_label: str = "rsync snapshot",
    ) -> None:
        if self.fail_on_label == command_label:
            raise RsyncCommandError(command_label, 1, "rsync failed")
        self.sync_calls.append((source, destination, timeout_seconds, command_label))


def _stack_paths(root: Path) -> StackPaths:
    return StackPaths(
        opencloud_root=root,
        config_dir=root / "config",
        data_dir=root / "data",
        compose_dir=root,
        compose_file=root / "docker-compose.yml",
    )


def _job_kwargs(stack_paths: StackPaths, snapshot_base: Path, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "stack_paths": stack_paths,
        "snapshot_base_dir": snapshot_base,
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


def test_run_restore_job_happy_path_returns_snapshot_path() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        (root / ".env").write_text("KEY=1\n", encoding="utf-8")
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        fake_runner = FakeComposeRunner()
        fake_syncer = FakeTreeSyncer()
        log_lines: list[str] = []
        fixed_timestamp = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ):
            snapshot_path = run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base),
                compose_runner=fake_runner,
                tree_syncer=fake_syncer,
                stderr_log=log_lines.append,
                snapshot_timestamp=fixed_timestamp,
            )

        assert snapshot_path == (snapshot_base / "pre-restore-2026-06-16_120000").resolve()
        assert len(fake_runner.down_calls) == 1
        assert fake_runner.up_calls == []
        assert len(fake_syncer.sync_calls) == 3
        assert fake_syncer.sync_calls[0][3] == "rsync snapshot config"
        assert fake_syncer.sync_calls[1][3] == "rsync snapshot data"
        assert fake_syncer.sync_calls[2][3] == "rsync snapshot env"
        assert any(line.endswith("restore: snapshot phase started") for line in log_lines)
        assert any(line.endswith("restore: snapshot phase finished") for line in log_lines)


def test_run_restore_job_include_env_false_skips_env_sync() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        (root / ".env").write_text("KEY=1\n", encoding="utf-8")
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        fake_syncer = FakeTreeSyncer()

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, include_env=False),
                compose_runner=FakeComposeRunner(),
                tree_syncer=fake_syncer,
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert len(fake_syncer.sync_calls) == 2
        assert all("env" not in call[3] for call in fake_syncer.sync_calls)


def test_run_restore_job_prereqs_fail_no_down_no_logs() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner()
    fake_syncer = FakeTreeSyncer()
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_failed_prereq_report(),
        ),
        pytest.raises(PrerequisiteCheckError),
    ):
        run_restore_job(
            **_job_kwargs(stack_paths, Path("/data/snapshots")),
            compose_runner=fake_runner,
            tree_syncer=fake_syncer,
            stderr_log=log_lines.append,
        )

    assert fake_runner.down_calls == []
    assert fake_syncer.sync_calls == []
    assert log_lines == []


def test_run_restore_job_compose_fail_logs_failed_no_snapshot() -> None:
    stack_paths = _stack_paths(Path("/data/opencloud"))
    fake_runner = FakeComposeRunner(should_fail_down=True)
    fake_syncer = FakeTreeSyncer()
    log_lines: list[str] = []

    with (
        patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ),
        pytest.raises(ComposeCommandError),
    ):
        run_restore_job(
            **_job_kwargs(stack_paths, Path("/data/snapshots"), stop_timeout_seconds=90),
            compose_runner=fake_runner,
            tree_syncer=fake_syncer,
            stderr_log=log_lines.append,
        )

    assert log_lines[0].endswith("restore: stop phase started")
    assert log_lines[1].endswith("restore: stop phase failed")
    assert fake_syncer.sync_calls == []
    assert fake_runner.up_calls == []


def test_run_restore_job_rsync_fail_logs_snapshot_failed() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        fake_syncer = FakeTreeSyncer(fail_on_label="rsync snapshot data")
        log_lines: list[str] = []

        with (
            patch(
                "opencloud_backup.jobs.restore.run_prerequisite_checks",
                return_value=_ok_prereq_report(),
            ),
            pytest.raises(RsyncCommandError),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base),
                compose_runner=FakeComposeRunner(),
                tree_syncer=fake_syncer,
                stderr_log=log_lines.append,
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert any(line.endswith("restore: snapshot phase started") for line in log_lines)
        assert any(line.endswith("restore: snapshot phase failed") for line in log_lines)
        assert "restore: snapshot phase finished" not in log_lines


def test_run_restore_job_replace_removes_old_snapshot() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        old_snapshot = snapshot_base / f"{SNAPSHOT_SUBDIR_PREFIX}-2026-01-01_000000"
        old_snapshot.mkdir(parents=True)
        stack_paths = _stack_paths(root)

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base),
                compose_runner=FakeComposeRunner(),
                tree_syncer=FakeTreeSyncer(),
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert not old_snapshot.exists()
        assert (snapshot_base / "pre-restore-2026-06-16_120000").is_dir()


def test_run_restore_job_keep_previous_snapshot() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        old_snapshot = snapshot_base / f"{SNAPSHOT_SUBDIR_PREFIX}-2026-01-01_000000"
        old_snapshot.mkdir(parents=True)
        stack_paths = _stack_paths(root)

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, keep_previous_snapshot=True),
                compose_runner=FakeComposeRunner(),
                tree_syncer=FakeTreeSyncer(),
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert old_snapshot.is_dir()
        assert (snapshot_base / "pre-restore-2026-06-16_120000").is_dir()


def test_run_restore_job_passes_snapshot_timeout_to_syncer() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        fake_syncer = FakeTreeSyncer()

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, snapshot_timeout_seconds=120),
                compose_runner=FakeComposeRunner(),
                tree_syncer=fake_syncer,
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert all(call[2] == 120 for call in fake_syncer.sync_calls)


def test_run_restore_job_passes_disk_check_path_and_threshold() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        stack_paths = _stack_paths(root)
        disk_path = Path(temporary_directory) / "mnt" / "volume"
        disk_path.mkdir(parents=True)
        threshold = DiskThreshold(kind="bytes", value=1024)

        with patch(
            "opencloud_backup.jobs.restore.run_prerequisite_checks",
            return_value=_ok_prereq_report(),
        ) as mock_checks:
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, disk_check_path=disk_path, disk_threshold=threshold),
                compose_runner=FakeComposeRunner(),
                tree_syncer=FakeTreeSyncer(),
                snapshot_timestamp=datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc),
            )

        assert mock_checks.call_args.kwargs["disk_check_path"] == disk_path
        assert mock_checks.call_args.kwargs["disk_threshold"] == threshold
        assert mock_checks.call_args.kwargs["mode"] == JobMode.RESTORE
        assert mock_checks.call_args.kwargs["compression"] is None


def test_run_restore_job_timestamp_collision_raises() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory) / "oc"
        make_valid_stack_tree(root)
        snapshot_base = root / "snapshots"
        snapshot_base.mkdir()
        fixed_timestamp = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
        (snapshot_base / "pre-restore-2026-06-16_120000").mkdir()
        stack_paths = _stack_paths(root)
        log_lines: list[str] = []

        with (
            patch(
                "opencloud_backup.jobs.restore.run_prerequisite_checks",
                return_value=_ok_prereq_report(),
            ),
            pytest.raises(ValidationError, match="already exists"),
        ):
            run_restore_job(
                **_job_kwargs(stack_paths, snapshot_base, keep_previous_snapshot=True),
                compose_runner=FakeComposeRunner(),
                tree_syncer=FakeTreeSyncer(),
                stderr_log=log_lines.append,
                snapshot_timestamp=fixed_timestamp,
            )

        assert any(line.endswith("restore: snapshot phase failed") for line in log_lines)
